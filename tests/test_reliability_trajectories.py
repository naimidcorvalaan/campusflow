from copy import deepcopy
from scripts.reliability_trajectories import cases,continuity_errors


def state():
    return {'a':dict(title='课程报告',total=40,done=10,remaining=30,state='active',source='ai_extracted_from_user_text')}


def test_trajectory_set_is_frozen_diverse_and_continuous():
    rows=cases()
    assert len(rows)==100 and len({r['id'] for r in rows})==100
    assert len({r['initial']+str(r['events']) for r in rows})==100
    assert all(len(r['events'])==5 for r in rows)
    assert {r['campus'] for r in rows}=={'beiyangyuan','weijinlu'}


def test_planned_work_does_not_count_as_actual_progress():
    before=state();after=deepcopy(before);after['a']['done']=20
    assert 'unreported_progress_changed' in continuity_errors(before,after,{'kind':'order'})


def test_explicit_remaining_change_preserves_actual_progress_and_identity():
    before=state();after=deepcopy(before);after['a'].update(total=60,remaining=50)
    assert continuity_errors(before,after,dict(kind='slower',target='课程报告',done=10,remaining=50))==[]
    after['a']['done']=0
    assert 'actual_progress_lost' in continuity_errors(before,after,dict(kind='slower',target='课程报告',done=10,remaining=50))


def test_cancelled_and_completed_cannot_revive_on_later_turn():
    for status in ('completed','abandoned'):
        before=state();before['a']['state']=status;after=deepcopy(before);after['a']['state']='active'
        assert 'terminal_task_resurrected' in continuity_errors(before,after,dict(kind='unchanged'))


def test_new_task_cannot_reuse_old_identity():
    before=state();after=deepcopy(before);after['a']['title']='新任务'
    errors=continuity_errors(before,after,dict(kind='insert',target='新任务',total=20))
    assert 'insert_identity_or_duration' in errors and 'identity_title_changed' in errors


def test_fixed_contract_ignores_generated_route_and_normalized_default_metadata():
    from dataclasses import replace
    from types import SimpleNamespace
    from datetime import datetime,timedelta
    from src.p1_window_models import FixedCommitment,AvailabilityLevel
    from scripts.reliability_trajectories import fixed_snapshot
    c=FixedCommitment('class','课程','课程',datetime(2026,9,17,16),datetime(2026,9,17,17),
                      '31教',AvailabilityLevel.UNAVAILABLE,{},(),commitment_kind='class')
    before=fixed_snapshot(SimpleNamespace(commitments=(c,)))
    route=replace(c,commitment_ref='generated_travel',commitment_kind='travel')
    assert fixed_snapshot(SimpleNamespace(commitments=(replace(c,class_arrival_lead_minutes=10),route)),set(before))==before
    assert fixed_snapshot(SimpleNamespace(commitments=(replace(c,starts_at=c.starts_at+timedelta(minutes=5)),)),set(before))!=before


def test_location_gold_uses_formal_alias_identity():
    from src.p3_location_resolver import resolve_location
    from src.p3_campus_registry import DEFAULT_CAMPUS_REGISTRY
    campus=DEFAULT_CAMPUS_REGISTRY.get_campus_map('beiyangyuan')
    assert resolve_location(campus,'第31教学楼',None,None).node_id==resolve_location(campus,'31教',None,None).node_id


def test_initial_unknown_end_cannot_pass_explicit_fixed_time_gold():
    from scripts.reliability_trajectories import initial_fixed_times_match
    fixed={'c':dict(start='2026-09-17T16:00:00',end=None)}
    assert not initial_fixed_times_match(fixed,[['16:00','17:00']])
    fixed['c']['end']='2026-09-17T17:00:00'
    assert initial_fixed_times_match(fixed,[['16:00','17:00']])
    fixed['d']=dict(start=None,end=None)
    assert not initial_fixed_times_match(fixed,[['16:00','17:00'],['17:40','18:00']])


def test_planning_failure_reports_safe_invariant_without_exception_text():
    from scripts.reliability_trajectories import planning_failure
    result=planning_failure(ValueError('invalid live final turn: known location transition has no reserved route: private task text'))
    assert result['planning_invariants']==['reserved_route_required']
    assert 'private task text' not in str(result)
