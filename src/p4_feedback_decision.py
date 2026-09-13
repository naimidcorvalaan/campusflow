"""Qwen-assisted feedback semantics with deterministic task-ref constraints.

The model decides what the user meant.  This module validates every referenced
task against the canonical day ledger and turns the accepted decision into
ordering/lifecycle facts.  It never calculates time, routes, or feasibility.
"""

import json
from dataclasses import dataclass, replace
from typing import Callable, Optional, Sequence, Tuple

from src.p2_agentic_models import (
    LifecycleAction,
    ReconciliationResult,
    ReconciliationUpdate,
    RECONCILIATION_SCHEMA_VERSION,
)
from src.p2_agentic_parser import AgenticParseError, extract_json_object
from src.p2_models import DayPlanningState, TaskState
from src.p2_state_reconciler import apply_reconciliation


FEEDBACK_DECISION_SCHEMA_VERSION = "p4.feedback-decision.v1"
FEEDBACK_DECISION_REVIEW_SCHEMA_VERSION = "p4.feedback-decision-review.v1"
INTENT_COMPLIANCE_SCHEMA_VERSION = "p4.intent-compliance-review.v1"
P4_FEEDBACK_DECISION_KEY = "p4_feedback_decision"

_INTENT_TYPES = (
    "no_change", "prioritize_now", "reorder", "cancel", "postpone",
    "complete", "restore", "location_correction", "concurrency", "what_if",
    "mixed", "clarify",
)
_PRIORITY_DIRECTIONS = ("raise", "lower")


@dataclass(frozen=True)
class TaskOrderingConstraint:
    before_task_ref: str
    after_task_ref: str

    def __post_init__(self):
        _require_ref("before_task_ref", self.before_task_ref)
        _require_ref("after_task_ref", self.after_task_ref)
        if self.before_task_ref == self.after_task_ref:
            raise ValueError("ordering constraint cannot reference one task twice")


@dataclass(frozen=True)
class TaskPriorityChange:
    task_ref: str
    direction: str

    def __post_init__(self):
        _require_ref("task_ref", self.task_ref)
        if self.direction not in _PRIORITY_DIRECTIONS:
            raise ValueError("priority direction must be raise or lower")


@dataclass(frozen=True)
class ConcurrencyDecision:
    """Qwen-bound semantic request; deterministic core validates feasibility."""

    action: str
    task_ref: str
    commitment_ref: str
    requested_minutes: Optional[int] = None
    placement: str = "earliest"
    start_offset_minutes: Optional[int] = None
    source: str = "explicit_user_request"

    def __post_init__(self):
        if self.action not in ("authorize", "revoke"):
            raise ValueError("concurrency action invalid")
        _require_ref("task_ref", self.task_ref)
        _require_ref("commitment_ref", self.commitment_ref)
        if self.placement not in ("earliest", "start", "end", "offset"):
            raise ValueError("concurrency placement invalid")
        if self.source != "explicit_user_request":
            raise ValueError("concurrency decision requires explicit user source")
        if self.action == "authorize":
            if isinstance(self.requested_minutes, bool) or not isinstance(self.requested_minutes, int) or self.requested_minutes < 1:
                raise ValueError("authorize concurrency requires requested_minutes")
        elif self.requested_minutes is not None:
            raise ValueError("revoke concurrency cannot request minutes")
        if self.start_offset_minutes is not None and (
            isinstance(self.start_offset_minutes, bool)
            or not isinstance(self.start_offset_minutes, int)
            or self.start_offset_minutes < 0
        ):
            raise ValueError("start_offset_minutes invalid")
        if self.placement == "offset" and self.start_offset_minutes is None:
            raise ValueError("offset placement requires start_offset_minutes")


