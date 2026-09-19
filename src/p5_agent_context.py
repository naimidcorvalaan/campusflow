"""Read-only decision view over CampusFlow's canonical planning facts.

``AgentDecisionContext`` is intentionally not a source of truth.  It contains
only immutable snapshots suitable for structured model prompts; mutations
continue to happen in DayPlanningState / ExecutionPlanContext and are always
revalidated by deterministic planning.
"""

import json
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Optional, Tuple

from src.p2_models import DayPlanningState, TaskState
from src.p4_execution_context import ExecutionPlanContext

AGENT_CONTEXT_SCHEMA_VERSION = "p5.agent-decision-context.v1"


@dataclass(frozen=True)
class AgentLocationFact:
    campus_id: str
    node_id: str
    display_name: str
    source: str


@dataclass(frozen=True)
class AgentTaskFact:
    task_ref: str
    title: str
    state: str
    total_minutes: Optional[int]
    completed_minutes: int
    remaining_minutes: Optional[int]
    effective_duration_minutes: Optional[int]
    duration_source: Optional[str]
    splittable: Optional[bool]
    minimum_chunk_minutes: Optional[int]
    preferred_chunk_minutes: Optional[int]
    requires_single_session: bool
    execution_location: Optional[AgentLocationFact]
    activity_kind: Optional[str]
    earliest_start: Optional[str]
    latest_end: Optional[str]
    after_commitment_ref: Optional[str]
    before_commitment_ref: Optional[str]
    explicitly_preferred: bool
    predecessor_task_refs: Tuple[str, ...] = ()
    departure_after_task_refs: Tuple[str, ...] = ()
    overlap_task_ref: Optional[str] = None
    attention_mode: str = "active"
    launch_task_ref: Optional[str] = None
    background_reason: Optional[str] = None
    user_reported_running: bool = False


@dataclass(frozen=True)
class AgentMealFact:
    """Temporal semantics for one existing meal task, never a second meal."""

    task_ref: str
    period: str
    preferred_window_start: Optional[str]
    preferred_window_end: Optional[str]
    before_commitment_ref: Optional[str]
    after_commitment_ref: Optional[str]
    explicit_time: Optional[str]
    temporal_source: Optional[str]
    preferred_duration_minutes: Optional[int]
    duration_source: Optional[str]


@dataclass(frozen=True)
class AgentCommitmentFact:
    commitment_ref: str
    title: str
    starts_at: Optional[str]
    ends_at: Optional[str]
    location_text: Optional[str]
    status: str
    commitment_kind: Optional[str]
    class_arrival_deadline: Optional[str]
    classroom_arrival_time: Optional[str]
    location: Optional[AgentLocationFact] = None


@dataclass(frozen=True)
class AgentMovementFact:
    origin_activity_ref: Optional[str]
    destination_activity_ref: Optional[str]
    origin_node_id: Optional[str]
    destination_node_id: Optional[str]
    starts_at: Optional[str]
    ends_at: Optional[str]
    minutes: Optional[int]
    mode: Optional[str]
    origin_name: Optional[str]
    destination_name: Optional[str]
    preparation_starts_at: Optional[str] = None


@dataclass(frozen=True)
class AgentConcurrencyFact:
    task_ref: str
    commitment_ref: str
    requested_minutes: int
    start_offset_minutes: Optional[int]
    source: str


@dataclass(frozen=True)
class AgentPlanFact:
    task_ref: str
    allocation_ref: str
    window_ref: Optional[str]
    planned_minutes: int
    remaining_after: int
    starts_at: Optional[str] = None
    occupies_attention: bool = True


