"""Generic elapsed processes: no title/locale dependent execution rules."""
from dataclasses import replace
import json
import pytest

from tests.test_p2_agentic_pipeline import build_state, dt, make_task, window
from src.p2_allocator import allocate_tasks_across_windows
from src.p2_models import TaskState
from src.task_attention import allocation_spans, attention_validation_errors
from src.task_dependencies import dependency_errors
from src.p3_route_planner import final_plan_overlap_errors


def scenario(title='External process'):
    launch = replace(make_task('launch', 'Activate', total=2), is_splittable=False)
    background = replace(make_task('process', title, total=40), is_splittable=False,
        attention_mode='background', launch_task_ref='launch',
        background_reason='The stated process proceeds without continuous human operation.')
    finish = replace(make_task('finish', 'Inspect result', total=5), is_splittable=False,
        predecessor_task_refs=('process',))
    foreground = replace(make_task('focus', 'Study', total=20), is_splittable=False)
    return build_state((launch, background, finish, foreground), (window('w', dt(9), dt(11), 120),))


@pytest.mark.parametrize('title', ['Download transfer', 'Unattended computation', 'Charging interval', 'Opaque process A'])
def test_unattended_process_overlaps_foreground_and_releases_successor(title):
    state = scenario(title)
    plan = allocate_tasks_across_windows(state)
    spans = {part.task_ref: (start, end) for part, start, end in allocation_spans(state, plan)}
    assert spans == {'launch': (dt(9), dt(9,2)), 'process': (dt(9,2), dt(9,42)),
                     'focus': (dt(9,2), dt(9,22)), 'finish': (dt(9,42), dt(9,47))}
    assert not final_plan_overlap_errors(state, plan)
    assert not attention_validation_errors(state, plan)
    assert not dependency_errors(state, plan)
    assert all(task.completed_minutes == 0 and not task.user_reported_running for task in state.tasks)
    assert plan.current_allocation.task_ref == 'launch'


def test_two_active_tasks_still_cannot_overlap():
    state = scenario()
    plan = allocate_tasks_across_windows(state)
    wrong = replace(plan, allocations=tuple(replace(p, starts_at=dt(9)) if p.task_ref == 'focus' else p
                                           for p in plan.allocations))
    assert final_plan_overlap_errors(state, wrong)


def test_completed_background_does_not_discard_unfinished_foreground():
    state = scenario()
    state = replace(state, tasks=(replace(state.tasks[0], completed_minutes=2, state=TaskState.COMPLETED),
        replace(state.tasks[1], completed_minutes=40, state=TaskState.COMPLETED),
        state.tasks[2], replace(state.tasks[3], overlap_task_ref='process')))
    plan = allocate_tasks_across_windows(state)
    assert plan.planned_minutes_by_task == {'finish': 5, 'focus': 20}
    assert not attention_validation_errors(state, plan)
    assert state.tasks[3].completed_minutes == 0


def test_background_allocator_preserves_soft_chunk_preference_without_losing_work():
    state = scenario()
    state = replace(state, tasks=state.tasks[:3] + (replace(state.tasks[3], total_minutes=90,
        is_splittable=True, minimum_slice_minutes=10),), windows=(
        window('a',dt(9),dt(10),60),window('b',dt(11),dt(13),120)), active_window_ref='a')
    plan = allocate_tasks_across_windows(state, preferred_chunk_by_task_ref={'focus':20})
    parts = [p for p in plan.allocations if p.task_ref == 'focus']
    assert parts[0].planned_minutes == 20
    assert sum(p.planned_minutes for p in parts) == 90
    assert not final_plan_overlap_errors(state, plan)


def test_result_cannot_be_used_before_background_completion():
    state = scenario()
    plan = allocate_tasks_across_windows(state)
    wrong = replace(plan, allocations=tuple(replace(p, starts_at=dt(9,25)) if p.task_ref == 'finish' else p
                                           for p in plan.allocations))
    assert dependency_errors(state, wrong)


@pytest.mark.parametrize('changes', [dict(launch_task_ref=None), dict(background_reason=None),
    dict(is_splittable=True), dict(launch_task_ref='process'), dict(user_reported_running='yes')])
