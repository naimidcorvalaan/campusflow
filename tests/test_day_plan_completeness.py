"""Remaining work is scheduled, not turned into a prose promise. No API calls."""
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from src.p2_allocator import allocate_tasks_across_windows
from src.p2_day_intake import DayIntakeProposal, IntakeTask, IntakeCommitment, apply_day_intake
from src.p2_day_plan import compact_plan_lines
from src.p2_session import _live_turn_execution_errors
from src.p3_campus_registry import DEFAULT_CAMPUS_REGISTRY
from src.p3_route_planner import final_plan_overlap_errors
from src.p4_execution_enrichment import (
    enrich_intake_execution_context, effective_duration_overrides,
    protected_meal_duration_overrides, earliest_start_overrides,
    latest_end_overrides, preferred_chunk_overrides,
)
from src.p4_execution_movement import (
    apply_execution_sequence_movements, _apply_execution_sequence_movements, execution_timeline,
)


def scenario(work=240, single=False, deadline=None, located=True, day_end='24:00', explicit=True):
    now = datetime(2026, 9, 18, 15)
    proposal = DayIntakeProposal('p2.day-intake.v1',
        (IntakeCommitment('固定课程', starts_at='17:00', ends_at='18:00',
                         location_text='第九教学楼', commitment_kind='class'),),
        (IntakeTask('项目练习', total_minutes=work if explicit else None,
                    is_splittable=not single, minimum_slice_minutes=15 if not single else None,
                    location_text='图书馆' if located else None,
                    activity_kind='generic', latest_end_time=deadline),
         IntakeTask('整理笔记', total_minutes=30, is_splittable=True, minimum_slice_minutes=15,
                    location_text='图书馆' if located else None, activity_kind='generic'),
         IntakeTask('晚餐', activity_kind='meal', meal_period='dinner',
                    meal_before_commitment_index=1)), day_end, ())
    applied = apply_day_intake(now, proposal)
    campus = DEFAULT_CAMPUS_REGISTRY.get_campus_map('weijinlu')
    context = enrich_intake_execution_context(proposal, applied, campus, current_location_text='图书馆')
    durations = effective_duration_overrides(context, applied.state)
    if not explicit:
        # A formal adopted/estimated total, not a new estimator in continuation.
        durations[applied.new_task_refs[0]] = work
    options = dict(effective_duration_by_task_ref=durations,
                   protected_duration_by_task_ref=protected_meal_duration_overrides(context, applied.state),
                   earliest_start_by_task_ref=earliest_start_overrides(context, applied.state),
                   latest_end_by_task_ref=latest_end_overrides(context, applied.state),
                   preferred_chunk_by_task_ref=preferred_chunk_overrides(context, applied.state))
    provisional = allocate_tasks_across_windows(applied.state, **options)
    old = _apply_execution_sequence_movements(applied.state, provisional, context, campus, **options)
    new = apply_execution_sequence_movements(applied.state, provisional, context, campus, **options)
    return applied, context, campus, old, new


def assert_safe(applied, context, campus, outcome):
    assert not final_plan_overlap_errors(outcome.state, outcome.allocation_plan, outcome.blocks)
    assert not _live_turn_execution_errors(outcome.state, outcome.allocation_plan, outcome.blocks, context, campus)
    assert outcome.state.tasks == applied.state.tasks
    assert outcome.state.commitments == applied.state.commitments
    assert all(e.ends_at <= applied.state.day_end for e in execution_timeline(
        outcome.state, outcome.allocation_plan, context, campus))


def test_interrupted_work_continues_after_class_with_real_return_route():
    applied, context, campus, old, new = scenario()
    ref = applied.new_task_refs[0]
    assert old.allocation_plan.planned_minutes_by_task.get(ref, 0) < 240
    assert new.allocation_plan.planned_minutes_by_task[ref] == 240
    assert new.allocation_plan.planned_minutes_by_task[applied.new_task_refs[1]] == 30
    assert len([a for a in new.allocation_plan.allocations if a.task_ref == ref]) > 1
    assert_safe(applied, context, campus, new)
    text = '\n'.join(compact_plan_lines(new.allocation_plan, new.state, context))
    assert '再做项目练习' in text
    assert ref not in new.allocation_plan.unallocated_task_refs


@pytest.mark.parametrize('work', [600, 1000])
def test_capacity_over_midnight_keeps_honest_remainder(work):
    applied, context, campus, old, new = scenario(work=work)
    ref = applied.new_task_refs[0]
    assert new.allocation_plan.planned_minutes_by_task[ref] < work
    assert ref in new.allocation_plan.unallocated_task_refs
    assert max(e.ends_at for e in execution_timeline(new.state, new.allocation_plan, context, campus)) == applied.state.day_end
    assert_safe(applied, context, campus, new)


