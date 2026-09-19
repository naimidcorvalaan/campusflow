"""P2c Agentic 日计划管线（Python 3.8 兼容）。

headless 管线：
  DayPlanningState + user_text
    -> Reconciliation Agent（可 repair）
    -> 程序安全应用任务反馈（src.p2_state_reconciler）
    -> Day Plan Agent（可 repair + 内容校验）
    -> 程序应用 AI 暂估（防覆盖）
    -> P2b deterministic allocator
    -> Review Agent（可 repair）
    -> 最多一次 Revision（可 repair）
    -> P2AgenticDayResult
    -> summarize_day_plan 安全摘要

模型调用硬上限：MAX_CALLS_PER_ROUND（默认 24）。所有调用必须通过可注入
callable（本轮只使用 mock，不绑定任何真实 adapter / API）。
skip_reconciliation=True 时跳过任务 reconciliation（用于首次全天计划 / 刷新）。
输入 state 永不修改；全部返回新实例。
"""

import json
from dataclasses import dataclass, replace
from typing import Callable, Optional, Sequence, Tuple

from src.p1_models import SourceKind
from src.p1_window_models import AvailabilityLevel
from src.p2_agentic_models import (
    DAY_PLAN_INTENT_SCHEMA_VERSION,
    MAX_CALLS_PER_ROUND,
    RECONCILIATION_SCHEMA_VERSION,
    REVIEW_SCHEMA_VERSION,
    DayPlanIntent,
    P2AgenticDayResult,
    ReconciliationResult,
    ReviewResult,
    TaskEstimate,
)
from src.p2_agentic_parser import (
    AgenticParseError,
    extract_json_object,
    parse_day_plan_intent,
    parse_reconciliation,
    parse_review,
)
from src.p2_agentic_prompt_builder import (
    build_day_plan_prompt,
    build_reconciliation_prompt,
    build_repair_prompt,
    build_review_prompt,
    build_revision_prompt,
    build_splittability_review_prompt,
)
from src.p2_allocator import allocate_tasks_across_windows
from src.p2_allocation_models import DayAllocationPlan
from src.p2_day_plan import summarize_day_plan
from src.p2_models import DayPlanningState, TaskProgress, append_history
from src.p2_state_reconciler import ReconciliationApplied, apply_reconciliation

AgentCaller = Callable[[str, str], str]

WARNING_RECONCILIATION_FAILED = "本轮任务反馈暂未结构化应用"
WARNING_PLAN_FALLBACK = "本轮计划决策未结构化或未通过校验，已使用默认任务顺序"
WARNING_REVIEW_FAILED = "本轮方案审查暂未完成"
WARNING_REVISION_FAILED = "修订建议未被应用，保留当前合法计划"
WARNING_ESTIMATE_IGNORED = "已忽略对任务 {ref}（{title}）的 AI 时长估计：总时长已知"
WARNING_ESTIMATE_BELOW_COMPLETED = "已忽略对任务 {ref}（{title}）的 AI 时长估计：估计值不高于已完成进度"

SPLITTABILITY_REVIEW_SCHEMA_NAME = "p2.splittability-review.v1"
MIN_MEANINGFUL_SLICE_MINUTES = 10
MAX_SPLITTABILITY_REVIEWS_PER_ROUND = 3
WARNING_SPLITTABILITY_REVIEW_FAILED = "任务拆分复核暂未完成，保留当前判断"


