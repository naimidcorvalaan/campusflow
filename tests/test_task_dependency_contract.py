"""Language-independent causal edges, distinct from preferred task order."""
from dataclasses import replace

import pytest

from tests.test_p2_agentic_pipeline import build_state, dt, make_task, window
from src.p2_models import TaskState
from src.p2_allocator import allocate_tasks_across_windows


def state_with_chain():
    return build_state((make_task('a', 'Produce', total=90, min_slice=10),
                        make_task('b', 'Consume', total=20, min_slice=10)),
                       (window('am', dt(9), dt(10), 60),
                        window('pm', dt(14), dt(16), 120)))


def test_dependency_waits_for_whole_predecessor_across_windows():
    state = state_with_chain()
    plan = allocate_tasks_across_windows(state, task_order=('b', 'a'),
        predecessors_by_task_ref={'b': ('a',)})
    assert plan.planned_minutes_by_task == {'a': 90, 'b': 20}
    assert [a.task_ref for a in plan.allocations if a.window_ref == 'pm'] == ['a', 'b']
    assert not any(a.task_ref == 'b' and a.window_ref == 'am' for a in plan.allocations)
    assert all(t.completed_minutes == 0 for t in state.tasks)


@pytest.mark.parametrize('state', [TaskState.ABANDONED, TaskState.SKIPPED_TODAY])
def test_cancelled_or_skipped_predecessor_is_not_completion(state):
    original = state_with_chain()
    changed = replace(original, tasks=(replace(original.tasks[0], state=state), original.tasks[1]))
    plan = allocate_tasks_across_windows(changed, predecessors_by_task_ref={'b': ('a',)})
    assert 'b' in plan.unallocated_task_refs
    assert 'b' not in plan.planned_minutes_by_task


def test_completed_predecessor_does_not_need_replanning():
    original = state_with_chain()
    changed = replace(original, tasks=(replace(original.tasks[0], completed_minutes=90,
                                               state=TaskState.COMPLETED), original.tasks[1]))
    plan = allocate_tasks_across_windows(changed, predecessors_by_task_ref={'b': ('a',)})
    assert plan.planned_minutes_by_task == {'b': 20}
    assert plan.allocations[0].window_ref == 'am'


def test_partial_capacity_does_not_release_dependent_action():
    original = state_with_chain()
    changed = replace(original, windows=(original.windows[0],))
    plan = allocate_tasks_across_windows(changed, predecessors_by_task_ref={'b': ('a',)})
    assert plan.planned_minutes_by_task == {'a': 60}
    assert 'b' in plan.unallocated_task_refs


@pytest.mark.parametrize('edges', [
    {'b': ('missing',)}, {'b': ('b',)}, {'b': ('a',), 'a': ('b',)},
])
def test_invalid_dependency_graph_is_rejected(edges):
    with pytest.raises(ValueError):
        allocate_tasks_across_windows(state_with_chain(), predecessors_by_task_ref=edges)


def test_order_without_dependency_remains_a_planning_preference():
    original = state_with_chain()
    plan = allocate_tasks_across_windows(original, task_order=('b', 'a'))
    assert plan.allocations[0].task_ref == 'b'


def test_formal_dependency_survives_estimation_and_progress_updates():
    from src.p2_agentic_pipeline import _apply_estimate_fields
    from src.p2_agentic_models import TaskEstimate
    from src.p2_task_progress import apply_progress_report
    original = state_with_chain()
    dependent = replace(original.tasks[1], predecessor_task_refs=('a',))
    estimated = _apply_estimate_fields(dependent, TaskEstimate('b', 25, True, 5))
    assert estimated.predecessor_task_refs == ('a',)
    updated = apply_progress_report(estimated, 5)
    assert updated.predecessor_task_refs == ('a',)
    assert updated.completed_minutes == 5


def test_semantic_graph_becomes_ref_bound_edge_without_title_matching():
    from src.p4_event_semantics import RawEvent, RawEventExtraction, EventSemanticGraph, materialize_day_intake
    from src.p2_day_intake import apply_day_intake
    raw = RawEventExtraction(events=(
        RawEvent('x', 'task', '生産', 1, explicit_duration_minutes=30),
        RawEvent('y', 'task', 'Consume', 2, explicit_duration_minutes=10)))
    proposal = materialize_day_intake(raw, EventSemanticGraph(task_dependencies=(('x', 'y'),)))
    state = apply_day_intake(dt(9), proposal).state
    assert state.tasks[1].predecessor_task_refs == (state.tasks[0].task_ref,)
    plan = allocate_tasks_across_windows(state, task_order=tuple(reversed([t.task_ref for t in state.tasks])))
    assert [item.task_ref for item in plan.allocations] == [t.task_ref for t in state.tasks]


def test_final_postcondition_rejects_valid_minutes_in_wrong_causal_order():
    from src.task_dependencies import dependency_errors
    original = state_with_chain()
    wrong = allocate_tasks_across_windows(original, task_order=('b', 'a'))
    state = replace(original, tasks=(original.tasks[0], replace(original.tasks[1], predecessor_task_refs=('a',))))
    assert dependency_errors(state, wrong)
    correct = allocate_tasks_across_windows(state, task_order=('b', 'a'))
    assert not dependency_errors(state, correct)


def test_legacy_snapshot_defaults_to_no_edges_and_new_snapshot_keeps_edges():
    from src.local_persistence import _encode_value, _decode_value
    task = replace(state_with_chain().tasks[1], predecessor_task_refs=('a',))
    payload = _encode_value(task)
    assert _decode_value(payload).predecessor_task_refs == ('a',)
    payload['fields'].pop('predecessor_task_refs')
    assert _decode_value(payload).predecessor_task_refs == ()


@pytest.mark.parametrize('invalid', [None, '1', [True], [0], [1.5]])
def test_dependency_schema_rejects_untyped_endpoints(invalid):
    from src.p2_day_intake import _parse_task
    from src.p2_agentic_parser import AgenticParseError
    with pytest.raises(AgenticParseError):
        _parse_task({'title': 'Consume', 'predecessor_task_indexes': invalid})
