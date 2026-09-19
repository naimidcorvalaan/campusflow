"""Bounded two-pass movement planning for execution-location facts.

This module deliberately does not create a second planner.  It reads the P2
provisional allocation, obtains real P3 routes for actual location changes,
reserves those intervals, and performs exactly one deterministic reallocation.
If that final allocation changes the location-transition order, it returns an
unapplied candidate. The final publication guard must still reject missing
routes; returning the provisional allocation does not certify it as safe.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import Optional, Tuple

from src.p2_allocator import allocate_tasks_across_windows
from src.p2_models import DayPlanningState, DayWindow
from src.p2_window_derivation import derive_active_window_ref
from src.p3_location_resolver import LocationResolution, LocationResolutionStatus, resolve_location
from src.p3_map_schema import CampusMapData, TransportMode, parse_transport_mode
from src.p3_route_planner import (
    MovementBlock,
    MovementStatus,
    effective_travel_minutes,
    final_plan_overlap_errors,
    plan_movement,
    reserve_timed_movement_intervals,
)
from src.p4_execution_context import CurrentLocationSource, ExecutionLocation, ExecutionPlanContext
from src.p4_execution_context import CurrentLocationContext, ExecutionConfirmation, ExecutionConfirmationKind


logger = logging.getLogger("campusflow.p4_execution_movement")


@dataclass(frozen=True)
class ExecutionTimelineEvent:
    activity_ref: str
    activity_type: str  # task / commitment
    starts_at: datetime
    ends_at: datetime
    location: Optional[ExecutionLocation]
    arrive_by: Optional[datetime] = None


@dataclass(frozen=True)
class ExecutionMovementOutcome:
    state: DayPlanningState
    allocation_plan: object
    blocks: Tuple[MovementBlock, ...]
    warnings: Tuple[str, ...]
    applied: bool


def apply_execution_sequence_movements(
    state, provisional_allocation_plan, context, map_data, **options
):
    """Reserve the primary sequence, then attempt its still-unplanned suffix.

    Continuation reuses the same allocator and route validation, without model
    calls or progress mutations. A meal/departure is not a workload deadline.
    """
    outcome = _apply_execution_sequence_movements(
        state, provisional_allocation_plan, context, map_data, **options
    )
    from src.p4_plan_continuation import continue_execution_work
    return continue_execution_work(outcome, context, map_data, options, state)


def _apply_execution_sequence_movements(
    state,
    provisional_allocation_plan,
    context,
    map_data,
    effective_duration_by_task_ref=None,
    protected_duration_by_task_ref=None,
    earliest_start_by_task_ref=None,
    preferred_start_by_task_ref=None,
    latest_end_by_task_ref=None,
    preferred_chunk_by_task_ref=None,
    concurrent_minutes_by_task_ref=None,
    task_order=None,
    include_low_attention=False,
    time_caller=None,
    location_caller=None,
    location_repair_caller=None,
):
    """Use one provisional sequence and one formal reallocation at most."""
    if not isinstance(state, DayPlanningState):
        raise TypeError("state must be a DayPlanningState")
    if not isinstance(context, ExecutionPlanContext):
        raise TypeError("context must be an ExecutionPlanContext")
    if not isinstance(map_data, CampusMapData) or not map_data.campus_id:
        raise TypeError("map_data 必须是带 campus_id 的 CampusMapData")
    _validate_context_campus(context, map_data)
    events = execution_timeline(
        state, provisional_allocation_plan, context, map_data, location_caller, location_repair_caller
    )
    if not events or not _has_sequence_location_fact(events, context):
        return ExecutionMovementOutcome(state, provisional_allocation_plan, (), (), False)
    mode = parse_transport_mode(context.transport_mode or TransportMode.WALK)
    sequence_events = _events_with_meal_temporal_relations(events, context)
    requests = _sequence_requests(sequence_events, context, map_data, mode, time_caller, state, provisional_allocation_plan)
    if not requests:
        return ExecutionMovementOutcome(state, provisional_allocation_plan, (), (), False)

    warnings = []
    formal = _reserve_and_reallocate(
        state, requests, task_order, include_low_attention, effective_duration_by_task_ref
        , protected_duration_by_task_ref, earliest_start_by_task_ref,
        preferred_start_by_task_ref, latest_end_by_task_ref, preferred_chunk_by_task_ref,
        concurrent_minutes_by_task_ref,
    )
    if formal is None:
        return ExecutionMovementOutcome(
            state, provisional_allocation_plan, (),
            ("没有足够窗口容纳执行地点之间的移动，当前候选尚未通过移动校验。",), False,
        )
    adjusted, final_plan, blocks = formal
    final_events = execution_timeline(
        adjusted, final_plan, context, map_data, location_caller, location_repair_caller
    )
    expected = tuple((item.origin.node_id, item.destination.node_id, item.destination_ref) for item in requests)
    final_sequence_events = _events_with_meal_temporal_relations(final_events, context)
    actual = _transition_signature(final_sequence_events, context, map_data)
    if not _transitions_still_match(expected, actual):
        if not _activity_order_is_preserved(sequence_events, final_sequence_events):
            return ExecutionMovementOutcome(
                state,
                provisional_allocation_plan,
                (),
                tuple(warnings) + ("执行顺序在重新分配后发生变化，当前候选尚未通过移动校验。",),
                False,
            )
        # One deterministic correction is allowed.  A route reservation can
        # shorten a flexible task, so recalculate the actual location changes
        # from that final task sequence instead of returning a stale route.
        corrected_requests = _sequence_requests(
            final_sequence_events, context, map_data, mode, time_caller, adjusted, final_plan
        )
        corrected = _reserve_and_reallocate(
            state,
            corrected_requests,
            task_order,
            include_low_attention,
            effective_duration_by_task_ref,
            protected_duration_by_task_ref, earliest_start_by_task_ref,
            preferred_start_by_task_ref, latest_end_by_task_ref,
            preferred_chunk_by_task_ref, concurrent_minutes_by_task_ref,
        )
        if corrected is not None:
            adjusted, final_plan, blocks = corrected
            final_events = execution_timeline(
                adjusted, final_plan, context, map_data, location_caller, location_repair_caller
            )
            expected = tuple(
                (item.origin.node_id, item.destination.node_id, item.destination_ref)
                for item in corrected_requests
            )
            final_sequence_events = _events_with_meal_temporal_relations(final_events, context)
            actual = _transition_signature(final_sequence_events, context, map_data)
            if _transitions_still_match(expected, actual):
                overlap_errors = final_plan_overlap_errors(adjusted, final_plan, blocks)
                if not overlap_errors:
                    return ExecutionMovementOutcome(
                        adjusted, final_plan, tuple(blocks), tuple(warnings), True
                    )
        return ExecutionMovementOutcome(
            state,
            provisional_allocation_plan,
            (),
            tuple(warnings) + ("执行顺序在重新分配后发生变化，当前候选尚未通过移动校验。",),
            False,
        )
    overlap_errors = final_plan_overlap_errors(adjusted, final_plan, blocks)
    if overlap_errors:
        return ExecutionMovementOutcome(
            state,
            provisional_allocation_plan,
            (),
            tuple(warnings) + ("最终时间线存在重叠，当前候选尚未通过移动校验。",),
            False,
        )
    return ExecutionMovementOutcome(adjusted, final_plan, tuple(blocks), tuple(warnings), True)


def _reserve_and_reallocate(
    state, requests, task_order, include_low_attention, effective_duration_by_task_ref,
    protected_duration_by_task_ref=None,
    earliest_start_by_task_ref=None,
    preferred_start_by_task_ref=None,
    latest_end_by_task_ref=None,
    preferred_chunk_by_task_ref=None,
    concurrent_minutes_by_task_ref=None,
):
    """Reserve verified requests and perform one deterministic allocation."""
    if not requests:
        return None
    adjusted, window_refs = reserve_timed_movement_intervals(
        state,
        tuple((item.movement_start, item.route_minutes, item.transition_minutes) for item in requests),
    )
    if len(window_refs) != len(requests):
        return None
    blocks = tuple(
        _movement_block(request, window_ref)
        for request, window_ref in zip(requests, window_refs)
    )
    if not blocks:
        return None
    presence = _returning_task_presence(state, requests)
    earliest_starts = dict(earliest_start_by_task_ref or {})
    movement_earliest = {
        block.destination_activity_ref: block.end_time
        for block in blocks
        if block.destination_activity_ref is not None
        and block.destination_activity_ref.startswith("day_task_")
        and block.destination_activity_ref not in presence
    }
    for task_ref, value in movement_earliest.items():
        earliest_starts[task_ref] = max(earliest_starts.get(task_ref, value), value)
    latest_ends = dict(latest_end_by_task_ref or {})
    for task_ref, value in _location_task_departure_boundaries(requests).items():
        if task_ref in presence:
            continue
        latest_ends[task_ref] = min(latest_ends.get(task_ref, value), value)
    # TaskAllocation intentionally stores a window ref plus sequence, not an
    # arbitrary wall-clock start.  Hard earliest-start facts therefore become
    # real window boundaries before the final allocator runs.
    adjusted = _split_windows_at_earliest_starts(adjusted, earliest_starts)
    logger.info(
        "[CampusFlow][p4_final_allocator] hard_movement=%s class_prep=%s "
        "fixed=%s earliest=%s latest=%s outbound=%s",
        tuple(
            (
                (block.transition_start or block.window_start).isoformat(),
                block.end_time.isoformat(),
            )
            for block in blocks
        ),
        _class_prep_diagnostics(state),
        tuple(
            (
                item.commitment_ref,
                item.starts_at.isoformat() if item.starts_at else None,
                item.ends_at.isoformat() if item.ends_at else None,
            )
            for item in state.commitments
        ),
        {key: value.isoformat() for key, value in earliest_starts.items()},
        {key: value.isoformat() for key, value in latest_ends.items()},
        _outbound_diagnostics(requests),
    )
    final_plan = allocate_tasks_across_windows(
        adjusted,
        task_order=task_order,
        include_low_attention=include_low_attention,
        effective_duration_by_task_ref=effective_duration_by_task_ref,
        protected_duration_by_task_ref=protected_duration_by_task_ref,
        earliest_start_by_task_ref=earliest_starts,
        latest_end_by_task_ref=latest_ends,
        preferred_chunk_by_task_ref=preferred_chunk_by_task_ref,
        concurrent_minutes_by_task_ref=concurrent_minutes_by_task_ref,
        presence_intervals_by_task_ref=presence,
    )
    # Default lunch/dinner starts are soft preferences.  Try a separate
    # boundary-aware candidate, but keep the reliable plan when the preferred
    # candidate would reduce another task, disturb a single-session task, or
    # make the meal itself less usable.  This is what distinguishes a default
    # dinner window from an explicit "not before 17:00" hard fact.
    soft_starts = dict(preferred_start_by_task_ref or {})
    if soft_starts:
        preferred_state = _split_windows_at_earliest_starts(adjusted, soft_starts)
        preferred_plan = allocate_tasks_across_windows(
            preferred_state,
            task_order=task_order,
            include_low_attention=include_low_attention,
            effective_duration_by_task_ref=effective_duration_by_task_ref,
            protected_duration_by_task_ref=protected_duration_by_task_ref,
            earliest_start_by_task_ref=earliest_starts,
            latest_end_by_task_ref=latest_ends,
            preferred_chunk_by_task_ref=preferred_chunk_by_task_ref,
            concurrent_minutes_by_task_ref=concurrent_minutes_by_task_ref,
            presence_intervals_by_task_ref=presence,
        )
        if _soft_start_candidate_is_better(
            adjusted, final_plan, preferred_state, preferred_plan,
            soft_starts, state.tasks,
        ):
            adjusted, final_plan = preferred_state, preferred_plan
    logger.info(
        "[CampusFlow][p4_final_allocator] final_allocations=%s free_windows=%s",
        _allocation_diagnostics(adjusted, final_plan),
        tuple(
            (item.window_ref, item.starts_at.isoformat(), item.ends_at.isoformat())
            for item in adjusted.windows
        ),
    )
    return adjusted, final_plan, blocks


def _soft_start_candidate_is_better(
    baseline_state, baseline_plan, preferred_state, preferred_plan,
    preferred_starts, tasks,
):
    """Accept a soft-window candidate only when it has no unrelated cost."""
    preferred_refs = set(preferred_starts)
    baseline_minutes = baseline_plan.planned_minutes_by_task
    preferred_minutes = preferred_plan.planned_minutes_by_task
    # A soft meal preference cannot reduce any task's planned work, including
    # the meal.  Capacity-constrained pre-window meals therefore survive.
    refs = set(baseline_minutes) | set(preferred_minutes)
    if any(preferred_minutes.get(ref, 0) < baseline_minutes.get(ref, 0) for ref in refs):
        return False

    baseline_intervals = _task_allocation_intervals(baseline_state, baseline_plan)
    preferred_intervals = _task_allocation_intervals(preferred_state, preferred_plan)
    task_by_ref = {item.task_ref: item for item in tasks}
    for ref in refs - preferred_refs:
        task = task_by_ref.get(ref)
        if task is not None and task.is_splittable is not True:
            if preferred_intervals.get(ref, ()) != baseline_intervals.get(ref, ()):
                return False

    improved = False
    for ref, desired in preferred_starts.items():
        baseline = baseline_intervals.get(ref, ())
        preferred = preferred_intervals.get(ref, ())
        if not preferred:
            return False
        if baseline and baseline[0][0] < desired <= preferred[0][0]:
            improved = True
    return improved


def _task_allocation_intervals(state, plan):
    from src.task_attention import allocation_spans
    result = {}
    for allocation, start, end in allocation_spans(state, plan):
        result.setdefault(allocation.task_ref, []).append((start, end))
    return {key: tuple(value) for key, value in result.items()}


def _split_windows_at_earliest_starts(state, earliest_starts):
    """Expose intra-window task start bounds without reserving fake time.

    The split preserves the original total capacity and chronological
    earliest-fit behaviour.  Earlier flexible work can still use the prefix;
    a task whose bound equals the split point can only use that point or a
    later segment.  The unused prefix remains genuine slack in the final
    timeline rather than an invented unavailable interval.
    """
    boundaries = tuple(sorted(set(
        value for value in (earliest_starts or {}).values()
        if isinstance(value, datetime)
    )))
    if not boundaries:
        return state
    windows = []
    changed = False
    for window in state.windows:
        points = tuple(
            value for value in boundaries
            if window.starts_at < value < window.ends_at
        )
        if not points:
            windows.append(window)
            continue
        changed = True
        cursor = window.starts_at
        remaining_capacity = window.capacity_minutes
        for index, end in enumerate(points + (window.ends_at,)):
            duration = int((end - cursor).total_seconds() // 60)
            capacity = min(remaining_capacity, max(0, duration))
            remaining_capacity -= capacity
            windows.append(DayWindow(
                window_ref=(
                    window.window_ref
                    if index == 0
                    else "{}__after_earliest_{}_{:02d}".format(
                        window.window_ref, cursor.strftime("%H%M"), index
                    )
                ),
                starts_at=cursor,
                ends_at=end,
                availability=window.availability,
                safety_buffer_minutes=(window.safety_buffer_minutes if index == 0 else 0),
                travel_minutes=(window.travel_minutes if index == 0 else 0),
                next_commitment_ref=window.next_commitment_ref,
                capacity_minutes=capacity,
            ))
            cursor = end
    if not changed:
        return state
    values = tuple(sorted(windows, key=lambda item: item.starts_at))
    return DayPlanningState(
        reference_datetime=state.reference_datetime,
        now=state.now,
        day_end=state.day_end,
        commitments=state.commitments,
        tasks=state.tasks,
        windows=values,
        active_window_ref=derive_active_window_ref(values, state.now),
        unresolved_commitment_refs=state.unresolved_commitment_refs,
        history=state.history,
    )


def _returning_task_presence(state, requests):
    """A repeated task may use each visit, never the interval spent elsewhere.

    Route reservations already split free windows at departure/arrival. Keep
    user earliest/deadline bounds independent of these visit-scoped limits.
    Only tasks with a verified departure and return need multiple intervals.
    Background processes do not describe the person's location.
    """
    active = {task.task_ref for task in state.tasks if task.attention_mode == 'active'}
    nodes = {}
    for departure in requests:
        ref = departure.origin_ref
        if ref not in active:
            continue
        if any(arrival.destination_ref == ref
               and arrival.destination.node_id == departure.origin.node_id
               and arrival.movement_start > departure.movement_start
               for arrival in requests):
            nodes[ref] = departure.origin.node_id
    result = {}
    ordered = sorted(requests, key=lambda r: r.movement_start)
    for ref, node in nodes.items():
        start = state.now if ordered[0].origin.node_id == node else None
        intervals = []
        for request in ordered:
            leave = request.movement_start - timedelta(minutes=request.transition_minutes)
            if start is not None:
                if leave > start:
                    intervals.append((start, leave))
                start = None
            if request.destination.node_id == node:
                start = request.movement_start + timedelta(minutes=request.route_minutes)
        if start is not None and start < state.day_end:
            intervals.append((start, state.day_end))
        result[ref] = tuple(intervals)
    return result


def _location_task_departure_boundaries(requests):
    """First outbound transition start for each located ordinary task.

    A task location remains valid only until the user starts packing to leave
    it.  Later capacity can hold later activities, but never a residual slice
    of that task at the destination location.
    """
    boundaries = {}
    for request in requests:
        task_ref = request.origin_ref
        if task_ref is None or not task_ref.startswith("day_task_"):
            continue
        if request.origin.node_id == request.destination.node_id:
            continue
        departure = request.movement_start - timedelta(minutes=request.transition_minutes)
        previous = boundaries.get(task_ref)
        if previous is None or departure < previous:
            boundaries[task_ref] = departure
    return boundaries


def _outbound_diagnostics(requests):
    return tuple(
        (
            item.origin_ref,
            item.origin.display_name,
            item.origin.node_id,
            (
                item.movement_start - timedelta(minutes=item.transition_minutes)
            ).isoformat(),
        )
        for item in requests
        if item.origin_ref is not None and item.origin_ref.startswith("day_task_")
    )


def _class_prep_diagnostics(state):
    from src.p3_class_prep import class_prep_interval

    result = []
    for commitment in state.commitments:
        interval = class_prep_interval(commitment)
        if interval is not None:
            result.append(
                (
                    commitment.commitment_ref,
                    interval[0].isoformat(),
                    interval[1].isoformat(),
                )
            )
    return tuple(result)


def _allocation_diagnostics(state, plan):
    from src.task_attention import allocation_spans
    return tuple((part.task_ref, start.isoformat(), end.isoformat())
                 for part, start, end in allocation_spans(state, plan))


def assume_current_location_for_timeline(context, events):
    """Request an origin without promoting a destination into a current fact.

    The legacy function name is retained for callers. An event location,
    including an automatically selected canteen, is never an origin assertion.
    """
    if not isinstance(context, ExecutionPlanContext):
        raise TypeError("context must be an ExecutionPlanContext")
    if context.current_location.source is not CurrentLocationSource.UNKNOWN:
        return context
    first = next((event for event in events if event.location is not None), None)
    if first is None:
        return context
    confirmations = tuple(
        item for item in context.confirmations
        if item.kind not in (ExecutionConfirmationKind.CURRENT_LOCATION_ASSUMED,
                             ExecutionConfirmationKind.CURRENT_LOCATION_REQUIRED)
    ) + (ExecutionConfirmation(first.activity_ref, ExecutionConfirmationKind.CURRENT_LOCATION_REQUIRED),)
    return context.with_confirmations(confirmations)


def replace_assumed_current_location(context, location):
    """Promote a verified feedback location and remove stale assumption facts."""
    if not isinstance(context, ExecutionPlanContext):
        raise TypeError("context must be an ExecutionPlanContext")
    if not isinstance(location, ExecutionLocation):
        raise TypeError("location must be an ExecutionLocation")
    confirmations = tuple(
        item for item in context.confirmations
        if item.kind not in (ExecutionConfirmationKind.CURRENT_LOCATION_ASSUMED,
                             ExecutionConfirmationKind.CURRENT_LOCATION_REQUIRED)
    )
    return context.with_current_location(
        CurrentLocationContext(location, CurrentLocationSource.USER)
    ).with_confirmations(confirmations)


@dataclass(frozen=True)
class _SequenceRequest:
    origin: ExecutionLocation
    destination: ExecutionLocation
    origin_ref: Optional[str]
    destination_ref: str
    destination_type: str
    movement_start: datetime
    transition_minutes: int
    route_minutes: int
    route_distance_m: int
    mode: TransportMode
    method: object
    low_minutes: int
    high_minutes: int
    approximate: bool


def execution_timeline(state, allocation_plan, context, map_data, location_caller=None, location_repair_caller=None, include_background=False):
    """Merge allocated task slices and fixed commitments in chronological order."""
    from src.task_attention import allocation_spans
    events = []
    for allocation, starts_at, ends_at in allocation_spans(state, allocation_plan):
        binding = context.binding_for(allocation.task_ref)
        task = next(item for item in state.tasks if item.task_ref == allocation.task_ref)
        background = task.attention_mode == 'background'
        if background and not include_background:
            continue
        events.append(ExecutionTimelineEvent(
            allocation.task_ref, "background" if background else "task", starts_at, ends_at,
            binding.execution_location if binding is not None else None,
        ))
    for commitment in state.commitments:
        if commitment.starts_at is None:
            continue
        # A newly imported timetable also contains earlier courses today.
        # Retain those canonical facts, but do not reserve travel into the
        # past. Unknown ends are deliberately not treated as completed.
        if commitment.ends_at is not None and commitment.ends_at <= state.now:
            continue
        location = _commitment_location(map_data, commitment.location_text, location_caller, location_repair_caller)
        from src.p3_class_prep import class_arrival_deadline
        events.append(ExecutionTimelineEvent(
            commitment.commitment_ref, "commitment", commitment.starts_at,
            commitment.ends_at or commitment.starts_at, location,
            class_arrival_deadline(commitment),
        ))
    return tuple(sorted(events, key=lambda item: (item.starts_at, item.ends_at, item.activity_ref)))


def _commitment_location(map_data, location_text, caller, repair_caller):
    if not location_text:
        return None
    resolution = resolve_location(map_data, location_text, caller=caller, repair_caller=repair_caller)
    if not resolution.usable or resolution.campus_id != map_data.campus_id:
        return None
    # A commitment's formal location is user/intake explicit, distinct from a
    # task binding but identical in stable routing identity.
    from src.p4_execution_context import ExecutionLocationSource
    return ExecutionLocation.from_resolution(resolution, ExecutionLocationSource.EXPLICIT_TASK_LOCATION)


def _has_sequence_location_fact(events, context):
    return bool(context.current_location.source in (CurrentLocationSource.USER, CurrentLocationSource.ASSUMED) and context.current_location.location) or any(
        event.location is not None for event in events
    )


def _events_with_meal_temporal_relations(events, context):
    """Apply stable meal↔commitment narrative order to the route sequence.

    Allocation timestamps are still the source of ordinary chronology.  This
    narrow adjustment is only for a meal whose intake semantics explicitly
    name a commitment before or after it.  It ensures the route reservation
    is built in the same order that the final allocator must satisfy, rather
    than letting a provisional free-form allocation reinterpret a pre-class
    meal as a post-class dinner.
    """
    ordered = list(events)
    for event in tuple(events):
        if event.activity_type != "task":
            continue
        binding = context.binding_for(event.activity_ref)
        if binding is None or binding.activity_kind != "meal":
            continue
        before_ref = getattr(binding, "meal_before_commitment_ref", None)
        after_ref = getattr(binding, "not_before_commitment_ref", None)
        target_ref = before_ref or after_ref
        if target_ref is None:
            continue
        try:
            meal_index = next(index for index, item in enumerate(ordered) if item.activity_ref == event.activity_ref)
            commitment_index = next(index for index, item in enumerate(ordered) if item.activity_ref == target_ref)
        except StopIteration:
            continue
        if before_ref is not None and meal_index > commitment_index:
            meal = ordered.pop(meal_index)
            commitment_index = next(index for index, item in enumerate(ordered) if item.activity_ref == before_ref)
            ordered.insert(commitment_index, meal)
        elif after_ref is not None and meal_index < commitment_index:
            meal = ordered.pop(meal_index)
            commitment_index = next(index for index, item in enumerate(ordered) if item.activity_ref == after_ref)
            ordered.insert(commitment_index + 1, meal)
    return tuple(ordered)


def _sequence_requests(events, context, map_data, mode, time_caller, state, allocation_plan=None):
    now = state.now
    anchor = context.current_location.location if context.current_location.source in (CurrentLocationSource.USER, CurrentLocationSource.ASSUMED) else None
    anchor_ref = None
    cursor = now
    requests = []
    from src.task_attention import allocation_spans
    finishes = {}
    if allocation_plan is not None:
        for part, _, end in allocation_spans(state, allocation_plan):
            finishes[part.task_ref] = max(finishes.get(part.task_ref, end), end)
    tasks = {task.task_ref: task for task in state.tasks}
    for event in events:
        # Unlocated tasks execute where the user already is; they do not invent
        # a new position or route.
        if event.location is None:
            cursor = max(cursor, event.ends_at)
            continue
        if anchor is None:
            anchor = event.location
            anchor_ref = event.activity_ref
            cursor = max(cursor, event.ends_at)
            continue
        if _same_physical_place(anchor, event.location, map_data):
            anchor = event.location
            anchor_ref = event.activity_ref
            cursor = max(cursor, event.ends_at)
            continue
        origin = _resolution_from_execution_location(anchor)
        destination = _resolution_from_execution_location(event.location)
        resolved = plan_movement(map_data, origin, destination, mode, time_caller)
        if resolved.status is not MovementStatus.OK or resolved.time_estimate is None or resolved.route is None:
            anchor = event.location
            anchor_ref = event.activity_ref
            cursor = max(cursor or event.ends_at, event.ends_at)
            continue
        base_minutes = resolved.time_estimate.estimated_minutes
        # Flexible activities follow the actual execution cursor.  Earlier
        # revisions specially back-planned an incoming meal route from its
        # next class deadline, which left a fake gap before dinner and made a
        # shorter meal look necessary.  A deadline only constrains the route
        # *to the commitment* below; normal tasks always move forward.
        if event.activity_type == "task":
            # For a meal with a fixed successor, reserve enough *earliest
            # feasible capacity* for its preferred duration and both real
            # routes.  This is not display-time backward packing: the final
            # allocator still fills all earlier flexible capacity first, so
            # the route starts as soon as the preceding activity finishes.
            reserved_start = _meal_capacity_reservation_start(
                events, event, context, map_data, mode, time_caller, base_minutes, state.now
            )
            if reserved_start is not None:
                # If the preceding work really finishes early, travel now and
                # leave any true slack after the meal.  Only when provisional
                # work would overrun the protected full meal do we use the
                # reservation boundary to make that work yield.
                proposed_start = min(
                    cursor + timedelta(minutes=5), reserved_start
                )
            else:
                padding = _origin_window_padding(state, cursor) if anchor_ref else 0
                proposed_start = cursor + timedelta(minutes=5 + padding)
            # A hard user-specified earliest start is not a reason to make
            # the user travel immediately and wait at the destination.
            # Keep all route/preparation costs; only defer this flexible leg.
            from src.p4_execution_enrichment import earliest_start_overrides
            hard_start = earliest_start_overrides(context, state).get(event.activity_ref)
            if hard_start is not None:
                proposed_start = max(proposed_start, hard_start - timedelta(minutes=base_minutes))
            task = tasks[event.activity_ref]
            for parent in task.departure_after_task_refs:
                if parent in finishes:
                    proposed_start = max(proposed_start, finishes[parent] + timedelta(minutes=5))
        else:
            proposed_start = event.starts_at - timedelta(minutes=base_minutes)
        proposed_buffer_start = proposed_start - timedelta(minutes=5)
        prior_end = (
            requests[-1].movement_start + timedelta(minutes=requests[-1].route_minutes)
            if requests else None
        )
        # Prefer destination-anchored routing for task-to-task legs.  If it
        # would collide with an already fixed prior leg (notably the user's
        # first current-location route), retain the established forward
        # placement; a later deterministic pass can still shorten work.
        if proposed_buffer_start >= now and (prior_end is None or proposed_buffer_start >= prior_end):
            movement_start = proposed_start
        else:
            movement_start = cursor + timedelta(minutes=5)
        # A fixed commitment is the only hard destination deadline.  For a
        # flexible task we leave from the previous activity's cursor and let
        # the formal allocator absorb the occupied interval.
        if event.activity_type == "commitment":
            deadline = event.arrive_by or event.starts_at
            movement_start = deadline - timedelta(minutes=base_minutes)
            # A provisional task may still occupy this interval.  Keep the
            # hard route request: reservation removes the capacity and the
            # one final allocation shortens flexible work instead of silently
            # dropping the route to the class building.
        effective = effective_travel_minutes(mode, base_minutes, movement_start, movement_start + timedelta(minutes=base_minutes))
        if effective != base_minutes and event.activity_type == "commitment":
            deadline = event.arrive_by or event.starts_at
            movement_start = deadline - timedelta(minutes=effective)
        requests.append(_SequenceRequest(
            anchor, event.location, anchor_ref, event.activity_ref, event.activity_type,
            movement_start, 5, effective, resolved.route.total_distance_m, mode,
            resolved.time_estimate.method, resolved.time_estimate.min_minutes,
            resolved.time_estimate.max_minutes,
            resolved.origin_resolution.status is LocationResolutionStatus.APPROXIMATE
            or resolved.destination_resolution.status is LocationResolutionStatus.APPROXIMATE,
        ))
        anchor = event.location
        anchor_ref = event.activity_ref
        if event.activity_type == "task":
            duration = event.ends_at - event.starts_at
            cursor = movement_start + timedelta(minutes=effective) + duration
        else:
            cursor = max(movement_start + timedelta(minutes=effective), event.ends_at)
    return tuple(requests)


def _meal_capacity_reservation_start(events, event, context, map_data, mode, time_caller, incoming_minutes, planning_now):
    """Reserve a meal's preferred capacity before a real fixed successor.

    The returned time is an allocator reservation boundary.  The final
    earliest-fit allocation occupies the preceding prefix, so it cannot leave
    an invisible gap before the route.
    """
    if event.activity_type != "task":
        return None
    binding = context.binding_for(event.activity_ref)
    if getattr(binding, "activity_kind", None) != "meal":
        return None
    preferred = getattr(binding, "effective_duration_minutes", None)
    if not preferred:
        return None
    # A selected lunch/dinner period is a strong soft preference for the
    # *start* of eating.  Reserve the inbound leg before that period closes;
    # the meal itself may naturally finish just after the boundary.
    period_start = getattr(binding, "meal_window_start_minutes", None)
    period_end = getattr(binding, "meal_window_end_minutes", None)
    now_minutes = planning_now.hour * 60 + planning_now.minute
    period_reservation = None
    if period_start is not None and period_start <= now_minutes < period_end:
        period_end_at = event.starts_at.replace(
            hour=period_end // 60, minute=period_end % 60, second=0, microsecond=0
        )
        period_end_at = planning_now.replace(
            hour=period_end // 60, minute=period_end % 60, second=0, microsecond=0
        )
        candidate = period_end_at - timedelta(minutes=incoming_minutes)
        # Never fabricate a departure before the planning horizon.
        period_reservation = max(planning_now, candidate)
    before_ref = getattr(binding, "meal_before_commitment_ref", None)
    if before_ref is not None:
        successors = tuple(item for item in events if item.activity_ref == before_ref)
    else:
        event_index = events.index(event)
        successors = events[event_index + 1:]
    for successor in successors:
        if successor.activity_type != "commitment" or successor.location is None:
            continue
        deadline = successor.arrive_by or successor.starts_at
        if deadline is None:
            continue
        onward = plan_movement(
            map_data, _resolution_from_execution_location(event.location),
            _resolution_from_execution_location(successor.location), mode, time_caller,
        )
        if onward.status is not MovementStatus.OK or onward.time_estimate is None:
            continue
        class_reservation = deadline - timedelta(
            minutes=onward.time_estimate.estimated_minutes + 5 + preferred + incoming_minutes
        )
        return min(class_reservation, period_reservation) if period_reservation else class_reservation
    return period_reservation


def _origin_window_padding(state, cursor):
    """Retain a legacy P2 window's end buffer when it is split by a route."""
    window = next(
        (item for item in state.windows if item.starts_at <= cursor < item.ends_at),
        None,
    )
    if window is None:
        return 0
    return int(window.safety_buffer_minutes + window.travel_minutes)