def run_p2_agentic_day_planning(
    state: DayPlanningState,
    user_text: str,
    caller: AgentCaller,
    reconciliation_caller: Optional[AgentCaller] = None,
    plan_caller: Optional[AgentCaller] = None,
    review_caller: Optional[AgentCaller] = None,
    revision_caller: Optional[AgentCaller] = None,
    repair_caller: Optional[AgentCaller] = None,
    splittability_caller: Optional[AgentCaller] = None,
    skip_reconciliation: bool = False,
    seed_day_plan_intent: Optional[DayPlanIntent] = None,
    skip_legacy_review: bool = False,
    max_calls_per_round: int = MAX_CALLS_PER_ROUND,
    effective_duration_by_task_ref=None,
) -> P2AgenticDayResult:
    """执行一轮 P2c Agentic 全天规划，返回结果快照（不修改输入 state）。"""
    _require_state(state)
    _require_user_text(user_text)
    _require_callable(caller)
    if seed_day_plan_intent is not None and not isinstance(
        seed_day_plan_intent, DayPlanIntent
    ):
        raise TypeError("seed_day_plan_intent must be DayPlanIntent or None")
    if not isinstance(skip_legacy_review, bool):
        raise ValueError("skip_legacy_review must be a bool")
    if isinstance(max_calls_per_round, bool) or not isinstance(max_calls_per_round, int):
        raise ValueError("max_calls_per_round must be an integer")
    if max_calls_per_round < 1:
        raise ValueError("max_calls_per_round must be >= 1")
    if max_calls_per_round > MAX_CALLS_PER_ROUND:
        raise ValueError("max_calls_per_round must not exceed MAX_CALLS_PER_ROUND")

    rec_caller = _pick(reconciliation_caller, caller)
    plan_caller = _pick(plan_caller, caller)
    review_caller = _pick(review_caller, caller)
    revision_caller = _pick(revision_caller, caller)
    repair_caller = _pick(repair_caller, caller)
    splittability_caller = _pick(splittability_caller, caller)

    calls = [0]

    def call(caller_fn: AgentCaller, system_prompt: str, user_prompt: str) -> Optional[str]:
        if calls[0] >= max_calls_per_round:
            return None
        calls[0] += 1
        return caller_fn(system_prompt, user_prompt)

    warnings = []
    questions = []

    # 1) Reconciliation（skip_reconciliation 时直接跳过，不调用任务 reconciler）
    rec_result = None
    rec_warning = None
    if not skip_reconciliation:
        rec_result, rec_warning = _run_reconciliation(state, user_text, call, rec_caller, repair_caller)
    if rec_warning is not None:
        warnings.append(rec_warning)
        applied = ReconciliationApplied(
            state=state, applied_entries=(), warnings=(), new_task_refs=(), questions=()
        )
    else:
        if rec_result is not None:
            applied = apply_reconciliation(state, rec_result)
        else:
            applied = ReconciliationApplied(
                state=state, applied_entries=(), warnings=(), new_task_refs=(), questions=()
            )
        warnings.extend(applied.warnings)
        questions.extend(applied.questions)

    planning_state = applied.state

    # 2) Day Plan intent.  A P5 feedback/what-if rebuild can safely reuse the
    # already parsed, ref-validated intent from the last reliable turn.  This
    # preserves missing-duration estimates without asking an older strategy
    # pass to reinterpret the same canonical facts again.
    if (
        seed_day_plan_intent is not None
        and _validate_intent(seed_day_plan_intent, planning_state) is None
    ):
        intent = seed_day_plan_intent
    else:
        intent, plan_warning = _run_day_plan(
            planning_state, user_text, call, plan_caller, repair_caller
        )
        if plan_warning is not None:
            warnings.append(plan_warning)
            intent = None

    # Keep the pre-estimate ledger: a same-round semantic correction may
    # replace tentative AI effort, never a user/adopted or prior-round fact.
    before_estimates = planning_state
    # 3) 程序应用 AI 暂估（防覆盖）
    planning_state, estimate_warnings = _apply_estimates(planning_state, intent)
    warnings.extend(estimate_warnings)

    # 4) P2b deterministic allocator
    task_order = intent.task_order if intent is not None else None
    include_low_attention = intent.include_low_attention if intent is not None else False
    allocation_plan = allocate_tasks_across_windows(
        planning_state,
        task_order=task_order,
        include_low_attention=include_low_attention,
        effective_duration_by_task_ref=effective_duration_by_task_ref,
    )

    # 5) Splittability review：因“不可拆/未知”而完全未分配的任务最多复核一次
    allocation_plan, planning_state, splittability_warnings = _run_splittability_review(
        planning_state, allocation_plan, task_order, include_low_attention, call,
        splittability_caller, repair_caller, effective_duration_by_task_ref,
    )
    warnings.extend(splittability_warnings)

    # 6) Review + 最多一次 Revision
    revision_used = False
    review_result = None
    review_warning = None
    has_new_estimate = bool(intent and any(
        item.total_minutes is None and item.task_ref in {e.task_ref for e in intent.task_estimates}
        for item in before_estimates.tasks
    ))
    if not skip_legacy_review or has_new_estimate:
        review_result, review_warning = _run_review(
            planning_state, user_text, intent, allocation_plan, call,
            review_caller, repair_caller,
        )
    if review_warning is not None:
        warnings.append(review_warning)
    elif review_result is not None and review_result.decision == "revise":
        revised_intent, revision_warning = _run_revision(
            _restore_round_estimate_slots(planning_state, before_estimates),
            user_text,
            intent,
            allocation_plan,
            review_result,
            call,
            revision_caller,
            repair_caller,
        )
        if (
            revision_warning is None
            and revised_intent is not None
            and _validate_intent(revised_intent, planning_state) is None
        ):
            intent = revised_intent
            # Revisions of a tentative estimate use its original unknown
            # slot. Applying onto the already-filled ledger made every AI
            # estimate immutable before any reviewer could correct it.
            estimation_base = _restore_round_estimate_slots(planning_state, before_estimates,
                {estimate.task_ref for estimate in revised_intent.task_estimates})
            planning_state, extra_warnings = _apply_estimates(estimation_base, revised_intent)
            warnings.extend(extra_warnings)
            allocation_plan = allocate_tasks_across_windows(
                planning_state,
                task_order=revised_intent.task_order,
                include_low_attention=revised_intent.include_low_attention,
                effective_duration_by_task_ref=effective_duration_by_task_ref,
            )
            revision_used = True
        else:
            warnings.append(WARNING_REVISION_FAILED)

    # 7) 安全摘要 + history
    day_summary = summarize_day_plan(allocation_plan, planning_state)
    history_entry = _build_history_entry(applied, rec_warning is not None)
    updated_state = _rebuild_state(planning_state, history=append_history(planning_state.history, history_entry))

    return P2AgenticDayResult(
        original_state=state,
        updated_state=updated_state,
        reconciliation_result=rec_result,
        day_plan_intent=intent,
        allocation_plan=allocation_plan,
        day_summary=day_summary,
        review_result=review_result,
        revision_used=revision_used,
        call_count=calls[0],
        warnings=tuple(warnings),
        questions=tuple(questions),
    )