@dataclass(frozen=True)
class FeedbackDecision:
    intent_type: str = "no_change"
    target_task_refs: Tuple[str, ...] = ()
    preferred_next_task_ref: Optional[str] = None
    priority_changes: Tuple[TaskPriorityChange, ...] = ()
    ordering_constraints: Tuple[TaskOrderingConstraint, ...] = ()
    cancelled_task_refs: Tuple[str, ...] = ()
    postponed_task_refs: Tuple[str, ...] = ()
    completed_task_refs: Tuple[str, ...] = ()
    restored_task_refs: Tuple[str, ...] = ()
    concurrency_changes: Tuple[ConcurrencyDecision, ...] = ()
    location_correction: Optional[str] = None
    explicit_user_preference: bool = False
    confidence: float = 0.0
    clarification_needed: bool = False
    clarification_question: Optional[str] = None
    # Set by the semantic interpreter only when this sentence may describe a
    # stable preference for the rest of today (not merely "do X now").
    day_preference_candidate: bool = False

    def __post_init__(self):
        if self.intent_type not in _INTENT_TYPES:
            raise ValueError("invalid feedback intent_type")
        for field in (
            self.target_task_refs, self.cancelled_task_refs,
            self.postponed_task_refs, self.completed_task_refs,
            self.restored_task_refs,
        ):
            _require_unique_refs(field)
        if self.preferred_next_task_ref is not None:
            _require_ref("preferred_next_task_ref", self.preferred_next_task_ref)
        if not isinstance(self.explicit_user_preference, bool):
            raise ValueError("explicit_user_preference must be bool")
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
            raise ValueError("confidence must be numeric")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if not isinstance(self.clarification_needed, bool):
            raise ValueError("clarification_needed must be bool")
        if self.clarification_needed != bool(self.clarification_question):
            raise ValueError("clarification question must match clarification_needed")
        if not isinstance(self.day_preference_candidate, bool):
            raise ValueError("day_preference_candidate must be bool")
        if self.location_correction is not None:
            _require_text("location_correction", self.location_correction)
        priority_refs = [item.task_ref for item in self.priority_changes]
        if len(priority_refs) != len(set(priority_refs)):
            raise ValueError("one task cannot have duplicate priority changes")
        ordering_pairs = [
            (item.before_task_ref, item.after_task_ref)
            for item in self.ordering_constraints
        ]
        if len(ordering_pairs) != len(set(ordering_pairs)):
            raise ValueError("ordering constraints cannot be duplicated")
        concurrency_pairs = [
            (item.action, item.task_ref, item.commitment_ref)
            for item in self.concurrency_changes
        ]
        if len(concurrency_pairs) != len(set(concurrency_pairs)):
            raise ValueError("concurrency changes cannot be duplicated")
        actions_by_pair = {}
        for item in self.concurrency_changes:
            pair = (item.task_ref, item.commitment_ref)
            actions_by_pair.setdefault(pair, set()).add(item.action)
        if any(len(actions) > 1 for actions in actions_by_pair.values()):
            raise ValueError("one feedback cannot authorize and revoke the same concurrency pair")

    @property
    def has_planning_constraints(self):
        return bool(
            self.preferred_next_task_ref or self.priority_changes
            or self.ordering_constraints or self.postponed_task_refs
            or self.concurrency_changes
        )


@dataclass(frozen=True)
class FeedbackDecisionOutcome:
    decision: FeedbackDecision
    interpreter_calls: int
    critic_calls: int
    used_fallback: bool = False


@dataclass(frozen=True)
class IntentComplianceReview:
    decision: str
    reason: Optional[str] = None
    repaired_decision: Optional[FeedbackDecision] = None

    def __post_init__(self):
        if self.decision not in ("approve", "replan", "reject"):
            raise ValueError("compliance decision must be approve, replan, or reject")


def decide_feedback(
    state: DayPlanningState,
    execution_context,
    user_text: str,
    current_plan_summary: str,
    caller: Callable[[str, str], str],
    repair_caller: Optional[Callable[[str, str], str]] = None,
    existing_decision: Optional[FeedbackDecision] = None,
) -> FeedbackDecisionOutcome:
    """Run Interpreter A and Critic B, each with at most one format repair."""
    repair = repair_caller if repair_caller is not None else caller
    system, user = build_feedback_interpreter_prompt(
        state, execution_context, user_text, current_plan_summary, existing_decision
    )
    decision, a_calls = _call_decision(caller, repair, system, user, state)
    if decision is None:
        return FeedbackDecisionOutcome(FeedbackDecision(), a_calls, 0, True)

    # A hypothetical is classified here, then interpreted in detail by P5's
    # dedicated What-if Interpreter against a cloned canonical state.  Running
    # the ordinary feedback critic as well would ask two passes to solve the
    # same semantic job before the preview pipeline even begins.
    if decision.intent_type == "what_if":
        return FeedbackDecisionOutcome(decision, a_calls, 0, False)

    critic_system, critic_user = build_feedback_critic_prompt(
        state, user_text, decision, execution_context
    )
    review, b_calls = _call_decision_review(
        caller, repair, critic_system, critic_user, state
    )
    if review is None:
        # A valid, ref-checked interpretation is safer than inventing an
        # alternative after the reviewer failed structurally.
        return FeedbackDecisionOutcome(decision, a_calls, b_calls, True)
    if review.decision == "approve":
        return FeedbackDecisionOutcome(decision, a_calls, b_calls, False)
    if review.decision == "replan" and review.repaired_decision is not None:
        return FeedbackDecisionOutcome(review.repaired_decision, a_calls, b_calls, False)
    return FeedbackDecisionOutcome(
        FeedbackDecision(
            intent_type="clarify",
            target_task_refs=decision.target_task_refs,
            confidence=decision.confidence,
            clarification_needed=True,
            clarification_question="你想优先调整哪一项任务？",
        ),
        a_calls,
        b_calls,
        True,
    )


