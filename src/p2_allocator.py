"""P2b 确定性跨窗口任务分配器（Python 3.8 兼容）。

纯函数、deterministic、offline：
- 无 LLM / API / UI；
- 无路线 provider / AI 路线；
- 无任务身份匹配（P2c 负责）；
- 不修改输入 DayPlanningState / TaskProgress。

只消费已经结构化好的字段：state / remaining_minutes / is_splittable /
minimum_slice_minutes / window capacity / availability。

默认策略是 deterministic baseline，不声称最优：
- 窗口按 starts_at 升序；
- 任务默认按 state.tasks 输入顺序；可用 task_order 覆盖（供 P2c Agent 注入）。
"""

from datetime import datetime
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from src.p1_window_models import AvailabilityLevel
from src.p2_allocation_models import DayAllocationPlan, TaskAllocation, make_allocation_ref
from src.p2_models import DayPlanningState, TaskState

DEFAULT_MINIMUM_SLICE_MINUTES = 15
MEAL_MINIMUM_MINUTES = 20

WARNING_UNKNOWN_DURATION = "任务剩余时长未知，需要 Agent 暂估后再分配"
WARNING_LOW_ATTENTION = "低注意力窗口的任务适配仍需后续 Agent 确认。"


def allocate_tasks_across_windows(
    state: DayPlanningState,
    task_order: Optional[Sequence[str]] = None,
    include_low_attention: bool = False,
    effective_duration_by_task_ref: Optional[Mapping[str, int]] = None,
    protected_duration_by_task_ref: Optional[Mapping[str, int]] = None,
    earliest_start_by_task_ref: Optional[Mapping[str, datetime]] = None,
    latest_end_by_task_ref: Optional[Mapping[str, datetime]] = None,
    preferred_chunk_by_task_ref: Optional[Mapping[str, int]] = None,
    concurrent_minutes_by_task_ref: Optional[Mapping[str, int]] = None,
) -> DayAllocationPlan:
    """把 ACTIVE 任务按确定性策略分配到今天剩余窗口，返回计划快照。

    不变量（程序保证）：
    - 不安排 COMPLETED / SKIPPED_TODAY / ABANDONED；
    - 同一任务 planned 总和 <= remaining；
    - 每个窗口 planned 总和 <= capacity；
    - 不可拆分任务只整体放入一个窗口；
    - 可拆分任务非最终片段 >= minimum_slice；最终收尾片段允许小于 minimum_slice；
    - is_splittable=None 按不可拆分保守处理；
    - 不修改输入对象；不增加 completed。
    """
    _require_state(state)
    if not isinstance(include_low_attention, bool):
        raise ValueError("include_low_attention must be a bool")
    duration_overrides = _validate_duration_overrides(state, effective_duration_by_task_ref)
    protected_durations = _validate_duration_overrides(state, protected_duration_by_task_ref)
    earliest_starts = _validate_earliest_starts(state, earliest_start_by_task_ref)
    latest_ends = _validate_latest_ends(state, latest_end_by_task_ref)
    preferred_chunks = _validate_chunk_preferences(state, preferred_chunk_by_task_ref)
    concurrent_minutes = _validate_concurrent_minutes(state, concurrent_minutes_by_task_ref)

    usable_windows = []
    for window in state.windows:
        if window.ends_at <= state.now:
            continue
        if window.capacity_minutes <= 0:
            continue
        if window.availability == AvailabilityLevel.LOW_ATTENTION and not include_low_attention:
            continue
        usable_windows.append(window)

    task_by_ref = {task.task_ref: task for task in state.tasks}
    task_refs = _resolve_task_order(state, task_order)

    remaining_capacity = {window.window_ref: window.capacity_minutes for window in usable_windows}
    allocations: List[TaskAllocation] = []
    unallocated: List[str] = []
    warnings: List[str] = []
    sequence_by_window: Dict[str, int] = {}
    allocation_index = 0
    low_attention_used = False

    for task_position, ref in enumerate(task_refs):
        task = task_by_ref[ref]
        if task.state != TaskState.ACTIVE:
            continue
        task_windows = [
            window for window in usable_windows
            if (
                ref not in earliest_starts
                or window.ends_at > earliest_starts[ref]
            )
            and (
                ref not in latest_ends
                or window.starts_at < latest_ends[ref]
            )
        ]
        remaining_task = _remaining_for_allocation(task, duration_overrides.get(ref))
        if remaining_task is not None:
            remaining_task = max(0, remaining_task - concurrent_minutes.get(ref, 0))
        if remaining_task is None:
            unallocated.append(ref)
            warnings.append("{}：{}".format(WARNING_UNKNOWN_DURATION, task.title))
            continue
        if remaining_task <= 0:
            continue

        # P4 execution contexts may mark a later user-requested activity
        # (currently a 40-minute meal) as protected.  Earlier flexible work
        # may be shortened, but must not consume the only capacity that makes
        # that later activity impossible.  Legacy callers pass no protection.
        protected_after = tuple(
            protected_durations[later_ref]
            for later_ref in task_refs[task_position + 1:]
            if (
                task_by_ref[later_ref].state == TaskState.ACTIVE
                and later_ref in protected_durations
            )
        )
        allocatable_after_protection = max(0, sum(remaining_capacity.values()))

        planned_total = 0
        # A default meal prefers its full duration, but remains a single,
        # usable 20--40 minute block when hard route/class constraints leave
        # less room.  It is never fragmented.  Explicit meal durations keep
        # the ordinary task contract instead.
        is_preferred_meal = ref in protected_durations
        if is_preferred_meal:
            preferred = protected_durations[ref]
            candidates = []
            for window in task_windows:
                available = _available_before_latest_end(
                    remaining_capacity[window.window_ref], window,
                    remaining_capacity[window.window_ref], latest_ends.get(ref),
                )
                if available >= MEAL_MINIMUM_MINUTES:
                    candidates.append((window, available))
            full = next(((window, available) for window, available in candidates if available >= preferred), None)
            selected = full or (max(candidates, key=lambda value: value[1]) if candidates else None)
            if selected is not None:
                window, available = selected
                planned = min(preferred, available)
                allocations.append(_make_allocation(
                    allocation_index, sequence_by_window.get(window.window_ref, 0), task,
                    window, planned, remaining_task,
                ))
                allocation_index += 1
                sequence_by_window[window.window_ref] = sequence_by_window.get(window.window_ref, 0) + 1
                planned_total = planned
                remaining_capacity[window.window_ref] -= planned
                if window.availability == AvailabilityLevel.LOW_ATTENTION:
                    low_attention_used = True
        elif task.is_splittable:
            min_slice = (
                task.minimum_slice_minutes
                if task.minimum_slice_minutes is not None
                else DEFAULT_MINIMUM_SLICE_MINUTES
            )
            remaining_to_plan = remaining_task
            preferred_chunk = preferred_chunks.get(ref)
            for window_index, window in enumerate(task_windows):
                if remaining_to_plan <= 0:
                    break
                available = min(
                    remaining_capacity[window.window_ref],
                    max(0, allocatable_after_protection - planned_total),
                )
                available = _available_before_latest_end(
                    available,
                    window,
                    remaining_capacity[window.window_ref],
                    latest_ends.get(ref),
                )
                available = _available_while_preserving_later_protected(
                    available,
                    window.window_ref,
                    (
                        dict(remaining_capacity)
                        if any(later_ref in earliest_starts for later_ref in task_refs[task_position + 1:])
                        else {
                            candidate.window_ref: remaining_capacity[candidate.window_ref]
                            for candidate in task_windows[:window_index + 1]
                        }
                    ),
                    protected_after,
                )
                if available <= 0:
                    continue
                if remaining_to_plan <= available:
                    planned = remaining_to_plan
                else:
                    planned = min(available, preferred_chunk) if preferred_chunk is not None else available
                    if planned < min_slice:
                        continue
                allocations.append(
                    _make_allocation(
                        allocation_index=allocation_index,
                        sequence_index=sequence_by_window.get(window.window_ref, 0),
                        task=task,
                        window=window,
                        planned_minutes=planned,
                        remaining_before=remaining_task - planned_total,
                    )
                )
                allocation_index += 1
                sequence_by_window[window.window_ref] = sequence_by_window.get(window.window_ref, 0) + 1
                planned_total += planned
                remaining_to_plan -= planned
                remaining_capacity[window.window_ref] -= planned
                if window.availability == AvailabilityLevel.LOW_ATTENTION:
                    low_attention_used = True
                # Once a contiguous slot for the immediately later protected
                # meal exists in the current timeline prefix, do not spill
                # this earlier flexible activity across a fixed-commitment
                # gap and thereby turn “报告→吃饭” into two interleaved tasks.
                if protected_after and _preferred_durations_fit(
                    {
                        candidate.window_ref: remaining_capacity[candidate.window_ref]
                        for candidate in (
                            task_windows
                            if any(later_ref in earliest_starts for later_ref in task_refs[task_position + 1:])
                            else task_windows[:window_index + 1]
                        )
                    },
                    protected_after,
                ):
                    break
        else:
            if remaining_task > allocatable_after_protection or not _preferred_durations_fit(
                _remaining_capacities_after(
                    remaining_capacity, None, remaining_task
                ),
                protected_after,
            ):
                unallocated.append(ref)
                continue
            for window in task_windows:
                if ref in earliest_starts and window.starts_at < earliest_starts[ref]:
                    continue
                available = _available_before_latest_end(
                    remaining_capacity[window.window_ref],
                    window,
                    remaining_capacity[window.window_ref],
                    latest_ends.get(ref),
                )
                if available >= remaining_task:
                    allocations.append(
                        _make_allocation(
                            allocation_index=allocation_index,
                            sequence_index=sequence_by_window.get(window.window_ref, 0),
                            task=task,
                            window=window,
                            planned_minutes=remaining_task,
                            remaining_before=remaining_task,
                        )
                    )
                    allocation_index += 1
                    sequence_by_window[window.window_ref] = sequence_by_window.get(window.window_ref, 0) + 1
                    planned_total = remaining_task
                    remaining_capacity[window.window_ref] -= remaining_task
                    if window.availability == AvailabilityLevel.LOW_ATTENTION:
                        low_attention_used = True
                    break

        if planned_total < remaining_task:
            unallocated.append(ref)

    if low_attention_used:
        warnings.append(WARNING_LOW_ATTENTION)

    active_window_ref = state.active_window_ref
    current_allocation_ref = None
    if active_window_ref is not None:
        for allocation in allocations:
            if allocation.window_ref == active_window_ref:
                current_allocation_ref = allocation.allocation_ref
                break

    later_allocations = tuple(
        allocation for allocation in allocations if allocation.window_ref != active_window_ref
    )
    unused_capacity = tuple(
        (window.window_ref, remaining_capacity[window.window_ref]) for window in usable_windows
    )

    return DayAllocationPlan(
        allocations=tuple(allocations),
        unallocated_task_refs=tuple(unallocated),
        unused_capacity_by_window=unused_capacity,
        current_window_ref=active_window_ref,
        current_allocation_ref=current_allocation_ref,
        later_allocations=later_allocations,
        warnings=tuple(warnings),
        total_planned_minutes=sum(allocation.planned_minutes for allocation in allocations),
    )