def _run_reconciliation(
    state: DayPlanningState,
    user_text: str,
    call: Callable,
    rec_caller: AgentCaller,
    repair_caller: AgentCaller,
) -> Tuple[Optional[ReconciliationResult], Optional[str]]:
    system, user = build_reconciliation_prompt(state, user_text)
    text = call(rec_caller, system, user)
    result = _parse_reconciliation(text)
    if result is not None:
        return result, None
    repair_system, repair_user = build_repair_prompt(
        "reconciliation", text, RECONCILIATION_SCHEMA_VERSION
    )
    repaired = call(repair_caller, repair_system, repair_user)
    result = _parse_reconciliation(repaired)
    if result is not None:
        return result, None
    return None, WARNING_RECONCILIATION_FAILED


def _run_day_plan(
    state: DayPlanningState,
    user_text: str,
    call: Callable,
    plan_caller: AgentCaller,
    repair_caller: AgentCaller,
) -> Tuple[Optional[DayPlanIntent], Optional[str]]:
    system, user = build_day_plan_prompt(state, user_text)
    text = call(plan_caller, system, user)
    intent = _parse_intent(text)
    if intent is not None and _validate_intent(intent, state) is None:
        return intent, None
    repair_system, repair_user = build_repair_prompt(
        "day_plan", text, DAY_PLAN_INTENT_SCHEMA_VERSION
    )
    repaired = call(repair_caller, repair_system, repair_user)
    intent = _parse_intent(repaired)
    if intent is not None and _validate_intent(intent, state) is None:
        return intent, None
    return None, WARNING_PLAN_FALLBACK


@dataclass(frozen=True)
class SplittabilityReviewResult:
    """Splittability Review Agent 的输出（程序校验后使用）。"""
    is_splittable: bool
    minimum_slice_minutes: Optional[int]
    reason: Optional[str]

    def __post_init__(self):
        if not isinstance(self.is_splittable, bool):
            raise ValueError("is_splittable must be a bool")
        if self.minimum_slice_minutes is not None:
            if isinstance(self.minimum_slice_minutes, bool) or not isinstance(
                self.minimum_slice_minutes, int
            ):
                raise ValueError("minimum_slice_minutes must be an int or None")
            if self.minimum_slice_minutes < 1:
                raise ValueError("minimum_slice_minutes must be >= 1")
        if self.reason is not None and not isinstance(self.reason, str):
            raise ValueError("reason must be a str or None")