def review_intent_compliance(
    state: DayPlanningState,
    user_text: str,
    decision: FeedbackDecision,
    candidate_summary: str,
    hard_facts_summary: str,
    caller: Callable[[str, str], str],
    repair_caller: Optional[Callable[[str, str], str]] = None,
) -> Tuple[Optional[IntentComplianceReview], int]:
    """Run Reviewer C with one bounded format repair."""
    repair = repair_caller if repair_caller is not None else caller
    system, user = build_intent_compliance_prompt(
        state, user_text, decision, candidate_summary, hard_facts_summary
    )
    calls = 1
    try:
        return parse_intent_compliance_review(caller(system, user), state), calls
    except (AgenticParseError, ValueError, TypeError):
        calls += 1
        repair_system = _repair_system(INTENT_COMPLIANCE_SCHEMA_VERSION)
        try:
            return parse_intent_compliance_review(repair(repair_system, user), state), calls
        except (AgenticParseError, ValueError, TypeError):
            return None, calls


def build_feedback_interpreter_prompt(
    state, execution_context, user_text, current_plan_summary, existing_decision=None
):
    system = (
        "你是 CampusFlow 的 Feedback Interpreter（p4.feedback-decision）。"
        "只判断用户最新反馈想改变什么，不规划时间、不计算路线。所有任务目标必须使用台账中的 task_ref。"
        "不要把‘现在就想做X’降级成 generic replan。\n"
        "输出 JSON schema："
        '{"schema_version":"p4.feedback-decision.v1","intent_type":'
        '"no_change|prioritize_now|reorder|cancel|postpone|complete|restore|location_correction|concurrency|what_if|mixed|clarify",'
        '"target_task_refs":[string],"preferred_next_task_ref":string|null,'
        '"priority_changes":[{"task_ref":string,"direction":"raise|lower"}],'
        '"ordering_constraints":[{"before_task_ref":string,"after_task_ref":string}],'
        '"cancelled_task_refs":[string],"postponed_task_refs":[string],'
        '"completed_task_refs":[string],"restored_task_refs":[string],'
        '"concurrency_changes":[{"action":"authorize|revoke","task_ref":string,'
        '"commitment_ref":string,"requested_minutes":int|null,'
        '"placement":"earliest|start|end|offset","start_offset_minutes":int|null,'
        '"source":"explicit_user_request"}],'
        '"location_correction":string|null,"explicit_user_preference":bool,'
        '"confidence":number,"clarification_needed":bool,'
        '"clarification_question":string|null,"day_preference_candidate":bool}.\n'
        "‘洗衣服放前面/现在就想洗衣服’应绑定对应 ref 并设置 preferred_next；"
        "‘先A再B’写 ordering；‘晚上再做/今天先别做’是 postpone，不是 cancel；"
        "‘已经做完’是 complete；位置纠正只填 location_correction。"
        "只有用户明确说某任务与某固定安排同时进行才输出 authorize；‘上课前/下课后’不是并行。"
        "用户以‘如果……呢/假如……’询问方案影响而没有明确要求立即执行时，intent_type=what_if，"
        "只绑定相关 ref，不把假设直接写入正式 state。"
        "只有‘今天别排太满/少切换/早点吃饭’这类当天持续偏好才将 day_preference_candidate=true；"
        "单次 preferred-next、位置纠正、完成或取消指令应为 false。"
        "‘算了，这节课认真听’若语义是在撤销已存在的并行，只 revoke pair，不取消任务。只输出 JSON。"
    )
    current_location = getattr(execution_context, "current_location", None)
    location = getattr(current_location, "location", None)
    current_text = getattr(location, "display_name", None) or "unknown"
    user = (
        "任务台账：\n{tasks}\n\n固定安排：\n{commitments}\n\n"
        "当前地点：{location}\n当前方案摘要：{plan}\n"
        "已有用户偏好约束：{existing}\n\n用户最新反馈：{feedback}"
    ).format(
        tasks=_task_facts(state, execution_context),
        commitments=_commitment_facts(state),
        location=current_text,
        plan=current_plan_summary or "（无）",
        existing=(
            json.dumps(_decision_payload(existing_decision), ensure_ascii=False)
            if isinstance(existing_decision, FeedbackDecision) else "（无）"
        ),
        feedback=user_text,
    )
    return system, user


