"""Known execution locations must never publish an unreserved transition."""
import json
from datetime import datetime

import pytest
from dataclasses import replace

from src.p2_day_intake import DayIntakeProposal,IntakeTask,apply_day_intake
from src.p2_session import P2SessionController,build_live_final_turn,_live_turn_execution_errors
from src.p3_campus_registry import DEFAULT_CAMPUS_REGISTRY
from src.p4_execution_context import EXECUTION_PLAN_CONTEXT_KEY
from src.p4_execution_enrichment import enrich_intake_execution_context


def make_turn(study=86,meal=56,split=False):
    campus='beiyangyuan';map_data=DEFAULT_CAMPUS_REGISTRY.get_campus_map(campus)
    proposal=DayIntakeProposal('p2.day-intake.v1',(),(
        IntakeTask('复习',total_minutes=study,is_splittable=True,minimum_slice_minutes=15,
                   activity_kind='generic',location_text='北洋园图书馆',duration_source='user_explicit'),
        IntakeTask('晚饭',total_minutes=meal,is_splittable=False,activity_kind='meal',meal_period='dinner',
                   meal_time='17:30',location_text='北洋园学一食堂',duration_source='user_explicit')),
        day_end='19:00',questions=(),transport_mode='walk')
    applied=apply_day_intake(datetime(2026,9,17,16),proposal)
    if split:
        from src.p4_execution_movement import _split_windows_at_earliest_starts
        applied=replace(applied,state=_split_windows_at_earliest_starts(
            applied.state,{'day_task_002':datetime(2026,9,17,17,30)}))
    context=enrich_intake_execution_context(proposal,applied,map_data,current_location_text='北洋园图书馆')
    def caller(system,user):
        if 'p2.day-plan-intent' in system:
            return json.dumps(dict(schema_version='p2.day-plan-intent.v1',task_order=[],
                                   include_low_attention=False,task_estimates=[],rationale=None))
        if 'p2.day-review' in system:
            return json.dumps(dict(schema_version='p2.day-review.v1',decision='accept',reason='ok',
                                   suggested_task_order=None,include_low_attention=None))
        if 'p3.travel-time' in system:
            return json.dumps(dict(schema_version='p3.travel-time.v1',min_minutes=12,max_minutes=14,reason=None))
        raise RuntimeError('Unexpected test stage')
    controller=P2SessionController({EXECUTION_PLAN_CONTEXT_KEY:context},caller,map_data=map_data,campus_id=campus)
    return controller.start_day(applied.state),map_data


@pytest.mark.parametrize('study,meal',[(82,52),(86,56)])
def test_explicit_duration_meal_has_a_real_incoming_route(study,meal):
    turn,map_data=make_turn(study,meal)
    assert turn.movement_blocks,turn.result.warnings
    build_live_final_turn(turn,map_data)


def test_route_crosses_artificial_earliest_start_window_boundary():
    turn,map_data=make_turn(split=True)
    assert turn.movement_blocks,turn.result.warnings
    build_live_final_turn(turn,map_data)


def test_publication_rejects_missing_known_route():
    turn,map_data=make_turn()
    errors=_live_turn_execution_errors(turn.result.updated_state,
        turn.result.allocation_plan,(),turn.execution_context,map_data)
    assert any('no reserved route' in error for error in errors)


def test_cross_window_reservation_does_not_cross_unavailable_gap():
    from src.p3_route_planner import reserve_timed_movement_intervals
    from src.p2_models import DayWindow
    from src.p1_window_models import AvailabilityLevel
    turn,_=make_turn()
    def window(ref,start,end):
        return DayWindow(ref,start,end,AvailabilityLevel.FULLY_AVAILABLE,0,0,None,
                         int((end-start).total_seconds()//60))
    first=window('first',datetime(2026,9,17,16),datetime(2026,9,17,17,30))
    second=window('second',datetime(2026,9,17,17,35),datetime(2026,9,17,19))
    state=replace(turn.result.updated_state,windows=(first,second),active_window_ref='first')
    result,refs=reserve_timed_movement_intervals(state,((datetime(2026,9,17,17,29),12,5),))
    assert result is state and not refs


def test_cross_window_route_reserves_once_and_preserves_capacity():
    from src.p3_route_planner import reserve_timed_movement_intervals
    from src.p4_execution_movement import _split_windows_at_earliest_starts
    # Use the original free horizon, not windows already occupied by routes.
    from src.p2_window_derivation import derive_day_state
    state=derive_day_state(datetime(2026,9,17,16),datetime(2026,9,17,19),(),(),
                           default_safety_buffer_minutes=0)
    state=_split_windows_at_earliest_starts(state,{'boundary':datetime(2026,9,17,17,30)})
    intervals=((datetime(2026,9,17,17,29),12,5),)
    adjusted,refs=reserve_timed_movement_intervals(state,intervals)
    assert len(refs)==1
    assert sum(w.capacity_minutes for w in adjusted.windows)==180-17
    assert len(state.windows)==2
    assert all(not(w.starts_at<datetime(2026,9,17,17,41) and
                   datetime(2026,9,17,17,24)<w.ends_at)
               for w in adjusted.windows if w.capacity_minutes)
    rejected,refs=reserve_timed_movement_intervals(state,intervals+((datetime(2026,9,17,17,35),10,0),))
    assert rejected is state and not refs


def test_start_window_semantics_shared_by_all_intake_paths():
    from src.p2_day_intake import TASK_TIME_BOUNDARY_SEMANTICS,build_day_intake_prompt
    from src.p4_event_semantics import build_raw_event_extractor_prompt
    from src.p4_intake_auditor import build_initial_intake_audit_prompt
    now=datetime(2026,9,17,16)
    proposal=DayIntakeProposal('p2.day-intake.v1',(),(),day_end='19:00',questions=())
    prompts=[build_day_intake_prompt(now,'task')[0],
             build_raw_event_extractor_prompt(now,'task')[0],
             build_initial_intake_audit_prompt(now,'task',proposal)[0]]
    assert all(TASK_TIME_BOUNDARY_SEMANTICS in prompt for prompt in prompts)
