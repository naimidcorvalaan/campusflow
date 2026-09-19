import json

from src.p2_live_main import LIVE_CAMPUS_SELECT_KEY, LIVE_SESSION_KEY
from src.p2_session import load_live_final_turn
from src.p2_session import MOVEMENT_DATA_KEY
from src.p4_execution_context import load_execution_context
from src.p5_agent_pipeline import AGENT_CALL_TRACE_KEY
from src.p5_what_if import WHAT_IF_PREVIEW_KEY, load_what_if_preview
from tests.test_p2_live_main import (
    INTAKE_P4C_LAUNDRY,
    PLAN_P4C_ALL,
    CountingCaller,
    _StubSt,
    _run_main,
    dt,
)


def _feedback_payload(intent_type, target=(), preferred=None, concurrency=()):
    return json.dumps({
        "schema_version": "p4.feedback-decision.v1",
        "intent_type": intent_type,
        "target_task_refs": list(target),
        "preferred_next_task_ref": preferred,
        "priority_changes": [], "ordering_constraints": [],
        "cancelled_task_refs": [], "postponed_task_refs": [],
        "completed_task_refs": [], "restored_task_refs": [],
        "concurrency_changes": list(concurrency),
        "location_correction": None,
        "explicit_user_preference": bool(target or concurrency),
        "confidence": 0.98,
        "clarification_needed": False, "clarification_question": None,
    }, ensure_ascii=False)


class _P5LiveCaller(CountingCaller):
    def __init__(self):
        super().__init__(intake=INTAKE_P4C_LAUNDRY, plan=PLAN_P4C_ALL)

    def __call__(self, system, user):
        if "Situation Analyst" in system:
            self.calls.append((system, user))
            needs_location = "current_location_assumed" in user
            return json.dumps({
                "schema_version": "p5.situation-analysis.v1",
                "primary_goal": "在固定课程前后稳妥推进任务",
                "important_constraints": ["固定课程和路线不可冲突"],
                "user_priority_signals": [], "task_priority_assessment": [],
                "timing_pressure": [], "fragmentation_concerns": [],
                "meal_context": "保留饭点", "movement_context": "按正式路线移动",
                "flexibility_opportunities": ["可拆任务利用碎片窗口"],
                "risk_flags": [], "recommended_strategy_focus": ["earliest_feasible"],
                "clarification_value": "high" if needs_location else "none",
            }, ensure_ascii=False)
        if "Plan Strategist" in system:
            self.calls.append((system, user))
            strategies = []
            for sid, fragmentation, slack in (
                ("balanced", "medium", "balanced"),
                ("fewer_switches", "low", "more"),
            ):
                strategies.append({
                    "strategy_id": sid,
                    "priority_order": [
                        "day_task_001", "day_task_002", "day_task_003",
                        "day_task_004", "day_task_005",
                    ],
                    "protected_preferences": ["fixed commitments"],
                    "fragmentation_level": fragmentation,
                    "slack_preference": slack,
                    "meal_preference": "preserve_window",
                    "concurrency_pairs": [],
                    "rationale_summary": "守住硬约束并减少无意义等待",
                    "constraints_to_preserve": ["locations", "durations"],
                })
            return json.dumps({
                "schema_version": "p5.plan-strategies.v1",
                "strategies": strategies,
            }, ensure_ascii=False)
        if "Plan Judge" in system:
            self.calls.append((system, user))
            return json.dumps({
                "schema_version": "p5.plan-judge.v1",
                "selected_candidate_id": "candidate_01",
                "concise_selection_reason": "当前确定性方案最稳妥",
                "concern": None, "confidence": "high",
            }, ensure_ascii=False)
        if "Intent Compliance Reviewer 2.0" in system:
            self.calls.append((system, user))
            return json.dumps({
                "schema_version": "p5.intent-compliance.v2",
                "decision": "approve", "reason": "符合最新明确意图",
                "repair_directives": [],
            }, ensure_ascii=False)
        if "Smart Clarification" in system:
            self.calls.append((system, user))
            needs_location = "current_location_assumed" in user
            return json.dumps({
                "schema_version": "p5.smart-clarification.v1",
                "should_ask": needs_location,
                "question": ("你现在是在图书馆吗？如果不是，告诉我你现在的位置，我会重新调整路上的时间。" if needs_location else ""),
                "reason_for_system": ("当前位置会影响校园路线" if needs_location else ""),
                "affected_decisions": (["movement"] if needs_location else []),
                "information_gain": "high" if needs_location else "none",
            }, ensure_ascii=False)
        if "Proactive Suggestion" in system:
            self.calls.append((system, user))
            return json.dumps({
                "schema_version": "p5.proactive-suggestion.v1",
                "should_show": False, "suggestion_type": "none", "text": "",
                "task_ref": None, "commitment_ref": None,
                "suggested_minutes": None,
            }, ensure_ascii=False)
        if "Grounded Narrator" in system:
            self.calls.append((system, user))
            return json.dumps({
                "schema_version": "p5.grounded-narrator.v1",
                "opening": "先从当前可行的任务开始。",
                "why_this_plan": "课程、饭点和路上的时间都已经留好。",
                "closing": "按这个节奏往下走就好。",
                "proactive_suggestion": None, "risk_note": None,
            }, ensure_ascii=False)
        if "Copy Fact Checker" in system:
            self.calls.append((system, user))
            return json.dumps({
                "schema_version": "p5.copy-fact-check.v1", "safe": True,
                "reason": None, "corrected_copy": None,
            }, ensure_ascii=False)
        if "Feedback Interpreter" in system:
            self.calls.append((system, user))
            latest = user.split("用户最新反馈：")[-1]
            if "如果" in latest:
                return _feedback_payload("what_if", ("day_task_005",))
            if "顺便" in latest:
                return _feedback_payload("concurrency", ("day_task_004",), concurrency=({
                    "action": "authorize", "task_ref": "day_task_004",
                    "commitment_ref": "day_commitment_001", "requested_minutes": 20,
                    "placement": "end", "start_offset_minutes": None,
                    "source": "explicit_user_request",
                },))
            if "认真听" in latest:
                return _feedback_payload("concurrency", ("day_task_004",), concurrency=({
                    "action": "revoke", "task_ref": "day_task_004",
                    "commitment_ref": "day_commitment_001", "requested_minutes": None,
                    "placement": "earliest", "start_offset_minutes": None,
                    "source": "explicit_user_request",
                },))
            return _feedback_payload("no_change")
        if "Feedback Decision Critic" in system:
            self.calls.append((system, user))
            return json.dumps({
                "schema_version": "p4.feedback-decision-review.v1",
                "decision": "approve", "reason": None, "repaired_decision": None,
            })
        if "What-if Interpreter" in system:
            self.calls.append((system, user))
            return json.dumps({
                "schema_version": "p5.what-if-intent.v1",
                "changes": [{
                    "kind": "prefer_task", "task_ref": "day_task_005",
                    "related_task_ref": None, "commitment_ref": None,
                    "location_text": None, "requested_minutes": None,
                }],
                "clarification_needed": False, "clarification_question": None,
            }, ensure_ascii=False)
        return super().__call__(system, user)


