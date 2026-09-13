import json
from dataclasses import FrozenInstanceError, replace

import pytest

from src.p5_agent_context import (
    AgentCommitmentFact,
    AgentConcurrencyFact,
    AgentDecisionContext,
    AgentLocationFact,
    AgentMealFact,
    AgentMovementFact,
    AgentPlanFact,
    AgentTaskFact,
    _preference_items,
)
from src.p5_agent_pipeline import run_agent_intelligence
from src.p5_candidate import DeterministicPlanCandidate
from src.p5_copy_guard import GroundedNarrative, generate_grounded_narrative
from src.p5_day_preferences import (
    DayPreferenceProfile,
    TaskSpecificPreference,
    extract_day_preferences,
)
from src.p5_plan_judge import CandidateSummary, judge_candidates
from src.p5_plan_strategy import generate_plan_strategies
from src.p5_situation_analyst import analyze_situation
from src.p5_smart_clarification import choose_smart_clarification
from src.p5_what_if import (
    WhatIfChange,
    WhatIfIntent,
    apply_what_if_preview,
    build_what_if_preview,
    fingerprint_context,
    interpret_what_if,
)


def _context(confirmations=(), concurrency=(), preferred=None):
    library = AgentLocationFact("weijinlu", "library", "图书馆", "explicit_task_location")
    tasks = (
        AgentTaskFact(
            "task_homework", "写作业", "active", 60, 0, 60, None,
            "user_explicit", True, 15, 30, False, library, "generic",
            None, None, None, None, preferred == "task_homework",
        ),
        AgentTaskFact(
            "task_vocab", "背单词", "active", 30, 0, 30, None,
            "user_explicit", True, 10, 20, False, None, "generic",
            None, None, None, None, preferred == "task_vocab",
        ),
    )
    commitment = AgentCommitmentFact(
        "class_1", "上课", "2026-09-05T19:00:00", "2026-09-05T20:30:00",
        "9教", "upcoming", "class", "2026-09-05T18:50:00",
        "2026-09-05T18:55:00",
    )
    return AgentDecisionContext(
        current_time="2026-09-05T14:00:00",
        selected_campus_id="weijinlu",
        current_location=library,
        current_location_source="user",
        active_tasks=tasks,
        fixed_commitments=(commitment,),
        meals=(),
        movements=(AgentMovementFact(
            "task_homework", "class_1", "library", "building_9",
            "2026-09-05T18:40:00", "2026-09-05T18:50:00", 10,
            "walk", "图书馆", "第九教学楼",
        ),),
        concurrency_authorizations=tuple(concurrency),
        unresolved_confirmations=tuple(confirmations),
        latest_user_text="先写作业再上课",
        latest_feedback_text=None,
        preferred_next_task_ref=preferred,
        ordering_constraints=(("task_homework", "task_vocab"),),
        day_preferences=(("slack_preference", "more"),),
        selected_plan=(AgentPlanFact("task_homework", "a1", "w1", 30, 30),),
        available_windows=(("w1", "2026-09-05T14:00:00", "2026-09-05T18:40:00", 280),),
        selected_timeline=("14:00–14:30：写作业 30 分钟",),
    )


def _summary(candidate_id="candidate_01", strategy_id="current_deterministic",
             sequence=("task_homework", "task_vocab"), concurrency=()):
    return CandidateSummary(
        candidate_id=candidate_id,
        strategy_id=strategy_id,
        timeline=("14:00–14:30：写作业 30 分钟",),
        task_completion=(("task_homework", 30), ("task_vocab", 20)),
        remaining_work=(("task_homework", 30), ("task_vocab", 10)),
        movement_count=1,
        task_switches=1,
        slack_minutes=20,
        meal_timing=(),
        user_preference_satisfied=True,
        concurrency_pairs=tuple(concurrency),
        unresolved_questions=(),
        task_sequence=tuple(sequence),
    )