def test_invalid_background_facts_rejected(changes):
    with pytest.raises(ValueError):
        replace(scenario().tasks[1], **changes)


def test_unknown_launch_reference_rejected():
    state = scenario()
    state = replace(state, tasks=(state.tasks[0], replace(state.tasks[1], launch_task_ref='absent')) + state.tasks[2:])
    with pytest.raises(ValueError):
        allocate_tasks_across_windows(state)


def test_cancelled_launch_does_not_start_process():
    state = scenario()
    state = replace(state, tasks=(replace(state.tasks[0], state=TaskState.ABANDONED),) + state.tasks[1:])
    plan = allocate_tasks_across_windows(state)
    assert set(plan.planned_minutes_by_task) == {'focus'}
    assert {'process','finish'}.issubset(plan.unallocated_task_refs)


def test_supervised_process_is_ordinary_exclusive_work():
    state = scenario()
    active = replace(state.tasks[1], attention_mode='active', launch_task_ref=None,
        background_reason=None, predecessor_task_refs=('launch',))
    state = replace(state, tasks=(state.tasks[0],active)+state.tasks[2:])
    plan = allocate_tasks_across_windows(state)
    assert not final_plan_overlap_errors(state, plan)
    spans = {p.task_ref:(a,b) for p,a,b in allocation_spans(state,plan)}
    assert spans['focus'][0] >= spans['process'][1]


def test_background_can_run_across_person_unavailable_window():
    state = scenario()
    state = replace(state, windows=(window('a',dt(9),dt(9,10),10),window('b',dt(10),dt(11),60)),
                    active_window_ref='a')
    plan=allocate_tasks_across_windows(state)
    spans={p.task_ref:(a,b) for p,a,b in allocation_spans(state,plan)}
    assert spans['process']==(dt(9,2),dt(9,42))
    assert spans['finish'][0]>=dt(10)
    assert not attention_validation_errors(state,plan)


def test_midnight_limit_does_not_invent_finished_process():
    state=scenario()
    state=replace(state, day_end=dt(9,30),windows=(window('w',dt(9),dt(9,30),30),))
    plan=allocate_tasks_across_windows(state)
    assert 'process' in plan.unallocated_task_refs and 'finish' in plan.unallocated_task_refs
    assert all(end<=state.day_end for _,_,end in allocation_spans(state,plan))


def test_progress_and_old_snapshot_compatibility():
    from src.p2_task_progress import apply_progress_report
    from src.local_persistence import _encode_value, _decode_value
    task=scenario().tasks[1]
    updated=apply_progress_report(task,10)
    assert updated.remaining_minutes==30 and updated.launch_task_ref=='launch'
    assert updated.attention_mode=='background'
    assert _decode_value(_encode_value(updated))==updated
    payload=_encode_value(scenario().tasks[0])
    for key in ('attention_mode','launch_task_ref','background_reason','user_reported_running'):
        payload['fields'].pop(key)
    assert _decode_value(payload).attention_mode=='active'


def test_current_action_prioritizes_foreground_for_reported_running_process():
    state=scenario()
    state=replace(state,tasks=(replace(state.tasks[0],completed_minutes=2),
        replace(state.tasks[1],user_reported_running=True))+state.tasks[2:])
    plan=allocate_tasks_across_windows(state)
    assert plan.current_allocation.task_ref=='focus'
    from src.p2_day_plan import compact_plan_lines
    lines=compact_plan_lines(plan,state)
    assert 'Study' in lines[0]
    assert any('External process' in line for line in lines[1:])


def test_running_remaining_report_keeps_identity_and_does_not_relaunch():
    from src.p3_unified_feedback import _parse_task_batch
    from src.p2_state_reconciler import apply_reconciliation
    state=scenario()
    update=_parse_task_batch([dict(target_task_ref='process',new_task_title=None,
        lifecycle_action='none',user_reported_running=True,reported_remaining_minutes=10)],state)
    changed=apply_reconciliation(state,update).state
    process=next(t for t in changed.tasks if t.task_ref=='process')
    assert process.user_reported_running and process.remaining_minutes==10
    assert process.completed_minutes==0  # no invented elapsed or active work
    assert changed.tasks[0].state is TaskState.COMPLETED  # running is explicit evidence of launch
    plan=allocate_tasks_across_windows(changed)
    assert 'launch' not in plan.planned_minutes_by_task
    assert plan.planned_minutes_by_task['process']==10
    again=apply_reconciliation(changed,update).state
    assert again==changed


