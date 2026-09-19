"""Explicit, user-authorized task execution during a fixed commitment.

This is intentionally a narrow execution fact, not an attention model.  A
normal task never gains access to a commitment interval merely because it is
low attention or splittable.  The only entrance is an authorization already
bound to stable task/commitment refs by an upstream semantic pass.
"""

from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import Dict, Optional, Tuple

from src.p2_models import DayPlanningState, TaskState


class ConcurrentExecutionSource(str, Enum):
    """Only explicit user authorization is acceptable in the core."""

    EXPLICIT_USER_REQUEST = "explicit_user_request"


@dataclass(frozen=True)
class ConcurrentExecutionAuthorization:
    """A task/commitment pair the user explicitly permits to run together."""

    task_ref: str
    commitment_ref: str
    requested_minutes: int
    source: ConcurrentExecutionSource
    # Offset from commitment start.  None means the deterministic earliest
    # feasible slot inside the commitment.
    start_offset_minutes: Optional[int] = None

    def __post_init__(self):
        _require_ref("task_ref", self.task_ref)
        _require_ref("commitment_ref", self.commitment_ref)
        _require_minutes("requested_minutes", self.requested_minutes, minimum=1)
        if not isinstance(self.source, ConcurrentExecutionSource):
            raise ValueError("concurrency source must be explicit user authorization")
        if self.start_offset_minutes is not None:
            _require_minutes("start_offset_minutes", self.start_offset_minutes, minimum=0)


