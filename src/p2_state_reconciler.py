"""P2c 任务反馈安全应用（Python 3.8 兼容）。

只把 Reconciliation Agent 的 proposal 转成 P2a 状态更新；复用
src.p2_task_progress 的全部生命周期逻辑（apply_progress_report /
apply_total_update / mark_skipped_today / mark_abandoned / mark_completed），
不复制第二套。输入 state 永不修改，全部返回新实例。
"""

from dataclasses import dataclass, replace
from typing import Optional, Sequence, Tuple

from src.p1_models import SourceKind
from src.p2_agentic_models import (
    LifecycleAction,
    ReconciliationResult,
    ReconciliationUpdate,
    TotalSourceChoice,
)
from src.p2_models import DayPlanningState, TaskProgress, TaskState
from src.p2_task_progress import (
    apply_progress_report,
    apply_total_update,
    mark_abandoned,
    mark_completed,
    mark_skipped_today,
    resume_today,
)

TASK_REF_PREFIX = "day_task_"


@dataclass(frozen=True)
class ReconciliationApplied:
    """Reconciliation proposal 安全应用后的纯输出。"""

    state: DayPlanningState
    applied_entries: Tuple[str, ...]
    warnings: Tuple[str, ...]
    new_task_refs: Tuple[str, ...]
    questions: Tuple[str, ...]


def apply_reconciliation(
    state: DayPlanningState, result: ReconciliationResult
) -> ReconciliationApplied:
    """按更新顺序把 ReconciliationResult 应用到 state，返回新状态与摘要。"""
    if not isinstance(state, DayPlanningState):
        raise TypeError("state must be a DayPlanningState")
    if not isinstance(result, ReconciliationResult):
        raise TypeError("result must be a ReconciliationResult")

    if result.questions:
        # 一边承认 identity 不确定，一边应用更新会破坏“程序兜底”原则：
        # questions 非空时本轮不应用任何 updates，ledger 保持不变，只透传 questions。
        return ReconciliationApplied(
            state=state,
            applied_entries=(),
            warnings=(),
            new_task_refs=(),
            questions=result.questions,
        )

    tasks = list(state.tasks)
    task_by_ref = {task.task_ref: task for task in tasks}
    applied_entries = []
    warnings = []
    new_task_refs = []
    next_number = _next_task_ref_number(tuple(task_by_ref.keys()))

    for update in result.updates:
        if update.target_task_ref is not None:
            task = task_by_ref.get(update.target_task_ref)
            if task is None:
                warnings.append(
                    "任务反馈引用了未知 task_ref：{}，已忽略该条更新".format(update.target_task_ref)
                )
                continue
            updated = _apply_update_to_task(task, update)
            if updated is not task:
                task_by_ref[task.task_ref] = updated
            entry = _describe_update(update, updated)
            if entry:
                applied_entries.append(entry)
        else:
            new_ref = _format_ref(next_number)
            next_number += 1
            initial = TaskProgress(
                task_ref=new_ref,
                title=update.new_task_title,
                total_minutes=None,
                completed_minutes=0,
                total_source=None,
                state=TaskState.ACTIVE,
                is_splittable=None,
                minimum_slice_minutes=None,
            )
            updated = _apply_update_to_task(initial, update)
            task_by_ref[new_ref] = updated
            new_task_refs.append(new_ref)
            applied_entries.append("新增任务：{}".format(update.new_task_title))

    new_tasks = _rebuild_task_list(tasks, task_by_ref, new_task_refs)
    updated_state = _rebuild_state(state, tasks=new_tasks)
    return ReconciliationApplied(
        state=updated_state,
        applied_entries=tuple(applied_entries),
        warnings=tuple(warnings),
        new_task_refs=tuple(new_task_refs),
        questions=result.questions,
    )


def append_structured_task(
    state,
    title,
    total_minutes,
    total_source,
    is_splittable=None,
    minimum_slice_minutes=None,
    allow_unknown=False,
):
    """Append one already-confirmed task without another language parse.

    This is the same canonical TaskProgress ledger used by reconciliation;
    planned minutes remain separate from completed progress.
    """
    if not isinstance(state, DayPlanningState):
        raise TypeError("state must be a DayPlanningState")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title must be non-empty")
    unknown = allow_unknown and total_minutes is None and total_source is None
    if not unknown and (isinstance(total_minutes, bool) or not isinstance(total_minutes, int) or total_minutes < 1):
        raise ValueError("total_minutes must be a positive integer")
    if not unknown and not isinstance(total_source, SourceKind):
        raise ValueError("total_source must be SourceKind")
    existing_refs = tuple(task.task_ref for task in state.tasks)
    ref = _format_ref(_next_task_ref_number(existing_refs))
    task = TaskProgress(
        task_ref=ref,
        title=title.strip(),
        total_minutes=total_minutes,
        completed_minutes=0,
        total_source=total_source,
        state=TaskState.ACTIVE,
        is_splittable=is_splittable,
        minimum_slice_minutes=minimum_slice_minutes,
    )
    return ReconciliationApplied(
        state=_rebuild_state(state, tasks=state.tasks + (task,)),
        applied_entries=("新增任务：{}".format(task.title),),
        warnings=(),
        new_task_refs=(ref,),
        questions=(),
    )