def build_feedback_critic_prompt(state, user_text, decision, execution_context=None):
    system = (
        "你是 CampusFlow 的 Feedback Decision Critic（p4.feedback-decision-review）。"
        "检查解释器有没有漏掉用户明确提到的任务、绑错 task_ref、误判‘现在/先/再/取消/推后/完成’。"
        "并专门复核 concurrency：必须是用户明确同时进行，task/commitment ref 和 duration 归属正确；"
        "‘上课前/下课后’不能误判为并行，撤销并行不能误判为取消任务。"
        "不计算时间路线。输出 JSON："
        '{"schema_version":"p4.feedback-decision-review.v1","decision":"approve|repair|reject",'
        '"reason":string|null,"repaired_decision":object|null}。'
        "repair 时 repaired_decision 必须完整符合 p4.feedback-decision.v1；approve/reject 时为 null。只输出 JSON。"
    )
    user = "任务台账：\n{}\n\n用户反馈：{}\n\n解释器决定：{}".format(
        _task_facts(state, execution_context), user_text,
        json.dumps(_decision_payload(decision), ensure_ascii=False)
    )
    return system, user


def build_intent_compliance_prompt(state, user_text, decision, candidate_summary, hard_facts_summary):
    system = (
        "你是 CampusFlow 的 Final Intent Compliance Reviewer（p4.intent-compliance-review）。"
        "只判断候选方案是否明显违背用户最新明确意图；不得修改时间、路线、地点、时长或固定安排。"
        "硬约束导致目标任务不可行且候选方案明确保留硬约束时可以 approve。"
        "输出 JSON："
        '{"schema_version":"p4.intent-compliance-review.v1","decision":"approve|replan|reject",'
        '"reason":string|null,"repaired_decision":object|null}。'
        "需要受控重排时用 replan，并可给完整 repaired_decision。只输出 JSON。"
    )
    user = (
        "任务台账：\n{tasks}\n\n用户反馈：{feedback}\n决定：{decision}\n\n"
        "硬约束摘要：{hard}\n候选方案：{candidate}"
    ).format(
        tasks=_task_facts(state), feedback=user_text,
        decision=json.dumps(_decision_payload(decision), ensure_ascii=False),
        hard=hard_facts_summary, candidate=candidate_summary,
    )
    return system, user


def parse_feedback_decision(text: str, state: DayPlanningState) -> FeedbackDecision:
    payload = _payload(text, FEEDBACK_DECISION_SCHEMA_VERSION)
    decision = _decision_from_payload(payload)
    _validate_decision_refs(decision, state)
    return decision


def parse_feedback_decision_review(text: str, state: DayPlanningState):
    payload = _payload(text, FEEDBACK_DECISION_REVIEW_SCHEMA_VERSION)
    result = payload.get("decision")
    if result not in ("approve", "repair", "reject"):
        raise AgenticParseError("invalid feedback critic decision")
    repaired = payload.get("repaired_decision")
    repaired_decision = None
    if repaired is not None:
        if not isinstance(repaired, dict):
            raise AgenticParseError("repaired_decision must be object or null")
        repaired_decision = _decision_from_payload(repaired)
        _validate_decision_refs(repaired_decision, state)
    if result == "repair" and repaired_decision is None:
        raise AgenticParseError("repair requires repaired_decision")
    if result != "repair" and repaired_decision is not None:
        raise AgenticParseError("only repair may include repaired_decision")
    return result, _optional_text(payload.get("reason")), repaired_decision


