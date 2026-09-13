from datetime import datetime

import pytest

from src.p1_models import SourceKind
from src.p1_window_models import AvailabilityLevel, FixedCommitment
from src.p2_models import (
    MAX_HISTORY_LEN,
    DayPlanningState,
    DayWindow,
    TaskProgress,
    TaskState,
    append_history,
    make_window_ref,
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


def make_commitment(ref="c1", start=None, end=None, availability=AvailabilityLevel.UNAVAILABLE):
    return FixedCommitment(ref, ref, ref, start, end, None, availability, {}, ())


def make_window(now, **overrides):
    from datetime import timedelta

    values = dict(
        window_ref="day_window_001",
        starts_at=now,
        ends_at=now + timedelta(hours=1),
        availability=AvailabilityLevel.FULLY_AVAILABLE,
        safety_buffer_minutes=10,
        travel_minutes=0,
        next_commitment_ref=None,
        capacity_minutes=50,
    )
    values.update(overrides)
    return DayWindow(**values)


def make_state(now=None, **overrides):
    now = now or datetime(2026, 9, 1, 9, 0)
    task = make_task()
    window = make_window(now)
    values = dict(
        reference_datetime=now,
        now=now,
        day_end=datetime(2026, 9, 1, 22, 0),
        commitments=(),
        tasks=(task,),
        windows=(window,),
        active_window_ref="day_window_001",
        unresolved_commitment_refs=(),
        history=(),
    )
    values.update(overrides)
    return DayPlanningState(**values)


# ---------------------------------------------------------------------------
# TaskProgress 基本校验
# ---------------------------------------------------------------------------


def test_legal_active_task_without_total():
    task = make_task(total_minutes=None, total_source=None, completed_minutes=0)
    assert task.state == TaskState.ACTIVE
    assert task.remaining_minutes is None


def test_legal_partial_progress_task():
    task = make_task(total_minutes=120, completed_minutes=40)
    assert task.state == TaskState.ACTIVE
    assert task.remaining_minutes == 80


def test_remaining_is_none_when_total_unknown():
    task = make_task(total_minutes=None, completed_minutes=25)
    assert task.remaining_minutes is None


def test_auto_completed_when_completed_equals_total():
    task = make_task(completed_minutes=120)
    assert task.state == TaskState.COMPLETED
    assert task.remaining_minutes == 0


def test_auto_completed_when_completed_exceeds_total():
    task = make_task(completed_minutes=150)
    assert task.state == TaskState.COMPLETED
    assert task.remaining_minutes == 0


def test_remaining_never_negative():
    task = make_task(total_minutes=30, completed_minutes=40)
    assert task.remaining_minutes == 0


def test_bool_rejected_as_completed_minutes():
    with pytest.raises(ValueError):
        make_task(completed_minutes=True)


def test_float_rejected_as_completed_minutes():
    with pytest.raises(ValueError):
        make_task(completed_minutes=1.5)
    with pytest.raises(ValueError):
        make_task(completed_minutes=float("nan"))


def test_bool_rejected_as_total_minutes():
    with pytest.raises(ValueError):
        make_task(total_minutes=True)


def test_float_rejected_as_total_minutes():
    with pytest.raises(ValueError):
        make_task(total_minutes=120.0)
    with pytest.raises(ValueError):
        make_task(total_minutes=float("nan"))


def test_zero_total_rejected():
    with pytest.raises(ValueError):
        make_task(total_minutes=0)


def test_negative_total_rejected():
    with pytest.raises(ValueError):
        make_task(total_minutes=-10)


def test_negative_completed_rejected():
    with pytest.raises(ValueError):
        make_task(completed_minutes=-1)


def test_bool_rejected_as_minimum_slice():
    with pytest.raises(ValueError):
        make_task(minimum_slice_minutes=True)


def test_float_rejected_as_minimum_slice():
    with pytest.raises(ValueError):
        make_task(minimum_slice_minutes=30.0)


def test_zero_minimum_slice_rejected():
    with pytest.raises(ValueError):
        make_task(minimum_slice_minutes=0)


def test_negative_minimum_slice_rejected():
    with pytest.raises(ValueError):
        make_task(minimum_slice_minutes=-5)


def test_empty_task_ref_rejected():
    with pytest.raises(ValueError):
        make_task(task_ref="  ")


def test_empty_title_rejected():
    with pytest.raises(ValueError):
        make_task(title="")


def test_invalid_total_source_rejected():
    with pytest.raises(ValueError):
        make_task(total_source="bogus")


def test_invalid_state_rejected():
    with pytest.raises(ValueError):
        make_task(state="active")


def test_invalid_splittable_rejected():
    with pytest.raises(ValueError):
        make_task(is_splittable="yes")


# ---------------------------------------------------------------------------
# DayWindow 校验
# ---------------------------------------------------------------------------


def test_valid_day_window():
    now = datetime(2026, 9, 1, 9, 0)
    window = make_window(now)
    assert window.capacity_minutes == 50


def test_negative_capacity_rejected():
    now = datetime(2026, 9, 1, 9, 0)
    with pytest.raises(ValueError):
        make_window(now, capacity_minutes=-1)


def test_unavailable_day_window_rejected():
    now = datetime(2026, 9, 1, 9, 0)
    with pytest.raises(ValueError):
        make_window(now, availability=AvailabilityLevel.UNAVAILABLE)


def test_reversed_bounds_rejected():
    now = datetime(2026, 9, 1, 9, 0)
    with pytest.raises(ValueError):
        make_window(now, starts_at=now.replace(hour=11), ends_at=now.replace(hour=10))


def test_bool_travel_rejected():
    now = datetime(2026, 9, 1, 9, 0)
    with pytest.raises(ValueError):
        make_window(now, travel_minutes=True)


def test_float_capacity_rejected():
    now = datetime(2026, 9, 1, 9, 0)
    with pytest.raises(ValueError):
        make_window(now, capacity_minutes=50.0)


def test_negative_buffer_rejected():
    now = datetime(2026, 9, 1, 9, 0)
    with pytest.raises(ValueError):
        make_window(now, safety_buffer_minutes=-1)


# ---------------------------------------------------------------------------
# DayPlanningState 校验
# ---------------------------------------------------------------------------


def test_valid_state_construction():
    state = make_state()
    assert state.active_window_ref == "day_window_001"
    assert len(state.tasks) == 1


def test_duplicate_task_ref_rejected():
    task = make_task()
    with pytest.raises(ValueError):
        make_state(tasks=(task, task))


def test_duplicate_window_ref_rejected():
    now = datetime(2026, 9, 1, 9, 0)
    first = make_window(now)
    second = make_window(now.replace(hour=10), window_ref="day_window_001")
    with pytest.raises(ValueError):
        make_state(windows=(first, second))


def test_duplicate_commitment_ref_rejected():
    now = datetime(2026, 9, 1, 9, 0)
    same = make_commitment("dup", now.replace(hour=10), now.replace(hour=11, minute=30))
    with pytest.raises(ValueError):
        make_state(commitments=(same, same))


def test_active_window_ref_not_found_rejected():
    with pytest.raises(ValueError):
        make_state(active_window_ref="day_window_999")


def test_active_window_ref_not_containing_now_rejected():
    base = datetime(2026, 9, 1, 9, 0)
    window = make_window(base)
    with pytest.raises(ValueError):
        make_state(
            now=datetime(2026, 9, 1, 10, 30),
            windows=(window,),
            active_window_ref="day_window_001",
        )


def test_active_window_ref_required_when_window_contains_now():
    with pytest.raises(ValueError):
        make_state(active_window_ref=None)


def test_unsorted_windows_rejected():
    now = datetime(2026, 9, 1, 9, 0)
    late = make_window(now.replace(hour=11), window_ref="day_window_002")
    early = make_window(now.replace(hour=8), window_ref="day_window_001")
    with pytest.raises(ValueError):
        make_state(windows=(late, early))


def test_overlapping_windows_rejected():
    now = datetime(2026, 9, 1, 9, 0)
    first = make_window(now)
    second = make_window(now.replace(hour=9, minute=30), window_ref="day_window_002")
    with pytest.raises(ValueError):
        make_state(windows=(first, second))


def test_history_too_long_rejected():
    with pytest.raises(ValueError):
        make_state(history=tuple("e{}".format(i) for i in range(MAX_HISTORY_LEN + 1)))


def test_history_entries_must_be_strings():
    with pytest.raises(ValueError):
        make_state(history=(123,))


def test_now_must_be_before_day_end():
    with pytest.raises(ValueError):
        make_state(now=datetime(2026, 9, 1, 23, 0))


# ---------------------------------------------------------------------------
# history 容器语义
# ---------------------------------------------------------------------------


def test_append_history_adds_entry():
    updated = append_history(("a", "b"), "c")
    assert updated == ("a", "b", "c")


def test_append_history_trims_oldest():
    history = tuple("e{}".format(i) for i in range(MAX_HISTORY_LEN))
    updated = append_history(history, "newest")
    assert len(updated) == MAX_HISTORY_LEN
    assert updated[-1] == "newest"
    assert updated[0] == "e1"
    assert "e0" not in updated


def test_append_history_rejects_invalid_entries():
    with pytest.raises(ValueError):
        append_history(("a",), "")
    with pytest.raises(ValueError):
        append_history(("a", 1), "b")


def test_make_window_ref_sequence():
    assert make_window_ref(0) == "day_window_001"
    assert make_window_ref(9) == "day_window_010"
    assert make_window_ref(99) == "day_window_100"


def test_make_window_ref_rejects_invalid_index():
    with pytest.raises(ValueError):
        make_window_ref(-1)
    with pytest.raises(ValueError):
        make_window_ref(True)
