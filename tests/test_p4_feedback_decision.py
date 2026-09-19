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


def test_feedback_and_critic_share_ownership_of_partial_and_new_tasks():
    from src.p4_feedback_decision import (build_feedback_interpreter_prompt,
        build_feedback_critic_prompt,COMPATIBILITY_FEEDBACK_SEMANTICS)
    from src.p2_session import _feedback_requires_unified_interpreter
    state=_state()
    normal,_=build_feedback_interpreter_prompt(state,None,'新增任务','')
    critic,_=build_feedback_critic_prompt(state,'已经完成一半',FeedbackDecision(intent_type='mixed'))
    assert COMPATIBILITY_FEEDBACK_SEMANTICS in normal and COMPATIBILITY_FEEDBACK_SEMANTICS in critic
    assert '本schema没有已做/剩余分钟或新任务详情字段' in critic
    assert '旧台账不是否定新事实的依据' in critic
    assert _feedback_requires_unified_interpreter(FeedbackDecision(intent_type='mixed'))
    assert not _feedback_requires_unified_interpreter(FeedbackDecision(intent_type='complete'))


def test_feedback_reviewers_receive_completed_and_remaining_as_separate_facts():
    from src.p4_feedback_decision import (
        build_feedback_interpreter_prompt, build_feedback_critic_prompt,
        build_intent_compliance_prompt,
    )
    task = TaskProgress('day_task_001', '课程作业', 80, 25,
                        SourceKind.USER_STATED, TaskState.ACTIVE, True, 10)
    state = _state([task])
    decision = FeedbackDecision(intent_type='mixed', target_task_refs=(task.task_ref,))
    prompts = [
        build_feedback_interpreter_prompt(state, None, '已做25分钟，还需20分钟', ''),
        build_feedback_critic_prompt(state, '已做25分钟，还需20分钟', decision),
        build_intent_compliance_prompt(state, '已做25分钟，还需20分钟', decision, '', ''),
    ]
    for _, user in prompts:
        assert 'total_minutes=80' in user
        assert 'completed_minutes=25' in user
        assert 'remaining_minutes=55' in user
        assert 'duration=55' not in user
    assert state.tasks[0] is task and task.completed_minutes == 25


def test_critic_has_same_complete_decision_contract_and_keeps_ref_guards():
    from src.p4_feedback_decision import (
        FEEDBACK_DECISION_CONTRACT, build_feedback_interpreter_prompt,
        build_feedback_critic_prompt, parse_feedback_decision_review,
    )
    state = _state()
    decision = FeedbackDecision(intent_type='mixed', target_task_refs=('day_task_001',))
    normal, _ = build_feedback_interpreter_prompt(state, None, '部分完成', '')
    critic, _ = build_feedback_critic_prompt(state, '部分完成', decision)
    assert normal.count(FEEDBACK_DECISION_CONTRACT) == 1
    assert critic.count(FEEDBACK_DECISION_CONTRACT) == 1
    assert '原始用户反馈会完整转交下一阶段' in critic
    with pytest.raises(AgenticParseError):
        parse_feedback_decision_review(json.dumps({
            'schema_version': 'p4.feedback-decision-review.v1',
            'decision': 'repair', 'reason': None,
            'repaired_decision': _payload(target_task_refs=['foreign_task']),
        }), state)


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
