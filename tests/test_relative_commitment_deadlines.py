"""Relative hard finish semantics across extraction, planning and publication."""
import json
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from scripts.evaluation_deadlines import DeadlineExpectation, deadline_evaluation_errors
from src.p2_day_intake import DayIntakeProposal, IntakeCommitment, IntakeTask, apply_day_intake, parse_day_intake, build_day_intake_prompt
from src.p2_live_main import make_live_session
from src.p2_session import commit_live_final_turn, load_live_final_turn
from src.p3_campus_registry import DEFAULT_CAMPUS_REGISTRY
from src.p4_event_semantics import RawEvent, RawEventExtraction, EventRelation, EventSemanticGraph, materialize_day_intake, build_semantic_linker_prompt
from src.p4_execution_context import ExecutableTaskBinding, ExecutionPlanContext, save_execution_context
from src.p4_execution_enrichment import enrich_intake_execution_context, latest_end_overrides, reconcile_execution_context
from src.p4_execution_movement import execution_timeline
from src.p4_intake_auditor import _proposal_payload, audit_initial_intake
from src.p5_agent_context import build_agent_decision_context

NOW = datetime(2026, 9, 15, 14)
MAP = DEFAULT_CAMPUS_REGISTRY.get_campus_map("beiyangyuan")


def proposal(task=None, commitments=None, current="第31教学楼"):
    return DayIntakeProposal("p2.day-intake.v1", tuple(commitments or (
        IntakeCommitment("课程", starts_at="14:50", ends_at="16:20", location_text="第31教学楼", commitment_kind="class"),
    )), (task or IntakeTask("习题", 30, before_commitment_index=1),), "22:00", (), current, "walk")


def apply(parsed):
    applied = apply_day_intake(NOW, parsed)
    context = enrich_intake_execution_context(parsed, applied, MAP, current_location_text=parsed.current_location)
    return applied, context


def plan(parsed):
    applied, context = apply(parsed)
    store = {}
    save_execution_context(store, context)

    def caller(system, user):
        if "day-review" in system:
            return json.dumps(dict(schema_version="p2.day-review.v1", decision="accept", reason=None,
                                   suggested_task_order=None, include_low_attention=None))
        return json.dumps(dict(schema_version="p2.day-plan-intent.v1", task_order=list(applied.new_task_refs),
                               include_low_attention=False, task_estimates=[], rationale=None))

    session = make_live_session(store, SimpleNamespace(agent_caller=caller), map_data=MAP,
                                campus_id=MAP.campus_id, companion_enabled=False, agent_intelligence_enabled=False)
    turn = session.start_day(applied.state)
    events = execution_timeline(turn.result.updated_state, turn.result.allocation_plan, turn.execution_context, MAP)
    return turn, events, session, store


@pytest.mark.parametrize("phrase", ["课前完成", "上课前做完", "出发前完成", "组会前完成"])
def test_relative_finish_protocol_is_consistent_for_explicit_phrases(phrase):
    raw = RawEventExtraction((RawEvent("c", "fixed_commitment", "约定", 1, "15:00", "16:00"),
                              RawEvent("t", "task", "报告", 2, explicit_duration_minutes=30, raw_evidence=phrase)))
    graph = EventSemanticGraph(commitment_relations=(EventRelation("t", "c", "before"),))
    parsed = materialize_day_intake(raw, graph)
    roundtrip = parse_day_intake(json.dumps(_proposal_payload(parsed)))
    assert roundtrip.tasks[0].before_commitment_index == 1
    assert roundtrip.tasks[0].after_commitment_index is None
    applied, context = apply(roundtrip)
    assert latest_end_overrides(context, applied.state) == {"day_task_001": NOW.replace(hour=15)}
    agent = build_agent_decision_context(applied.state, context, MAP.campus_id)
    assert agent.active_tasks[0].before_commitment_ref == "day_commitment_001"
    assert agent.active_tasks[0].latest_end == "2026-09-15T15:00:00"
    assert "before_commitment_index" in build_day_intake_prompt(NOW, phrase)[0]
    assert "普通任务" in build_semantic_linker_prompt(NOW, phrase, raw)[0]


def test_same_place_pre_class_work_finishes_before_class_with_full_duration():
    turn, events, _, _ = plan(proposal())
    assert not deadline_evaluation_errors(turn.result.updated_state, events, turn.movement_blocks,
                                         [DeadlineExpectation("day_task_001", "day_commitment_001")])
    assert sum(a.planned_minutes for a in turn.result.allocation_plan.allocations) == 30


