"""Bridge Day Intake task semantics into the formal execution-context layer.

This module deliberately creates facts only.  It does not alter allocation,
movement, session persistence, or any user-facing presentation.
"""

from typing import Optional

from src.p2_day_intake import DayIntakeProposal, IntakeApplied
from src.p2_models import DayPlanningState
from src.p3_location_resolver import resolve_location
from src.p3_map_schema import CampusMapData, TransportMode, parse_transport_mode
from src.p4_meal_rules import choose_canteen_nearest_to_destination, choose_nearest_canteen
from src.p4_execution_context import (
    CurrentLocationContext,
    CurrentLocationSource,
    ExecutableTaskBinding,
    ExecutionConfirmation,
    ExecutionConfirmationKind,
    ExecutionLocation,
    ExecutionLocationSource,
    ExecutionPlanContext,
    TaskExecutionProfile,
)


MEAL_DEFAULT_MINUTES = 40
DURATION_SOURCE_USER_EXPLICIT = "user_explicit"
DURATION_SOURCE_SEMANTIC_ESTIMATE = "semantic_estimate"
DURATION_SOURCE_MEAL_DEFAULT = "meal_default"


def enrich_intake_execution_context(
    proposal: DayIntakeProposal,
    applied: IntakeApplied,
    map_data: CampusMapData,
    context: Optional[ExecutionPlanContext] = None,
    current_location_text: Optional[str] = None,
    caller=None,
    repair_caller=None,
    personal_settings=None,
) -> ExecutionPlanContext:
    """Return execution bindings for the tasks created by one Day Intake run.

    ``apply_day_intake`` deterministically emits one ``new_task_ref`` per
    proposed task, in proposal order.  This is the formal join boundary; no
    title matching is used.  An unresolved explicit location leaves the task
    binding intact but does not manufacture an execution location.
    """
    if not isinstance(proposal, DayIntakeProposal):
        raise TypeError("proposal 必须是 DayIntakeProposal")
    if not isinstance(applied, IntakeApplied):
        raise TypeError("applied 必须是 IntakeApplied")
    if not isinstance(map_data, CampusMapData) or not map_data.campus_id:
        raise TypeError("map_data 必须是带 campus_id 的 CampusMapData")
    if context is None:
        context = ExecutionPlanContext()
    if not isinstance(context, ExecutionPlanContext):
        raise TypeError("context 必须是 ExecutionPlanContext")
    if len(proposal.tasks) != len(applied.new_task_refs):
        raise ValueError("Day Intake task 与 task_ref 映射不完整")

    enriched = context
    for task, task_ref in zip(proposal.tasks, applied.new_task_refs):
        if task.activity_kind is None and task.location_text is None:
            continue

        execution_location = None
        if task.location_text is not None:
            location_text = _personal_location_text(
                task.location_text, map_data.campus_id, personal_settings
            )
            resolution = resolve_location(
                map_data,
                location_text,
                caller=caller,
                repair_caller=repair_caller,
            )
            if resolution.usable:
                if resolution.campus_id != map_data.campus_id:
                    raise ValueError("地点解析结果与 selected campus 不一致")
                execution_location = ExecutionLocation.from_resolution(
                    resolution,
                    ExecutionLocationSource.EXPLICIT_TASK_LOCATION,
                )

        effective_duration, duration_source = _effective_duration_for(task)
        after_ref = _after_commitment_ref(task, applied)
        before_ref = _before_commitment_ref(task, applied)
        temporal_source = _meal_temporal_source(task, after_ref, before_ref, duration_source)
        meal_window = _default_meal_window(applied.state.now, duration_source, task.meal_period)
        enriched = enriched.upsert(
            ExecutableTaskBinding(
                task_ref=task_ref,
                execution_location=execution_location,
                activity_kind=task.activity_kind,
                effective_duration_minutes=effective_duration,
                duration_source=duration_source,
                execution_profile=_execution_profile_for(task_ref, task),
                not_before_commitment_ref=after_ref,
                meal_period=task.meal_period if task.activity_kind == "meal" else None,
                meal_before_commitment_ref=before_ref,
                meal_explicit_time=task.meal_time if task.activity_kind == "meal" else None,
                meal_temporal_source=temporal_source,
                meal_window_start_minutes=meal_window[0] if meal_window else None,
                meal_window_end_minutes=meal_window[1] if meal_window else None,
            )
        )
    enriched = enriched.with_current_location(
        _current_location_context(
            map_data, current_location_text, caller, repair_caller, personal_settings
        )
    ).with_transport_mode(proposal.transport_mode)
    return _enrich_unspecified_meal_locations(
        proposal, applied, map_data, enriched, caller=caller, repair_caller=repair_caller
    )