def parse_intent_compliance_review(text: str, state: DayPlanningState):
    payload = _payload(text, INTENT_COMPLIANCE_SCHEMA_VERSION)
    result = payload.get("decision")
    repaired = payload.get("repaired_decision")
    repaired_decision = None
    if repaired is not None:
        if not isinstance(repaired, dict):
            raise AgenticParseError("repaired_decision must be object or null")
        repaired_decision = _decision_from_payload(repaired)
        _validate_decision_refs(repaired_decision, state)
    if result == "replan" and repaired_decision is None:
        raise AgenticParseError("replan requires repaired_decision")
    if result == "approve" and repaired_decision is not None:
        raise AgenticParseError("approve cannot include repaired_decision")
    return IntentComplianceReview(result, _optional_text(payload.get("reason")), repaired_decision)


def apply_feedback_decision_to_state(state, decision):
    """Apply ref-bound lifecycle facts omitted by older unified feedback."""
    actions = []
    for refs, action in (
        (decision.cancelled_task_refs, LifecycleAction.ABANDON),
        (decision.completed_task_refs, LifecycleAction.COMPLETE),
        (decision.restored_task_refs, LifecycleAction.RESUME_TODAY),
    ):
        for ref in refs:
            actions.append(ReconciliationUpdate(
                ref, None, None, None, None, action, None, None
            ))
    if not actions:
        return state
    result = ReconciliationResult(RECONCILIATION_SCHEMA_VERSION, tuple(actions), ())
    return apply_reconciliation(state, result).state


def apply_feedback_task_order(state, base_order, decision):
    """Convert accepted semantic constraints to one deterministic task order."""
    state_refs = [task.task_ref for task in state.tasks]
    ordered = [ref for ref in (base_order or ()) if ref in state_refs]
    ordered += [ref for ref in state_refs if ref not in ordered]
    if decision is None:
        return tuple(ordered)

    lower = [item.task_ref for item in decision.priority_changes if item.direction == "lower"]
    postponed = list(decision.postponed_task_refs)
    tail = [ref for ref in ordered if ref in set(lower + postponed)]
    ordered = [ref for ref in ordered if ref not in set(tail)] + tail
    raised = [item.task_ref for item in decision.priority_changes if item.direction == "raise"]
    for ref in reversed(raised):
        if ref in ordered:
            ordered.remove(ref)
            ordered.insert(0, ref)

    # Stable topological relaxation; cycles were rejected during validation.
    for _ in range(len(ordered)):
        changed = False
        for item in decision.ordering_constraints:
            left, right = ordered.index(item.before_task_ref), ordered.index(item.after_task_ref)
            if left > right:
                ordered.pop(left)
                right = ordered.index(item.after_task_ref)
                ordered.insert(right, item.before_task_ref)
                changed = True
        if not changed:
            break
    preferred = decision.preferred_next_task_ref
    if preferred in ordered:
        ordered.remove(preferred)
        ordered.insert(0, preferred)
    return tuple(ordered)


def merge_feedback_decisions(previous, current, state):
    """Keep still-valid preferences unless the latest feedback supersedes them."""
    if not isinstance(previous, FeedbackDecision):
        return current
    if current.has_planning_constraints:
        preferred = current.preferred_next_task_ref
        priority = current.priority_changes
        ordering = current.ordering_constraints
        postponed = current.postponed_task_refs
    else:
        preferred = previous.preferred_next_task_ref
        priority = previous.priority_changes
        ordering = previous.ordering_constraints
        postponed = previous.postponed_task_refs
    known = {item.task_ref for item in state.tasks}
    removed = set(current.cancelled_task_refs + current.completed_task_refs)
    if preferred in removed or preferred not in known:
        preferred = None
    merged = replace(
        current,
        preferred_next_task_ref=preferred,
        priority_changes=tuple(
            item for item in priority
            if item.task_ref not in removed and item.task_ref in known
        ),
        ordering_constraints=tuple(
            item for item in ordering
            if item.before_task_ref not in removed and item.after_task_ref not in removed
            and item.before_task_ref in known and item.after_task_ref in known
        ),
        postponed_task_refs=tuple(
            ref for ref in postponed if ref not in removed and ref in known
        ),
        concurrency_changes=current.concurrency_changes,
    )
    _validate_decision_refs(merged, state)
    return merged