def _make_allocation(
    allocation_index: int,
    sequence_index: int,
    task,
    window,
    planned_minutes: int,
    remaining_before: int,
) -> TaskAllocation:
    return TaskAllocation(
        allocation_ref=make_allocation_ref(allocation_index),
        task_ref=task.task_ref,
        task_title=task.title,
        window_ref=window.window_ref,
        planned_minutes=planned_minutes,
        sequence_index=sequence_index,
        is_partial=planned_minutes < remaining_before,
        remaining_before=remaining_before,
        remaining_after=remaining_before - planned_minutes,
    )


def _resolve_task_order(state: DayPlanningState, task_order: Optional[Sequence[str]]) -> List[str]:
    state_refs = [task.task_ref for task in state.tasks]
    if task_order is None:
        return state_refs
    if not isinstance(task_order, (tuple, list)):
        raise TypeError("task_order must be a sequence of task_ref strings")
    seen = set()
    ordered = []
    for ref in task_order:
        if not isinstance(ref, str) or not ref:
            raise ValueError("task_order entries must be non-empty strings")
        if ref not in state_refs:
            raise ValueError("task_order contains unknown task_ref: {}".format(ref))
        if ref in seen:
            raise ValueError("task_order contains duplicate task_ref: {}".format(ref))
        seen.add(ref)
        ordered.append(ref)
    for ref in state_refs:
        if ref not in seen:
            ordered.append(ref)
    return ordered