def reconcile_execution_context(context: ExecutionPlanContext, state: DayPlanningState) -> ExecutionPlanContext:
    """Remove bindings only for task refs no longer present in formal state.

    Task lifecycle changes do not remove a binding: ``DayPlanningState.tasks``
    remains the sole existence authority.  No task titles are considered.
    """
    if not isinstance(context, ExecutionPlanContext):
        raise TypeError("context 必须是 ExecutionPlanContext")
    if not isinstance(state, DayPlanningState):
        raise TypeError("state 必须是 DayPlanningState")
    active_refs = {task.task_ref for task in state.tasks}
    commitment_refs = {item.commitment_ref for item in state.commitments}
    return ExecutionPlanContext(
        tuple(binding for binding in context.bindings if binding.task_ref in active_refs),
        context.current_location,
        tuple(
            item for item in context.confirmations
            if item.task_ref in active_refs
            or item.kind is ExecutionConfirmationKind.CURRENT_LOCATION_ASSUMED
        ),
        context.transport_mode,
        tuple(
            item for item in context.concurrency_authorizations
            if item.task_ref in active_refs and item.commitment_ref in commitment_refs
        ),
    )


def effective_duration_overrides(context: ExecutionPlanContext, state: DayPlanningState):
    """Extract the small allocator-facing mapping; no context object leaks downstream."""
    if not isinstance(context, ExecutionPlanContext):
        raise TypeError("context 必须是 ExecutionPlanContext")
    if not isinstance(state, DayPlanningState):
        raise TypeError("state 必须是 DayPlanningState")
    state_refs = {task.task_ref for task in state.tasks}
    return {
        binding.task_ref: binding.effective_duration_minutes
        for binding in context.bindings
        if binding.task_ref in state_refs and binding.effective_duration_minutes is not None
    }


def preferred_chunk_overrides(context: ExecutionPlanContext, state: DayPlanningState):
    """Small allocator adapter for task_ref-bound chunk preferences."""
    state_refs = {task.task_ref for task in state.tasks}
    return {
        binding.task_ref: binding.execution_profile.preferred_chunk_minutes
        for binding in context.bindings
        if (
            binding.task_ref in state_refs
            and binding.execution_profile is not None
            and binding.execution_profile.splittable
        )
    }


def protected_meal_duration_overrides(context: ExecutionPlanContext, state: DayPlanningState):
    """Reserve capacity for explicit meal semantics after earlier flexible work.

    This is a P4 execution-policy input, not a new TaskProgress truth: only
    meals with an already formal effective duration participate.
    """
    state_refs = {task.task_ref for task in state.tasks}
    return {
        binding.task_ref: binding.effective_duration_minutes
        for binding in context.bindings
        if (
            binding.task_ref in state_refs
            and binding.activity_kind == "meal"
            and binding.effective_duration_minutes is not None
            and binding.duration_source == DURATION_SOURCE_MEAL_DEFAULT
        )
    }


def _enrich_unspecified_meal_locations(proposal, applied, map_data, context, caller, repair_caller):
    """Bind each unspecified meal from its ordered, campus-local route context."""
    mode = parse_transport_mode(proposal.transport_mode or TransportMode.WALK)
    task_refs = tuple(applied.new_task_refs)
    confirmations = []
    enriched = context
    for index, (task, task_ref) in enumerate(zip(proposal.tasks, task_refs)):
        binding = enriched.binding_for(task_ref)
        if task.activity_kind != "meal" or binding is None or binding.execution_location is not None:
            continue
        previous = _previous_execution_anchor(enriched, task_refs, index)
        next_anchor = _next_execution_anchor(
            enriched, task_refs, index, applied.state, map_data, caller, repair_caller
        )
        choice = None
        if previous is not None:
            choice = choose_nearest_canteen(map_data, previous.node_id, mode)
        elif next_anchor is not None:
            choice = choose_canteen_nearest_to_destination(map_data, next_anchor.node_id, mode)
        if choice is None:
            confirmations.append(
                ExecutionConfirmation(task_ref, ExecutionConfirmationKind.MEAL_LOCATION_CONTEXT_REQUIRED)
            )
            continue
        enriched = enriched.upsert(
            ExecutableTaskBinding(
                task_ref=task_ref,
                execution_location=_auto_meal_location(map_data, choice.node_id, choice.name),
                activity_kind=binding.activity_kind,
                effective_duration_minutes=binding.effective_duration_minutes,
                duration_source=binding.duration_source,
                execution_profile=binding.execution_profile,
                not_before_commitment_ref=binding.not_before_commitment_ref,
                meal_period=binding.meal_period,
                meal_before_commitment_ref=binding.meal_before_commitment_ref,
                meal_explicit_time=binding.meal_explicit_time,
                meal_temporal_source=binding.meal_temporal_source,
                meal_window_start_minutes=binding.meal_window_start_minutes,
                meal_window_end_minutes=binding.meal_window_end_minutes,
            )
        )
    return enriched.with_confirmations(confirmations)


