import json
from datetime import datetime

import pytest

from src.p1_models import SourceKind
from src.p2_models import DayPlanningState, TaskProgress, TaskState
from src.p2_agentic_parser import AgenticParseError
from src.p4_feedback_decision import (
    FeedbackDecision,
    TaskOrderingConstraint,
    apply_feedback_decision_to_state,
    apply_feedback_task_order,
    decide_feedback,
    decision_clarification_questions,
    merge_feedback_decisions,
    parse_feedback_decision,
    review_intent_compliance,
)


def _task(ref, title, state=TaskState.ACTIVE):
    return TaskProgress(ref, title, 20, 0, SourceKind.AI_EXTRACTED_FROM_USER_TEXT,
                        state, False, None)


def _state(tasks=None):
    now = datetime(2026, 9, 1, 14, 0)
    return DayPlanningState(
        now, now, now.replace(hour=22), (),
        tuple(tasks or (
            _task("day_task_001", "写作业"),
            _task("day_task_002", "背单词"),
            _task("day_task_003", "洗衣服"),
            _task("day_task_004", "吃饭"),
        )),
        (), None, (), (),
    )


def _payload(**updates):
    data = {
        "schema_version": "p4.feedback-decision.v1",
        "intent_type": "prioritize_now",
        "target_task_refs": ["day_task_003"],
        "preferred_next_task_ref": "day_task_003",
        "priority_changes": [],
        "ordering_constraints": [],
        "cancelled_task_refs": [],
        "postponed_task_refs": [],
        "completed_task_refs": [],
        "restored_task_refs": [],
        "location_correction": None,
        "explicit_user_preference": True,
        "confidence": 0.96,
        "clarification_needed": False,
        "clarification_question": None,
    }
    data.update(updates)
    return data


def _json(**updates):
    return json.dumps(_payload(**updates), ensure_ascii=False)


def _critic(decision="approve", repaired=None, reason=None):
    return json.dumps({
        "schema_version": "p4.feedback-decision-review.v1",
        "decision": decision,
        "reason": reason,
        "repaired_decision": repaired,
    }, ensure_ascii=False)


def test_interpreter_and_critic_bind_prioritize_now_to_stable_task_ref():
    calls = []
    def caller(system, user):
        calls.append((system, user))
        if "Feedback Interpreter" in system:
            return _json()
        return _critic()

    outcome = decide_feedback(_state(), None, "我现在就想洗衣服", "现在吃饭", caller)
    assert outcome.decision.preferred_next_task_ref == "day_task_003"
    assert outcome.decision.target_task_refs == ("day_task_003",)
    assert outcome.decision.explicit_user_preference is True
    assert outcome.interpreter_calls == 1 and outcome.critic_calls == 1
    assert len(calls) == 2
    assert "day_task_003 | 洗衣服" in calls[0][1]


def test_why_not_now_is_the_same_general_prioritize_semantics():
    responses = iter((_json(), _critic()))
    outcome = decide_feedback(
        _state(), None, "为什么不让我现在洗衣服", "现在吃饭", lambda *_: next(responses)
    )
    assert outcome.decision.intent_type == "prioritize_now"
    assert outcome.decision.preferred_next_task_ref == "day_task_003"


def test_meal_preference_and_relative_order_enter_deterministic_order():
    state = _state()
    meal = parse_feedback_decision(_json(
        target_task_refs=["day_task_004"],
        preferred_next_task_ref="day_task_004",
    ), state)
    assert apply_feedback_task_order(
        state,
        ("day_task_001", "day_task_002", "day_task_003", "day_task_004"),
        meal,
    )[0] == "day_task_004"

    ordered = parse_feedback_decision(_json(
        intent_type="reorder",
        target_task_refs=["day_task_001", "day_task_002"],
        preferred_next_task_ref=None,
        ordering_constraints=[{
            "before_task_ref": "day_task_001",
            "after_task_ref": "day_task_002",
        }],
    ), state)
    result = apply_feedback_task_order(
        state, ("day_task_002", "day_task_001", "day_task_003", "day_task_004"), ordered
    )
    assert result.index("day_task_001") < result.index("day_task_002")


@pytest.mark.parametrize(
    "intent,field,expected",
    (
        ("cancel", "cancelled_task_refs", TaskState.ABANDONED),
        ("complete", "completed_task_refs", TaskState.COMPLETED),
    ),
)
def test_cancel_and_complete_are_applied_to_canonical_task_lifecycle(intent, field, expected):
    state = _state()
    updates = {
        "intent_type": intent,
        "target_task_refs": ["day_task_003"],
        "preferred_next_task_ref": None,
        field: ["day_task_003"],
    }
    decision = parse_feedback_decision(_json(**updates), state)
    changed = apply_feedback_decision_to_state(state, decision)
    assert next(item for item in changed.tasks if item.task_ref == "day_task_003").state is expected


def test_postpone_moves_task_later_without_cancelling_it():
    state = _state()
    decision = parse_feedback_decision(_json(
        intent_type="postpone", target_task_refs=["day_task_003"],
        preferred_next_task_ref=None, postponed_task_refs=["day_task_003"],
    ), state)
    changed = apply_feedback_decision_to_state(state, decision)
    assert changed.tasks[2].state is TaskState.ACTIVE
    assert apply_feedback_task_order(state, tuple(t.task_ref for t in state.tasks), decision)[-1] == "day_task_003"