def test_route_reallocation_reserves_travel_and_class_arrival_time():
    parsed = proposal(IntakeTask("报告", 60, is_splittable=True, minimum_slice_minutes=10,
                                location_text="图书馆", activity_kind="generic", before_commitment_index=1),
                      [IntakeCommitment("课程", starts_at="15:00", ends_at="16:00",
                                        location_text="第31教学楼", commitment_kind="class")], "图书馆")
    turn, events, _, _ = plan(parsed)
    assert turn.movement_blocks
    outbound = next(b for b in turn.movement_blocks if b.destination_activity_ref == "day_commitment_001")
    task_events = [e for e in events if e.activity_type == "task"]
    assert task_events
    assert max(e.ends_at for e in task_events) <= outbound.transition_start
    assert outbound.end_time <= NOW.replace(hour=14, minute=50)
    assert not deadline_evaluation_errors(turn.result.updated_state, events, turn.movement_blocks,
                                         [DeadlineExpectation("day_task_001", "day_commitment_001", require_complete=False)])
    assert turn.result.allocation_plan.planned_minutes_by_task["day_task_001"] < 60


def test_no_required_deadline_does_not_inherit_next_class_boundary():
    task = IntakeTask("练习", 90, is_splittable=True, minimum_slice_minutes=10, activity_kind="generic")
    turn, events, _, _ = plan(proposal(task))
    assert any(e.ends_at > NOW.replace(hour=16, minute=20) for e in events if e.activity_type == "task")
    assert latest_end_overrides(turn.execution_context, turn.result.updated_state) == {}
    assert not deadline_evaluation_errors(turn.result.updated_state, events, turn.movement_blocks, [])


def test_soft_meal_preference_is_not_a_deadline():
    context = ExecutionPlanContext(bindings=(ExecutableTaskBinding("day_task_001", activity_kind="meal",
                                    meal_window_start_minutes=17*60, meal_window_end_minutes=19*60),))
    applied, _ = apply(proposal(IntakeTask("饭", 30)))
    assert latest_end_overrides(context, applied.state) == {}
    prompt = build_day_intake_prompt(NOW, "最好课前做完报告")[0]
    assert "只是偏好" in prompt


def test_adjacent_commitments_bind_by_exact_ref_not_nearest_time():
    parsed = proposal(IntakeTask("准备", 30, before_commitment_index=2), [
        IntakeCommitment("课程", starts_at="15:00", ends_at="16:00"),
        IntakeCommitment("组会", starts_at="17:00", ends_at="18:00"),
    ])
    applied, context = apply(parsed)
    assert latest_end_overrides(context, applied.state)["day_task_001"] == NOW.replace(hour=17)
    shifted = replace(applied, state=replace(applied.state, commitments=applied.state.commitments[1:]),
                      new_commitment_refs=("day_commitment_002",))
    assert enrich_intake_execution_context(parsed, shifted, MAP).bindings[0].before_commitment_ref == "day_commitment_002"


def test_missing_target_is_not_rebound_to_adjacent_commitment():
    parsed = proposal()
    applied, _ = apply(parsed)
    with pytest.raises(ValueError, match="未应用"):
        enrich_intake_execution_context(parsed, replace(applied, new_commitment_refs=()), MAP)


def test_midnight_boundary_uses_full_commitment_date_and_tightest_absolute_bound():
    applied, context = apply(proposal())
    tomorrow = NOW.replace(hour=0, minute=30) + timedelta(days=1)
    commitment = replace(applied.state.commitments[0], starts_at=tomorrow, ends_at=tomorrow + timedelta(hours=1))
    state = replace(applied.state, commitments=(commitment,))
    assert latest_end_overrides(context, state)["day_task_001"] == tomorrow
    earlier = tomorrow - timedelta(minutes=15)
    context = context.upsert(replace(context.bindings[0], deadline_at=earlier))
    assert latest_end_overrides(context, state)["day_task_001"] == earlier


@pytest.mark.parametrize("kwargs", [{"before_commitment_index": 0}, {"before_commitment_index": True},
                                   {"before_commitment_index": 1, "after_commitment_index": 1}])
def test_invalid_or_contradictory_before_index_is_rejected(kwargs):
    with pytest.raises(ValueError):
        IntakeTask("任务", **kwargs)


def test_unknown_before_refs_and_out_of_range_indexes_are_rejected():
    with pytest.raises(ValueError):
        proposal(IntakeTask("任务", before_commitment_index=2))
    applied, context = apply(proposal())
    context = context.upsert(replace(context.bindings[0], before_commitment_ref="missing"))
    with pytest.raises(ValueError, match="未知"):
        latest_end_overrides(context, applied.state)