def _previous_execution_anchor(context, task_refs, index):
    for task_ref in reversed(task_refs[:index]):
        binding = context.binding_for(task_ref)
        if binding is not None and binding.execution_location is not None:
            return binding.execution_location
    if context.current_location.source is CurrentLocationSource.USER:
        return context.current_location.location
    return None


def _next_execution_anchor(context, task_refs, index, state, map_data, caller, repair_caller):
    for task_ref in task_refs[index + 1:]:
        binding = context.binding_for(task_ref)
        if binding is not None and binding.execution_location is not None:
            return binding.execution_location
    for commitment in sorted(state.commitments, key=lambda item: item.starts_at):
        if not commitment.location_text:
            continue
        resolution = resolve_location(map_data, commitment.location_text, caller=caller, repair_caller=repair_caller)
        if resolution.usable and resolution.campus_id == map_data.campus_id:
            return ExecutionLocation.from_resolution(resolution, ExecutionLocationSource.EXPLICIT_TASK_LOCATION)
    return None


def _auto_meal_location(map_data, node_id, display_name):
    node = next((item for item in map_data.nodes if item.id == node_id), None)
    if node is None or node.node_kind != "poi":
        raise ValueError("食堂候选不是当前地图正式 POI")
    return ExecutionLocation(
        campus_id=map_data.campus_id,
        node_id=node.id,
        display_name=display_name,
        source=ExecutionLocationSource.AUTO_SELECTED_MEAL,
    )


def _effective_duration_for(task):
    """Return the execution duration only for meals; allocator remains unchanged."""
    if task.activity_kind != "meal":
        return None, None
    if task.total_minutes is not None and task.duration_source in (
        None,
        DURATION_SOURCE_USER_EXPLICIT,
        DURATION_SOURCE_SEMANTIC_ESTIMATE,
    ):
        # Legacy intake emitted total_minutes only for user-stated duration.
        return task.total_minutes, task.duration_source or DURATION_SOURCE_USER_EXPLICIT
    return MEAL_DEFAULT_MINUTES, DURATION_SOURCE_MEAL_DEFAULT


def _execution_profile_for(task_ref, task):
    """Create a profile only when Qwen supplied actual execution semantics."""
    if task.is_splittable is None and task.requires_single_session is None:
        return None
    splittable = bool(task.is_splittable) and not bool(task.requires_single_session)
    minimum = task.minimum_slice_minutes or 15
    preferred = task.preferred_chunk_minutes or max(minimum, 30)
    return TaskExecutionProfile(
        task_ref=task_ref,
        splittable=splittable,
        minimum_chunk_minutes=minimum,
        preferred_chunk_minutes=preferred,
        requires_single_session=bool(task.requires_single_session),
        source=task.execution_profile_source or "qwen_semantic",
    )


def _after_commitment_ref(task, applied):
    """Resolve proposal-local dependency index at the formal apply boundary."""
    index = getattr(task, "after_commitment_index", None)
    if index is None:
        return None
    if index > len(applied.new_commitment_refs):
        # Invalid/malformed model linkage must not become a guessed dependency.
        return None
    return applied.new_commitment_refs[index - 1]


def _before_commitment_ref(task, applied):
    """Resolve a meal's proposal-local before relation without title matching."""
    index = getattr(task, "meal_before_commitment_index", None)
    if index is None or index > len(applied.new_commitment_refs):
        return None
    return applied.new_commitment_refs[index - 1]


def _meal_temporal_source(task, after_ref, before_ref, duration_source):
    if task.activity_kind != "meal":
        return None
    if task.meal_time is not None:
        return "explicit_user"
    if after_ref is not None or before_ref is not None:
        return "narrative_order"
    if _default_meal_window_marker(duration_source, task.meal_period):
        return "inferred_meal_window"
    return "recovery"