@dataclass(frozen=True)
class AgentDecisionContext:
    current_time: str
    selected_campus_id: str
    current_location: Optional[AgentLocationFact]
    current_location_source: str
    active_tasks: Tuple[AgentTaskFact, ...]
    fixed_commitments: Tuple[AgentCommitmentFact, ...]
    meals: Tuple[AgentMealFact, ...]
    movements: Tuple[AgentMovementFact, ...]
    concurrency_authorizations: Tuple[AgentConcurrencyFact, ...]
    unresolved_confirmations: Tuple[str, ...]
    latest_user_text: Optional[str]
    latest_feedback_text: Optional[str]
    preferred_next_task_ref: Optional[str]
    ordering_constraints: Tuple[Tuple[str, str], ...]
    day_preferences: Tuple[Tuple[str, str], ...]
    selected_plan: Tuple[AgentPlanFact, ...]
    available_windows: Tuple[Tuple[str, str, str, int], ...]
    selected_timeline: Tuple[str, ...] = ()
    personal_defaults: Tuple[Tuple[str, str], ...] = ()
    personal_settings_revision: int = 0

    def __post_init__(self):
        task_refs = [item.task_ref for item in self.active_tasks]
        commitment_refs = [item.commitment_ref for item in self.fixed_commitments]
        if len(task_refs) != len(set(task_refs)):
            raise ValueError("AgentDecisionContext task refs must be unique")
        if len(commitment_refs) != len(set(commitment_refs)):
            raise ValueError("AgentDecisionContext commitment refs must be unique")
        known_tasks = set(task_refs)
        known_commitments = set(commitment_refs)
        if self.preferred_next_task_ref is not None and self.preferred_next_task_ref not in known_tasks:
            raise ValueError("preferred next task ref is stale")
        if any(left not in known_tasks or right not in known_tasks for left, right in self.ordering_constraints):
            raise ValueError("ordering constraint contains stale task ref")
        for item in self.concurrency_authorizations:
            if item.task_ref not in known_tasks or item.commitment_ref not in known_commitments:
                raise ValueError("concurrency authorization contains stale ref")
        if any(item.task_ref not in known_tasks for item in self.selected_plan):
            raise ValueError("selected plan contains stale task ref")
        for meal in self.meals:
            if meal.task_ref not in known_tasks:
                raise ValueError("meal fact contains stale task ref")
            if meal.before_commitment_ref is not None and meal.before_commitment_ref not in known_commitments:
                raise ValueError("meal before relation contains stale commitment ref")
            if meal.after_commitment_ref is not None and meal.after_commitment_ref not in known_commitments:
                raise ValueError("meal after relation contains stale commitment ref")
            if meal.before_commitment_ref is not None and meal.before_commitment_ref == meal.after_commitment_ref:
                raise ValueError("meal cannot be before and after the same commitment")
        if self.current_location_source == "unknown" and self.current_location is not None:
            raise ValueError("unknown current location cannot have a location fact")
        if self.current_location_source != "unknown" and self.current_location is None:
            raise ValueError("known current location source requires a location fact")
        if self.current_location is not None:
            if self.current_location.campus_id != self.selected_campus_id:
                raise ValueError("current location campus mismatch")
        for item in self.active_tasks:
            if item.execution_location is not None and item.execution_location.campus_id != self.selected_campus_id:
                raise ValueError("task location campus mismatch")
        for item in self.fixed_commitments:
            if item.location is not None and item.location.campus_id != self.selected_campus_id:
                raise ValueError("commitment location campus mismatch")
        if isinstance(self.personal_settings_revision, bool) or self.personal_settings_revision < 0:
            raise ValueError("personal settings revision invalid")

    def to_payload(self):
        payload = asdict(self)
        payload["schema_version"] = AGENT_CONTEXT_SCHEMA_VERSION
        return payload

    def to_json(self):
        return json.dumps(self.to_payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_agent_decision_context(
    state,
    execution_context,
    selected_campus_id,
    allocation_plan=None,
    movement_blocks=(),
    feedback_decision=None,
    latest_user_text=None,
    latest_feedback_text=None,
    day_preferences=None,
    personal_settings=None,
):
    if not isinstance(state, DayPlanningState):
        raise TypeError("state must be DayPlanningState")
    if not isinstance(execution_context, ExecutionPlanContext):
        raise TypeError("execution_context must be ExecutionPlanContext")
    if not isinstance(selected_campus_id, str) or not selected_campus_id.strip():
        raise ValueError("selected_campus_id must be non-empty")

    from src.p4_execution_enrichment import earliest_start_overrides, latest_end_overrides
    from src.p2_allocator import _remaining_for_allocation
    earliest = earliest_start_overrides(execution_context, state)
    latest = latest_end_overrides(execution_context, state)
    preferred = getattr(feedback_decision, "preferred_next_task_ref", None)
    tasks = []
    for task in state.tasks:
        if task.state is not TaskState.ACTIVE:
            continue
        binding = execution_context.binding_for(task.task_ref)
        profile = getattr(binding, "execution_profile", None)
        location = _location_fact(getattr(binding, "execution_location", None))
        tasks.append(AgentTaskFact(
            task_ref=task.task_ref,
            title=task.title,
            state=task.state.value,
            total_minutes=task.total_minutes,
            completed_minutes=task.completed_minutes,
            remaining_minutes=_remaining_for_allocation(task, getattr(binding, "effective_duration_minutes", None)),
            effective_duration_minutes=getattr(binding, "effective_duration_minutes", None),
            duration_source=getattr(binding, "duration_source", None) or _enum_value(task.total_source),
            splittable=(profile.splittable if profile is not None else task.is_splittable),
            minimum_chunk_minutes=(profile.minimum_chunk_minutes if profile is not None else task.minimum_slice_minutes),
            preferred_chunk_minutes=(profile.preferred_chunk_minutes if profile is not None else None),
            requires_single_session=bool(getattr(profile, "requires_single_session", False)),
            execution_location=location,
            activity_kind=getattr(binding, "activity_kind", None),
            earliest_start=_iso(earliest.get(task.task_ref)),
            latest_end=_iso(latest.get(task.task_ref)),
            after_commitment_ref=getattr(binding, "not_before_commitment_ref", None),
            before_commitment_ref=(getattr(binding, "before_commitment_ref", None)
                                   or getattr(binding, "meal_before_commitment_ref", None)),
            explicitly_preferred=(preferred == task.task_ref),
            predecessor_task_refs=task.predecessor_task_refs,
            departure_after_task_refs=task.departure_after_task_refs,
            overlap_task_ref=task.overlap_task_ref,
        attention_mode=task.attention_mode, launch_task_ref=task.launch_task_ref,
        background_reason=task.background_reason, user_reported_running=task.user_reported_running,
        ))
    movement_by_destination = {
        getattr(item, "destination_activity_ref", None): item
        for item in tuple(movement_blocks or ())
        if getattr(item, "destination_activity_ref", None)
    }
    commitments = []
    for item in state.commitments:
        status = "upcoming"
        if item.ends_at is not None and item.ends_at <= state.now:
            status = "past"
        elif item.starts_at is not None and item.starts_at <= state.now and (
            item.ends_at is None or state.now < item.ends_at
        ):
            status = "active"
        from src.p3_class_prep import class_arrival_deadline
        movement = movement_by_destination.get(item.commitment_ref)
        resolved_location = None
        if movement is not None and getattr(movement, "destination_node_id", None):
            resolved_location = AgentLocationFact(
                selected_campus_id,
                movement.destination_node_id,
                getattr(movement, "destination_name", None) or item.location_text or "固定安排地点",
                "fixed_commitment_location",
            )
        commitments.append(AgentCommitmentFact(
            commitment_ref=item.commitment_ref,
            title=item.title,
            starts_at=_iso(item.starts_at),
            ends_at=_iso(item.ends_at),
            location_text=item.location_text,
            status=status,
            commitment_kind=item.commitment_kind,
            class_arrival_deadline=_iso(class_arrival_deadline(item)),
            classroom_arrival_time=_iso(
                item.starts_at - timedelta(minutes=5)
                if item.commitment_kind == "class" and item.starts_at is not None
                else None
            ),
            location=resolved_location,
        ))
    current = execution_context.current_location
    active_task_refs = {item.task_ref for item in tasks}
    # A binding tuple is a keyed fact collection, not a sequence source:
    # ``upsert`` deliberately moves a replaced binding to the end (automatic
    # meal enrichment does this), so deriving order from that tuple invents
    # false constraints.  Only stable, ref-bound feedback ordering belongs in
    # this field. Initial narrative semantics continue to be enforced by the
    # existing intake/execution planner and reviewed against latest_user_text.
    ordering = tuple(
        (item.before_task_ref, item.after_task_ref)
        for item in getattr(feedback_decision, "ordering_constraints", ())
    )
    preferences = _preference_items(day_preferences, active_task_refs)
    plan_items = tuple(
        AgentPlanFact(
            item.task_ref, item.allocation_ref, item.window_ref,
            item.planned_minutes, item.remaining_after,
            item.starts_at.isoformat() if item.starts_at else None,
            item.occupies_attention,
        )
        for item in tuple(getattr(allocation_plan, "allocations", ()) or ())
        if item.task_ref in active_task_refs
    )
    selected_timeline = ()
    if allocation_plan is not None:
        try:
            from src.p3_route_planner import render_movement_lines
            selected_timeline = tuple(render_movement_lines(
                tuple(movement_blocks or ()), allocation_plan, state,
                execution_context=execution_context,
            ))
        except Exception:
            # The decision view remains available even for a legacy plan that
            # lacks enough presentation facts.  Raw object repr is never used.
            selected_timeline = ()
    return AgentDecisionContext(
        current_time=_iso(state.now),
        selected_campus_id=selected_campus_id,
        current_location=_location_fact(current.location),
        current_location_source=current.source.value,
        active_tasks=tuple(tasks),
        fixed_commitments=tuple(commitments),
        meals=tuple(
            AgentMealFact(
                task_ref=item.task_ref,
                period=getattr(execution_context.binding_for(item.task_ref), "meal_period", None) or "unspecified",
                preferred_window_start=_minute_of_day_text(
                    getattr(execution_context.binding_for(item.task_ref), "meal_window_start_minutes", None)
                ),
                preferred_window_end=_minute_of_day_text(
                    getattr(execution_context.binding_for(item.task_ref), "meal_window_end_minutes", None)
                ),
                before_commitment_ref=item.before_commitment_ref,
                after_commitment_ref=item.after_commitment_ref,
                explicit_time=getattr(execution_context.binding_for(item.task_ref), "meal_explicit_time", None),
                temporal_source=getattr(execution_context.binding_for(item.task_ref), "meal_temporal_source", None),
                preferred_duration_minutes=item.effective_duration_minutes,
                duration_source=item.duration_source,
            )
            for item in tasks if item.activity_kind == "meal"
        ),
        movements=tuple(_movement_fact(item) for item in movement_blocks or ()),
        concurrency_authorizations=tuple(
            AgentConcurrencyFact(
                item.task_ref, item.commitment_ref, item.requested_minutes,
                item.start_offset_minutes, _enum_value(item.source),
            )
            for item in execution_context.concurrency_authorizations
            if item.task_ref in active_task_refs
            and item.commitment_ref in {value.commitment_ref for value in state.commitments}
        ),
        unresolved_confirmations=(
            tuple(
                "{}:{}".format(item.kind.value, item.task_ref)
                for item in execution_context.confirmations
            )
            + (
                ("feedback:{}".format(feedback_decision.clarification_question),)
                if feedback_decision is not None
                and feedback_decision.clarification_needed
                and feedback_decision.clarification_question
                else ()
            )
        ),
        latest_user_text=_optional_text(latest_user_text),
        latest_feedback_text=_optional_text(latest_feedback_text),
        preferred_next_task_ref=preferred,
        ordering_constraints=ordering,
        day_preferences=preferences,
        selected_plan=plan_items,
        available_windows=tuple(
            (item.window_ref, _iso(item.starts_at), _iso(item.ends_at), item.capacity_minutes)
            for item in state.windows
        ),
        selected_timeline=selected_timeline,
        personal_defaults=(
            personal_settings.planning_context_items()
            if hasattr(personal_settings, "planning_context_items") else ()
        ),
        personal_settings_revision=(
            int(getattr(personal_settings, "revision", 0) or 0)
        ),
    )


def _movement_fact(block):
    # Packing and physical departure are distinct formal timeline events.
    # Narration and its guard must compare departure against the route start,
    # not the beginning of the reserved preparation interval.
    start = getattr(block, "window_start", None)
    end = getattr(block, "end_time", None)
    return AgentMovementFact(
        getattr(block, "origin_activity_ref", None),
        getattr(block, "destination_activity_ref", None),
        getattr(block, "origin_node_id", None),
        getattr(block, "destination_node_id", None),
        _iso(start), _iso(end), getattr(block, "estimated_minutes", None),
        _enum_value(getattr(block, "mode", None)),
        _optional_text(getattr(block, "origin_name", None)),
        _optional_text(getattr(block, "destination_name", None)),
        _iso(getattr(block, "transition_start", None)),
    )


def _location_fact(location):
    if location is None:
        return None
    return AgentLocationFact(
        location.campus_id, location.node_id, location.display_name,
        _enum_value(location.source),
    )


def _preference_items(profile, active_task_refs=()):
    if profile is None:
        return ()
    payload = profile.to_payload() if hasattr(profile, "to_payload") else {}
    values = [
        (str(key), str(value)) for key, value in payload.items()
        if value is not None and key not in (
            "provenance", "last_updated_turn", "task_specific_preferences"
        )
    ]
    active = set(active_task_refs or ())
    for item in getattr(profile, "task_specific_preferences", ()):
        # Day preferences survive ordinary replans, but a task-scoped entry
        # must not keep feeding Qwen after that task has completed, been
        # cancelled, or otherwise left the active canonical task set.
        if item.task_ref in active:
            values.append(("task_specific:{}".format(item.task_ref), item.preference))
    return tuple(sorted(values))


def _enum_value(value):
    return getattr(value, "value", value)


def _iso(value):
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else None


def _optional_text(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _minute_of_day_text(value):
    if value is None:
        return None
    return "{:02d}:{:02d}".format(value // 60, value % 60)