def _start(caller):
    stub = _StubSt().set_inputs(
        reference_hour=14, reference_minute=0,
        intake=(
            "我想在图书馆写实验报告，然后吃饭，17:00去9教上课，"
            "18:30下课后写一小时作业，还想背单词，还需要洗20分钟衣服"
        ),
        intake_submitted=True,
    )
    stub.session_state[LIVE_CAMPUS_SELECT_KEY] = "卫津路校区"
    _run_main(stub, caller, now=dt(14))
    assert not stub.errors
    bundle = load_live_final_turn(stub.session_state)
    assert bundle.agent_intelligence is not None
    assert bundle.agent_intelligence.trace.call_count == 8
    # Three event-semantic passes + one legacy duration-estimate pass + eight
    # bounded P5 jobs.  The superseded legacy plan review is not duplicated.
    assert caller.count == 12
    assert all(record.success for record in bundle.agent_intelligence.trace.records)
    return stub


def test_same_place_strategies_are_not_rejected_for_needing_no_route():
    class SamePlaceCaller(_P5LiveCaller):
        def __init__(self):
            super().__init__()
            payload = json.loads(self.intake)
            payload['commitments'] = []
            payload['current_location'] = '图书馆'
            payload['tasks'] = payload['tasks'][2:4]
            for task in payload['tasks']:
                task.update(location_text='图书馆', total_minutes=20,
                            after_commitment_index=None, before_commitment_index=None)
            self.intake = json.dumps(payload, ensure_ascii=False)
            plan = json.loads(self.plan)
            plan['task_order'] = ['day_task_001', 'day_task_002']
            plan['task_estimates'] = []
            self.plan = json.dumps(plan)

        def __call__(self, system, user):
            value = super().__call__(system, user)
            if 'Plan Strategist' in system:
                parsed = json.loads(value)
                for strategy in parsed['strategies']:
                    strategy['priority_order'] = ['day_task_002', 'day_task_001']
                return json.dumps(parsed)
            return value

    caller = SamePlaceCaller()
    stub = _StubSt().set_inputs(reference_hour=14, reference_minute=0,
        intake='我在图书馆，写作业20分钟、背单词20分钟，都在这里，顺序不限。', intake_submitted=True)
    stub.session_state[LIVE_CAMPUS_SELECT_KEY] = '卫津路校区'
    _run_main(stub, caller, now=dt(14))
    assert not stub.errors
    bundle = load_live_final_turn(stub.session_state)
    assert bundle.agent_intelligence is not None
    assert len(bundle.agent_intelligence.candidates) >= 2
    assert all(not c.movement_blocks for c in bundle.agent_intelligence.candidates)


