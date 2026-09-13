"""P4d-3a explicit concurrency core; all semantic facts are mocked objects."""

from datetime import datetime

import pytest

from src.p1_models import SourceKind
from src.p1_window_models import AvailabilityLevel, FixedCommitment
from src.p2_agentic_models import P2AgenticDayResult
from src.p2_allocation_models import DayAllocationPlan
from src.p2_allocator import allocate_tasks_across_windows
from src.p2_day_plan import compact_plan_lines, summarize_day_plan
from src.p2_models import DayPlanningState, DayWindow, TaskProgress, TaskState
from src.p2_session import P2SessionController, P2SessionTurn, build_live_final_turn
from src.p2_window_derivation import derive_active_window_ref
from src.p3_campus_registry import DEFAULT_CAMPUS_REGISTRY, resolve_location_in_campus
from src.p4_concurrency import (
    ConcurrentAllocation,
    ConcurrentExecutionAuthorization,
    ConcurrentExecutionSource,
    concurrent_minutes_by_task,
    concurrency_validation_errors,
    plan_explicit_concurrency,
)
from src.p4_execution_context import (
    ExecutableTaskBinding,
    ExecutionLocation,
    ExecutionLocationSource,
    ExecutionPlanContext,
)
from src.p4_execution_enrichment import reconcile_execution_context


def _dt(hour, minute=0):
    return datetime(2026, 9, 5, hour, minute)


