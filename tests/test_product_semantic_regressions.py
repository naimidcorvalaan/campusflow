# -*- coding: utf-8 -*-
"""Regressions found by visible-output review, not schema-success scoring."""
import json
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace

import pytest

from src.p2_agentic_parser import AgenticParseError
from src.p2_commitment_reconciler import (
    apply_commitment_reconciliation, parse_commitment_reconciliation,
)
from src.p2_day_intake import build_day_intake_prompt, day_intake_field_contract, run_day_intake
from src.p2_question_filter import filter_pending_questions
from src.p3_campus_registry import DEFAULT_CAMPUS_REGISTRY
from src.p4_event_semantics import parse_event_semantic_graph, parse_raw_event_extraction
from src.p4_execution_context import ExecutableTaskBinding, ExecutionPlanContext
from src.p4_execution_enrichment import earliest_start_overrides
from src.planning_confirmation_ui import PlanningConfirmation, verify_answer_result
from tests.test_p2_commitment_reconciler import build_state, dt
from tests.test_p4_event_semantics import _raw, _event, _graph


def venue_update(**extra):
    item = dict(target_commitment_ref='day_commitment_001', action='update_location',
                title=None, starts_at=None, ends_at=None, delay_minutes=None,
                location_text='46教学楼')
    item.update(extra)
    return parse_commitment_reconciliation(json.dumps(dict(
        schema_version='p2.commitment-reconciliation.v1', updates=[item], questions=[])))


def test_commitment_venue_update_preserves_clock_progress_and_identity():
    before = build_state()
    after = apply_commitment_reconciliation(before, venue_update()).state
    assert after.tasks == before.tasks
    assert after.now == before.now
    assert replace(after.commitments[0], location_text=None) == before.commitments[0]
    assert after.commitments[1] == before.commitments[1]


@pytest.mark.parametrize('field,value', [('starts_at','15:00'), ('ends_at','18:00'),
    ('delay_minutes',20), ('class_arrival_lead_minutes',5),
    ('location_text',None), ('target_commitment_ref',None)])
def test_venue_answer_cannot_also_mutate_other_fields(field, value):
    with pytest.raises(AgenticParseError):
        venue_update(**{field:value})


def test_unknown_venue_identity_is_not_reused():
    before = build_state()
    applied = apply_commitment_reconciliation(before, venue_update(target_commitment_ref='missing'))
    assert applied.state.commitments == before.commitments
    assert applied.warnings


def test_targeted_venue_question_survives_other_known_destination():
    state = build_state()
    state = replace(state, commitments=(state.commitments[0],
        replace(state.commitments[1], location_text='31教学楼')))
    q = '“上课”在哪个地点上课？'
    assert filter_pending_questions((q,),state) == (q,)
    updated = apply_commitment_reconciliation(state,venue_update()).state
    assert filter_pending_questions((q,),updated) == ()


def test_unknown_class_end_does_not_release_after_class_work_early():
    state = build_state()
    state = replace(state, commitments=(replace(state.commitments[0],ends_at=None),))
    context = ExecutionPlanContext(bindings=(ExecutableTaskBinding(
        state.tasks[0].task_ref,not_before_commitment_ref=state.commitments[0].commitment_ref),))
    assert earliest_start_overrides(context,state)[state.tasks[0].task_ref] == state.day_end
    fixed = replace(state,commitments=(replace(state.commitments[0],ends_at=dt(12)),))
    assert earliest_start_overrides(context,fixed)[state.tasks[0].task_ref] == dt(12)


def test_invalid_optional_profile_does_not_erase_raw_evidence_from_auditor():
    raw = _raw([_event('e','task','写报告',1,duration=40,location='诚园7斋')])
    seen=[]
    fallback=dict(schema_version='p2.day-intake.v1',day_end=None,commitments=[],
        tasks=[dict(title='写报告',total_minutes=40)],questions=[])
    def auditor(system,user):
        seen.append((system,user))
        return json.dumps(dict(schema_version='p4.initial-intake-audit.v1',decision='approve',issues=[],repaired_proposal=None))
    outcome=run_day_intake(dt(14),'在诚园7斋写报告40分钟',lambda s,u:json.dumps(fallback),
        raw_event_caller=lambda s,u:json.dumps(raw),semantic_linker_caller=lambda s,u:'{}',
        semantic_auditor_caller=auditor)
    assert outcome.applied is not None
    assert 'Raw Events' in seen[0][1] and '诚园7斋' in seen[0][1]
    assert day_intake_field_contract(include_process=False) in seen[0][0]
    assert day_intake_field_contract() in build_day_intake_prompt(dt(14),'安排任务')[0]


def test_profile_null_is_still_rejected():
    raw=parse_raw_event_extraction(json.dumps(_raw([_event('e','task','写报告',1)])))
    graph=_graph(profiles=[('e',True,None,None,False,'qwen_semantic')])
    with pytest.raises(AgenticParseError):
        parse_event_semantic_graph(json.dumps(graph),raw)


def test_spatial_intake_asks_missing_class_venue_without_inventing_one():
    payload=dict(schema_version='p2.day-intake.v1',day_end=None,
        commitments=[dict(title='上课',starts_at='16:00',ends_at='18:00',commitment_kind='class')],
        tasks=[],questions=[])
    outcome=run_day_intake(dt(14),'16:00到18:00上课',lambda s,u:json.dumps(payload),
        map_data=DEFAULT_CAMPUS_REGISTRY.get_campus_map('beiyangyuan'),defer_spatial_intake=True)
    assert outcome.applied.state.commitments[0].location_text is None
    assert outcome.questions == ('“上课”在哪个地点上课？',)


