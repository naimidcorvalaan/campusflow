import pytest

from src.p1_models import SourceKind
from src.p2_models import TaskProgress, TaskState
from src.p2_task_progress import (
    apply_progress_report,
    apply_total_update,
    is_task_schedulable_today,
    mark_abandoned,
    mark_completed,
    mark_skipped_today,
    resume_today,
)


def make_task(**overrides):
    values = dict(
        task_ref="task-1",
        title="计组实验3",
        total_minutes=120,
        completed_minutes=0,
        total_source=SourceKind.AI_ESTIMATED,
        state=TaskState.ACTIVE,
        is_splittable=True,
        minimum_slice_minutes=30,
    )
    values.update(overrides)
    return TaskProgress(**values)


# ---------------------------------------------------------------------------
# apply_total_update
# ---------------------------------------------------------------------------


def test_apply_total_update_preserves_completed():
    task = make_task(total_minutes=120, completed_minutes=40)
    updated = apply_total_update(task, 150, SourceKind.USER_CONFIRMED)
    assert updated.total_minutes == 150
    assert updated.completed_minutes == 40
    assert updated.remaining_minutes == 110
    assert updated.total_source == SourceKind.USER_CONFIRMED
    assert updated.state == TaskState.ACTIVE
    assert task.total_minutes == 120


def test_apply_total_update_smaller_total_auto_completes():
    task = make_task(total_minutes=120, completed_minutes=40)
    updated = apply_total_update(task, 30, SourceKind.USER_CONFIRMED)
    assert updated.completed_minutes == 40
    assert updated.remaining_minutes == 0
    assert updated.state == TaskState.COMPLETED


def test_apply_total_update_rejects_invalid_total():
    task = make_task()
    for bad in (0, -5, True, 120.0, "120"):
        with pytest.raises(ValueError):
            apply_total_update(task, bad, SourceKind.USER_CONFIRMED)


def test_apply_total_update_rejects_invalid_source():
    task = make_task()
    with pytest.raises(ValueError):
        apply_total_update(task, 150, "bogus")


# ---------------------------------------------------------------------------
# apply_progress_report
# ---------------------------------------------------------------------------


def test_apply_progress_report_accumulates():
    task = make_task(completed_minutes=50)
    updated = apply_progress_report(task, 30)
    assert updated.completed_minutes == 80
    assert updated.remaining_minutes == 40
    assert updated.state == TaskState.ACTIVE


def test_apply_progress_report_auto_completes():
    task = make_task(completed_minutes=90)
    updated = apply_progress_report(task, 30)
    assert updated.completed_minutes == 120
    assert updated.state == TaskState.COMPLETED
    assert updated.remaining_minutes == 0


def test_apply_progress_report_with_unknown_total():
    task = make_task(total_minutes=None, total_source=None, completed_minutes=0)
    updated = apply_progress_report(task, 25)
    assert updated.completed_minutes == 25
    assert updated.state == TaskState.ACTIVE
    assert updated.remaining_minutes is None


def test_apply_progress_report_rejects_non_positive():
    task = make_task()
    for bad in (0, -5, True, 1.5, "10"):
        with pytest.raises(ValueError):
            apply_progress_report(task, bad)


def test_progress_report_keeps_skip_state():
    task = mark_skipped_today(make_task(completed_minutes=10))
    updated = apply_progress_report(task, 30)
    assert updated.completed_minutes == 40
    assert updated.state == TaskState.SKIPPED_TODAY


# ---------------------------------------------------------------------------
# mark_skipped_today
# ---------------------------------------------------------------------------


def test_mark_skipped_today_active():
    task = make_task(completed_minutes=20)
    updated = mark_skipped_today(task)
    assert updated.state == TaskState.SKIPPED_TODAY
    assert updated.completed_minutes == 20
    assert updated.total_minutes == 120


def test_mark_skipped_today_does_not_resurrect_completed():
    task = make_task(completed_minutes=120)
    updated = mark_skipped_today(task)
    assert updated.state == TaskState.COMPLETED


def test_mark_skipped_today_does_not_resurrect_abandoned():
    task = mark_abandoned(make_task())
    updated = mark_skipped_today(task)
    assert updated.state == TaskState.ABANDONED


# ---------------------------------------------------------------------------
# mark_abandoned
# ---------------------------------------------------------------------------


def test_mark_abandoned_active():
    updated = mark_abandoned(make_task())
    assert updated.state == TaskState.ABANDONED


def test_mark_abandoned_from_skipped():
    skipped = mark_skipped_today(make_task())
    updated = mark_abandoned(skipped)
    assert updated.state == TaskState.ABANDONED


def test_mark_abandoned_keeps_completed():
    task = make_task(completed_minutes=120)
    updated = mark_abandoned(task)
    assert updated.state == TaskState.COMPLETED


# ---------------------------------------------------------------------------
# mark_completed
# ---------------------------------------------------------------------------


def test_mark_completed_raises_completed_to_total():
    task = make_task(total_minutes=120, completed_minutes=40)
    updated = mark_completed(task)
    assert updated.state == TaskState.COMPLETED
    assert updated.completed_minutes == 120
    assert updated.remaining_minutes == 0


def test_mark_completed_preserves_completed_without_total():
    task = make_task(total_minutes=None, total_source=None, completed_minutes=25)
    updated = mark_completed(task)
    assert updated.state == TaskState.COMPLETED
    assert updated.completed_minutes == 25


def test_mark_completed_does_not_clamp_over_completed():
    task = make_task(total_minutes=30, completed_minutes=40)
    assert task.state == TaskState.COMPLETED
    updated = mark_completed(task)
    assert updated.completed_minutes == 40
    assert updated.total_minutes == 30


# ---------------------------------------------------------------------------
# is_task_schedulable_today
# ---------------------------------------------------------------------------


def test_only_active_is_schedulable():
    assert is_task_schedulable_today(make_task()) is True
    assert is_task_schedulable_today(make_task(completed_minutes=120)) is False
    assert is_task_schedulable_today(mark_skipped_today(make_task())) is False
    assert is_task_schedulable_today(mark_abandoned(make_task())) is False


def test_partial_progress_still_schedulable():
    task = make_task(total_minutes=120, completed_minutes=80)
    assert is_task_schedulable_today(task) is True


# ---------------------------------------------------------------------------
# resume_today
# ---------------------------------------------------------------------------


def test_resume_today_reactivates_skipped():
    task = mark_skipped_today(make_task(completed_minutes=20))
    updated = resume_today(task)
    assert updated.state == TaskState.ACTIVE
    assert updated.completed_minutes == 20
    assert updated.total_minutes == 120


def test_resume_today_noop_on_active():
    task = make_task(completed_minutes=20)
    updated = resume_today(task)
    assert updated is task
    assert updated.state == TaskState.ACTIVE


def test_resume_today_does_not_resurrect_completed():
    task = make_task(completed_minutes=120)
    updated = resume_today(task)
    assert updated.state == TaskState.COMPLETED


def test_resume_today_does_not_resurrect_abandoned():
    task = mark_abandoned(make_task())
    updated = resume_today(task)
    assert updated.state == TaskState.ABANDONED