def _run_splittability_review(
    state: DayPlanningState,
    allocation_plan: DayAllocationPlan,
    task_order: Optional[Sequence[str]],
    include_low_attention: bool,
    call: Callable,
    review_caller: AgentCaller,
    repair_caller: AgentCaller,
    effective_duration_by_task_ref=None,
):
    """对“因不可拆而完全未分配”的任务做最多一次复核，然后重新分配一次。

    - 触发条件（全部满足）：任务 ACTIVE、有 remaining、本轮完全未分配、
      is_splittable 非 True、remaining > 最大可用窗口、且最大窗口 >= 10 分钟；
      第一次模型给出的 minimum_slice_minutes 不参与候选判断，不能阻止复核；
    - 每任务最多一次 review（可带一次 repair），不无限循环；
    - review 明确返回 false 时尊重模型判断，不强改；
    - 复核结果通过程序校验后才写回 TaskProgress，随后重新运行 allocator。
    """
    if not isinstance(state, DayPlanningState):
        raise TypeError("state must be a DayPlanningState")
    task_by_ref = {task.task_ref: task for task in state.tasks}
    planned_refs = {allocation.task_ref for allocation in allocation_plan.allocations}
    candidates = [
        task
        for task in state.tasks
        if task.task_ref in allocation_plan.unallocated_task_refs
        and task.task_ref not in planned_refs
        and task.remaining_minutes is not None
        and task.remaining_minutes > 0
        and task.is_splittable is not True
    ]
    if not candidates:
        return allocation_plan, state, ()

    usable = [
        window
        for window in state.windows
        if window.ends_at > state.now
        and window.capacity_minutes > 0
        and (include_low_attention or window.availability != AvailabilityLevel.LOW_ATTENTION)
    ]
    if not usable:
        return allocation_plan, state, ()
    max_capacity = max(window.capacity_minutes for window in usable)
    # 只有“剩余比任何可用窗口都大”才可能属于误判不可拆；
    # 剩余 <= 某窗口容量仍未被安排，说明是任务本身生命周期/顺序问题，不进入复核。
    if max_capacity < MIN_MEANINGFUL_SLICE_MINUTES:
        return allocation_plan, state, ()
    candidates = [
        task
        for task in candidates
        if task.remaining_minutes > max_capacity
    ]
    if not candidates:
        return allocation_plan, state, ()

    changed_tasks = {}
    warnings = []
    for task in candidates[:MAX_SPLITTABILITY_REVIEWS_PER_ROUND]:
        result = _review_task_splittability(task, usable, call, review_caller, repair_caller)
        if result is None:
            warnings.append(WARNING_SPLITTABILITY_REVIEW_FAILED)
            continue
        if not result.is_splittable:
            # 尊重模型“必须一次完成”的判断，不程序强行改 true
            continue
        slice_value = result.minimum_slice_minutes
        if slice_value is not None and slice_value > max(
            window.capacity_minutes for window in usable
        ):
            # 模型给出的最小片段超过所有窗口容量：按不可排处理，不写回
            continue
        changed_tasks[task.task_ref] = slice_value

    if not changed_tasks:
        return allocation_plan, state, tuple(warnings)

    new_tasks = tuple(
        _apply_reviewed_splittability(task, changed_tasks.get(task.task_ref))
        if task.task_ref in changed_tasks
        else task
        for task in state.tasks
    )
    reviewed_state = _rebuild_state(state, tasks=new_tasks)
    reviewed_plan = allocate_tasks_across_windows(
        reviewed_state,
        task_order=task_order,
        include_low_attention=include_low_attention,
        effective_duration_by_task_ref=effective_duration_by_task_ref,
    )
    return reviewed_plan, reviewed_state, tuple(warnings)


def _review_task_splittability(
    task: TaskProgress,
    usable_windows: Sequence,
    call: Callable,
    review_caller: AgentCaller,
    repair_caller: AgentCaller,
) -> Optional[SplittabilityReviewResult]:
    """对单个任务执行一次 review（+最多一次 repair），返回校验通过的结果或 None。"""
    system, user = build_splittability_review_prompt(
        task,
        task.remaining_minutes,
        tuple(usable_windows),
        task.is_splittable,
        task.minimum_slice_minutes,
    )
    text = call(review_caller, system, user)
    result = _parse_splittability_review(text)
    if result is not None:
        return result
    repair_system, repair_user = build_repair_prompt(
        "splittability_review", text, SPLITTABILITY_REVIEW_SCHEMA_NAME
    )
    repaired = call(repair_caller, repair_system, repair_user)
    return _parse_splittability_review(repaired)