def candidate_complies_with_decision(state, plan, decision):
    """Deterministic last guard for ref ordering; infeasible targets may be absent."""
    allocations = tuple(getattr(plan, "allocations", ()) or ())
    order = []
    for item in allocations:
        if item.task_ref not in order:
            order.append(item.task_ref)
    preferred = decision.preferred_next_task_ref
    if preferred in order and order[0] != preferred:
        return False
    positions = {ref: index for index, ref in enumerate(order)}
    if any(ref in positions for ref in decision.cancelled_task_refs + decision.completed_task_refs):
        return False
    for item in decision.ordering_constraints:
        if item.before_task_ref in positions and item.after_task_ref in positions:
            if positions[item.before_task_ref] > positions[item.after_task_ref]:
                return False
    return True


def candidate_plan_summary(state, plan):
    """Compact structured candidate facts for Reviewer C; no rendered copy."""
    task_by_ref = {item.task_ref: item for item in state.tasks}
    rows = []
    for item in getattr(plan, "allocations", ()) or ():
        task = task_by_ref.get(item.task_ref)
        rows.append(
            "{} | {} | {}min | window={} | sequence={}".format(
                item.task_ref,
                task.title if task is not None else item.task_title,
                item.planned_minutes,
                item.window_ref,
                item.sequence_index,
            )
        )
    unplanned = ",".join(getattr(plan, "unallocated_task_refs", ()) or ()) or "none"
    return "allocations:\n{}\nunplanned: {}".format(
        "\n".join(rows) if rows else "none", unplanned
    )


def hard_facts_summary(state):
    windows = "\n".join(
        "- {}–{} | capacity={}min".format(
            item.starts_at.strftime("%H:%M"), item.ends_at.strftime("%H:%M"),
            item.capacity_minutes,
        )
        for item in state.windows
    ) or "none"
    return "now={}\ncommitments:\n{}\navailable windows:\n{}".format(
        state.now.strftime("%H:%M"), _commitment_facts(state), windows
    )


def decision_clarification_questions(decision, state, plan):
    questions = []
    if decision.clarification_needed and decision.clarification_question:
        questions.append(decision.clarification_question)
    preferred = decision.preferred_next_task_ref
    if preferred and preferred not in getattr(plan, "planned_minutes_by_task", {}):
        task = next((item for item in state.tasks if item.task_ref == preferred), None)
        if task is not None and task.state is TaskState.ACTIVE:
            questions.append(
                "现在的可用时间不足以安全安排“{}”，要不要把它放到固定安排之后？".format(task.title)
            )
    return tuple(dict.fromkeys(questions))


def load_feedback_decision(store):
    value = store.get(P4_FEEDBACK_DECISION_KEY)
    return value if isinstance(value, FeedbackDecision) else None


def _call_decision(caller, repair, system, user, state):
    calls = 1
    try:
        return parse_feedback_decision(caller(system, user), state), calls
    except (AgenticParseError, ValueError, TypeError):
        calls += 1
        try:
            return parse_feedback_decision(repair(_repair_system(FEEDBACK_DECISION_SCHEMA_VERSION), user), state), calls
        except (AgenticParseError, ValueError, TypeError):
            return None, calls


def _call_decision_review(caller, repair, system, user, state):
    calls = 1
    try:
        result, reason, repaired = parse_feedback_decision_review(caller(system, user), state)
    except (AgenticParseError, ValueError, TypeError):
        calls += 1
        try:
            result, reason, repaired = parse_feedback_decision_review(
                repair(_repair_system(FEEDBACK_DECISION_REVIEW_SCHEMA_VERSION), user), state
            )
        except (AgenticParseError, ValueError, TypeError):
            return None, calls
    return IntentComplianceReview(
        "approve" if result == "approve" else ("replan" if result == "repair" else "reject"),
        reason, repaired,
    ), calls


