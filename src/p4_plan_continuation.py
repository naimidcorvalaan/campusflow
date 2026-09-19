"""Complete a feasible day-plan suffix using the existing allocator and routes.

The prefix is immutable. Temporary residual tasks are allocation inputs only;
the published task ledger, progress, references and execution context stay intact.
"""
from dataclasses import replace

from src.p2_allocator import allocate_tasks_across_windows, _remaining_for_allocation, MEAL_MINIMUM_MINUTES
from src.p2_allocation_models import DayAllocationPlan, make_allocation_ref
from src.p2_models import TaskState
from src.p2_window_derivation import derive_active_window_ref
from src.p4_execution_context import CurrentLocationContext, CurrentLocationSource


def continue_execution_work(outcome, context, map_data, options, base_state):
    from src.p4_execution_movement import (
        _apply_execution_sequence_movements, execution_timeline,
        _has_sequence_location_fact,
    )
    from src.p3_route_planner import final_plan_overlap_errors

    # A failed route candidate must not become valid merely by adding work.
    if outcome.warnings and not outcome.applied:
        return outcome
    original = outcome.state
    if original.unresolved_commitment_refs:
        return outcome
    effective = options.get('effective_duration_by_task_ref') or {}
    concurrent = options.get('concurrent_minutes_by_task_ref') or {}
    # Each successful pass consumes work and a strictly later suffix. No model
    # retry, no midnight rollover, no unbounded fill loop.
    retry_cursor = None
    for round_index in range(len(original.tasks) + len(original.commitments) + len(base_state.windows) + 1):
        state, plan = outcome.state, outcome.allocation_plan
        residual = {}
        for task in original.tasks:
            remaining = _remaining_for_allocation(task, effective.get(task.task_ref))
            if task.state is TaskState.ACTIVE and remaining is not None:
                left = remaining - plan.planned_minutes_by_task.get(task.task_ref, 0) - concurrent.get(task.task_ref, 0)
                # Default meals may intentionally use their validated shorter
                # single block. Never turn the unused preference into a snack.
                protected = options.get('protected_duration_by_task_ref') or {}
                if left > 0 and not (task.task_ref in protected and plan.planned_minutes_by_task.get(task.task_ref, 0)):
                    residual[task.task_ref] = left
        if not residual:
            break
        events = execution_timeline(state, plan, context, map_data)
        cursor = max([state.now] + [e.ends_at for e in events if e.activity_type == 'task'])
        from src.task_attention import allocation_spans
        cursor = max([cursor] + [end for part, _, end in allocation_spans(state, plan)
                                if not part.occupies_attention])
        if retry_cursor is not None:
            cursor = max(cursor, retry_cursor)
        # A future class is not the end of all usable time before that class.
        # Only finish an interval already overlapping the suffix boundary.
        for event in events:
            if event.starts_at <= cursor < event.ends_at:
                cursor = event.ends_at
        for block in outcome.blocks:
            if (block.transition_start or block.window_start) < cursor < block.end_time:
                cursor = block.end_time
        if cursor >= state.day_end:
            break
        # An infeasible prefix of the suffix is not proof that every later
        # window is infeasible. Advance only to an existing formal boundary;
        # never invent capacity or modify the accepted prefix.
        retry_cursor = min((w.starts_at for w in base_state.windows if w.starts_at > cursor), default=None)
        # Do not invent a location. Start from the last formally planned place.
        located = [e for e in events if e.location is not None and e.ends_at <= cursor]
        location = located[-1].location if located else context.current_location.location
        tail_context = context
        if location is not None:
            tail_context = context.with_current_location(CurrentLocationContext(location, CurrentLocationSource.ASSUMED))
        # Only proven prefix completion releases an edge. Cancellation and
        # unknown workload are not completion. These are private suffix inputs;
        # the published ledger retains the original edges and actual progress.
        satisfied = {t.task_ref for t in original.tasks if t.state is TaskState.COMPLETED or (
            t.state is TaskState.ACTIVE
            and _remaining_for_allocation(t, effective.get(t.task_ref)) is not None
            and (plan.planned_minutes_by_task.get(t.task_ref, 0) >= _remaining_for_allocation(t, effective.get(t.task_ref))
                 or (t.task_ref in (options.get('protected_duration_by_task_ref') or {})
                     and plan.planned_minutes_by_task.get(t.task_ref, 0) >= MEAL_MINIMUM_MINUTES)))}
        spans = allocation_spans(state, plan)
        fulfilled_overlap = {t.task_ref for t in original.tasks if t.overlap_task_ref and any(
            a < d and c < b for part,a,b in spans for process,c,d in spans
            if part.task_ref == t.task_ref and process.task_ref == t.overlap_task_ref)}
        expired_overlap = {t.task_ref for t in original.tasks if t.overlap_task_ref in satisfied
                           and t.task_ref not in fulfilled_overlap}
        tasks = tuple(replace(t,
            total_minutes=residual[t.task_ref] if t.task_ref in residual else t.total_minutes,
            completed_minutes=0 if t.task_ref in residual else t.completed_minutes,
            launch_task_ref=None if t.launch_task_ref in satisfied else t.launch_task_ref,
            user_reported_running=t.user_reported_running or (t.attention_mode == 'background' and t.launch_task_ref in satisfied),
            state=TaskState.SKIPPED_TODAY if t.task_ref in expired_overlap else t.state,
            overlap_task_ref=None if t.overlap_task_ref in satisfied else t.overlap_task_ref,
            departure_after_task_refs=tuple(ref for ref in t.departure_after_task_refs if ref not in satisfied),
            predecessor_task_refs=tuple(ref for ref in t.predecessor_task_refs if ref not in satisfied))
            for t in original.tasks if t.task_ref not in satisfied)
        # Reuse the original pre-route windows, including their buffers and
        # attention limits. Recompute only future routes; never invent capacity.
        tail_windows = []
        for window in base_state.windows:
            start = max(cursor, window.starts_at)
            if start >= window.ends_at:
                continue
            consumed_prefix = int((start - window.starts_at).total_seconds() // 60)
            tail_windows.append(replace(window, starts_at=start,
                capacity_minutes=max(0, window.capacity_minutes - consumed_prefix)))
        tail = replace(base_state, now=cursor, tasks=tasks, windows=tuple(tail_windows),
                       active_window_ref=derive_active_window_ref(tuple(tail_windows), cursor))
        tail_options = {
            name: {ref: value for ref, value in (options.get(name) or {}).items() if ref in residual}
            for name in ('protected_duration_by_task_ref', 'earliest_start_by_task_ref',
                         'latest_end_by_task_ref', 'preferred_chunk_by_task_ref',
                         'preferred_start_by_task_ref')
        }
        tail_options['effective_duration_by_task_ref'] = residual
        tail_options['task_order'] = tuple(ref for ref in (options.get('task_order') or residual) if ref in residual)
        tail_options['include_low_attention'] = options.get('include_low_attention', False)
        from src.p4_execution_movement import _split_windows_at_earliest_starts
        tail = _split_windows_at_earliest_starts(tail, tail_options['earliest_start_by_task_ref'])
        allocator_options = {key: value for key, value in tail_options.items() if key != 'preferred_start_by_task_ref'}
        provisional = allocate_tasks_across_windows(tail, **allocator_options)
        if not provisional.allocations:
            if retry_cursor is None:
                break
            continue
        addition = _apply_execution_sequence_movements(tail, provisional, tail_context, map_data, **tail_options)
        if not addition.applied and _has_sequence_location_fact(
                execution_timeline(tail, provisional, tail_context, map_data), tail_context):
            # No movement requests is legitimate for a same-place suffix.
            from src.p4_execution_movement import _transition_signature
            if addition.warnings or _transition_signature(
                    execution_timeline(tail, provisional, tail_context, map_data), tail_context, map_data):
                if retry_cursor is None:
                    break
                continue
        if not addition.allocation_plan.allocations:
            if retry_cursor is None:
                break
            continue
        merged = _merge(outcome, addition, cursor, round_index)
        if final_plan_overlap_errors(merged.state, merged.allocation_plan, merged.blocks):
            if retry_cursor is None:
                break
            continue
        from src.task_dependencies import dependency_errors
        if dependency_errors(merged.state, merged.allocation_plan, effective,
                             options.get('protected_duration_by_task_ref')):
            if retry_cursor is None:
                break
            continue
        outcome = merged
        retry_cursor = None
    return outcome


def _merge(prefix, suffix, cursor, round_index):
    """Join immutable allocation snapshots, retaining original progress facts."""
    from src.p4_execution_movement import ExecutionMovementOutcome
    old, extra = prefix.allocation_plan, suffix.allocation_plan
    windows = []
    for window in prefix.state.windows:
        if window.starts_at >= cursor:
            continue
        end = min(window.ends_at, cursor)
        windows.append(replace(window, ends_at=end, capacity_minutes=min(
            window.capacity_minutes, int((end - window.starts_at).total_seconds() // 60))))
    refs = {w.window_ref: 'continuation_{}_{}'.format(round_index, w.window_ref) for w in suffix.state.windows}
    windows.extend(replace(w, window_ref=refs[w.window_ref]) for w in suffix.state.windows)
    state = replace(prefix.state, windows=tuple(windows),
                    active_window_ref=derive_active_window_ref(tuple(windows), prefix.state.now))
    allocations = list(old.allocations)
    for item in extra.allocations:
        allocations.append(replace(item, allocation_ref=make_allocation_ref(len(allocations)),
                                   window_ref=refs[item.window_ref] if item.window_ref is not None else None))
    # Residual amounts in the suffix are already measured after the prefix.
    planned = {}
    for item in allocations:
        if item.occupies_attention:
            planned[item.window_ref] = planned.get(item.window_ref, 0) + item.planned_minutes
    unallocated = tuple(ref for ref in old.unallocated_task_refs
                        if ref not in extra.planned_minutes_by_task or ref in extra.unallocated_task_refs)
    plan = DayAllocationPlan(
        tuple(allocations), unallocated,
        tuple((w.window_ref, w.capacity_minutes - planned.get(w.window_ref, 0)) for w in windows),
        state.active_window_ref,
        next((a.allocation_ref for a in allocations if a.window_ref == state.active_window_ref and a.occupies_attention
              and (a.starts_at is None or a.starts_at <= state.now)), None),
        tuple(a for a in allocations if a.window_ref != state.active_window_ref),
        old.warnings + extra.warnings, sum(a.planned_minutes for a in allocations),
    )
    blocks = tuple(b for b in prefix.blocks if b.end_time <= cursor) + tuple(
        replace(b, window_ref=refs.get(b.window_ref, b.window_ref)) for b in suffix.blocks)
    return ExecutionMovementOutcome(state, plan, blocks, prefix.warnings + suffix.warnings, True)