@pytest.mark.parametrize('departure_waits', [False, True])
def test_person_can_leave_and_return_while_process_location_stays_put(departure_waits):
    from datetime import datetime
    from src.p2_day_intake import IntakeTask,DayIntakeProposal,apply_day_intake
    from src.p4_execution_enrichment import enrich_intake_execution_context
    from src.p4_execution_movement import apply_execution_sequence_movements,execution_route_coverage_errors,execution_timeline
    from src.p3_campus_registry import DEFAULT_CAMPUS_REGISTRY
    tasks=(IntakeTask('Activate',2,False,location_text='诚园7斋',activity_kind='generic'),
        IntakeTask('Run',40,False,location_text='诚园7斋',activity_kind='generic',attention_mode='background',
                   launch_task_index=1,background_reason='Unattended external process'),
        IntakeTask('Read',20,False,location_text='郑东图书馆',activity_kind='generic'),
        IntakeTask('Inspect',5,False,location_text='诚园7斋',activity_kind='generic',predecessor_task_indexes=(2,),
                   departure_after_task_indexes=(2,) if departure_waits else ()))
    proposal=DayIntakeProposal('p2.day-intake.v1',(),tasks,'24:00',(),current_location='诚园7斋')
    applied=apply_day_intake(datetime(2026,9,19,14),proposal,semantically_reviewed=True)
    campus=DEFAULT_CAMPUS_REGISTRY.get_campus_map('beiyangyuan')
    context=enrich_intake_execution_context(proposal,applied,campus,current_location_text='诚园7斋')
    outcome=apply_execution_sequence_movements(applied.state,allocate_tasks_across_windows(applied.state),context,campus)
    assert outcome.applied and len(outcome.blocks)==2
    assert outcome.blocks[0].origin_node_id==outcome.blocks[1].destination_node_id=='chengyuan_7zhai'
    assert outcome.blocks[0].destination_node_id==outcome.blocks[1].origin_node_id=='zhengdong_library'
    assert not execution_route_coverage_errors(outcome.state,outcome.allocation_plan,outcome.blocks,context,campus)
    assert not attention_validation_errors(outcome.state,outcome.allocation_plan,context)
    events=execution_timeline(outcome.state,outcome.allocation_plan,context,campus,include_background=True)
    process=next(e for e in events if e.activity_type=='background')
    assert process.location.node_id=='chengyuan_7zhai'
    read=next(e for e in events if e.activity_ref=='day_task_003')
    pickup=next(e for e in events if e.activity_ref=='day_task_004')
    assert read.starts_at<process.ends_at
    assert pickup.starts_at>=max(process.ends_at,outcome.blocks[1].end_time)
    assert context.current_location.location.node_id=='chengyuan_7zhai'
    from src.task_dependencies import departure_dependency_errors
    assert not departure_dependency_errors(outcome.state,outcome.allocation_plan,outcome.blocks)
    if departure_waits:
        assert outcome.blocks[1].window_start>=process.ends_at
        forged=tuple(replace(b,window_start=process.starts_at) if i==1 else b for i,b in enumerate(outcome.blocks))
        assert departure_dependency_errors(outcome.state,outcome.allocation_plan,forged)
    else:
        assert outcome.blocks[1].window_start<process.ends_at or read.ends_at>=process.ends_at


def test_background_cannot_bypass_intake_semantic_audit():
    from src.p2_day_intake import IntakeTask,DayIntakeProposal,apply_day_intake
    from src.p2_agentic_parser import AgenticParseError
    proposal=DayIntakeProposal('p2.day-intake.v1',(),(
        IntakeTask('Start',2,False),IntakeTask('Process',40,False,attention_mode='background',
            launch_task_index=1,background_reason='Unattended')), '24:00',())
    with pytest.raises(AgenticParseError,match='semantic audit'):
        apply_day_intake(dt(9),proposal)