def _analysis_json():
    return json.dumps({
        "schema_version": "p5.situation-analysis.v1",
        "primary_goal": "先推进作业并守住课程",
        "important_constraints": ["课程不可冲突"],
        "user_priority_signals": ["先写作业"],
        "task_priority_assessment": [
            {"task_ref": "task_homework", "priority": "high", "reason": "用户明确先做"},
            {"task_ref": "task_vocab", "priority": "medium", "reason": "仍需完成"},
        ],
        "timing_pressure": [], "fragmentation_concerns": [],
        "meal_context": None, "movement_context": "需按时去9教",
        "flexibility_opportunities": ["作业可拆"], "risk_flags": [],
        "recommended_strategy_focus": ["earliest_feasible"],
        "clarification_value": "none",
    }, ensure_ascii=False)


def _strategies_json():
    return json.dumps({
        "schema_version": "p5.plan-strategies.v1",
        "strategies": [
            {
                "strategy_id": "early", "priority_order": ["task_homework", "task_vocab"],
                "protected_preferences": ["用户顺序"], "fragmentation_level": "medium",
                "slack_preference": "balanced", "meal_preference": "neutral",
                "concurrency_pairs": [], "rationale_summary": "尽早推进",
                "constraints_to_preserve": ["课程"],
            },
            {
                "strategy_id": "calm", "priority_order": ["task_homework", "task_vocab"],
                "protected_preferences": ["更多余量"], "fragmentation_level": "low",
                "slack_preference": "more", "meal_preference": "neutral",
                "concurrency_pairs": [], "rationale_summary": "减少切换",
                "constraints_to_preserve": ["课程"],
            },
        ],
    }, ensure_ascii=False)


class _Caller:
    def __init__(self, overrides=None):
        self.calls = []
        self.overrides = overrides or {}

    def __call__(self, system, user):
        self.calls.append((system, user))
        for marker, value in self.overrides.items():
            if marker in system:
                return value
        if "Situation Analyst" in system:
            return _analysis_json()
        if "Plan Strategist" in system:
            return _strategies_json()
        if "Plan Judge" in system:
            return json.dumps({
                "schema_version": "p5.plan-judge.v1",
                "selected_candidate_id": "candidate_02",
                "concise_selection_reason": "更符合明确顺序", "concern": None,
                "confidence": "high",
            }, ensure_ascii=False)
        if "Intent Compliance Reviewer 2.0" in system:
            return json.dumps({
                "schema_version": "p5.intent-compliance.v2", "decision": "approve",
                "reason": "符合最新意图", "repair_directives": [],
            }, ensure_ascii=False)
        if "Proactive Suggestion" in system:
            return json.dumps({
                "schema_version": "p5.proactive-suggestion.v1", "should_show": False,
                "suggestion_type": "none", "text": "", "task_ref": None,
                "commitment_ref": None, "suggested_minutes": None,
            }, ensure_ascii=False)
        if "Grounded Narrator" in system:
            return json.dumps({
                "schema_version": "p5.grounded-narrator.v1", "opening": "先从作业开始。",
                "why_this_plan": "课程前先推进一段，路上的时间已经留好。",
                "closing": "按这个节奏来就好。", "proactive_suggestion": None,
                "risk_note": None,
            }, ensure_ascii=False)
        if "Copy Fact Checker" in system:
            return json.dumps({
                "schema_version": "p5.copy-fact-check.v1", "safe": True,
                "reason": None, "corrected_copy": None,
            }, ensure_ascii=False)
        return "{}"


def test_agent_decision_context_is_immutable_stable_and_contains_no_repr():
    context = _context()
    assert context.to_json() == context.to_json()
    assert context.to_payload()["schema_version"] == "p5.agent-decision-context.v1"
    assert "object at 0x" not in context.to_json()
    assert context.fixed_commitments[0].classroom_arrival_time.endswith("18:55:00")
    with pytest.raises(FrozenInstanceError):
        context.current_time = "later"