def test_live_what_if_preview_does_not_commit_and_apply_is_atomic():
    caller = _P5LiveCaller()
    stub = _start(caller)
    baseline = load_live_final_turn(stub.session_state)
    baseline_state = baseline.state
    baseline_context = baseline.execution_context
    baseline_movement_data = dict(stub.session_state[MOVEMENT_DATA_KEY])
    baseline_movement_object = stub.session_state[MOVEMENT_DATA_KEY]
    calls_before_preview = caller.count

    stub.set_inputs(
        reference_hour=14, reference_minute=0,
        feedback="如果我先洗衣服呢？", feedback_submitted=True,
    )
    _run_main(stub, caller, now=dt(14))
    # The semantic intelligence stages are one lightweight hypothetical
    # classification, one dedicated interpreter, Situation/Strategy/
    # Judge/Reviewer, then the existing bounded clarification/suggestion/
    # narrator/copy-check flow over the isolated final candidate.  The last
    # reliable DayPlanIntent is reused; no planner model pass is added.
    semantic_systems = [
        system for system, _ in caller.calls[calls_before_preview:]
        if any(marker in system for marker in (
            "Feedback Interpreter", "What-if Interpreter",
            "Situation Analyst", "Plan Strategist", "Plan Judge",
            "Intent Compliance Reviewer 2.0",
        ))
    ]
    assert len(semantic_systems) == 6
    assert caller.count - calls_before_preview == 10
    assert not any(
        "Feedback Decision Critic" in system
        for system, _ in caller.calls[calls_before_preview:]
    )
    preview = load_what_if_preview(stub.session_state)
    assert preview is not None and preview.feasibility == "feasible"
    assert load_live_final_turn(stub.session_state) is baseline
    assert load_live_final_turn(stub.session_state).state is baseline_state
    assert load_execution_context(stub.session_state) is baseline_context
    assert stub.session_state[MOVEMENT_DATA_KEY] is baseline_movement_object
    assert stub.session_state[MOVEMENT_DATA_KEY] == baseline_movement_data
    trace = stub.session_state[AGENT_CALL_TRACE_KEY]
    assert trace.call_count == 9
    assert tuple(item.stage for item in trace.records if not item.fallback) == (
        "what_if_interpreter",
        "situation_analyst",
        "plan_strategist",
        "plan_judge",
        "intent_compliance_reviewer",
        "smart_clarification",
        "proactive_suggestion",
        "grounded_narrator",
        "copy_fact_checker",
    )

    calls = caller.count
    stub.set_inputs(reference_hour=14, reference_minute=0)
    _run_main(stub, caller, now=dt(14))
    assert caller.count == calls
    assert "如果这样调整" in "\n".join(stub.markdown_calls)

    session = stub.session_state[LIVE_SESSION_KEY]
    committed = session.apply_what_if_preview_atomic()
    assert committed.version == baseline.version + 1
    assert committed.result.allocation_plan.current_allocation.task_ref == "day_task_005"
    assert WHAT_IF_PREVIEW_KEY not in stub.session_state


