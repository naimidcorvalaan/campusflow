from src.p2_main import build_demo_state
from src.p4_concurrency import plan_explicit_concurrency
from src.p4_execution_context import ExecutionPlanContext
from src.p4_feedback_decision import ConcurrencyDecision, FeedbackDecision
from src.p5_concurrency_semantics import apply_concurrency_feedback


def _decision(action="authorize", placement="earliest", minutes=20):
    return FeedbackDecision(
        intent_type="concurrency",
        target_task_refs=("day_task_001",),
        concurrency_changes=(ConcurrencyDecision(
            action=action,
            task_ref="day_task_001",
            commitment_ref="day_commitment_001",
            requested_minutes=minutes if action == "authorize" else None,
            placement=placement,
            source="explicit_user_request",
        ),),
        explicit_user_preference=True,
        confidence=0.95,
    )


def test_qwen_bound_concurrency_authorization_becomes_pair_specific_fact():
    state = build_demo_state()
    context = apply_concurrency_feedback(ExecutionPlanContext(), _decision(), state)
    authorization = context.concurrency_authorizations[0]
    assert (authorization.task_ref, authorization.commitment_ref) == (
        "day_task_001", "day_commitment_001"
    )
    result = plan_explicit_concurrency(state, context)
    assert len(result.allocations) == 1
    assert result.allocations[0].planned_minutes == 20


def test_end_placement_is_deterministic_and_class_prep_is_not_used():
    state = build_demo_state()
    context = apply_concurrency_feedback(
        ExecutionPlanContext(), _decision(placement="end", minutes=20), state
    )
    authorization = context.concurrency_authorizations[0]
    assert authorization.start_offset_minutes == 70
    allocation = plan_explicit_concurrency(state, context).allocations[0]
    assert allocation.starts_at.strftime("%H:%M") == "11:10"
    assert allocation.ends_at.strftime("%H:%M") == "11:30"


def test_revoke_removes_authorization_without_cancelling_task():
    state = build_demo_state()
    context = apply_concurrency_feedback(ExecutionPlanContext(), _decision(), state)
    context = apply_concurrency_feedback(context, _decision(action="revoke", minutes=None), state)
    assert context.concurrency_authorizations == ()
    assert state.tasks[0].state.value == "active"


def test_concurrency_feedback_rejects_stale_refs_and_non_user_source():
    state = build_demo_state()
    bad = FeedbackDecision(
        intent_type="concurrency",
        concurrency_changes=(ConcurrencyDecision(
            "authorize", "missing", "day_commitment_001", 10,
        ),),
    )
    try:
        apply_concurrency_feedback(ExecutionPlanContext(), bad, state)
    except ValueError as exc:
        assert "stale" in str(exc)
    else:
        raise AssertionError("stale task ref was accepted")