def test_agent_context_keeps_one_structured_meal_fact_not_a_display_string():
    context = replace(_context(), meals=(AgentMealFact(
        "task_vocab", "dinner", "17:00", "19:00", "class_1", None,
        None, "narrative_order", 40, "meal_default",
    ),))
    payload = context.to_payload()["meals"][0]
    assert payload["task_ref"] == "task_vocab"
    assert payload["before_commitment_ref"] == "class_1"
    assert payload["preferred_duration_minutes"] == 40


def test_context_rejects_cross_campus_and_stale_concurrency_refs():
    bad_location = AgentLocationFact("beiyangyuan", "x", "郑东图书馆", "explicit")
    with pytest.raises(ValueError, match="campus mismatch"):
        replace(_context(), current_location=bad_location)
    with pytest.raises(ValueError, match="stale ref"):
        _context(concurrency=(AgentConcurrencyFact("missing", "class_1", 10, 0, "explicit_user_request"),))


def test_situation_strategy_and_judge_use_strict_refs_and_bounded_fallback():
    context = _context()
    caller = _Caller({"Situation Analyst": "not json"})
    analysis, trace = analyze_situation(context, caller)
    assert analysis.generated is False
    assert trace.call_count == 2 and trace.fallback_count == 1

    strategies, trace = generate_plan_strategies(context, analysis, _Caller(), trace=trace)
    assert len(strategies) == 2
    summaries = (_summary(), _summary("candidate_02", "early"))
    decision, _ = judge_candidates(context, analysis, summaries, _Caller())
    assert decision.selected_candidate_id == "candidate_02"


def test_plan_judge_hallucinated_candidate_id_repairs_then_falls_back():
    context = _context()
    analysis, _ = analyze_situation(context, _Caller())
    invalid = json.dumps({
        "schema_version": "p5.plan-judge.v1",
        "selected_candidate_id": "candidate_99",
        "concise_selection_reason": "选择不存在的方案",
        "concern": None,
        "confidence": "high",
    }, ensure_ascii=False)
    decision, trace = judge_candidates(
        context,
        analysis,
        (_summary(), _summary("candidate_02", "early")),
        _Caller({"Plan Judge": invalid}),
    )
    assert decision.selected_candidate_id in {"candidate_01", "candidate_02"}
    assert decision.generated is False
    assert trace.call_count == 2 and trace.fallback_count == 1


def test_full_agent_pipeline_selects_only_valid_deterministic_candidate():
    context = _context()
    caller = _Caller()

    def build(strategy, candidate_id):
        summary = _summary(candidate_id, strategy.strategy_id)
        return DeterministicPlanCandidate(
            candidate_id, strategy, object(), (), (), summary
        )

    result = run_agent_intelligence(
        context, caller, build,
        candidate_validator=lambda candidate: (
            ("invalid",) if candidate.candidate_id == "candidate_03" else ()
        ),
        latest_user_text="先写作业",
    )
    assert result.selected_candidate.candidate_id == "candidate_02"
    assert result.review.decision == "approve"
    assert result.trace.call_count == 7
    assert not result.suggestion.should_show
    assert all(candidate.candidate_id != "candidate_03" for candidate in result.candidates)


def test_agent_pipeline_has_a_global_bounded_repair_budget():
    class _Malformed:
        def __init__(self):
            self.calls = 0

        def __call__(self, system, user):
            self.calls += 1
            return "not-json"

    caller = _Malformed()

    def build(strategy, candidate_id):
        summary = _summary(candidate_id, strategy.strategy_id)
        return DeterministicPlanCandidate(
            candidate_id, strategy, object(), (), (), summary
        )

    result = run_agent_intelligence(_context(), caller, build)
    assert result.trace.call_count == 9
    assert caller.calls == 9
    assert result.trace.fallback_count >= 1