def test_completion_and_restore_are_distinct_semantics():
    completed_state = _state((_task("day_task_003", "洗衣服", TaskState.SKIPPED_TODAY),))
    restore = parse_feedback_decision(_json(
        intent_type="restore", target_task_refs=["day_task_003"],
        preferred_next_task_ref=None, restored_task_refs=["day_task_003"],
    ), completed_state)
    assert apply_feedback_decision_to_state(completed_state, restore).tasks[0].state is TaskState.ACTIVE


def test_location_correction_remains_separate_from_task_preference():
    decision = parse_feedback_decision(_json(
        intent_type="location_correction", target_task_refs=[],
        preferred_next_task_ref=None, location_correction="31斋",
        explicit_user_preference=False,
    ), _state())
    assert decision.location_correction == "31斋"
    assert decision.has_planning_constraints is False


def test_critic_can_repair_a_wrong_target_once():
    repaired = _payload(
        target_task_refs=["day_task_003"], preferred_next_task_ref="day_task_003"
    )
    responses = iter((
        _json(target_task_refs=["day_task_004"], preferred_next_task_ref="day_task_004"),
        _critic("repair", repaired, "用户明确说的是洗衣服"),
    ))
    outcome = decide_feedback(
        _state(), None, "我现在就想洗衣服", "现在吃饭", lambda *_: next(responses)
    )
    assert outcome.decision.preferred_next_task_ref == "day_task_003"
    assert outcome.critic_calls == 1


def test_unknown_ref_and_cyclic_order_are_rejected():
    with pytest.raises(AgenticParseError):
        parse_feedback_decision(_json(
            target_task_refs=["day_task_999"],
            preferred_next_task_ref="day_task_999",
        ), _state())


def test_same_title_tasks_are_never_matched_by_title():
    state = _state((
        _task("day_task_010", "写作业"),
        _task("day_task_011", "写作业"),
    ))
    decision = parse_feedback_decision(_json(
        target_task_refs=["day_task_011"],
        preferred_next_task_ref="day_task_011",
    ), state)
    assert apply_feedback_task_order(
        state, ("day_task_010", "day_task_011"), decision
    ) == ("day_task_011", "day_task_010")


def test_completed_or_skipped_preferred_task_is_not_revived_by_ordering():
    state = _state((
        _task("day_task_001", "写作业"),
        _task("day_task_003", "洗衣服", TaskState.SKIPPED_TODAY),
    ))
    with pytest.raises(AgenticParseError):
        parse_feedback_decision(_json(
            target_task_refs=["day_task_003"], preferred_next_task_ref="day_task_003"
        ), state)
    assert state.tasks[1].state is TaskState.SKIPPED_TODAY


def test_infeasible_preferred_task_can_be_preserved_as_intent_without_mutating_duration():
    state = _state()
    decision = parse_feedback_decision(_json(), state)
    changed = apply_feedback_decision_to_state(state, decision)
    laundry = next(item for item in changed.tasks if item.task_ref == "day_task_003")
    assert laundry.total_minutes == 20
    assert laundry.completed_minutes == 0
    class EmptyPlan:
        planned_minutes_by_task = {}
    questions = decision_clarification_questions(decision, state, EmptyPlan())
    assert len(questions) == 1
    assert "洗衣服" in questions[0]
    with pytest.raises(AgenticParseError):
        parse_feedback_decision(_json(
            intent_type="reorder",
            preferred_next_task_ref=None,
            ordering_constraints=[
                {"before_task_ref": "day_task_001", "after_task_ref": "day_task_002"},
                {"before_task_ref": "day_task_002", "after_task_ref": "day_task_001"},
            ],
        ), _state())


def test_malformed_qwen_output_has_bounded_safe_fallback():
    calls = []
    def caller(*args):
        calls.append(args)
        return "not json"
    outcome = decide_feedback(_state(), None, "我现在就想洗衣服", "", caller)
    assert outcome.decision == FeedbackDecision()
    assert outcome.used_fallback is True
    assert len(calls) == 2


def test_new_explicit_preference_overrides_previous_but_location_only_preserves_it():
    state = _state()
    old = parse_feedback_decision(_json(), state)
    location = parse_feedback_decision(_json(
        intent_type="location_correction", target_task_refs=[],
        preferred_next_task_ref=None, location_correction="31斋",
        explicit_user_preference=False,
    ), state)
    assert merge_feedback_decisions(old, location, state).preferred_next_task_ref == "day_task_003"
    newer = parse_feedback_decision(_json(
        target_task_refs=["day_task_004"], preferred_next_task_ref="day_task_004"
    ), state)
    assert merge_feedback_decisions(old, newer, state).preferred_next_task_ref == "day_task_004"


def test_compliance_reviewer_is_one_call_when_valid():
    def caller(system, user):
        assert "Final Intent Compliance Reviewer" in system
        assert "day_task_003" in user
        return json.dumps({
            "schema_version": "p4.intent-compliance-review.v1",
            "decision": "approve", "reason": None, "repaired_decision": None,
        })
    review, calls = review_intent_compliance(
        _state(), "我现在就想洗衣服", parse_feedback_decision(_json(), _state()),
        "day_task_003 first", "17:00 class", caller,
    )
    assert review.decision == "approve"
    assert calls == 1