def test_copy_facts_use_parallel_interval_instead_of_sequential_cursor():
    from src.p5_agent_context import build_agent_decision_context
    from src.p5_copy_semantics import copy_semantic_facts
    from src.p4_execution_context import ExecutionPlanContext
    state=scenario()
    plan=allocate_tasks_across_windows(state)
    context=build_agent_decision_context(state,ExecutionPlanContext(), 'beiyangyuan',allocation_plan=plan)
    facts=copy_semantic_facts(context)
    actual={s['task_ref']:(s['start'],s['end']) for s in facts['segments']}
    expected={p.task_ref:(a.isoformat(),b.isoformat()) for p,a,b in allocation_spans(state,plan)}
    assert actual==expected


def test_initial_running_and_pending_launch_are_inconsistent():
    from src.p2_day_intake import IntakeTask,DayIntakeProposal
    with pytest.raises(ValueError,match='pending launch'):
        DayIntakeProposal('p2.day-intake.v1',(),(IntakeTask('Activate',2,False),
            IntakeTask('Run',40,False,attention_mode='background',launch_task_index=1,
                background_reason='Autonomous',user_reported_running=True)),None,())


def test_forged_background_duration_cannot_exceed_formal_remaining():
    state=scenario()
    plan=allocate_tasks_across_windows(state)
    wrong=replace(plan,allocations=tuple(replace(p,planned_minutes=45,remaining_before=45)
        if p.task_ref=='process' else p for p in plan.allocations),total_planned_minutes=72)
    assert 'allocated minutes exceed formal remaining work' in attention_validation_errors(state,wrong)


def test_running_process_continues_during_unavailable_person_window():
    state=scenario()
    state=replace(state,active_window_ref=None,windows=(window('later',dt(10),dt(11),60),),
        tasks=(replace(state.tasks[0],state=TaskState.COMPLETED,completed_minutes=2),
        replace(state.tasks[1],user_reported_running=True))+state.tasks[2:])
    plan=allocate_tasks_across_windows(state)
    spans={p.task_ref:(a,b) for p,a,b in allocation_spans(state,plan)}
    assert spans['process']==(dt(9),dt(9,40))
    assert spans['focus'][0]>=dt(10)
    assert not attention_validation_errors(state,plan)


def test_background_branch_preserves_later_protected_meal_capacity():
    state=scenario()
    meal=replace(make_task('meal','Meal',total=40),is_splittable=False)
    state=replace(state,windows=(window('w',dt(9),dt(10),60),),
        tasks=state.tasks[:2]+(replace(state.tasks[3],total_minutes=100,is_splittable=True,minimum_slice_minutes=10),meal))
    plan=allocate_tasks_across_windows(state,protected_duration_by_task_ref={'meal':40})
    assert plan.planned_minutes_by_task['meal']==40
    assert plan.planned_minutes_by_task['focus']==18
    assert plan.planned_minutes_by_window['w']==60


def test_invalid_background_audit_has_one_correction_then_stops():
    from src.p2_day_intake import IntakeTask,DayIntakeProposal
    from src.p4_intake_auditor import audit_initial_intake
    proposal=DayIntakeProposal('p2.day-intake.v1',(),(IntakeTask('Activate',2,False),
        IntakeTask('Run',40,False,attention_mode='background',launch_task_index=1,
            background_reason='Autonomous')),None,())
    calls=[]
    def malformed(system,user):
        calls.append(user)
        return '{'
    result=audit_initial_intake(dt(9),'Start then run unattended',proposal,malformed)
    assert result.audit_failed and result.proposal==proposal
    assert result.call_count==len(calls)==2
    calls.clear()
    def repaired(system,user):
        calls.append(user)
        return '{' if len(calls)==1 else json.dumps(dict(schema_version='p4.initial-intake-audit.v1',
            decision='approve',issues=[],repaired_proposal=None))
    result=audit_initial_intake(dt(9),'Start then run unattended',proposal,repaired)
    assert not result.audit_failed and result.proposal==proposal and result.call_count==2