def test_reviewer_repair_candidate_is_deterministically_rechecked():
    reject = json.dumps({
        "schema_version": "p5.intent-compliance.v2",
        "decision": "reject",
        "reason": "候选没有把作业放在前面",
        "repair_directives": [{
            "kind": "prefer_task", "task_ref": "task_homework",
            "commitment_ref": None, "related_task_ref": None,
        }],
    }, ensure_ascii=False)
    caller = _Caller({"Intent Compliance Reviewer 2.0": reject})

    def build(strategy, candidate_id):
        sequence = (
            ("task_vocab", "task_homework")
            if candidate_id == "candidate_02"
            else ("task_homework", "task_vocab")
        )
        summary = _summary(candidate_id, strategy.strategy_id, sequence=sequence)
        return DeterministicPlanCandidate(
            candidate_id, strategy, object(), (), (), summary
        )

    result = run_agent_intelligence(
        _context(preferred="task_homework"), caller, build
    )
    assert result.controlled_repair_used
    assert result.selected_candidate.summary.task_sequence[0] == "task_homework"
    assert result.review.decision == "approve"
    assert result.review.generated is False


def test_smart_clarification_asks_at_most_one_grounded_question_on_fallback():
    context = _context(confirmations=("current_location_assumed:task_homework",))
    analysis, _ = analyze_situation(context, _Caller())
    value, _ = choose_smart_clarification(
        context, analysis, _Caller({"Smart Clarification": "malformed"})
    )
    assert value.should_ask
    assert value.question.count("？") <= 1


def test_smart_clarification_rejects_model_meal_pressure_when_final_fact_is_location_assumption():
    context = _context(confirmations=("current_location_assumed:task_homework",))
    context = replace(context, current_location_source="assumed")
    analysis, _ = analyze_situation(context, _Caller())
    misleading = json.dumps({
        "schema_version": "p5.smart-clarification.v1",
        "should_ask": True, "question": "晚饭时间较紧，要不要再留一点缓冲？",
        "reason_for_system": "模型猜测", "affected_decisions": ["meal"],
        "information_gain": "medium",
    }, ensure_ascii=False)
    value, _ = choose_smart_clarification(
        context, analysis, _Caller({"Smart Clarification": misleading})
    )
    assert value.should_ask
    assert "你现在是在图书馆吗" in value.question
    assert "晚饭时间较紧" not in value.question
    assert "图书馆" in value.question


def test_day_preference_is_explicit_day_scoped_and_ref_checked():
    payload = json.dumps({
        "schema_version": "p5.day-preference.v1", "should_update": True,
        "profile": {
            "slack_preference": "more", "meal_timing_preference": "earlier",
            "fragmentation_tolerance": "lower", "task_switching_tolerance": "fewer",
            "concurrency_suggestion_preference": "suppress",
            "movement_buffer_preference": "more",
            "task_specific_preferences": [
                {"task_ref": "task_homework", "preference": "尽量一次多写一点"}
            ],
        },
    }, ensure_ascii=False)
    result, _ = extract_day_preferences(
        _context(), "今天别排太满", _Caller({"Day Preference Extractor": payload}),
        existing=DayPreferenceProfile(), turn_version=3,
    )
    assert result.should_update
    assert result.profile.slack_preference == "more"
    assert result.profile.last_updated_turn == 3


def test_task_specific_day_preference_drops_stale_task_refs_from_agent_view():
    profile = DayPreferenceProfile(task_specific_preferences=(
        # task_vocab is no longer active in the hypothetical canonical state.
        # It must not survive in a Qwen decision snapshot.
        TaskSpecificPreference("task_vocab", "优先完整完成"),
    ))
    values = _preference_items(profile, {"task_homework"})
    assert not any("task_vocab" in key for key, _ in values)


def test_copy_guard_rejects_wrong_movement_task_association_even_if_checker_approves():
    narrator = json.dumps({
        "schema_version": "p5.grounded-narrator.v1", "opening": "先写作业。",
        "why_this_plan": "到点去第九教学楼背单词。", "closing": "慢慢来。",
        "proactive_suggestion": None, "risk_note": None,
    }, ensure_ascii=False)
    checker = json.dumps({
        "schema_version": "p5.copy-fact-check.v1", "safe": True,
        "reason": None, "corrected_copy": None,
    }, ensure_ascii=False)
    copy, _ = generate_grounded_narrative(
        _context(), _summary(), "先写作业", _Caller({
            "Grounded Narrator": narrator, "Copy Fact Checker": checker,
        })
    )
    assert copy.generated is False
    assert "去第九教学楼背单词" not in " ".join(
        item for item in (copy.opening, copy.why_this_plan, copy.closing) if item
    )