def _movement_block(request, window_ref):
    return MovementBlock(
        window_ref=window_ref,
        window_start=request.movement_start,
        origin_text=request.origin.display_name,
        destination_text=request.destination.display_name,
        origin_node_id=request.origin.node_id,
        destination_node_id=request.destination.node_id,
        origin_name=request.origin.display_name,
        destination_name=request.destination.display_name,
        mode=request.mode,
        distance_m=request.route_distance_m,
        estimated_minutes=request.route_minutes,
        low_minutes=request.low_minutes,
        high_minutes=request.high_minutes,
        method=request.method,
        approximate=request.approximate,
        transition_minutes=request.transition_minutes,
        origin_activity_ref=request.origin_ref,
        destination_activity_ref=request.destination_ref,
    )


def _resolution_from_execution_location(location):
    return LocationResolution(
        LocationResolutionStatus.RESOLVED,
        location.display_name,
        location.node_id,
        location.display_name,
        "execution_context",
        None,
        location.campus_id,
    )


def _same_physical_place(left, right, map_data):
    return _same_physical_nodes(left.node_id, right.node_id, map_data)


def _same_physical_nodes(left_id, right_id, map_data):
    if left_id == right_id:
        return True
    nodes = {node.id: node for node in map_data.nodes}
    left_anchor = getattr(nodes.get(left_id), "physical_anchor_id", None)
    right_anchor = getattr(nodes.get(right_id), "physical_anchor_id", None)
    return bool(left_anchor and left_anchor == right_anchor)


