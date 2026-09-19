"""P4d-2 execution chunks: deterministic allocation, no real model calls."""

from datetime import datetime

from src.p1_models import SourceKind
from src.p1_window_models import AvailabilityLevel
from src.p2_allocator import allocate_tasks_across_windows
from src.p2_day_plan import compact_plan_lines
from src.p2_models import DayPlanningState, DayWindow, TaskProgress, TaskState
from src.p2_window_derivation import derive_active_window_ref
from src.p4_execution_context import ExecutableTaskBinding, ExecutionPlanContext, TaskExecutionProfile
from src.p4_execution_enrichment import preferred_chunk_overrides


def _dt(hour, minute=0):
    return datetime(2026, 9, 2, hour, minute)


def _window(ref, start, end, capacity):
    return DayWindow(ref, start, end, AvailabilityLevel.FULLY_AVAILABLE, 0, 0, None, capacity)


def _task(total=60, completed=0, splittable=True, minimum=15):
    return TaskProgress(
        "day_task_001", "整理资料", total, completed, SourceKind.AI_ESTIMATED,
        TaskState.ACTIVE, splittable, minimum,
    )


def _state(windows, task, now=None):
    now = now or _dt(14)
    windows = tuple(windows)
    return DayPlanningState(
        now, now, _dt(22), (), (task,), windows,
        derive_active_window_ref(windows, now), (), (),
    )


def test_task_execution_profile_is_task_ref_bound_and_drives_preferred_chunks():
    state = _state((_window("w1", _dt(14), _dt(14, 35), 35), _window("w2", _dt(16), _dt(16, 40), 40)), _task())
    profile = TaskExecutionProfile("day_task_001", True, 15, 30, False, "qwen_semantic")
    context = ExecutionPlanContext((ExecutableTaskBinding("day_task_001", execution_profile=profile),))
    plan = allocate_tasks_across_windows(
        state, preferred_chunk_by_task_ref=preferred_chunk_overrides(context, state)
    )
    assert [(item.window_ref, item.planned_minutes) for item in plan.allocations] == [("w1", 30), ("w2", 30)]
    assert plan.planned_minutes_by_task == {"day_task_001": 60}
    assert plan.unallocated_task_refs == ()
    # A plan is not a progress update.
    assert state.tasks[0].completed_minutes == 0


def test_partial_split_retains_true_remaining_work_without_marking_complete():
    state = _state((_window("w1", _dt(14), _dt(14, 30), 30), _window("w2", _dt(16), _dt(16, 20), 20)), _task())
    plan = allocate_tasks_across_windows(state)
    assert [item.planned_minutes for item in plan.allocations] == [30, 20]
    assert plan.remaining_after_plan_by_task == {"day_task_001": 10}
    assert plan.unallocated_task_refs == ("day_task_001",)
    assert state.tasks[0].remaining_minutes == 60


def test_short_fragment_below_profile_minimum_becomes_slack_not_garbage_work():
    state = _state((_window("w1", _dt(14), _dt(14, 7), 7),), _task())
    plan = allocate_tasks_across_windows(state)
    assert plan.allocations == ()
    assert plan.unallocated_task_refs == ("day_task_001",)


def test_split_task_finishes_before_later_ordered_task_can_begin():
    first = _task(total=60)
    second = TaskProgress("day_task_002", "背单词", 20, 0, SourceKind.USER_STATED, TaskState.ACTIVE, True, 15)
    windows = (_window("w1", _dt(14), _dt(14, 35), 35), _window("w2", _dt(16), _dt(17), 60))
    state = DayPlanningState(_dt(14), _dt(14), _dt(22), (), (first, second), windows, "w1", (), ())
    plan = allocate_tasks_across_windows(state, task_order=("day_task_001", "day_task_002"))
    assert [(item.task_ref, item.planned_minutes) for item in plan.allocations] == [
        ("day_task_001", 35), ("day_task_001", 25), ("day_task_002", 20),
    ]


def test_single_session_profile_remains_indivisible():
    state = _state((_window("w1", _dt(14), _dt(14, 30), 30), _window("w2", _dt(16), _dt(16, 40), 40)), _task(total=40, splittable=False, minimum=None))
    profile = TaskExecutionProfile("day_task_001", False, 40, 40, True, "user_explicit")
    context = ExecutionPlanContext((ExecutableTaskBinding("day_task_001", execution_profile=profile),))
    plan = allocate_tasks_across_windows(state, preferred_chunk_by_task_ref={})
    assert len(plan.allocations) == 1 and plan.allocations[0].window_ref == "w2"
    assert context.binding_for("day_task_001").execution_profile.requires_single_session


def test_real_progress_limits_next_round_to_remaining_work():
    state = _state((_window("w1", _dt(14), _dt(15), 60),), _task(total=60, completed=30))
    plan = allocate_tasks_across_windows(state)
    assert plan.total_planned_minutes == 30
    assert plan.allocations[0].remaining_before == 30


def test_feedback_single_session_supersedes_old_split_preferences():
    from src.p4_execution_enrichment import reconcile_execution_context
    from src.p2_session import P2SessionController
    from src.p4_execution_context import EXECUTION_PLAN_CONTEXT_KEY
    state=_state((_window('w1',_dt(14),_dt(16),120),),_task(splittable=False,minimum=None))
    profile=TaskExecutionProfile('day_task_001',True,15,30,False,'qwen_semantic')
    context=ExecutionPlanContext((ExecutableTaskBinding('day_task_001',execution_profile=profile),))
    # Even before reconciliation a stale soft preference cannot override the
    # canonical task's hard indivisibility.
    assert preferred_chunk_overrides(context,state)=={}
    updated=reconcile_execution_context(context,state)
    assert not updated.binding_for('day_task_001').execution_profile.splittable
    session=P2SessionController({EXECUTION_PLAN_CONTEXT_KEY:updated},lambda *args: '')
    for fragmentation in ('low','medium','high'):
        preferences=session._strategy_chunk_overrides(state,fragmentation)
        assert preferences=={}
        plan=allocate_tasks_across_windows(state,preferred_chunk_by_task_ref=preferences)
        assert len(plan.allocations)==1 and plan.total_planned_minutes==60
    assert state.tasks[0].completed_minutes==0


def test_feedback_raised_minimum_updates_preference_without_changing_work():
    from src.p4_execution_enrichment import reconcile_execution_context
    state=_state((_window('w1',_dt(14),_dt(16),120),),_task(minimum=45))
    profile=TaskExecutionProfile('day_task_001',True,15,30,False,'qwen_semantic')
    context=ExecutionPlanContext((ExecutableTaskBinding('day_task_001',execution_profile=profile),))
    updated=reconcile_execution_context(context,state)
    assert preferred_chunk_overrides(updated,state)=={'day_task_001':45}
    assert context.binding_for('day_task_001').execution_profile.minimum_chunk_minutes==15


def test_presentation_marks_only_later_chunk_as_continuation_and_does_not_repeat_ai_badge():
    state = _state((_window("w1", _dt(14), _dt(14, 30), 30), _window("w2", _dt(16), _dt(16, 30), 30)), _task(), now=_dt(13))
    plan = allocate_tasks_across_windows(state)
    lines = compact_plan_lines(plan, state)
    rendered = "\n".join(lines)
    assert "整理资料 30 分钟（AI暂估）" in rendered
    assert "再做整理资料 30 分钟" in rendered
    assert rendered.count("AI暂估") == 1