@pytest.mark.parametrize("change", ["omit", "after", "rebind"])
def test_intake_audit_revision_cannot_erase_or_reverse_existing_deadline(change):
    parsed = proposal()
    payload = _proposal_payload(parsed)
    payload["tasks"][0]["before_commitment_index"] = None
    if change == "after":
        payload["tasks"][0]["after_commitment_index"] = 1
    if change == "rebind":
        payload["commitments"].append(dict(payload["commitments"][0], title="别的课", starts_at="17:00", ends_at="18:00"))
        payload["tasks"][0]["before_commitment_index"] = 2
    caller = lambda *args: json.dumps(dict(schema_version="p4.initial-intake-audit.v1", decision="repair",
                                           issues=["revision"], repaired_proposal=payload))
    audit = audit_initial_intake(NOW, "习题课前完成", parsed, caller, confirmed_proposal=parsed)
    assert audit.audit_failed and audit.proposal == parsed


def test_reordered_intake_revision_preserves_same_deadline_identity():
    parsed = proposal(IntakeTask("准备", 30, before_commitment_index=2), [
        IntakeCommitment("课程", starts_at="15:00", ends_at="16:00"),
        IntakeCommitment("组会", starts_at="17:00", ends_at="18:00"),
    ])
    payload = _proposal_payload(parsed)
    payload["commitments"].reverse()
    payload["tasks"][0]["before_commitment_index"] = 1
    caller = lambda *args: json.dumps(dict(schema_version="p4.initial-intake-audit.v1", decision="repair",
                                           issues=["revision"], repaired_proposal=payload))
    audit = audit_initial_intake(NOW, "组会前完成准备", parsed, caller)
    assert audit.repaired and audit.proposal.tasks[0].before_commitment_index == 1


def test_force_replan_and_context_reconciliation_keep_finish_relation():
    turn, _, session, _ = plan(proposal())
    state = turn.result.updated_state
    assert reconcile_execution_context(turn.execution_context, state).bindings[0].before_commitment_ref == "day_commitment_001"
    again = session.start_day(state, force=True)
    events = execution_timeline(again.result.updated_state, again.result.allocation_plan, again.execution_context, MAP)
    assert not deadline_evaluation_errors(again.result.updated_state, events, again.movement_blocks,
                                         [DeadlineExpectation("day_task_001", "day_commitment_001")])


def test_atomic_publication_rejects_late_plan_even_without_activity_kind():
    turn, events, _, store = plan(proposal(IntakeTask("任务", 90, is_splittable=True, minimum_slice_minutes=10)))
    old = commit_live_final_turn(store, turn, MAP)
    context = turn.execution_context.upsert(ExecutableTaskBinding("day_task_001", before_commitment_ref="day_commitment_001"))
    with pytest.raises(ValueError, match="task ends after explicit boundary"):
        commit_live_final_turn(store, replace(turn, execution_context=context), MAP)
    assert load_live_final_turn(store) is old
    errors = deadline_evaluation_errors(turn.result.updated_state, events, turn.movement_blocks,
                                        [DeadlineExpectation("day_task_001", "day_commitment_001")])
    assert "task_finishes_after_required_deadline:day_task_001" in errors


def test_evaluation_checks_target_date_and_never_passes_missing_work():
    turn, events, _, _ = plan(proposal())
    state = turn.result.updated_state
    assert "required_work_not_completed_by_deadline:day_task_001" in deadline_evaluation_errors(
        state, [], (), [DeadlineExpectation("day_task_001", "day_commitment_001")])
    target = state.commitments[0].starts_at + timedelta(days=1)
    next_day = replace(state, commitments=(replace(state.commitments[0], starts_at=target,
                                                   ends_at=target + timedelta(hours=1)),))
    assert not deadline_evaluation_errors(next_day, events, (), [DeadlineExpectation("day_task_001", "day_commitment_001")])


def test_evaluation_catches_late_arrival_even_when_task_finishes_before_class():
    turn, events, _, _ = plan(proposal())
    block = SimpleNamespace(destination_activity_ref="day_commitment_001", origin_activity_ref="day_task_001",
                            end_time=NOW.replace(minute=49), transition_start=NOW.replace(minute=20),
                            window_start=NOW.replace(minute=25))
    errors = deadline_evaluation_errors(turn.result.updated_state, events, (block,),
                                        [DeadlineExpectation("day_task_001", "day_commitment_001")])
    assert "required_commitment_arrival_missed:day_commitment_001" in errors  # class arrival is 14:40
    assert "deadline_task_overruns_departure:day_task_001" in errors


def test_relative_commitment_start_crossing_midnight_survives_intake_to_binding():
    parsed = proposal(commitments=[IntakeCommitment("出发", starts_in_minutes=650, duration_minutes=30)])
    applied, context = apply(parsed)
    assert latest_end_overrides(context, applied.state)["day_task_001"] == datetime(2026, 9, 16, 0, 50)