def test_explicit_overlap_is_start_relation_not_completion_dependency():
    state=scenario()
    state=replace(state,tasks=state.tasks[:3]+(replace(state.tasks[3],overlap_task_ref='process'),))
    plan=allocate_tasks_across_windows(state,task_order=('focus','finish','process','launch'))
    spans={p.task_ref:(a,b) for p,a,b in allocation_spans(state,plan)}
    assert spans['focus']==(dt(9,2),dt(9,22))
    assert spans['finish'][0]>=spans['process'][1]
    assert not attention_validation_errors(state,plan)
    wrong=replace(plan,allocations=tuple(replace(p,starts_at=dt(10)) if p.task_ref=='focus' else p for p in plan.allocations))
    assert 'required background overlap is absent' in attention_validation_errors(state,wrong)


def test_overlap_cannot_authorize_two_active_tasks():
    state=scenario()
    state=replace(state,tasks=state.tasks[:3]+(replace(state.tasks[3],overlap_task_ref='launch'),))
    with pytest.raises(ValueError,match='background process'):
        allocate_tasks_across_windows(state)


def test_conflicting_start_and_completion_relations_rejected():
    with pytest.raises(ValueError,match='overlap'):
        replace(scenario().tasks[3],overlap_task_ref='process',predecessor_task_refs=('process',))


def test_required_overlap_cannot_be_postponed_after_process_finishes():
    state=scenario()
    state=replace(state,tasks=state.tasks[:3]+(replace(state.tasks[3],overlap_task_ref='process'),))
    plan=allocate_tasks_across_windows(state,earliest_start_by_task_ref={'focus':dt(10)})
    assert 'focus' in plan.unallocated_task_refs
    assert 'focus' not in plan.planned_minutes_by_task


def test_reported_background_running_is_copy_fact_not_inferred_progress():
    from src.p5_agent_context import build_agent_decision_context
    from src.p5_copy_semantics import copy_semantic_facts
    from src.p4_execution_context import ExecutionPlanContext
    state=scenario()
    state=replace(state,tasks=(replace(state.tasks[0],state=TaskState.COMPLETED,completed_minutes=2),
        replace(state.tasks[1],user_reported_running=True))+state.tasks[2:])
    context=build_agent_decision_context(state,ExecutionPlanContext(),'beiyangyuan',
        allocation_plan=allocate_tasks_across_windows(state))
    fact=next(t for t in copy_semantic_facts(context)['tasks'] if t['task_ref']=='process')
    assert fact['progress_status']=='running' and fact['reported_in_progress']
    assert fact['completed_minutes']==0


def event_candidate():
    from tests.test_p4_event_semantics import _event, _raw, _graph
    from src.p4_event_semantics import parse_raw_event_extraction
    raw=parse_raw_event_extraction(json.dumps(_raw([
        _event('launch','task','Activate',1,duration=2),
        _event('run','task','Autonomous process',2,duration=40),
        _event('read','task','Read',3,duration=20),
        _event('inspect','task','Inspect',4,duration=5)])))
    graph=_graph()
    graph['background_processes']=[dict(event_id='run',launch_event_id='launch',reason='No supervision required',already_running=False)]
    graph['task_overlaps']=[dict(background_event_id='run',active_event_id='read')]
    graph['task_dependencies']=[dict(before_event_id='run',after_event_id='inspect',target_boundary='departure')]
    return raw,graph


def test_dependency_boundary_projects_one_relation_without_losing_legacy_contract():
    from src.p4_event_semantics import parse_event_semantic_graph,materialize_day_intake
    raw,payload=event_candidate()
    graph=parse_event_semantic_graph(json.dumps(payload),raw)
    assert graph.task_dependencies==graph.departure_dependencies==(('run','inspect'),)
    task=materialize_day_intake(raw,graph).tasks[3]
    assert task.predecessor_task_indexes==task.departure_after_task_indexes==(2,)
    payload['task_dependencies'][0].pop('target_boundary')
    legacy=parse_event_semantic_graph(json.dumps(payload),raw)
    assert legacy.task_dependencies==(('run','inspect'),) and not legacy.departure_dependencies


