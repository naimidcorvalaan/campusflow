"""Narration uses the allocator's workload, never a stale second estimate."""
from dataclasses import replace
from types import SimpleNamespace

from tests.test_p2_agentic_pipeline import build_state, dt, make_task, window
from src.p2_allocator import allocate_tasks_across_windows
from src.p2_models import TaskState
from src.p5_candidate import summarize_candidate
from src.p5_copy_semantics import copy_semantic_facts


def summary(total=50, capacity=60, effective=None, protected=None):
    task = make_task('a', 'Meal', total=total, min_slice=5)
    state = build_state((task,), (window('w', dt(9), dt(10), capacity),))
    plan = allocate_tasks_across_windows(state,
        effective_duration_by_task_ref=effective, protected_duration_by_task_ref=protected)
    result = summarize_candidate('c', SimpleNamespace(strategy_id='s'), state,
        SimpleNamespace(allocation_plan=plan), effective_duration_by_task_ref=effective,
        protected_duration_by_task_ref=protected)
    return state, plan, result


def test_effective_meal_duration_does_not_leave_phantom_work():
    state, plan, result = summary(effective={'a': 40}, protected={'a': 40})
    assert plan.planned_minutes_by_task == {'a': 40}
    assert result.remaining_work == (('a', 0),)
    assert state.tasks[0].total_minutes == 50 and state.tasks[0].completed_minutes == 0


def test_valid_shorter_default_meal_is_not_a_second_snack():
    _, plan, result = summary(capacity=25, effective={'a': 40}, protected={'a': 40})
    assert plan.planned_minutes_by_task == {'a': 25}
    assert result.remaining_work == (('a', 0),)


def test_explicit_workload_still_has_real_remainder():
    _, plan, result = summary(capacity=25)
    assert result.remaining_work == (('a', 25),)


def test_cancelled_task_is_not_reported_as_remaining_work():
    state, plan, _ = summary()
    state = replace(state, tasks=(replace(state.tasks[0], state=TaskState.ABANDONED),))
    empty = allocate_tasks_across_windows(state)
    result = summarize_candidate('c', SimpleNamespace(strategy_id='s'), state,
                                 SimpleNamespace(allocation_plan=empty))
    assert result.remaining_work == ()


def test_copy_effective_workload_is_not_completed_progress():
    state, _, _ = summary()
    task = state.tasks[0]
    fact = SimpleNamespace(task_ref='a', title='Meal', total_minutes=50,
        completed_minutes=0, remaining_minutes=40, effective_duration_minutes=40, state='active')
    context = SimpleNamespace(active_tasks=(fact,), latest_feedback_text=None,
        latest_user_text=None, available_windows=(), selected_plan=())
    result = copy_semantic_facts(context, (task,))['tasks'][0]
    assert result['remaining_minutes'] == 40
    assert result['progress_status'] == 'not_started'
    assert result['completed_minutes'] == 0