def _transition_signature(events, context, map_data):
    anchor = context.current_location.location if context.current_location.source in (CurrentLocationSource.USER, CurrentLocationSource.ASSUMED) else None
    pairs = []
    for event in events:
        if event.location is None:
            continue
        if anchor is None:
            anchor = event.location
            continue
        if not _same_physical_place(anchor, event.location, map_data):
            pairs.append((anchor.node_id, event.location.node_id, event.activity_ref))
        anchor = event.location
    return tuple(pairs)


def execution_route_coverage_errors(state, plan, blocks, context, map_data):
    """A known change of physical location needs a reserved incoming route.

    This is a publication guard, not a second route planner. Unknown locations
    remain unknown; same-building aliases reuse the formal map's anchor.
    """
    events = execution_timeline(state, plan, context, map_data)
    anchor = (context.current_location.location if context.current_location.source in
              (CurrentLocationSource.USER, CurrentLocationSource.ASSUMED) else None)
    cursor = state.now
    unused = list(blocks)
    errors = []
    for event in events:
        if event.location is None:
            cursor = max(cursor, event.ends_at)
            continue
        if anchor is not None and not _same_physical_place(anchor, event.location, map_data):
            match = next((block for block in unused
                          if _same_physical_nodes(block.origin_node_id, anchor.node_id, map_data)
                          and _same_physical_nodes(block.destination_node_id, event.location.node_id, map_data)
                          and (block.transition_start or block.window_start) >= cursor
                          and block.end_time <= (event.arrive_by or event.starts_at)), None)
            if match is None:
                errors.append("known location transition has no reserved route: {}".format(event.activity_ref))
            else:
                unused.remove(match)
        anchor = event.location
        cursor = max(cursor, event.ends_at)
    return tuple(errors)


def _transitions_still_match(expected, actual):
    # The final plan may shift times, but it must retain each reserved
    # destination transition in order.  Missing/reordered facts degrade.
    return tuple(expected) == tuple(actual)


def _activity_order_is_preserved(provisional_events, final_events):
    """A retry may drop work for capacity, never reverse shared activities."""
    original = [item.activity_ref for item in provisional_events]
    final = [item.activity_ref for item in final_events]
    shared = set(final)
    return [ref for ref in original if ref in shared] == [ref for ref in final if ref in set(original)]


def _validate_context_campus(context, map_data):
    locations = [item.execution_location for item in context.bindings if item.execution_location]
    if context.current_location.location:
        locations.append(context.current_location.location)
    if any(item.campus_id != map_data.campus_id for item in locations):
        raise ValueError("ExecutionPlanContext 与 selected campus 不一致")