def confirm_unknown_task_duration(state, task_ref, minutes, source):
    """Confirm an existing unstarted task without changing its identity or constraints."""
    task = next((t for t in state.tasks if t.task_ref == task_ref), None)
    if task is None or task.state is not TaskState.ACTIVE:
        raise ValueError("待补时长的任务已变化，请重新选择。")
    if task.total_minutes is not None or task.completed_minutes != 0:
        raise ValueError("任务已有用时或实际进度，请通过调整计划更新，原任务未改变。")
    updated = apply_total_update(task, minutes, source)
    return ReconciliationApplied(
        state=replace(state, tasks=tuple(updated if t.task_ref == task_ref else t for t in state.tasks)),
        applied_entries=("确认任务用时：{}".format(task.title),), warnings=(), new_task_refs=(), questions=(),
    )


def _apply_update_to_task(task: TaskProgress, update: ReconciliationUpdate) -> TaskProgress:
    """按固定顺序应用：total -> progress -> attributes -> lifecycle。"""
    current = task
    if update.set_total_minutes is not None:
        current = apply_total_update(current, update.set_total_minutes, _map_source(update.set_total_source))
    if update.progress_delta_minutes is not None:
        current = apply_progress_report(current, update.progress_delta_minutes)
    current = _with_attributes(current, update.is_splittable, update.minimum_slice_minutes)
    if update.lifecycle_action == LifecycleAction.SKIP_TODAY:
        current = mark_skipped_today(current)
    elif update.lifecycle_action == LifecycleAction.ABANDON:
        current = mark_abandoned(current)
    elif update.lifecycle_action == LifecycleAction.COMPLETE:
        current = mark_completed(current)
    elif update.lifecycle_action == LifecycleAction.RESUME_TODAY:
        current = resume_today(current)
    return current


def _map_source(choice: Optional[TotalSourceChoice]) -> Optional[SourceKind]:
    if choice is None:
        return None
    if choice == TotalSourceChoice.USER_TEXT:
        return SourceKind.AI_EXTRACTED_FROM_USER_TEXT
    if choice == TotalSourceChoice.AI_ESTIMATE:
        return SourceKind.AI_ESTIMATED
    raise ValueError("unknown total source choice: {}".format(choice))


def _with_attributes(
    task: TaskProgress,
    is_splittable: Optional[bool],
    minimum_slice_minutes: Optional[int],
) -> TaskProgress:
    if is_splittable is None and minimum_slice_minutes is None:
        return task
    return TaskProgress(
        task_ref=task.task_ref,
        title=task.title,
        total_minutes=task.total_minutes,
        completed_minutes=task.completed_minutes,
        total_source=task.total_source,
        state=task.state,
        is_splittable=is_splittable if is_splittable is not None else task.is_splittable,
        minimum_slice_minutes=minimum_slice_minutes
        if minimum_slice_minutes is not None
        else task.minimum_slice_minutes,
    )


def _describe_update(update: ReconciliationUpdate, updated: TaskProgress) -> Optional[str]:
    parts = []
    if update.progress_delta_minutes is not None:
        parts.append("又完成{}分钟".format(update.progress_delta_minutes))
    if update.set_total_minutes is not None:
        parts.append("总时长设为{}分钟".format(update.set_total_minutes))
    if update.lifecycle_action == LifecycleAction.SKIP_TODAY:
        parts.append("今天先不安排")
    elif update.lifecycle_action == LifecycleAction.ABANDON:
        parts.append("已永久放弃")
    elif update.lifecycle_action == LifecycleAction.COMPLETE:
        parts.append("已完成")
    elif update.lifecycle_action == LifecycleAction.RESUME_TODAY:
        parts.append("今天恢复安排")
    if update.is_splittable is not None:
        parts.append("可拆分={}".format("是" if update.is_splittable else "否"))
    if update.minimum_slice_minutes is not None:
        parts.append("最小片段={}分钟".format(update.minimum_slice_minutes))
    if not parts:
        return None
    return "{}：{}".format(updated.title, "，".join(parts))


def _next_task_ref_number(existing_refs: Sequence[str]) -> int:
    numbers = []
    for ref in existing_refs:
        if ref.startswith(TASK_REF_PREFIX):
            suffix = ref[len(TASK_REF_PREFIX):]
            if suffix.isdigit():
                numbers.append(int(suffix))
    return (max(numbers) + 1) if numbers else 1


def _format_ref(number: int) -> str:
    return "{}{:03d}".format(TASK_REF_PREFIX, number)


def _rebuild_task_list(
    original_tasks: Sequence[TaskProgress],
    task_by_ref: dict,
    new_refs: Sequence[str],
) -> Tuple[TaskProgress, ...]:
    result = []
    for task in original_tasks:
        result.append(task_by_ref[task.task_ref])
    for ref in new_refs:
        result.append(task_by_ref[ref])
    return tuple(result)


def _rebuild_state(state: DayPlanningState, tasks: Tuple[TaskProgress, ...]) -> DayPlanningState:
    return DayPlanningState(
        reference_datetime=state.reference_datetime,
        now=state.now,
        day_end=state.day_end,
        commitments=state.commitments,
        tasks=tasks,
        windows=state.windows,
        active_window_ref=state.active_window_ref,
        unresolved_commitment_refs=state.unresolved_commitment_refs,
        history=state.history,
    )