def test_background_linker_review_preserves_exact_structure_without_second_rewrite():
    from src.p4_event_semantics import parse_event_semantic_graph,materialize_day_intake
    from src.p4_intake_auditor import audit_initial_intake
    raw,payload=event_candidate()
    graph=parse_event_semantic_graph(json.dumps(payload),raw)
    proposal=materialize_day_intake(raw,graph)
    result=audit_initial_intake(dt(9),'Tasks',proposal,
        lambda *_:pytest.fail('Validated autonomous semantic graph must not be rewritten'),
        raw_events=raw,semantic_graph=graph)
    assert result.proposal is proposal and result.call_count==0 and not result.audit_failed


def test_invalid_background_graph_repairs_in_original_stage_once():
    from src.p2_day_intake import run_day_intake
    from src.p4_event_semantics import raw_event_payload
    raw,valid=event_candidate()
    broken=json.loads(json.dumps(valid))
    broken['background_processes'][0]['launch_event_id']='run'
    raw_calls=[];link_calls=[]
    def extract(system,user):
        raw_calls.append(user);return json.dumps(raw_event_payload(raw))
    def link(system,user):
        link_calls.append(user);return json.dumps(broken if len(link_calls)==1 else valid)
    outcome=run_day_intake(dt(9),'Autonomous process with a launch',lambda *_:pytest.fail('no legacy fallback'),
        raw_event_caller=extract,semantic_linker_caller=link,
        semantic_auditor_caller=lambda *_:pytest.fail('no second rewrite'))
    assert outcome.applied is not None and outcome.repair_used
    assert outcome.call_count==4 and len(raw_calls)==len(link_calls)==2
    assert 'invalid background launch event' in raw_calls[1]
    assert 'process=run launch=run' in raw_calls[1]
    assert 'distinct raw events' in raw_calls[1]
    assert raw_event_payload(raw)['schema_version'] in raw_calls[1]


def test_exhausted_background_repair_cannot_silently_publish_serial_fallback():
    from src.p2_day_intake import run_day_intake
    from src.p4_event_semantics import raw_event_payload
    raw,broken=event_candidate()
    broken['background_processes'][0]['launch_event_id']='run'
    calls=[]
    def extract(*args):
        calls.append('raw');return json.dumps(raw_event_payload(raw))
    def link(*args):
        calls.append('graph');return json.dumps(broken)
    outcome=run_day_intake(dt(9),'Autonomous process',lambda *_:pytest.fail('no serial fallback'),
        raw_event_caller=extract,semantic_linker_caller=link,
        semantic_auditor_caller=lambda *_:pytest.fail('no discarded relations'))
    assert outcome.applied is None and outcome.repair_used
    assert outcome.call_count==4 and calls==['raw','graph','raw','graph']


def test_unknown_dependency_boundary_still_rejected():
    from src.p4_event_semantics import parse_event_semantic_graph
    from src.p2_agentic_parser import AgenticParseError
    raw,payload=event_candidate()
    payload['task_dependencies'][0]['target_boundary']='whenever'
    with pytest.raises(AgenticParseError):parse_event_semantic_graph(json.dumps(payload),raw)


def test_travel_source_survives_without_program_interpreting_its_language():
    from src.p4_event_semantics import raw_event_payload,parse_raw_event_extraction,materialize_day_intake,EventSemanticGraph
    raw,_=event_candidate()
    raw=replace(raw,events=raw.events[:3]+(replace(raw.events[3],
        travel_instruction='Return only after the process has finished.'),))
    restored=parse_raw_event_extraction(json.dumps(raw_event_payload(raw)))
    assert restored==raw
    # The source span alone never becomes a program-inferred constraint.
    assert materialize_day_intake(restored,EventSemanticGraph()).tasks[3].departure_after_task_indexes==()


def test_background_and_explicit_intervals_roundtrip_existing_persistence():
    from src.local_persistence import _encode_value,_decode_value
    state=scenario()
    state=replace(state,tasks=state.tasks[:3]+(replace(state.tasks[3],overlap_task_ref='process'),))
    plan=allocate_tasks_across_windows(state)
    assert _decode_value(json.loads(json.dumps(_encode_value(state))))==state
    assert _decode_value(json.loads(json.dumps(_encode_value(plan))))==plan
    legacy=_encode_value(state.tasks[0])
    for key in ('attention_mode','launch_task_ref','background_reason','user_reported_running',
                'overlap_task_ref','departure_after_task_refs'):
        legacy['fields'].pop(key)
    assert _decode_value(legacy)==state.tasks[0]