def _decision_from_payload(payload):
    if not isinstance(payload, dict):
        raise AgenticParseError("feedback decision must be object")
    if payload.get("schema_version") != FEEDBACK_DECISION_SCHEMA_VERSION:
        raise AgenticParseError("feedback decision schema_version mismatch")
    priority = tuple(
        TaskPriorityChange(_required_text(item, "task_ref"), _required_text(item, "direction"))
        for item in _list(payload, "priority_changes")
    )
    ordering = tuple(
        TaskOrderingConstraint(
            _required_text(item, "before_task_ref"),
            _required_text(item, "after_task_ref"),
        )
        for item in _list(payload, "ordering_constraints")
    )
    concurrency = tuple(
        ConcurrencyDecision(
            _required_text(item, "action"),
            _required_text(item, "task_ref"),
            _required_text(item, "commitment_ref"),
            _optional_positive_int(item.get("requested_minutes")),
            _required_text(item, "placement"),
            _optional_nonnegative_int(item.get("start_offset_minutes")),
            _required_text(item, "source"),
        )
        for item in _optional_list(payload, "concurrency_changes")
    )
    clarification = payload.get("clarification_question")
    return FeedbackDecision(
        intent_type=_required_text(payload, "intent_type"),
        target_task_refs=_refs(payload, "target_task_refs"),
        preferred_next_task_ref=_optional_text(payload.get("preferred_next_task_ref")),
        priority_changes=priority,
        ordering_constraints=ordering,
        cancelled_task_refs=_refs(payload, "cancelled_task_refs"),
        postponed_task_refs=_refs(payload, "postponed_task_refs"),
        completed_task_refs=_refs(payload, "completed_task_refs"),
        restored_task_refs=_refs(payload, "restored_task_refs"),
        concurrency_changes=concurrency,
        location_correction=_optional_text(payload.get("location_correction")),
        explicit_user_preference=_required_bool(payload, "explicit_user_preference"),
        confidence=_required_number(payload, "confidence"),
        clarification_needed=_required_bool(payload, "clarification_needed"),
        clarification_question=_optional_text(clarification),
        day_preference_candidate=_optional_bool(
            payload, "day_preference_candidate", False
        ),
    )


def _validate_decision_refs(decision, state):
    known = {task.task_ref for task in state.tasks}
    refs = set(
        decision.target_task_refs + decision.cancelled_task_refs
        + decision.postponed_task_refs + decision.completed_task_refs
        + decision.restored_task_refs
    )
    if decision.preferred_next_task_ref:
        refs.add(decision.preferred_next_task_ref)
    refs.update(item.task_ref for item in decision.priority_changes)
    refs.update(item.task_ref for item in decision.concurrency_changes)
    for item in decision.ordering_constraints:
        refs.update((item.before_task_ref, item.after_task_ref))
    unknown = refs - known
    if unknown:
        raise AgenticParseError("feedback decision contains unknown task_ref")
    known_commitments = {item.commitment_ref for item in state.commitments}
    if any(item.commitment_ref not in known_commitments for item in decision.concurrency_changes):
        raise AgenticParseError("feedback decision contains unknown commitment_ref")
    lifecycle_groups = (
        set(decision.cancelled_task_refs), set(decision.completed_task_refs),
        set(decision.restored_task_refs),
    )
    if any(left & right for index, left in enumerate(lifecycle_groups)
           for right in lifecycle_groups[index + 1:]):
        raise AgenticParseError("one task cannot have conflicting lifecycle decisions")
    if decision.preferred_next_task_ref in (
        set(decision.cancelled_task_refs) | set(decision.completed_task_refs)
    ):
        raise AgenticParseError("cancelled/completed task cannot be preferred next")
    if decision.preferred_next_task_ref:
        preferred_task = next(
            item for item in state.tasks
            if item.task_ref == decision.preferred_next_task_ref
        )
        if (
            preferred_task.state is not TaskState.ACTIVE
            and decision.preferred_next_task_ref not in decision.restored_task_refs
        ):
            raise AgenticParseError("inactive task cannot be preferred without restore")
    _reject_ordering_cycles(decision.ordering_constraints)


def _reject_ordering_cycles(constraints):
    graph = {}
    for item in constraints:
        graph.setdefault(item.before_task_ref, set()).add(item.after_task_ref)
    visiting, visited = set(), set()
    def visit(node):
        if node in visiting:
            raise AgenticParseError("ordering constraints contain cycle")
        if node in visited:
            return
        visiting.add(node)
        for nxt in graph.get(node, ()):
            visit(nxt)
        visiting.remove(node)
        visited.add(node)
    for node in tuple(graph):
        visit(node)