def test_live_concurrency_authorize_and_revoke_use_explicit_pair_only():
    caller = _P5LiveCaller()
    stub = _start(caller)
    session = stub.session_state[LIVE_SESSION_KEY]
    assert session.live_final_turn().concurrent_allocations == ()

    calls_before_authorize = caller.count
    authorized = session.rebuild_feedback_atomic("这节课我想顺便背20分钟单词")
    assert caller.count - calls_before_authorize <= 10
    assert [(item.task_ref, item.commitment_ref, item.planned_minutes)
            for item in authorized.concurrent_allocations] == [
        ("day_task_004", "day_commitment_001", 20)
    ]
    assert authorized.concurrent_allocations[0].ends_at == dt(18, 30)

    revoked = session.rebuild_feedback_atomic("算了，这节课认真听吧")
    assert revoked.concurrent_allocations == ()
    assert revoked.execution_context.concurrency_authorizations == ()
    vocab = next(item for item in revoked.state.tasks if item.task_ref == "day_task_004")
    assert vocab.state.value == "active"


def test_deferred_feedback_question_survives_final_rebuild_and_clears_when_resolved():
    from dataclasses import replace
    from types import SimpleNamespace
    from src.p4_feedback_decision import parse_feedback_decision

    caller = _P5LiveCaller()
    stub = _start(caller)
    session = stub.session_state[LIVE_SESSION_KEY]
    baseline = session.live_final_turn()
    question = "新增任务需要多少分钟？"
    pending = [question]

    def interpret(text, state, movement):
        return SimpleNamespace(
            result=replace(baseline.result, updated_state=state,
                           questions=tuple(pending)),
            current_location_text=None,
        )

    session.unified_handler = interpret
    decision = parse_feedback_decision(_feedback_payload("mixed"), baseline.state)
    result = session.rebuild_feedback_atomic("新增任务的时长待确认", accepted_decision=decision)
    assert question in result.extra_questions
    assert question in result.result.questions
    assert result.state.tasks == baseline.state.tasks

    unrelated = session.rebuild_feedback_atomic("只重新排一下顺序", accepted_decision=decision, use_unified=False)
    assert question in unrelated.extra_questions

    pending.clear()
    resolved = session.rebuild_feedback_atomic("不增加任务了", accepted_decision=decision)
    assert question not in resolved.extra_questions
    assert question not in resolved.result.questions


def test_feedback_ui_clock_advances_future_windows_without_progress():
    from src.p4_feedback_decision import parse_feedback_decision
    caller=_P5LiveCaller();stub=_start(caller)
    session=stub.session_state[LIVE_SESSION_KEY];before=session.live_final_turn()
    decision=parse_feedback_decision(_feedback_payload('no_change'),before.state)
    after=session.rebuild_feedback_atomic('尚未开始，安排剩余时间',accepted_decision=decision,
                                           use_unified=False,reference_datetime=dt(14,20))
    assert after.state.now==dt(14,20)
    assert after.state.reference_datetime==before.state.reference_datetime
    assert after.state.tasks==before.state.tasks
    assert all(window.starts_at>=dt(14,20) for window in after.state.windows)
    assert after.version==before.version+1


def test_clock_rebase_remains_private_when_final_rebuild_fails(monkeypatch):
    import pytest
    import src.p2_session as session_module
    from src.p4_feedback_decision import parse_feedback_decision
    caller=_P5LiveCaller();stub=_start(caller)
    session=stub.session_state[LIVE_SESSION_KEY];before=session.live_final_turn()
    movement=stub.session_state[MOVEMENT_DATA_KEY]
    decision=parse_feedback_decision(_feedback_payload('no_change'),before.state)
    def fail(*args,**kwargs):raise RuntimeError('simulated candidate failure')
    monkeypatch.setattr(session_module,'build_live_final_turn',fail)
    with pytest.raises(RuntimeError,match='simulated candidate failure'):
        session.rebuild_feedback_atomic('现在更新',accepted_decision=decision,
                                        use_unified=False,reference_datetime=dt(14,20))
    assert session.live_final_turn() is before
    assert stub.session_state[MOVEMENT_DATA_KEY] is movement


def test_feedback_clock_cannot_silently_invent_a_new_day_horizon():
    import pytest
    caller=_P5LiveCaller();stub=_start(caller)
    session=stub.session_state[LIVE_SESSION_KEY];before=session.live_final_turn()
    with pytest.raises(ValueError,match='时间范围'):
        session.rebuild_feedback_atomic('继续',reference_datetime=before.state.day_end)
    assert session.live_final_turn() is before
