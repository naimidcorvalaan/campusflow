from datetime import datetime

import pytest

from src.p1_models import SourceKind
from src.p1_window_models import AvailabilityLevel
from src.p2_agentic_models import (
    RECONCILIATION_SCHEMA_VERSION,
    LifecycleAction,
    ReconciliationResult,
    ReconciliationUpdate,
    TotalSourceChoice,
)
from src.p2_models import DayPlanningState, DayWindow, TaskProgress, TaskState
from src.p2_state_reconciler import apply_reconciliation
from src.p2_window_derivation import derive_active_window_ref


def dt(hour, minute=0):
    return datetime(2026, 9, 1, hour, minute)


def make_task(
    ref="day_task_001",
    title="计组实验3",
    total=120,
    completed=0,
    state=TaskState.ACTIVE,
    splittable=True,
    min_slice=30,
    source=SourceKind.AI_ESTIMATED,
):
    return TaskProgress(ref, title, total, completed, source, state, splittable, min_slice)


def make_window(now):
    return DayWindow(
        "day_window_001",
        now,
        dt(10),
        AvailabilityLevel.FULLY_AVAILABLE,
        0,
        0,
        None,
        60,
    )


def build_state(tasks, now=None, windows=None):
    now = now or dt(9)
    windows = windows if windows is not None else (make_window(now),)
    active = derive_active_window_ref(tuple(windows), now)
    return DayPlanningState(now, now, dt(22), (), tuple(tasks), tuple(windows), active, (), ())


def make_update(**overrides):
    values = dict(
        target_task_ref="day_task_001",
        new_task_title=None,
        progress_delta_minutes=None,
        set_total_minutes=None,
        set_total_source=None,
        lifecycle_action=LifecycleAction.NONE,
        is_splittable=None,
        minimum_slice_minutes=None,
    )
    values.update(overrides)
    return ReconciliationUpdate(**values)


def make_result(*updates, questions=()):
    return ReconciliationResult(RECONCILIATION_SCHEMA_VERSION, tuple(updates), tuple(questions))


def test_progress_delta_applies():
    state = build_state((make_task(completed=50),))
    result = make_result(make_update(progress_delta_minutes=30))
    applied = apply_reconciliation(state, result)
    updated = applied.state.tasks[0]
    assert updated.completed_minutes == 80
    assert updated.remaining_minutes == 40


def test_skip_today():
    state = build_state((make_task(),))
    applied = apply_reconciliation(
        state, make_result(make_update(lifecycle_action=LifecycleAction.SKIP_TODAY))
    )
    assert applied.state.tasks[0].state == TaskState.SKIPPED_TODAY


def test_abandon():
    state = build_state((make_task(),))
    applied = apply_reconciliation(
        state, make_result(make_update(lifecycle_action=LifecycleAction.ABANDON))
    )
    assert applied.state.tasks[0].state == TaskState.ABANDONED


def test_complete_raises_completed_to_total():
    state = build_state((make_task(total=120, completed=50),))
    applied = apply_reconciliation(
        state, make_result(make_update(lifecycle_action=LifecycleAction.COMPLETE))
    )
    updated = applied.state.tasks[0]
    assert updated.state == TaskState.COMPLETED
    assert updated.completed_minutes == 120


def test_new_task_ref_after_001_002():
    state = build_state(
        (make_task(ref="day_task_001"), make_task(ref="day_task_002", title="实验报告", total=60))
    )
    applied = apply_reconciliation(
        state, make_result(make_update(target_task_ref=None, new_task_title="整理操作系统实验"))
    )
    assert applied.new_task_refs == ("day_task_003",)
    assert applied.state.tasks[-1].task_ref == "day_task_003"


def test_new_task_ref_skips_gap():
    state = build_state(
        (make_task(ref="day_task_001"), make_task(ref="day_task_003", title="物理实验报告"))
    )
    applied = apply_reconciliation(
        state, make_result(make_update(target_task_ref=None, new_task_title="整理操作系统实验"))
    )
    assert applied.new_task_refs == ("day_task_004",)


def test_multiple_new_tasks_stable():
    state = build_state((make_task(ref="day_task_001"),))
    applied = apply_reconciliation(
        state,
        make_result(
            make_update(target_task_ref=None, new_task_title="整理操作系统实验"),
            make_update(target_task_ref=None, new_task_title="背英语单词"),
        ),
    )
    assert applied.new_task_refs == ("day_task_002", "day_task_003")
    assert [task.title for task in applied.state.tasks] == [
        "计组实验3",
        "整理操作系统实验",
        "背英语单词",
    ]


def test_unknown_target_ref_warns_and_keeps_state():
    state = build_state((make_task(),))
    applied = apply_reconciliation(
        state, make_result(make_update(target_task_ref="day_task_999", progress_delta_minutes=30))
    )
    assert applied.warnings
    assert applied.state.tasks[0].completed_minutes == 0
    assert "day_task_999" in applied.warnings[0]