def _parse_splittability_review(text: Optional[str]) -> Optional[SplittabilityReviewResult]:
    if text is None:
        return None
    try:
        payload = extract_json_object(text)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if "is_splittable" not in payload:
        return None
    try:
        return SplittabilityReviewResult(
            is_splittable=payload["is_splittable"],
            minimum_slice_minutes=payload.get("minimum_slice_minutes"),
            reason=payload.get("reason"),
        )
    except (TypeError, ValueError):
        return None


def _apply_reviewed_splittability(task: TaskProgress, slice_value: Optional[int]) -> TaskProgress:
    if task.attention_mode == 'background':
        return task  # elapsed process is continuous; attention is reviewed upstream
    return TaskProgress(
        task_ref=task.task_ref,
        title=task.title,
        total_minutes=task.total_minutes,
        completed_minutes=task.completed_minutes,
        total_source=task.total_source,
        state=task.state,
        is_splittable=True,
        minimum_slice_minutes=slice_value if slice_value is not None else task.minimum_slice_minutes,
        predecessor_task_refs=task.predecessor_task_refs,
        departure_after_task_refs=task.departure_after_task_refs,
        overlap_task_ref=task.overlap_task_ref,
        attention_mode=task.attention_mode, launch_task_ref=task.launch_task_ref,
        background_reason=task.background_reason, user_reported_running=task.user_reported_running,
    )


def _run_review(
    state: DayPlanningState,
    user_text: str,
    intent: Optional[DayPlanIntent],
    plan,
    call: Callable,
    review_caller: AgentCaller,
    repair_caller: AgentCaller,
) -> Tuple[Optional[ReviewResult], Optional[str]]:
    system, user = build_review_prompt(state, user_text, intent, plan)
    text = call(review_caller, system, user)
    result = _parse_review(text)
    if result is not None:
        return result, None
    repair_system, repair_user = build_repair_prompt("review", text, REVIEW_SCHEMA_VERSION)
    repaired = call(repair_caller, repair_system, repair_user)
    result = _parse_review(repaired)
    if result is not None:
        return result, None
    return None, WARNING_REVIEW_FAILED


def _run_revision(
    state: DayPlanningState,
    user_text: str,
    intent: Optional[DayPlanIntent],
    plan,
    review: ReviewResult,
    call: Callable,
    revision_caller: AgentCaller,
    repair_caller: AgentCaller,
) -> Tuple[Optional[DayPlanIntent], Optional[str]]:
    system, user = build_revision_prompt(state, user_text, intent, plan, review)
    text = call(revision_caller, system, user)
    revised = _parse_intent(text)
    if revised is not None:
        return revised, None
    repair_system, repair_user = build_repair_prompt(
        "revision", text, DAY_PLAN_INTENT_SCHEMA_VERSION
    )
    repaired = call(repair_caller, repair_system, repair_user)
    revised = _parse_intent(repaired)
    if revised is not None:
        return revised, None
    return None, WARNING_REVISION_FAILED


def _restore_round_estimate_slots(current, original, refs=None):
    """Reopen only fields that were unknown at entry to this transaction."""
    before = {task.task_ref: task for task in original.tasks}
    tasks = []
    for task in current.tasks:
        old = before.get(task.task_ref)
        if old is not None and old.total_minutes is None and (refs is None or task.task_ref in refs):
            task = replace(task, total_minutes=None, total_source=old.total_source,
                is_splittable=old.is_splittable, minimum_slice_minutes=old.minimum_slice_minutes)
        tasks.append(task)
    return _rebuild_state(current, tasks=tuple(tasks))


def _apply_estimates(
    state: DayPlanningState, intent: Optional[DayPlanIntent]
) -> Tuple[DayPlanningState, Tuple[str, ...]]:
    """只补缺失信息：total 未知时才估计；is_splittable / minimum_slice 仅原值未知时补。

    total 已存在时（包括 AI_ESTIMATED 来源）保持稳定，不每轮重估；
    非 AI_ESTIMATED 来源的已知 total 被 AI 建议覆盖时给出 warning。
    """
    if intent is None:
        return state, ()
    warnings = []
    tasks = list(state.tasks)
    task_by_ref = {task.task_ref: task for task in tasks}
    changed = False
    for estimate in intent.task_estimates:
        task = task_by_ref.get(estimate.task_ref)
        if task is None:
            continue
        if task.total_minutes is not None:
            if task.total_source != SourceKind.AI_ESTIMATED:
                warnings.append(
                    WARNING_ESTIMATE_IGNORED.format(ref=task.task_ref, title=task.title)
                )
            continue
        if estimate.estimated_total_minutes <= task.completed_minutes:
            # AI 对总时长的估计不得仅因 estimate <= completed 就间接触发
            # P2a 的自动完成规范化；跳过该估计，保留原 total 与生命周期。
            warnings.append(
                WARNING_ESTIMATE_BELOW_COMPLETED.format(ref=task.task_ref, title=task.title)
            )
            continue
        task_by_ref[estimate.task_ref] = _apply_estimate_fields(task, estimate)
        changed = True
    if not changed:
        return state, tuple(warnings)
    new_tasks = tuple(task_by_ref[task.task_ref] for task in tasks)
    return _rebuild_state(state, tasks=new_tasks), tuple(warnings)