def test_existing_estimate_is_used_without_user_minutes():
    applied, context, campus, old, new = scenario(explicit=False)
    assert new.allocation_plan.planned_minutes_by_task[applied.new_task_refs[0]] == 240
    assert_safe(applied, context, campus, new)


def test_indivisible_work_cannot_be_forced_into_short_windows():
    applied, context, campus, old, new = scenario(work=600, single=True)
    assert not new.allocation_plan.planned_minutes_by_task.get(applied.new_task_refs[0], 0)
    assert_safe(applied, context, campus, new)


def test_explicit_deadline_still_prevents_evening_continuation():
    applied, context, campus, old, new = scenario(deadline='16:00')
    assert new.allocation_plan.planned_minutes_by_task.get(applied.new_task_refs[0], 0) <= 60
    assert_safe(applied, context, campus, new)


def test_explicit_earlier_day_end_is_not_overridden():
    applied, context, campus, old, new = scenario(day_end='18:00')
    assert applied.state.day_end.hour == 18
    assert new.allocation_plan.unallocated_task_refs
    assert_safe(applied, context, campus, new)


def test_return_route_is_required_not_a_relaxed_departure_guard():
    applied, context, campus, old, new = scenario()
    return_blocks = [b for b in new.blocks if b.window_start.hour >= 18]
    assert return_blocks
    missing = tuple(b for b in new.blocks if b not in return_blocks)
    errors = _live_turn_execution_errors(new.state, new.allocation_plan, missing, context, campus)
    assert errors


def test_default_horizon_is_midnight_and_does_not_guess_fixed_end():
    proposal = DayIntakeProposal('p2.day-intake.v1', (), (), None, ())
    now = datetime(2026, 9, 18, 23)
    assert apply_day_intake(now, proposal).state.day_end == datetime(2026, 9, 19)
    unknown = replace(proposal, commitments=(IntakeCommitment('课程', starts_at='23:15'),))
    state = apply_day_intake(now, unknown).state
    assert state.unresolved_commitment_refs
    assert all(w.ends_at <= now.replace(minute=15) for w in state.windows)


def test_soft_chunk_does_not_waste_last_window_capacity():
    from tests.test_p4_splittable_execution import _state, _window, _dt, _task
    state = _state((_window('w1', _dt(14), _dt(16), 120),), _task(total=300))
    plan = allocate_tasks_across_windows(state, preferred_chunk_by_task_ref={'day_task_001':30})
    assert plan.total_planned_minutes == 120
    assert plan.remaining_after_plan_by_task['day_task_001'] == 180


def test_nonpriority_active_work_is_not_dropped_when_time_remains():
    from tests.test_p4_splittable_execution import _state, _window, _dt, _task
    first = _task(total=30)
    optional = replace(first, task_ref='day_task_002', total_minutes=45)
    state = replace(_state((_window('w1', _dt(14), _dt(16), 120),), first), tasks=(first,optional))
    plan = allocate_tasks_across_windows(state, task_order=(first.task_ref,))
    assert plan.planned_minutes_by_task == {first.task_ref:30,optional.task_ref:45}
    # Explicit cancellation is different from merely absent priority.
    from src.p2_models import TaskState
    cancelled = replace(state, tasks=(first,replace(optional,state=TaskState.SKIPPED_TODAY)))
    assert optional.task_ref not in allocate_tasks_across_windows(cancelled).planned_minutes_by_task


def test_capacity_copy_reports_unplanned_work_instead_of_future_promise():
    from src.p2_main import _unallocated_plan_copy
    from types import SimpleNamespace
    applied,context,campus,old,new = scenario(work=600)
    copy = _unallocated_plan_copy('今天暂未安排：项目练习',SimpleNamespace(
        updated_state=new.state, allocation_plan=new.allocation_plan))
    assert '24:00' in copy and '未排入今天' in copy
    assert '之后可以再继续' not in copy


def test_midnight_boundary_is_not_permission_for_a_next_day_plan():
    from src.p2_window_derivation import derive_day_state
    now = datetime(2026,9,18,22)
    with pytest.raises(ValueError):
        derive_day_state(now,datetime(2026,9,19,0,1),(),())