def _decision_payload(decision):
    return {
        "schema_version": FEEDBACK_DECISION_SCHEMA_VERSION,
        "intent_type": decision.intent_type,
        "target_task_refs": list(decision.target_task_refs),
        "preferred_next_task_ref": decision.preferred_next_task_ref,
        "priority_changes": [vars(item) for item in decision.priority_changes],
        "ordering_constraints": [vars(item) for item in decision.ordering_constraints],
        "cancelled_task_refs": list(decision.cancelled_task_refs),
        "postponed_task_refs": list(decision.postponed_task_refs),
        "completed_task_refs": list(decision.completed_task_refs),
        "restored_task_refs": list(decision.restored_task_refs),
        "concurrency_changes": [
            {
                "action": item.action,
                "task_ref": item.task_ref,
                "commitment_ref": item.commitment_ref,
                "requested_minutes": item.requested_minutes,
                "placement": item.placement,
                "start_offset_minutes": item.start_offset_minutes,
                "source": item.source,
            }
            for item in decision.concurrency_changes
        ],
        "location_correction": decision.location_correction,
        "explicit_user_preference": decision.explicit_user_preference,
        "confidence": decision.confidence,
        "clarification_needed": decision.clarification_needed,
        "clarification_question": decision.clarification_question,
        "day_preference_candidate": decision.day_preference_candidate,
    }


def _task_facts(state, execution_context=None):
    def location_for(item):
        binding = (
            execution_context.binding_for(item.task_ref)
            if execution_context is not None and hasattr(execution_context, "binding_for")
            else None
        )
        location = getattr(binding, "execution_location", None)
        return getattr(location, "display_name", None) or "none"
    return "\n".join(
        "- {ref} | {title} | status={status} | duration={duration} | location={location}".format(
            ref=item.task_ref, title=item.title, status=item.state.value,
            duration=item.remaining_minutes if item.remaining_minutes is not None else "unknown",
            location=location_for(item),
        )
        for item in state.tasks
    ) or "（无任务）"


def _commitment_facts(state):
    return "\n".join(
        "- {} | {} | {}–{}".format(
            item.commitment_ref, item.title,
            item.starts_at.strftime("%H:%M") if item.starts_at else "unknown",
            item.ends_at.strftime("%H:%M") if item.ends_at else "unknown",
        )
        for item in state.commitments
    ) or "（无固定安排）"


def _payload(text, version):
    if not isinstance(text, str) or not text.strip():
        raise AgenticParseError("empty model output")
    payload = extract_json_object(text)
    if not isinstance(payload, dict) or payload.get("schema_version") != version:
        raise AgenticParseError("schema_version mismatch")
    return payload


def _repair_system(version):
    return "你是格式修复器。只输出符合 {} 的完整 JSON，不解释、不增加事实。".format(version)


def _refs(payload, name):
    return tuple(_required_text(item, None) for item in _list(payload, name))


def _list(payload, name):
    value = payload.get(name)
    if not isinstance(value, list):
        raise AgenticParseError("{} must be list".format(name))
    return value


def _optional_list(payload, name):
    value = payload.get(name, [])
    if not isinstance(value, list):
        raise AgenticParseError("{} must be list".format(name))
    return value


def _optional_positive_int(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise AgenticParseError("optional minutes must be positive int")
    return value


def _optional_nonnegative_int(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AgenticParseError("optional offset must be non-negative int")
    return value


def _required_text(value, name):
    if name is not None:
        if not isinstance(value, dict):
            raise AgenticParseError("object expected")
        value = value.get(name)
    _require_text(name or "value", value)
    return value.strip()


def _optional_text(value):
    if value is None:
        return None
    _require_text("optional text", value)
    return value.strip()


def _required_bool(payload, name):
    value = payload.get(name)
    if not isinstance(value, bool):
        raise AgenticParseError("{} must be bool".format(name))
    return value


def _optional_bool(payload, name, default=False):
    value = payload.get(name, default)
    if not isinstance(value, bool):
        raise AgenticParseError("{} must be bool".format(name))
    return value


def _required_number(payload, name):
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AgenticParseError("{} must be number".format(name))
    return float(value)


def _require_unique_refs(refs):
    for ref in refs:
        _require_ref("task_ref", ref)
    if len(refs) != len(set(refs)):
        raise ValueError("task refs must be unique")


def _require_ref(name, value):
    _require_text(name, value)


def _require_text(name, value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("{} must be non-empty text".format(name))