def _validate_duration_overrides(state, overrides):
    if overrides is None:
        return {}
    if not isinstance(overrides, Mapping):
        raise TypeError("effective_duration_by_task_ref 必须是 mapping 或 None")
    known_refs = {task.task_ref for task in state.tasks}
    validated = {}
    for task_ref, minutes in overrides.items():
        if task_ref not in known_refs:
            raise ValueError("effective duration 包含未知 task_ref：{}".format(task_ref))
        if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes < 1:
            raise ValueError("effective duration 必须是正整数")
        validated[task_ref] = minutes
    return validated


def _validate_concurrent_minutes(state, overrides):
    """Validate already-authorized commitment-time work without changing progress.

    The concurrent slice is planned work, not completed work.  It is deducted
    only at the allocator boundary so ordinary window allocations cannot plan
    the same task minutes again.
    """
    if overrides is None:
        return {}
    if not isinstance(overrides, Mapping):
        raise TypeError("concurrent_minutes_by_task_ref 必须是 mapping 或 None")
    known_refs = {task.task_ref for task in state.tasks}
    validated = {}
    for task_ref, minutes in overrides.items():
        if task_ref not in known_refs:
            raise ValueError("concurrent minutes 包含未知 task_ref：{}".format(task_ref))
        if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes < 1:
            raise ValueError("concurrent minutes 必须是正整数")
        validated[task_ref] = minutes
    return validated