def test_raw_intake_uses_shared_default_not_last_commitment_as_horizon():
    from src.p4_event_semantics import build_raw_event_extractor_prompt, parse_raw_event_extraction
    from src.p2_day_intake import DEFAULT_DAY_END
    system,_ = build_raw_event_extractor_prompt(datetime(2026,9,18,15),'下午学习，傍晚开会')
    assert DEFAULT_DAY_END in system and '未给时返回 null' in system
    import json
    raw = parse_raw_event_extraction(json.dumps(dict(
        schema_version='p4.raw-event-extraction.v1',day_end='24:00',
        current_location=None,transport_mode=None,events=[],questions=[])))
    assert raw.day_end == '24:00'


def test_continuation_is_deterministic_and_does_not_count_planning_as_progress():
    first = scenario()
    second = scenario()
    assert first[-1] == second[-1]
    assert all(t.completed_minutes == 0 for t in first[-1].state.tasks)
    assert first[-1].state.tasks == first[0].state.tasks
    assert first[-1].state.history == first[0].state.history


@pytest.mark.parametrize('buffer,work', [(0,100),(0,105),(10,90),(10,100),(0,120),(10,120),(0,240)])
def test_short_remainder_waits_for_later_window_when_departure_consumes_first_suffix(buffer,work):
    now=datetime(2026,9,18,14)
    proposal=DayIntakeProposal('p2.day-intake.v1',
        (IntakeCommitment('Class',starts_at='16:00',ends_at='18:00',
                         location_text='46教学楼',commitment_kind='class'),),
        (IntakeTask('Project',total_minutes=work,is_splittable=True,minimum_slice_minutes=30,
                    location_text='诚园7斋',activity_kind='generic'),),'24:00',())
    applied=apply_day_intake(now,proposal,default_safety_buffer_minutes=buffer)
    campus=DEFAULT_CAMPUS_REGISTRY.get_campus_map('beiyangyuan')
    context=enrich_intake_execution_context(proposal,applied,campus,current_location_text='诚园7斋')
    provisional=allocate_tasks_across_windows(applied.state)
    result=apply_execution_sequence_movements(applied.state,provisional,context,campus)
    assert result.allocation_plan.planned_minutes_by_task[applied.new_task_refs[0]]==work
    assert any(b.window_start.hour>=18 and b.destination_node_id=='chengyuan_7zhai' for b in result.blocks)
    assert_safe(applied,context,campus,result)


@pytest.mark.parametrize('background', [False, True])
@pytest.mark.parametrize('single,deadline,expected', [(False,False,90),(True,False,0),(False,True,45)])
def test_visit_scoped_availability_preserves_attention_deadline_and_single_session(background,single,deadline,expected):
    from tests.test_p2_agentic_pipeline import build_state, dt, make_task, window
    from src.task_attention import allocation_spans
    work=replace(make_task('work','Focused work',total=90),is_splittable=not single)
    tasks=(work,)
    if background:
        tasks+=(replace(make_task('process','Autonomous process',total=120),is_splittable=False,
            attention_mode='background',user_reported_running=True,
            background_reason='Already running without continuous operation.'),)
    state=build_state(tasks,(window('first',dt(9),dt(9,45),45),
        window('away',dt(10),dt(11),60),window('return',dt(12),dt(12,45),45)))
    plan=allocate_tasks_across_windows(state,
        presence_intervals_by_task_ref={'work':((dt(9),dt(9,45)),(dt(12),dt(12,45)))},
        latest_end_by_task_ref={'work':dt(11)} if deadline else None)
    assert plan.planned_minutes_by_task.get('work',0)==expected
    assert all(part.task_ref!='work' or end<=dt(9,45) or start>=dt(12)
               for part,start,end in allocation_spans(state,plan))
    assert all(task.completed_minutes==0 for task in state.tasks)
    assert not final_plan_overlap_errors(state,plan)


def test_arrival_then_departure_is_one_visit_not_a_return():
    from types import SimpleNamespace as NS
    from src.p4_execution_movement import _returning_task_presence
    from tests.test_p2_agentic_pipeline import dt, make_task
    state=NS(now=dt(9),day_end=dt(22),tasks=(make_task('work','Work',total=30),))
    a,b,c=(NS(node_id=name) for name in ('a','b','c'))
    incoming=NS(origin=a,destination=b,origin_ref=None,destination_ref='work',
        movement_start=dt(10),transition_minutes=5,route_minutes=10)
    outgoing=NS(origin=b,destination=c,origin_ref='work',destination_ref='class',
        movement_start=dt(11),transition_minutes=5,route_minutes=10)
    assert _returning_task_presence(state,(incoming,outgoing))=={}
