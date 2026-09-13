"""P2b “今天接下来”确定性摘要（Python 3.8 兼容）。

不是最终 Streamlit UI；只生成结构化文本行，作为 P2d UI / P2c presenter 之前的安全 fallback。
不调用模型，不做华丽中文；内部 refs / enum repr / dataclass repr 一律不进入输出文本。
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Optional, Tuple

from src.p1_models import SourceKind
from src.p2_allocation_models import DayAllocationPlan
from src.p2_models import DayPlanningState
from src.p3_class_prep import class_prep_display_segments

@dataclass(frozen=True)
class DayPlanSummary:
    current_action_line: Optional[str]
    later_window_lines: Tuple[str, ...]
    unallocated_lines: Tuple[str, ...]


NO_CURRENT_TASK_NEXT_TEMPLATE = "现在暂时没有安排哦，先休息一下～下一项安排从{time}开始："
NO_CURRENT_TASK_IDLE_TEXT = "现在暂时没有安排啦，可以先休息一下～"


def no_current_task_line(next_start=None) -> str:
    """无进行中安排时的确定性首行文案（不调用模型）。

    next_start 为“当前时间之后第一项实际安排”的真实开始时间（datetime 或 "HH:MM" 字符串）；
    今天后续完全无安排时传 None，只输出休息文案，不硬生成“下一项安排”。
    """
    if next_start is None:
        return NO_CURRENT_TASK_IDLE_TEXT
    time_text = (
        next_start.strftime("%H:%M") if hasattr(next_start, "strftime") else str(next_start)
    )
    return NO_CURRENT_TASK_NEXT_TEMPLATE.format(time=time_text)


def compact_plan_lines(
    plan: DayAllocationPlan, state: DayPlanningState, execution_context=None,
    concurrent_allocations=(),
) -> Tuple[str, ...]:
    """生成“当前方案”区块的确定性文本行（P2e 唯一主输出）。

    - 第一行优先告诉用户“现在该干什么”：
      当前处于固定安排时显示“现在：正在X；下一步HH:MM开始Y”；
      有进行中的任务片段时显示“现在：X，做到HH:MM”；
      否则按下一项真实安排动态显示：
      “现在暂时没有安排哦，先休息一下～下一项安排从HH:MM开始：”，
      今天后续完全无安排时显示“现在暂时没有安排啦，可以先休息一下～”。
    - 后续安排按真实时间段列出（window start + allocation sequence + planned_minutes），
      不再出现多个任务共用同一个“HH:MM 后”。
    - 剩余窗口没有任务可安排时保持空闲，不自动生成“准备去X”之类的泛化动作填充。
    - AI 暂估直接内联为“（AI暂估）”，不单独成区块。
    - 不输出内部 ref / enum repr / dataclass repr。
    """
    if not isinstance(plan, DayAllocationPlan):
        raise TypeError("plan must be a DayAllocationPlan")
    if not isinstance(state, DayPlanningState):
        raise TypeError("state must be a DayPlanningState")

    window_by_ref = {window.window_ref: window for window in state.windows}
    task_by_ref = {task.task_ref: task for task in state.tasks}
    commitment_by_ref = {commitment.commitment_ref: commitment for commitment in state.commitments}
    for allocation in plan.allocations:
        if allocation.window_ref not in window_by_ref:
            raise ValueError("allocation references unknown window: {}".format(allocation.window_ref))
    for ref in plan.unallocated_task_refs:
        if ref not in task_by_ref:
            raise ValueError("unallocated_task_refs references unknown task: {}".format(ref))

    now = state.now
    segments = _build_segments(
        plan, state, window_by_ref, task_by_ref, commitment_by_ref, execution_context,
        concurrent_allocations,
    )
    segments.sort(key=lambda seg: (seg.start, seg.end))

    current_commitment = next(
        (commitment for commitment in state.commitments
         if commitment.starts_at <= now
         and (commitment.ends_at is None or now < commitment.ends_at)),
        None,
    )
    current_allocation = plan.current_allocation
    excluded_refs = set()
    if current_allocation is not None:
        excluded_refs.add(current_allocation.allocation_ref)

    lines = []
    if current_commitment is not None:
        _append_current_commitment_line(lines, current_commitment, segments, now)
    elif current_allocation is not None:
        segment = next(
            (seg for seg in segments if seg.alloc_ref == current_allocation.allocation_ref),
            None,
        )
        if segment is not None:
            lines.append("现在：{}，做到{}{}".format(
                segment.title, segment.end.strftime("%H:%M"), segment.source_label))
        else:
            lines.append("现在：{}".format(current_allocation.task_title))
    else:
        next_segment = _first_future_segment(segments, now, excluded_refs, current_commitment)
        lines.append(
            no_current_task_line(next_segment.start if next_segment is not None else None)
        )

    for segment in segments:
        if segment.end is not None and segment.end <= now:
            continue
        if segment.alloc_ref is not None and segment.alloc_ref in excluded_refs:
            continue
        if current_commitment is not None and segment.kind == "commitment" and segment.start == current_commitment.starts_at:
            continue
        if segment.end is None:
            lines.append(
                "{}起：{}".format(segment.start.strftime("%H:%M"), segment.display)
            )
        else:
            lines.append(
                "{}–{}：{}".format(
                    segment.start.strftime("%H:%M"), segment.end.strftime("%H:%M"), segment.display
                )
            )

    if plan.unallocated_task_refs:
        titles = "、".join(task_by_ref[ref].title for ref in plan.unallocated_task_refs)
        lines.append("今天暂未安排：{}".format(titles))
    return tuple(lines)


@dataclass(frozen=True)
class _PlanSegment:
    start: object
    end: object
    title: str
    display: str
    kind: str
    alloc_ref: Optional[str] = None
    source_label: str = ""


def _first_future_segment(segments, now, excluded_refs, current_commitment):
    """当前时间之后第一条会被渲染的真实安排片段（排除已结束/进行中片段）。"""
    for segment in segments:
        if segment.end is not None and segment.end <= now:
            continue
        if segment.alloc_ref is not None and segment.alloc_ref in excluded_refs:
            continue
        if (
            current_commitment is not None
            and segment.kind == "commitment"
            and segment.start == current_commitment.starts_at
        ):
            continue
        if segment.start <= now:
            continue
        return segment
    return None


def _build_segments(
    plan, state, window_by_ref, task_by_ref, commitment_by_ref, execution_context=None,
    concurrent_allocations=(),
):
    segments = []
    seen_task_refs = set()
    for window in state.windows:
        allocations = sorted(
            (allocation for allocation in plan.allocations
             if allocation.window_ref == window.window_ref),
            key=lambda allocation: (allocation.sequence_index, allocation.allocation_ref),
        )
        cursor = window.starts_at
        for allocation in allocations:
            segment_end = cursor + timedelta(minutes=allocation.planned_minutes)
            task = task_by_ref.get(allocation.task_ref)
            suffix = ""
            binding = (
                execution_context.binding_for(allocation.task_ref)
                if execution_context is not None
                and hasattr(execution_context, "binding_for")
                else None
            )
            # P4's meal_default/user-explicit execution duration is a program
            # fact for this plan round.  It must not inherit an older generic
            # task-workload estimate label from TaskProgress.
            execution_duration_is_fact = bool(
                binding is not None
                and getattr(binding, "effective_duration_minutes", None) is not None
                and getattr(binding, "duration_source", None)
                in ("meal_default", "user_explicit", "semantic_estimate")
            )
            is_continuation = allocation.task_ref in seen_task_refs
            if (
                task is not None
                and task.total_source == SourceKind.AI_ESTIMATED
                and not execution_duration_is_fact
                and not is_continuation
            ):
                suffix = "（AI暂估）"
            title = "继续{}".format(allocation.task_title) if is_continuation else allocation.task_title
            display = "{} {} 分钟{}".format(title, allocation.planned_minutes, suffix)
            segments.append(
                _PlanSegment(
                    start=cursor,
                    end=segment_end,
                    title=title,
                    display=display,
                    kind="task",
                    alloc_ref=allocation.allocation_ref,
                    source_label=suffix,
                )
            )
            seen_task_refs.add(allocation.task_ref)
            cursor = segment_end
    for commitment in state.commitments:
        for prep_start, prep_end, prep_display in class_prep_display_segments(commitment):
            segments.append(_PlanSegment(
                start=prep_start,
                end=prep_end,
                title=prep_display,
                display=prep_display,
                kind="class_prep",
            ))
        segments.append(
            _PlanSegment(
                start=commitment.starts_at,
                end=commitment.ends_at,
                title=commitment.title,
                display=commitment.title,
                kind="commitment",
            )
        )
    # These slices are deliberately not normal TaskAllocation entries: they
    # overlap a fixed commitment by explicit user authorization.  Keep the
    # relation visible instead of presenting it as a sequential action.
    for allocation in concurrent_allocations or ():
        task = task_by_ref.get(getattr(allocation, "task_ref", None))
        if task is None:
            continue
        segments.append(
            _PlanSegment(
                start=allocation.starts_at,
                end=allocation.ends_at,
                title=task.title,
                display="同时：{} {} 分钟".format(task.title, allocation.planned_minutes),
                kind="concurrent",
            )
        )
    return segments


def _append_current_commitment_line(lines, current_commitment, segments, now):
    end = current_commitment.ends_at if current_commitment.ends_at is not None else now
    next_segment = next(
        (segment for segment in segments
         if segment.start >= end and (segment.end is None or segment.end > now)),
        None,
    )
    if next_segment is None:
        lines.append("现在：正在{}".format(current_commitment.title))
        return
    lines.append(
        "现在：正在{}；下一步{}开始{}".format(
            current_commitment.title,
            next_segment.start.strftime("%H:%M"),
            next_segment.title,
        )
    )


def summarize_day_plan(plan: DayAllocationPlan, state: DayPlanningState) -> DayPlanSummary:
    """把计划快照转成简短的中文文本行。

    - current_action_line：active window 中第一个 allocation（“现在：……”）；
    - later_window_lines：未来窗口按时间顺序（“HH:MM 后：……”）；
    - unallocated_lines：今天未排入的任务（“今天暂未安排：……”）。
    """
    if not isinstance(plan, DayAllocationPlan):
        raise TypeError("plan must be a DayAllocationPlan")
    if not isinstance(state, DayPlanningState):
        raise TypeError("state must be a DayPlanningState")

    window_by_ref = {window.window_ref: window for window in state.windows}
    task_by_ref = {task.task_ref: task for task in state.tasks}
    for allocation in plan.allocations:
        if allocation.window_ref not in window_by_ref:
            raise ValueError("allocation references unknown window: {}".format(allocation.window_ref))
    for ref in plan.unallocated_task_refs:
        if ref not in task_by_ref:
            raise ValueError("unallocated_task_refs references unknown task: {}".format(ref))

    current_line = None
    current = plan.current_allocation
    if current is not None:
        current_line = "现在：{} {} 分钟".format(current.task_title, current.planned_minutes)

    later_lines = []
    for window in state.windows:
        if window.window_ref == plan.current_window_ref:
            continue
        window_allocations = [
            allocation for allocation in plan.allocations if allocation.window_ref == window.window_ref
        ]
        if not window_allocations:
            continue
        label = window.starts_at.strftime("%H:%M")
        for allocation in window_allocations:
            later_lines.append(
                "{} 后：{} {} 分钟".format(label, allocation.task_title, allocation.planned_minutes)
            )

    unallocated_lines = []
    for ref in plan.unallocated_task_refs:
        unallocated_lines.append("今天暂未安排：{}".format(task_by_ref[ref].title))

    return DayPlanSummary(
        current_action_line=current_line,
        later_window_lines=tuple(later_lines),
        unallocated_lines=tuple(unallocated_lines),
    )
