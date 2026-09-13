"""Apply ref-bound Qwen concurrency semantics to deterministic P4 facts."""

from src.p4_concurrency import (
    ConcurrentExecutionAuthorization,
    ConcurrentExecutionSource,
)
from src.p4_execution_context import ExecutionPlanContext
from src.p4_feedback_decision import ConcurrencyDecision, FeedbackDecision


def apply_concurrency_feedback(context, decision, state):
    """Return a new context; authorizations are never inferred by Python."""
    if not isinstance(context, ExecutionPlanContext):
        raise TypeError("context invalid")
    if not isinstance(decision, FeedbackDecision):
        raise TypeError("decision invalid")
    tasks = {item.task_ref for item in state.tasks}
    commitments = {item.commitment_ref: item for item in state.commitments}
    values = list(context.concurrency_authorizations)
    for change in decision.concurrency_changes:
        if not isinstance(change, ConcurrencyDecision):
            raise TypeError("concurrency decision invalid")
        if change.task_ref not in tasks or change.commitment_ref not in commitments:
            raise ValueError("concurrency decision references stale facts")
        pair = (change.task_ref, change.commitment_ref)
        values = [
            item for item in values
            if (item.task_ref, item.commitment_ref) != pair
        ]
        if change.action == "revoke":
            continue
        commitment = commitments[change.commitment_ref]
        offset = _placement_offset(change, commitment)
        values.append(ConcurrentExecutionAuthorization(
            task_ref=change.task_ref,
            commitment_ref=change.commitment_ref,
            requested_minutes=change.requested_minutes,
            source=ConcurrentExecutionSource.EXPLICIT_USER_REQUEST,
            start_offset_minutes=offset,
        ))
    return context.with_concurrency_authorizations(tuple(values))


def _placement_offset(change, commitment):
    if change.placement == "offset":
        return change.start_offset_minutes
    if change.placement in ("earliest", "start"):
        return 0
    if change.placement == "end":
        if commitment.starts_at is None or commitment.ends_at is None:
            raise ValueError("end placement needs a resolved commitment interval")
        duration = int((commitment.ends_at - commitment.starts_at).total_seconds() // 60)
        if change.requested_minutes > duration:
            raise ValueError("concurrency duration exceeds commitment")
        return duration - change.requested_minutes
    raise ValueError("unsupported concurrency placement")