def _apply_estimate_fields(task: TaskProgress, estimate: TaskEstimate) -> TaskProgress:
    is_splittable = task.is_splittable
    if is_splittable is None and estimate.is_splittable is not None:
        is_splittable = estimate.is_splittable
    minimum_slice = task.minimum_slice_minutes
    if minimum_slice is None and estimate.minimum_slice_minutes is not None:
        minimum_slice = estimate.minimum_slice_minutes
    return TaskProgress(
        task_ref=task.task_ref,
        title=task.title,
        total_minutes=estimate.estimated_total_minutes,
        completed_minutes=task.completed_minutes,
        total_source=SourceKind.AI_ESTIMATED,
        state=task.state,
        is_splittable=is_splittable,
        minimum_slice_minutes=minimum_slice,
        predecessor_task_refs=task.predecessor_task_refs,
        departure_after_task_refs=task.departure_after_task_refs,
        overlap_task_ref=task.overlap_task_ref,
        attention_mode=task.attention_mode, launch_task_ref=task.launch_task_ref,
        background_reason=task.background_reason, user_reported_running=task.user_reported_running,
    )


def _validate_intent(intent: DayPlanIntent, state: DayPlanningState) -> Optional[str]:
    """内容校验：task_order / estimates 只能引用真实存在的 task_ref，且不重复。"""
    if intent is None:
        return "intent is None"
    refs = {task.task_ref for task in state.tasks}
    if len(intent.task_order) != len(set(intent.task_order)):
        return "task_order 包含重复 ref"
    for ref in intent.task_order:
        if ref not in refs:
            return "task_order 包含未知 task_ref：{}".format(ref)
    for estimate in intent.task_estimates:
        if estimate.task_ref not in refs:
            return "task_estimates 包含未知 task_ref：{}".format(estimate.task_ref)
    return None


def _parse_reconciliation(text: Optional[str]) -> Optional[ReconciliationResult]:
    if text is None:
        return None
    try:
        return parse_reconciliation(text)
    except AgenticParseError:
        return None


def _parse_intent(text: Optional[str]) -> Optional[DayPlanIntent]:
    if text is None:
        return None
    try:
        return parse_day_plan_intent(text)
    except AgenticParseError:
        return None


def _parse_review(text: Optional[str]) -> Optional[ReviewResult]:
    if text is None:
        return None
    try:
        return parse_review(text)
    except AgenticParseError:
        return None


def _build_history_entry(applied: ReconciliationApplied, reconciliation_failed: bool) -> str:
    if reconciliation_failed:
        return "本轮任务反馈暂未应用；已生成当天计划。"
    if applied.applied_entries:
        return "用户反馈：{}；当前计划已重新生成。".format("；".join(applied.applied_entries))
    return "当前计划已重新生成。"


def _rebuild_state(state: DayPlanningState, tasks=None, history=None) -> DayPlanningState:
    return DayPlanningState(
        reference_datetime=state.reference_datetime,
        now=state.now,
        day_end=state.day_end,
        commitments=state.commitments,
        tasks=tasks if tasks is not None else state.tasks,
        windows=state.windows,
        active_window_ref=state.active_window_ref,
        unresolved_commitment_refs=state.unresolved_commitment_refs,
        history=history if history is not None else state.history,
    )


def _pick(override: Optional[AgentCaller], default: AgentCaller) -> AgentCaller:
    return override if override is not None else default


def _require_state(state: DayPlanningState) -> None:
    if not isinstance(state, DayPlanningState):
        raise TypeError("state must be a DayPlanningState")


def _require_user_text(user_text: str) -> None:
    if not isinstance(user_text, str) or not user_text.strip():
        raise ValueError("user_text must be a non-empty string")


def _require_callable(fn: AgentCaller) -> None:
    if not callable(fn):
        raise TypeError("caller must be callable")