@dataclass(frozen=True)
class ConcurrentAllocation:
    """One validated task slice that overlaps one fixed commitment."""

    task_ref: str
    commitment_ref: str
    starts_at: object
    ends_at: object
    planned_minutes: int
    source: ConcurrentExecutionSource

    def __post_init__(self):
        _require_ref("task_ref", self.task_ref)
        _require_ref("commitment_ref", self.commitment_ref)
        _require_minutes("planned_minutes", self.planned_minutes, minimum=1)
        if not isinstance(self.source, ConcurrentExecutionSource):
            raise ValueError("concurrent allocation source must be explicit user authorization")
        if self.starts_at is None or self.ends_at is None or self.ends_at <= self.starts_at:
            raise ValueError("concurrent allocation requires a positive time interval")
        actual = int((self.ends_at - self.starts_at).total_seconds() // 60)
        if actual != self.planned_minutes:
            raise ValueError("concurrent allocation duration must match its interval")


@dataclass(frozen=True)
class ConcurrentPlanningResult:
    """Pure planner output; rejected entries never become a live-plan fact."""

    allocations: Tuple[ConcurrentAllocation, ...] = ()
    rejected_authorizations: Tuple[ConcurrentExecutionAuthorization, ...] = ()


def plan_explicit_concurrency(
    state, context, map_data=None, effective_duration_by_task_ref=None
):
    """Create only safe concurrent slices from explicit authorization facts.

    Commitment prep is deliberately outside each commitment's active interval,
    so this function can never use T-10/T-5 class-prep capacity.  A task with
    a resolved execution location is accepted only when it is physically
    compatible with the commitment location on the selected map.
    """
    if not isinstance(state, DayPlanningState):
        raise TypeError("state must be a DayPlanningState")
    authorizations = tuple(getattr(context, "concurrency_authorizations", ()) or ())
    tasks = {item.task_ref: item for item in state.tasks}
    commitments = {item.commitment_ref: item for item in state.commitments}
    effective = dict(effective_duration_by_task_ref or {})
    consumed: Dict[str, int] = {}
    occupied = []
    allocations = []
    rejected = []

    for authorization in authorizations:
        if not isinstance(authorization, ConcurrentExecutionAuthorization):
            raise TypeError("concurrency authorizations must be structured facts")
        task = tasks.get(authorization.task_ref)
        commitment = commitments.get(authorization.commitment_ref)
        if (
            authorization.source is not ConcurrentExecutionSource.EXPLICIT_USER_REQUEST
            or task is None
            or commitment is None
            or task.state is not TaskState.ACTIVE
            or task.attention_mode == 'background'
            or commitment.starts_at is None
            or commitment.ends_at is None
            or commitment.ends_at <= state.now
            or not _locations_are_compatible(context, task.task_ref, commitment, map_data)
        ):
            rejected.append(authorization)
            continue
        if task.task_ref in effective:
            # Effective duration is the round's executable amount.  It is
            # already a consumption-boundary fact and must not have durable
            # completed progress subtracted a second time.
            available_task = effective[task.task_ref]
        else:
            available_task = task.remaining_minutes
        if available_task is None:
            rejected.append(authorization)
            continue
        available_task = max(
            0, available_task - consumed.get(task.task_ref, 0)
        )
        planned = min(authorization.requested_minutes, available_task)
        if planned <= 0:
            rejected.append(authorization)
            continue
        start = max(
            commitment.starts_at + timedelta(minutes=authorization.start_offset_minutes or 0),
            state.now,
        )
        end = start + timedelta(minutes=planned)
        if end > commitment.ends_at or _overlaps_any(start, end, occupied):
            rejected.append(authorization)
            continue
        allocation = ConcurrentAllocation(
            task.task_ref,
            commitment.commitment_ref,
            start,
            end,
            planned,
            authorization.source,
        )
        allocations.append(allocation)
        consumed[task.task_ref] = consumed.get(task.task_ref, 0) + planned
        occupied.append((start, end))
    return ConcurrentPlanningResult(tuple(allocations), tuple(rejected))


def concurrent_minutes_by_task(allocations):
    """Return the deterministic amount already planned inside commitments."""
    result = {}
    for allocation in allocations or ():
        if not isinstance(allocation, ConcurrentAllocation):
            raise TypeError("concurrent allocations must be structured facts")
        result[allocation.task_ref] = result.get(allocation.task_ref, 0) + allocation.planned_minutes
    return result


def concurrency_validation_errors(
    state, context, allocations, map_data=None, ordinary_plan=None
):
    """Fail-closed invariant audit for the final live bundle."""
    if not isinstance(state, DayPlanningState):
        raise TypeError("state must be a DayPlanningState")
    authorizations = {
        (item.task_ref, item.commitment_ref): item
        for item in tuple(getattr(context, "concurrency_authorizations", ()) or ())
        if isinstance(item, ConcurrentExecutionAuthorization)
    }
    tasks = {item.task_ref: item for item in state.tasks}
    commitments = {item.commitment_ref: item for item in state.commitments}
    errors = []
    seen = []
    concurrent_minutes = {}
    for allocation in allocations or ():
        if not isinstance(allocation, ConcurrentAllocation):
            errors.append("concurrent allocation is malformed")
            continue
        authorization = authorizations.get((allocation.task_ref, allocation.commitment_ref))
        task = tasks.get(allocation.task_ref)
        commitment = commitments.get(allocation.commitment_ref)
        if authorization is None or authorization.source is not ConcurrentExecutionSource.EXPLICIT_USER_REQUEST:
            errors.append("concurrent allocation lacks explicit authorization")
            continue
        if allocation.source is not ConcurrentExecutionSource.EXPLICIT_USER_REQUEST:
            errors.append("concurrent allocation has non-user source")
        if task is None or task.state is not TaskState.ACTIVE:
            errors.append("concurrent allocation task is stale")
        if commitment is None or commitment.starts_at is None or commitment.ends_at is None:
            errors.append("concurrent allocation commitment is unresolved")
        elif allocation.starts_at < commitment.starts_at or allocation.ends_at > commitment.ends_at:
            errors.append("concurrent allocation exceeds commitment interval")
        if not _locations_are_compatible(context, allocation.task_ref, commitment, map_data):
            errors.append("concurrent allocation has incompatible location")
        if allocation.planned_minutes > authorization.requested_minutes:
            errors.append("concurrent allocation exceeds authorized duration")
        if _overlaps_any(allocation.starts_at, allocation.ends_at, seen):
            errors.append("concurrent allocations overlap each other")
        seen.append((allocation.starts_at, allocation.ends_at))
        concurrent_minutes[allocation.task_ref] = (
            concurrent_minutes.get(allocation.task_ref, 0) + allocation.planned_minutes
        )
    for task_ref, minutes in concurrent_minutes.items():
        task = tasks.get(task_ref)
        allowed = _available_task_minutes(task, context, task_ref)
        if allowed is not None and minutes > allowed:
            errors.append("concurrent allocation exceeds task remaining")
    if ordinary_plan is not None:
        ordinary = getattr(ordinary_plan, "planned_minutes_by_task", {})
        for task_ref, minutes in concurrent_minutes.items():
            task = tasks.get(task_ref)
            if task is None:
                continue
            allowed = _available_task_minutes(task, context, task_ref)
            if allowed is not None and ordinary.get(task_ref, 0) + minutes > allowed:
                errors.append("concurrent allocation double-counts task duration")
    return tuple(dict.fromkeys(errors))


def _locations_are_compatible(context, task_ref, commitment, map_data):
    binding = context.binding_for(task_ref) if hasattr(context, "binding_for") else None
    location = getattr(binding, "execution_location", None)
    if location is None:
        return True
    if map_data is None or getattr(location, "campus_id", None) != getattr(map_data, "campus_id", None):
        return False
    location_text = getattr(commitment, "location_text", None)
    node_id = map_data.resolve_node_id(location_text) if location_text else None
    if node_id is None:
        return False
    if location.node_id == node_id:
        return True
    nodes = {item.id: item for item in map_data.nodes}
    left = getattr(nodes.get(location.node_id), "physical_anchor_id", None)
    right = getattr(nodes.get(node_id), "physical_anchor_id", None)
    return bool(left and left == right)


def _available_task_minutes(task, context, task_ref):
    if task is None:
        return 0
    binding = context.binding_for(task_ref) if hasattr(context, "binding_for") else None
    effective = getattr(binding, "effective_duration_minutes", None)
    total = effective if effective is not None else task.total_minutes
    if total is None:
        return None
    return max(0, total - task.completed_minutes)


def _overlaps_any(start, end, intervals):
    return any(start < right and left < end for left, right in intervals)


def _require_ref(name, value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("{} must be a non-empty ref".format(name))


def _require_minutes(name, value, minimum):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError("{} must be an integer >= {}".format(name, minimum))