def test_bound_venue_answer_rejects_collateral_time_change():
    state=build_state()
    updated=apply_commitment_reconciliation(state,venue_update()).state
    binding=PlanningConfirmation('token','venue','location_text','day_commitment_001','original')
    bundle=lambda value:SimpleNamespace(turn=SimpleNamespace(result=SimpleNamespace(updated_state=value)))
    verify_answer_result(binding,bundle(state),bundle(updated))
    changed=replace(updated,commitments=(replace(updated.commitments[0],ends_at=dt(13)),)+updated.commitments[1:])
    with pytest.raises(ValueError):
        verify_answer_result(binding,bundle(state),bundle(changed))


def test_hard_meal_start_does_not_send_user_to_wait_an_hour_early():
    from tests.test_p4_execution_movement import _outcome
    from src.p2_day_intake import IntakeTask
    from src.p4_execution_movement import execution_timeline
    _, applied, context, outcome = _outcome((
        IntakeTask('算法练习', total_minutes=60, is_splittable=True, minimum_slice_minutes=10),
        IntakeTask('晚饭', activity_kind='meal', meal_period='dinner', earliest_start_time='18:30'),
    ), current='诚园7斋',campus_id='beiyangyuan',reference=dt(16,30))
    assert outcome.applied
    meal_ref=applied.new_task_refs[1]
    route=next(b for b in outcome.blocks if b.destination_activity_ref==meal_ref)
    assert route.end_time == dt(18,30)
    assert outcome.allocation_plan.planned_minutes_by_task[applied.new_task_refs[0]] == 60


def test_missing_end_is_not_narrated_as_proven_capacity_exhaustion():
    from src.p2_main import _unallocated_plan_copy
    state=build_state()
    state=replace(state,commitments=(replace(state.commitments[0],ends_at=None),))
    result=SimpleNamespace(updated_state=state,allocation_plan=SimpleNamespace(allocations=()))
    copy=_unallocated_plan_copy('今天暂未安排：'+state.tasks[0].title,result)
    assert '确认' in copy and '结束时间' in copy
    assert '无法容纳' not in copy


def test_feedback_critic_sees_commitments_and_shared_stage_ownership():
    from src.p4_feedback_decision import (FeedbackDecision, build_feedback_interpreter_prompt,
        build_feedback_critic_prompt, COMPATIBILITY_FEEDBACK_SEMANTICS)
    state=build_state()
    state=replace(state,commitments=(replace(state.commitments[0],location_text='46教学楼'),))
    normal,_=build_feedback_interpreter_prompt(state,ExecutionPlanContext(),'18:30','')
    review,user=build_feedback_critic_prompt(state,'18:30',FeedbackDecision(intent_type='mixed'))
    assert COMPATIBILITY_FEEDBACK_SEMANTICS in normal
    assert COMPATIBILITY_FEEDBACK_SEMANTICS in review
    assert state.commitments[0].commitment_ref in user and '46教学楼' in user


@pytest.mark.parametrize('packing', [None, dt(18,35)])
def test_agent_movement_departure_is_not_preparation_start(packing):
    from src.p5_agent_context import _movement_fact
    block=SimpleNamespace(window_start=dt(18,40), transition_start=packing,
        end_time=dt(18,50), estimated_minutes=10)
    fact=_movement_fact(block)
    assert fact.starts_at == dt(18,40).isoformat()
    assert fact.preparation_starts_at == (packing.isoformat() if packing else None)
    assert fact.ends_at == dt(18,50).isoformat()


@pytest.mark.parametrize('text,invalid', [
    ('18:35出发。',True), ('18:40出发。',False),
    ('18:40开始收拾。',True), ('18:35开始收拾。',False),
    ('18:40前完成收拾。',False),
])
def test_copy_distinguishes_packing_departure_and_preparation_deadline(text,invalid):
    from tests.test_p5_agent_intelligence import _context
    from src.p5_copy_guard import _has_invalid_final_time_claim
    context=_context()
    movement=replace(context.movements[0],preparation_starts_at='2026-09-05T18:35:00')
    context=replace(context,movements=(movement,))
    assert _has_invalid_final_time_claim(context,text) is invalid


@pytest.mark.parametrize('end,text,invalid', [
    (None,'19:00上课结束后再安排。',True),
    ('2026-09-05T20:30:00','19:00上课结束后再安排。',True),
    ('2026-09-05T20:30:00','上课将于20:30结束。',False),
    ('2026-09-05T20:30:00','上课结束后20:45回到宿舍。',False),
])
def test_copy_does_not_borrow_class_start_as_end_or_misbind_later_arrival(end,text,invalid):
    from tests.test_p5_agent_intelligence import _context
    from src.p5_copy_guard import _has_invalid_final_time_claim
    context=_context()
    context=replace(context,fixed_commitments=(replace(context.fixed_commitments[0],ends_at=end),))
    assert _has_invalid_final_time_claim(context,text) is invalid


def test_summary_fallback_does_not_claim_pending_plan_was_confirmed():
    from tests.test_p5_agent_intelligence import _context
    from src.p5_copy_guard import observable_plan_opening
    context=replace(_context(),selected_timeline=())
    text=observable_plan_opening(context)
    assert '确认' not in text and '目前信息' in text


def test_legacy_commitment_prompt_covers_formal_actions_including_location():
    from src.p2_commitment_reconciler import CommitmentAction,_commitment_system_prompt
    prompt=_commitment_system_prompt()
    assert all(action.value in prompt for action in CommitmentAction)
    assert 'location_text' in prompt