def test_initial_summary_without_final_fact_is_replaced_not_treated_as_preference_proof():
    narrator = json.dumps({
        "schema_version": "p5.grounded-narrator.v1",
        "opening": "已经为你预留了充足的缓冲与余量。",
        "why_this_plan": "写作业和背单词连续安排。",
        "closing": "按节奏来就好。",
        "proactive_suggestion": None,
        "risk_note": None,
    }, ensure_ascii=False)
    checker = json.dumps({
        "schema_version": "p5.copy-fact-check.v1",
        "safe": True,
        "reason": None,
        "corrected_copy": None,
    }, ensure_ascii=False)
    copy, _ = generate_grounded_narrative(
        _context(), _summary(), "今天别排太满",
        _Caller({"Grounded Narrator": narrator, "Copy Fact Checker": checker}),
    )
    assert "充足的缓冲与余量" not in copy.opening
    assert "写作业" in copy.opening


def test_copy_guard_rejects_a_forward_relation_that_reverses_final_timeline():
    context = replace(
        _context(),
        selected_timeline=(
            "14:00–16:30：写作业 150 分钟",
            "17:00–17:40：吃晚饭 40 分钟",
        ),
    )
    narrator = json.dumps({
        "schema_version": "p5.grounded-narrator.v1",
        "opening": "先从写作业开始。",
        "why_this_plan": "晚饭17:00–17:40，随后14:00–16:30写作业。",
        "closing": "按时间线往下走就好。",
        "proactive_suggestion": None,
        "risk_note": None,
    }, ensure_ascii=False)
    checker = json.dumps({
        "schema_version": "p5.copy-fact-check.v1", "safe": True,
        "reason": None, "corrected_copy": None,
    }, ensure_ascii=False)
    copy, _ = generate_grounded_narrative(
        context, _summary(), "今天按顺序安排",
        _Caller({"Grounded Narrator": narrator, "Copy Fact Checker": checker}),
    )
    visible = " ".join(
        item for item in (copy.opening, copy.why_this_plan, copy.closing) if item
    )
    assert "随后14:00" not in visible
    assert copy.generated is False


def test_only_feasibility_checked_suggestion_reaches_copy_fact_checker():
    suggestion = json.dumps({
        "schema_version": "p5.proactive-suggestion.v1", "should_show": True,
        "suggestion_type": "postpone", "text": "如果你愿意，剩余作业可以晚些继续。",
        "task_ref": "task_homework", "commitment_ref": None,
        "suggested_minutes": None,
    }, ensure_ascii=False)
    narrator = json.dumps({
        "schema_version": "p5.grounded-narrator.v1", "opening": "先推进作业。",
        "why_this_plan": "固定安排已经留好。", "closing": "按节奏来就好。",
        "proactive_suggestion": "去不存在的地点学习。", "risk_note": None,
    }, ensure_ascii=False)
    checker = json.dumps({
        "schema_version": "p5.copy-fact-check.v1", "safe": True,
        "reason": None, "corrected_copy": None,
    })
    caller = _Caller({
        "Proactive Suggestion": suggestion,
        "Grounded Narrator": narrator,
        "Copy Fact Checker": checker,
    })

    def build(strategy, candidate_id):
        summary = _summary(candidate_id, strategy.strategy_id)
        return DeterministicPlanCandidate(
            candidate_id, strategy, object(), (), (), summary
        )

    result = run_agent_intelligence(_context(), caller, build)
    assert result.suggestion.should_show
    assert result.narrative.proactive_suggestion == "如果你愿意，剩余作业可以晚些继续。"
    copy_check_call = next(
        user for system, user in caller.calls if "Copy Fact Checker" in system
    )
    assert "去不存在的地点学习" not in copy_check_call
    assert "如果你愿意，剩余作业可以晚些继续。" in copy_check_call