def test_ordinary_audit_projection_is_lossless_and_uses_shared_contract():
    from src.p2_day_intake import DayIntakeProposal, IntakeTask, parse_day_intake, day_intake_field_contract
    from src.p4_intake_auditor import _audit_proposal_payload, _uses_process_fields, build_initial_intake_audit_prompt
    proposal=DayIntakeProposal(schema_version='p2.day-intake.v1', commitments=(), day_end='24:00', questions=(), tasks=(IntakeTask(title='Ordinary work',total_minutes=90),))
    assert not _uses_process_fields(proposal)
    payload=_audit_proposal_payload(proposal,False)
    assert parse_day_intake(json.dumps(payload))==proposal
    system,user=build_initial_intake_audit_prompt(dt(9),'Ordinary work',proposal)
    assert day_intake_field_contract(False) in system
    assert 'attention_mode' not in system+user
    assert 'predecessor_task_indexes' in system


def test_nondefault_process_facts_cannot_be_projected_out_of_audit():
    from src.p4_event_semantics import parse_event_semantic_graph,materialize_day_intake
    from src.p4_intake_auditor import _audit_proposal_payload, _uses_process_fields, build_initial_intake_audit_prompt
    raw,payload=event_candidate()
    proposal=materialize_day_intake(raw,parse_event_semantic_graph(json.dumps(payload),raw))
    assert _uses_process_fields(proposal)
    with pytest.raises(ValueError):_audit_proposal_payload(proposal,False)
    system,user=build_initial_intake_audit_prompt(dt(9),'Process work',proposal)
    assert 'attention_mode' in system and 'background' in user


def test_reported_running_process_survives_without_any_person_free_window():
    from src.p2_day_plan import compact_plan_lines, summarize_day_plan
    from src.p2_companion_copy import extract_plan_facts
    from src.p4_execution_movement import execution_timeline
    from types import SimpleNamespace
    original = scenario()
    process = replace(original.tasks[1], launch_task_ref=None, user_reported_running=True)
    state = replace(original, tasks=(process,), windows=(), active_window_ref=None)
    plan = allocate_tasks_across_windows(state)
    assert not plan.unallocated_task_refs
    assert len(plan.allocations) == 1
    assert plan.allocations[0].window_ref is None
    assert plan.planned_minutes_by_window == {}
    assert plan.current_allocation is None
    assert allocation_spans(state, plan)[0][1:] == (dt(9), dt(9,40))
    assert not attention_validation_errors(state, plan)
    assert not final_plan_overlap_errors(state, plan)
    assert any('External process' in line for line in compact_plan_lines(plan,state))
    assert any('External process' in line for line in summarize_day_plan(plan,state).later_window_lines)
    assert 'External process' in str(extract_plan_facts(state,plan))
    context = SimpleNamespace(binding_for=lambda ref:None)
    assert not execution_timeline(state,plan,context,None)
    assert execution_timeline(state,plan,context,None,include_background=True)[0].activity_type == 'background'
    assert state.tasks[0].completed_minutes == 0


def test_active_allocation_cannot_claim_windowless_background_permission():
    part = allocate_tasks_across_windows(scenario()).allocations[0]
    with pytest.raises(ValueError):replace(part,window_ref=None)
    process_part = next(p for p in allocate_tasks_across_windows(scenario()).allocations if not p.occupies_attention)
    with pytest.raises(ValueError):replace(process_part,starts_at=None)
    forged = replace(process_part, task_ref='focus')
    original = allocate_tasks_across_windows(scenario())
    forged_plan = replace(original, allocations=tuple(forged if p==process_part else p for p in original.allocations),
                         later_allocations=tuple(forged if p==process_part else p for p in original.later_allocations))
    assert final_plan_overlap_errors(scenario(),forged_plan)