def _validate_earliest_starts(state, earliest_starts):
    if earliest_starts is None:
        return {}
    if not isinstance(earliest_starts, Mapping):
        raise TypeError("earliest_start_by_task_ref 必须是 mapping 或 None")
    known_refs = {task.task_ref for task in state.tasks}
    validated = {}
    for task_ref, value in earliest_starts.items():
        if task_ref not in known_refs:
            raise ValueError("earliest start 包含未知 task_ref：{}".format(task_ref))
        if not isinstance(value, datetime):
            raise TypeError("earliest start 必须是 datetime")
        validated[task_ref] = value
    return validated


def _validate_latest_ends(state, latest_ends):
    if latest_ends is None:
        return {}
    if not isinstance(latest_ends, Mapping):
        raise TypeError("latest_end_by_task_ref 必须是 mapping 或 None")
    known_refs = {task.task_ref for task in state.tasks}
    validated = {}
    for task_ref, value in latest_ends.items():
        if task_ref not in known_refs:
            raise ValueError("latest end 包含未知 task_ref：{}".format(task_ref))
        if not isinstance(value, datetime):
            raise TypeError("latest end 必须是 datetime")
        validated[task_ref] = value
    return validated


def _validate_chunk_preferences(state, preferences):
    if preferences is None:
        return {}
    if not isinstance(preferences, Mapping):
        raise TypeError("preferred_chunk_by_task_ref 必须是 mapping 或 None")
    known = {task.task_ref: task for task in state.tasks}
    validated = {}
    for task_ref, minutes in preferences.items():
        task = known.get(task_ref)
        if task is None:
            raise ValueError("preferred chunk 包含未知 task_ref：{}".format(task_ref))
        if not task.is_splittable:
            raise ValueError("preferred chunk 只能用于 splittable task：{}".format(task_ref))
        if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes < 1:
            raise ValueError("preferred chunk 必须为正整数")
        if task.minimum_slice_minutes is not None and minutes < task.minimum_slice_minutes:
            raise ValueError("preferred chunk 不得小于 minimum slice")
        validated[task_ref] = minutes
    return validated


def _available_before_latest_end(available, window, remaining_capacity, latest_end):
    """Cap sequential allocation at a location-bound departure boundary.

    Allocations occupy each window from its start in sequence order.  Capacity
    can be shorter than the wall-clock window because of existing P2 buffers,
    so the boundary is applied to that schedulable prefix, not to raw duration.
    """
    if latest_end is None or available <= 0:
        return available
    used = max(0, window.capacity_minutes - remaining_capacity)
    minutes_before_boundary = max(
        0,
        int((latest_end - window.starts_at).total_seconds() // 60),
    )
    schedulable_before_boundary = min(window.capacity_minutes, minutes_before_boundary)
    return min(available, max(0, schedulable_before_boundary - used))


def _available_while_preserving_later_protected(
    available, window_ref, remaining_capacity, protected_durations
):
    """Cap a flexible slice so later protected work still fits contiguously."""
    if not protected_durations or available <= 0:
        return available
    for candidate in range(available, -1, -1):
        capacities = _remaining_capacities_after(remaining_capacity, window_ref, candidate)
        if _preferred_durations_fit(capacities, protected_durations):
            return candidate
    return 0


def _remaining_capacities_after(remaining_capacity, window_ref, consume):
    capacities = dict(remaining_capacity)
    if window_ref is not None:
        capacities[window_ref] = max(0, capacities.get(window_ref, 0) - consume)
    return capacities


def _preferred_durations_fit(capacities, protected_durations):
    """Reserve full preferred meals where possible, otherwise their 20-min floor."""
    slots = sorted((int(value) for value in capacities.values()), reverse=True)
    for duration in sorted((int(value) for value in protected_durations), reverse=True):
        required = duration if any(capacity >= duration for capacity in slots) else MEAL_MINIMUM_MINUTES
        for index, capacity in enumerate(slots):
            if capacity >= required:
                slots[index] = capacity - required
                break
        else:
            return False
        slots.sort(reverse=True)
    return True


def _remaining_for_allocation(task, effective_duration):
    """Keep TaskProgress workload intact while consuming a round-local override.

    The override is the total execution time for this planning round.  Recorded
    progress is still respected, so an ACTIVE task with prior completed work
    cannot receive more than its effective remainder.
    """
    if effective_duration is None:
        return task.remaining_minutes
    return max(0, effective_duration - task.completed_minutes)


def _require_state(state: DayPlanningState) -> None:
    if not isinstance(state, DayPlanningState):
        raise TypeError("state must be a DayPlanningState")