def test_existing_final_slack_does_not_turn_into_a_generic_buffer_suggestion():
    suggestion = json.dumps({
        "schema_version": "p5.proactive-suggestion.v1", "should_show": True,
        "suggestion_type": "slack", "text": "如果你愿意，可以再留一点缓冲。",
        "task_ref": None, "commitment_ref": None, "suggested_minutes": None,
    }, ensure_ascii=False)
    analysis, _ = analyze_situation(_context(), _Caller())
    from src.p5_proactive_suggestion import propose_suggestion
    value, _ = propose_suggestion(
        _context(), analysis, _summary(),
        _Caller({"Proactive Suggestion": suggestion}),
    )
    assert not value.should_show


def test_copy_checker_correction_cannot_replace_checked_suggestion():
    narrator = json.dumps({
        "schema_version": "p5.grounded-narrator.v1", "opening": "先推进作业。",
        "why_this_plan": "固定安排已经留好。", "closing": "按节奏来就好。",
        "proactive_suggestion": None, "risk_note": None,
    }, ensure_ascii=False)
    checker = json.dumps({
        "schema_version": "p5.copy-fact-check.v1", "safe": False,
        "reason": "需要调整措辞",
        "corrected_copy": {
            "opening": "先推进作业。", "why_this_plan": "固定安排已经留好。",
            "closing": "按节奏来就好。",
            "proactive_suggestion": "去不存在的地点学习。", "risk_note": None,
        },
    }, ensure_ascii=False)
    approved = "如果你愿意，剩余作业可以晚些继续。"
    copy, _ = generate_grounded_narrative(
        _context(), _summary(), "先写作业",
        _Caller({"Grounded Narrator": narrator, "Copy Fact Checker": checker}),
        approved_suggestion=approved,
    )
    assert copy.proactive_suggestion == approved


def test_what_if_is_ref_bound_preview_and_requires_explicit_apply():
    payload = json.dumps({
        "schema_version": "p5.what-if-intent.v1",
        "changes": [{
            "kind": "prefer_task", "task_ref": "task_vocab",
            "related_task_ref": None, "commitment_ref": None,
            "location_text": None, "requested_minutes": None,
        }],
        "clarification_needed": False, "clarification_question": None,
    }, ensure_ascii=False)
    context = _context()
    intent, trace = interpret_what_if(
        context, "如果先背单词呢？", _Caller({"What-if Interpreter": payload})
    )
    assert intent.changes[0].task_ref == "task_vocab"
    assert trace.call_count == 1
    candidate = type("Candidate", (), {"summary": _summary()})()
    preview = build_what_if_preview(context, intent, candidate)
    calls = []
    result = apply_what_if_preview(
        preview, context, lambda value: calls.append(value) or "committed"
    )
    assert result == "committed" and calls == [intent]
    with pytest.raises(ValueError, match="已过期"):
        apply_what_if_preview(
            preview, replace(context, latest_feedback_text="new fact"), lambda value: value
        )


def test_what_if_unknown_ref_fails_safely_without_mutating_context():
    payload = json.dumps({
        "schema_version": "p5.what-if-intent.v1",
        "changes": [{
            "kind": "cancel_task", "task_ref": "missing",
            "related_task_ref": None, "commitment_ref": None,
            "location_text": None, "requested_minutes": None,
        }],
        "clarification_needed": False, "clarification_question": None,
    })
    context = _context()
    intent, trace = interpret_what_if(
        context, "如果不做那项呢？", _Caller({"What-if Interpreter": payload})
    )
    assert intent is None
    assert trace.call_count == 2 and trace.fallback_count == 1
    assert fingerprint_context(context) == fingerprint_context(_context())