def _default_meal_window_marker(duration_source, meal_period):
    return duration_source == DURATION_SOURCE_MEAL_DEFAULT and meal_period in (None, "lunch", "dinner", "unspecified")


def earliest_start_overrides(context: ExecutionPlanContext, state: DayPlanningState):
    """Translate only hard task start facts into deterministic timestamps.

    Default lunch/dinner windows are deliberately excluded: they are soft
    preferences and may yield to a class route or an explicit single-session
    task.  Explicit meal times and after-commitment relations remain hard.
    """
    commitments = {item.commitment_ref: item for item in state.commitments}
    result = {}
    for binding in context.bindings:
        ref = binding.not_before_commitment_ref
        commitment = commitments.get(ref) if ref else None
        if commitment is not None and commitment.ends_at is not None:
            result[binding.task_ref] = commitment.ends_at
        if binding.meal_explicit_time is not None:
            explicit = state.now.replace(
                hour=int(binding.meal_explicit_time.split(":")[0]),
                minute=int(binding.meal_explicit_time.split(":")[1]),
                second=0, microsecond=0,
            )
            result[binding.task_ref] = max(result.get(binding.task_ref, explicit), explicit)
    return result


def preferred_start_overrides(context: ExecutionPlanContext, state: DayPlanningState):
    """Return soft meal-window starts for bounded candidate comparison."""
    result = {}
    for binding in context.bindings:
        if (
            binding.activity_kind != "meal"
            or binding.meal_window_start_minutes is None
            or binding.meal_explicit_time is not None
        ):
            continue
        result[binding.task_ref] = state.now.replace(
            hour=binding.meal_window_start_minutes // 60,
            minute=binding.meal_window_start_minutes % 60,
            second=0, microsecond=0,
        )
    return result


def latest_end_overrides(context: ExecutionPlanContext, state: DayPlanningState):
    # A preferred meal window is not an expiration boundary.  Hard latest-end
    # facts are derived from narrative-before relations and real outbound
    # movement in the execution planner, not from the default clock window.
    result = {}
    for binding in context.bindings:
        value = getattr(binding, "latest_end_time", None)
        absolute = getattr(binding, "deadline_at", None)
        if absolute is not None:
            result[binding.task_ref] = absolute
        if value is None:
            continue
        hour, minute = (int(part) for part in value.split(":"))
        clock_boundary = state.now.replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        result[binding.task_ref] = min(absolute, clock_boundary) if absolute else clock_boundary
    return result


def _default_meal_window(now, duration_source, meal_period=None):
    if duration_source != DURATION_SOURCE_MEAL_DEFAULT:
        return None
    minute = now.hour * 60 + now.minute
    if meal_period == "lunch":
        return (11 * 60, 13 * 60) if minute < 13 * 60 else None
    if meal_period == "dinner":
        return (17 * 60, 19 * 60) if minute < 19 * 60 else None
    if minute < 13 * 60:
        return (11 * 60, 13 * 60)
    if minute < 17 * 60:
        # Missed lunch is recovery, not a synthetic window ending at dinner:
        # leave it unconstrained so normal earliest-fit schedules a prompt
        # catch-up meal rather than back-planning it toward 17:00.
        return None
    if minute < 19 * 60:
        return (17 * 60, 19 * 60)
    # Likewise, a missed dinner remains an actionable meal, not an expired
    # task or a fake window stretched to midnight.
    return None


def _current_location_context(
    map_data, current_location_text, caller, repair_caller, personal_settings=None
):
    if not isinstance(current_location_text, str) or not current_location_text.strip():
        return CurrentLocationContext()
    resolution = resolve_location(
        map_data,
        _personal_location_text(
            current_location_text, map_data.campus_id, personal_settings
        ),
        caller=caller,
        repair_caller=repair_caller,
    )
    if not resolution.usable:
        return CurrentLocationContext()
    if resolution.campus_id != map_data.campus_id:
        raise ValueError("当前位置解析结果与 selected campus 不一致")
    return CurrentLocationContext(
        ExecutionLocation.from_resolution(
            resolution,
            ExecutionLocationSource.USER_CURRENT_LOCATION,
        ),
        CurrentLocationSource.USER,
    )


def _personal_location_text(text, campus_id, settings):
    from src.personal_settings import personal_location_alias
    alias = personal_location_alias(text, campus_id, settings)
    return alias.display_name if alias is not None else text