def _window(ref, start, end, capacity=None):
    return DayWindow(
        ref, start, end, AvailabilityLevel.FULLY_AVAILABLE, 0, 0, None,
        capacity if capacity is not None else int((end - start).total_seconds() // 60),
    )


def _task(ref="day_task_vocab", title="背单词", total=60):
    return TaskProgress(ref, title, total, 0, SourceKind.USER_STATED, TaskState.ACTIVE, True, 15)


def _class(ref="day_commitment_class", location=None):
    return FixedCommitment(
        ref, "上课", "上课", _dt(19), _dt(20, 30), location,
        AvailabilityLevel.UNAVAILABLE, {}, (), commitment_kind="class",
    )


def _state(tasks=(), commitments=(), windows=()):
    now = _dt(18)
    return DayPlanningState(
        now, now, _dt(22), tuple(commitments), tuple(tasks), tuple(windows),
        derive_active_window_ref(tuple(windows), now), (), (),
    )


def _auth(task_ref="day_task_vocab", commitment_ref="day_commitment_class", minutes=30, offset=None):
    return ConcurrentExecutionAuthorization(
        task_ref, commitment_ref, minutes,
        ConcurrentExecutionSource.EXPLICIT_USER_REQUEST, offset,
    )


def test_default_has_no_concurrency_even_for_splittable_task():
    task = _task()
    state = _state((task,), (_class(),), (_window("w_before", _dt(18), _dt(18, 50)),))
    context = ExecutionPlanContext((ExecutableTaskBinding(task.task_ref, activity_kind="generic"),))
    concurrent = plan_explicit_concurrency(state, context)
    plan = allocate_tasks_across_windows(state, concurrent_minutes_by_task_ref=concurrent_minutes_by_task(concurrent.allocations))
    assert concurrent.allocations == ()
    assert plan.planned_minutes_by_task == {task.task_ref: 50}


def test_explicit_authorization_creates_only_the_requested_pair_and_uses_earliest_slot():
    vocab = _task()
    homework = _task("day_task_homework", "写作业", 60)
    state = _state((vocab, homework), (_class(),), ())
    context = ExecutionPlanContext(
        (
            ExecutableTaskBinding(vocab.task_ref, activity_kind="generic"),
            ExecutableTaskBinding(homework.task_ref, activity_kind="generic"),
        ),
        concurrency_authorizations=(_auth(minutes=30, offset=20),),
    )
    result = plan_explicit_concurrency(state, context)
    assert [(item.task_ref, item.commitment_ref, item.starts_at, item.ends_at) for item in result.allocations] == [
        (vocab.task_ref, "day_commitment_class", _dt(19, 20), _dt(19, 50))
    ]
    assert result.rejected_authorizations == ()
    assert all(item.task_ref != homework.task_ref for item in result.allocations)


def test_concurrent_minutes_are_deducted_at_allocator_boundary_without_completion_mutation():
    vocab = _task(total=60)
    state = _state(
        (vocab,),
        (_class(),),
        (_window("w_after", _dt(20, 30), _dt(21, 30)),),
    )
    context = ExecutionPlanContext(
        (ExecutableTaskBinding(vocab.task_ref, activity_kind="generic"),),
        concurrency_authorizations=(_auth(minutes=30),),
    )
    concurrent = plan_explicit_concurrency(state, context)
    plan = allocate_tasks_across_windows(
        state, concurrent_minutes_by_task_ref=concurrent_minutes_by_task(concurrent.allocations)
    )
    assert concurrent_minutes_by_task(concurrent.allocations) == {vocab.task_ref: 30}
    assert plan.planned_minutes_by_task == {vocab.task_ref: 30}
    assert vocab.completed_minutes == 0
    assert plan.planned_minutes_by_task[vocab.task_ref] + concurrent.allocations[0].planned_minutes == 60


def test_existing_completed_progress_is_not_subtracted_twice_from_concurrent_work():
    vocab = TaskProgress(
        "day_task_vocab", "背单词", 60, 30, SourceKind.USER_STATED,
        TaskState.ACTIVE, True, 15,
    )
    state = _state((vocab,), (_class(),), ())
    context = ExecutionPlanContext(
        (ExecutableTaskBinding(vocab.task_ref, activity_kind="generic"),),
        concurrency_authorizations=(_auth(minutes=30),),
    )
    concurrent = plan_explicit_concurrency(state, context).allocations
    assert [(item.task_ref, item.planned_minutes) for item in concurrent] == [
        (vocab.task_ref, 30)
    ]


def test_effective_execution_duration_is_also_a_hard_cap_for_concurrent_accounting():
    vocab = _task(total=60)
    state = _state((vocab,), (_class(),), (_window("w_after", _dt(20, 30), _dt(21)),))
    context = ExecutionPlanContext(
        (ExecutableTaskBinding(vocab.task_ref, activity_kind="generic", effective_duration_minutes=40, duration_source="user_explicit"),),
        concurrency_authorizations=(_auth(minutes=30),),
    )
    concurrent = plan_explicit_concurrency(
        state, context, effective_duration_by_task_ref={vocab.task_ref: 40}
    ).allocations
    plan = allocate_tasks_across_windows(
        state,
        effective_duration_by_task_ref={vocab.task_ref: 40},
        concurrent_minutes_by_task_ref=concurrent_minutes_by_task(concurrent),
    )
    assert plan.planned_minutes_by_task == {vocab.task_ref: 10}
    assert concurrency_validation_errors(state, context, concurrent, ordinary_plan=plan) == ()


def test_location_bound_task_is_rejected_when_class_location_is_incompatible():
    map_data = DEFAULT_CAMPUS_REGISTRY.get_campus_map("weijinlu")
    vocab = _task()
    resolution = resolve_location_in_campus(DEFAULT_CAMPUS_REGISTRY, "weijinlu", "图书馆")
    location = ExecutionLocation.from_resolution(resolution, ExecutionLocationSource.EXPLICIT_TASK_LOCATION)
    state = _state((vocab,), (_class(location="第九教学楼"),), ())
    context = ExecutionPlanContext(
        (ExecutableTaskBinding(vocab.task_ref, location, "generic"),),
        concurrency_authorizations=(_auth(),),
    )
    result = plan_explicit_concurrency(state, context, map_data=map_data)
    assert result.allocations == ()
    assert result.rejected_authorizations == context.concurrency_authorizations


def test_invariant_remains_fail_closed_for_unmatched_or_out_of_commitment_overlap():
    vocab = _task(total=30)
    state = _state((vocab,), (_class(),), ())
    context = ExecutionPlanContext(
        (ExecutableTaskBinding(vocab.task_ref, activity_kind="generic"),),
        concurrency_authorizations=(_auth(minutes=30),),
    )
    invalid = ConcurrentAllocation(
        vocab.task_ref, "day_commitment_class", _dt(20, 20), _dt(20, 50), 30,
        ConcurrentExecutionSource.EXPLICIT_USER_REQUEST,
    )
    assert "concurrent allocation exceeds commitment interval" in concurrency_validation_errors(
        state, context, (invalid,)
    )
    with pytest.raises(ValueError):
        ConcurrentExecutionAuthorization(vocab.task_ref, "day_commitment_class", 30, "inferred")


def test_class_prep_is_not_an_authorizable_concurrent_window():
    vocab = _task(total=30)
    state = _state((vocab,), (_class(),), ())
    context = ExecutionPlanContext(
        (ExecutableTaskBinding(vocab.task_ref, activity_kind="generic"),),
        concurrency_authorizations=(_auth(minutes=10),),
    )
    allocation = plan_explicit_concurrency(state, context).allocations[0]
    assert allocation.starts_at == _dt(19)
    assert allocation.ends_at == _dt(19, 10)
    assert allocation.starts_at >= _dt(19)  # never consumes 18:50--19:00 prep


def test_live_final_turn_accepts_only_valid_explicit_concurrency_and_presentation_marks_relation():
    vocab = _task(total=30)
    windows = (_window("w_before", _dt(18), _dt(18, 50)),)
    state = _state((vocab,), (_class(),), windows)
    context = ExecutionPlanContext(
        (ExecutableTaskBinding(vocab.task_ref, activity_kind="generic"),),
        concurrency_authorizations=(_auth(minutes=30, offset=20),),
    )
    concurrent = plan_explicit_concurrency(state, context).allocations
    plan = allocate_tasks_across_windows(
        state, concurrent_minutes_by_task_ref=concurrent_minutes_by_task(concurrent)
    )
    result = P2AgenticDayResult(
        state, state, None, None, plan, summarize_day_plan(plan, state), None, False, 0, (), (),
    )
    turn = P2SessionTurn(
        "mocked explicit concurrency", result, (), (), "key",
        execution_context=context, concurrent_allocations=concurrent,
    )

    class _Map:
        campus_id = "weijinlu"

    bundle = build_live_final_turn(turn, _Map())
    assert bundle.concurrent_allocations == concurrent
    lines = compact_plan_lines(plan, state, execution_context=context, concurrent_allocations=concurrent)
    assert "19:20–19:50：同时：背单词 30 分钟" in lines


def test_controller_planning_boundary_excludes_authorized_minutes_from_ordinary_allocation():
    """A live-like controller turn uses the same context-bound deduction."""
    vocab = _task(total=60)
    state = _state(
        (vocab,),
        (_class(),),
        (_window("w_before", _dt(18), _dt(18, 50)),),
    )
    context = ExecutionPlanContext(
        (ExecutableTaskBinding(vocab.task_ref, activity_kind="generic"),),
        concurrency_authorizations=(_auth(minutes=30, offset=20),),
    )

    def caller(system, user):
        if "day-plan-intent" in system:
            return '{"schema_version":"p2.day-plan-intent.v1","task_order":[],"include_low_attention":false,"task_estimates":[],"rationale":null}'
        return '{"schema_version":"p2.day-review.v1","decision":"accept","reason":"ok","suggested_task_order":null,"include_low_attention":null}'

    store = {"p4_execution_plan_context": context}
    session = P2SessionController(
        store,
        caller,
        map_data=DEFAULT_CAMPUS_REGISTRY.get_campus_map("weijinlu"),
        campus_id="weijinlu",
    )
    turn = session.start_day(state)
    assert turn.result.allocation_plan.planned_minutes_by_task == {vocab.task_ref: 30}
    assert [(item.starts_at, item.ends_at) for item in turn.concurrent_allocations] == [
        (_dt(19, 20), _dt(19, 50))
    ]


def test_reconciliation_and_new_context_do_not_keep_stale_authorizations():
    vocab = _task()
    state = _state((), (_class(),), ())
    context = ExecutionPlanContext(
        (ExecutableTaskBinding(vocab.task_ref, activity_kind="generic"),),
        concurrency_authorizations=(_auth(),),
    )
    reconciled = reconcile_execution_context(context, state)
    assert reconciled.bindings == ()
    assert reconciled.concurrency_authorizations == ()
    assert context.without_concurrency_authorization(vocab.task_ref).concurrency_authorizations == ()