def test_user_text_source_mapping():
    state = build_state((make_task(total=120),))
    applied = apply_reconciliation(
        state,
        make_result(
            make_update(
                set_total_minutes=150, set_total_source=TotalSourceChoice.USER_TEXT
            )
        ),
    )
    updated = applied.state.tasks[0]
    assert updated.total_minutes == 150
    assert updated.total_source == SourceKind.AI_EXTRACTED_FROM_USER_TEXT
    assert updated.completed_minutes == 0
    assert updated.remaining_minutes == 150


def test_ai_estimate_source_mapping():
    state = build_state((make_task(total=None, source=None),))
    applied = apply_reconciliation(
        state,
        make_result(
            make_update(
                set_total_minutes=100, set_total_source=TotalSourceChoice.AI_ESTIMATE
            )
        ),
    )
    updated = applied.state.tasks[0]
    assert updated.total_minutes == 100
    assert updated.total_source == SourceKind.AI_ESTIMATED


def test_attributes_filled_only_when_provided():
    state = build_state((make_task(splittable=None, min_slice=None),))
    applied = apply_reconciliation(
        state,
        make_result(make_update(is_splittable=True, minimum_slice_minutes=15)),
    )
    updated = applied.state.tasks[0]
    assert updated.is_splittable is True
    assert updated.minimum_slice_minutes == 15


def test_input_state_immutable():
    state = build_state((make_task(completed=50),))
    before = state
    apply_reconciliation(
        state, make_result(make_update(progress_delta_minutes=30))
    )
    assert state is before
    assert state.tasks[0].completed_minutes == 50


def test_questions_preserved():
    state = build_state((make_task(),))
    applied = apply_reconciliation(
        state, make_result(questions=("你说的是哪个实验？",))
    )
    assert applied.questions == ("你说的是哪个实验？",)


def test_new_task_with_progress_and_total():
    state = build_state((make_task(ref="day_task_001"),))
    applied = apply_reconciliation(
        state,
        make_result(
            make_update(
                target_task_ref=None,
                new_task_title="整理操作系统实验",
                progress_delta_minutes=20,
                set_total_minutes=100,
                set_total_source=TotalSourceChoice.USER_TEXT,
            )
        ),
    )
    new_task = applied.state.tasks[-1]
    assert new_task.task_ref == "day_task_002"
    assert new_task.completed_minutes == 20
    assert new_task.total_minutes == 100
    assert new_task.remaining_minutes == 80


def test_new_task_lifecycle_skip():
    state = build_state((make_task(ref="day_task_001"),))
    applied = apply_reconciliation(
        state,
        make_result(
            make_update(
                target_task_ref=None,
                new_task_title="背单词",
                lifecycle_action=LifecycleAction.SKIP_TODAY,
            )
        ),
    )
    assert applied.state.tasks[-1].state == TaskState.SKIPPED_TODAY


def test_applied_entries_deterministic():
    state = build_state((make_task(completed=50),))
    applied = apply_reconciliation(
        state, make_result(make_update(progress_delta_minutes=30))
    )
    assert applied.applied_entries == ("计组实验3：又完成30分钟",)


def test_invalid_result_type_rejected():
    with pytest.raises(TypeError):
        apply_reconciliation(build_state((make_task(),)), "not a result")

def test_questions_suppress_updates():
    state = build_state((make_task(completed=50),))
    applied = apply_reconciliation(
        state,
        make_result(
            make_update(progress_delta_minutes=30), questions=("哪个实验？",)
        ),
    )
    assert applied.state.tasks[0].completed_minutes == 50
    assert applied.questions == ("哪个实验？",)
    assert applied.applied_entries == ()


def test_resume_today_reactivates_skipped():
    state = build_state((make_task(),))
    skipped = apply_reconciliation(
        state, make_result(make_update(lifecycle_action=LifecycleAction.SKIP_TODAY))
    ).state.tasks[0]
    assert skipped.state == TaskState.SKIPPED_TODAY
    applied = apply_reconciliation(
        state, make_result(make_update(lifecycle_action=LifecycleAction.RESUME_TODAY))
    )
    assert applied.state.tasks[0].state == TaskState.ACTIVE
    assert applied.applied_entries == ("计组实验3：今天恢复安排",)


def test_resume_today_noop_on_active():
    state = build_state((make_task(),))
    applied = apply_reconciliation(
        state, make_result(make_update(lifecycle_action=LifecycleAction.RESUME_TODAY))
    )
    assert applied.state.tasks[0].state == TaskState.ACTIVE


def test_resume_today_does_not_resurrect_abandoned():
    state = build_state((make_task(),))
    abandoned = apply_reconciliation(
        state, make_result(make_update(lifecycle_action=LifecycleAction.ABANDON))
    ).state.tasks[0]
    abandoned_state = build_state((abandoned,))
    applied = apply_reconciliation(
        abandoned_state, make_result(make_update(lifecycle_action=LifecycleAction.RESUME_TODAY))
    )
    assert applied.state.tasks[0].state == TaskState.ABANDONED


def test_resume_today_does_not_resurrect_completed():
    state = build_state((make_task(completed=120),))
    applied = apply_reconciliation(
        state, make_result(make_update(lifecycle_action=LifecycleAction.RESUME_TODAY))
    )
    assert applied.state.tasks[0].state == TaskState.COMPLETED
