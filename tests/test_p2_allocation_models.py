import pytest

from src.p2_allocation_models import DayAllocationPlan, TaskAllocation, make_allocation_ref


def make_allocation(**overrides):
    values = dict(
        allocation_ref="allocation_001",
        task_ref="task-1",
        task_title="计组实验3",
        window_ref="day_window_001",
        planned_minutes=40,
        sequence_index=0,
        is_partial=True,
        remaining_before=120,
        remaining_after=80,
    )
    values.update(overrides)
    return TaskAllocation(**values)


def make_alloc(ref="allocation_001", window_ref="day_window_001", planned=40):
    return TaskAllocation(
        allocation_ref=ref,
        task_ref="task-1",
        task_title="计组实验3",
        window_ref=window_ref,
        planned_minutes=planned,
        sequence_index=0,
        is_partial=planned < 120,
        remaining_before=120,
        remaining_after=120 - planned,
    )


def make_plan(**overrides):
    alloc = make_alloc()
    values = dict(
        allocations=(alloc,),
        unallocated_task_refs=(),
        unused_capacity_by_window=(("day_window_001", 10),),
        current_window_ref="day_window_001",
        current_allocation_ref="allocation_001",
        later_allocations=(),
        warnings=(),
        total_planned_minutes=40,
    )
    values.update(overrides)
    return DayAllocationPlan(**values)


# ---------------------------------------------------------------------------
# TaskAllocation 校验
# ---------------------------------------------------------------------------


def test_valid_partial_allocation():
    allocation = make_allocation()
    assert allocation.is_partial is True
    assert allocation.remaining_after == 80


def test_valid_complete_allocation():
    allocation = make_allocation(planned_minutes=120, remaining_before=120, remaining_after=0, is_partial=False)
    assert allocation.is_partial is False


def test_is_partial_must_be_derived():
    with pytest.raises(ValueError):
        make_allocation(planned_minutes=120, remaining_before=120, remaining_after=0, is_partial=True)


def test_is_partial_true_required_for_partial():
    with pytest.raises(ValueError):
        make_allocation(is_partial=False)


def test_remaining_after_must_be_consistent():
    with pytest.raises(ValueError):
        make_allocation(remaining_after=90)


def test_planned_must_not_exceed_remaining_before():
    with pytest.raises(ValueError):
        make_allocation(planned_minutes=130, remaining_after=-10)


def test_planned_zero_rejected():
    with pytest.raises(ValueError):
        make_allocation(planned_minutes=0, remaining_after=120)


def test_planned_bool_rejected():
    with pytest.raises(ValueError):
        make_allocation(planned_minutes=True)


def test_planned_float_rejected():
    with pytest.raises(ValueError):
        make_allocation(planned_minutes=40.0)


def test_planned_negative_rejected():
    with pytest.raises(ValueError):
        make_allocation(planned_minutes=-5, remaining_after=125)


def test_sequence_index_bool_rejected():
    with pytest.raises(ValueError):
        make_allocation(sequence_index=True)


def test_sequence_index_negative_rejected():
    with pytest.raises(ValueError):
        make_allocation(sequence_index=-1)


def test_empty_refs_rejected():
    with pytest.raises(ValueError):
        make_allocation(allocation_ref="")
    with pytest.raises(ValueError):
        make_allocation(task_ref="  ")
    with pytest.raises(ValueError):
        make_allocation(task_title="")
    with pytest.raises(ValueError):
        make_allocation(window_ref="  ")


def test_negative_remaining_before_rejected():
    with pytest.raises(ValueError):
        make_allocation(remaining_before=-1)


def test_make_allocation_ref_sequence():
    assert make_allocation_ref(0) == "allocation_001"
    assert make_allocation_ref(9) == "allocation_010"
    assert make_allocation_ref(99) == "allocation_100"


def test_make_allocation_ref_rejects_invalid():
    with pytest.raises(ValueError):
        make_allocation_ref(-1)
    with pytest.raises(ValueError):
        make_allocation_ref(True)


# ---------------------------------------------------------------------------
# DayAllocationPlan 校验
# ---------------------------------------------------------------------------


def test_valid_plan():
    plan = make_plan()
    assert plan.current_allocation is not None
    assert plan.current_allocation.allocation_ref == "allocation_001"


def test_duplicate_allocation_ref_rejected():
    first = make_alloc("allocation_001", "day_window_001")
    second = make_alloc("allocation_001", "day_window_002")
    with pytest.raises(ValueError):
        make_plan(allocations=(first, second))


def test_total_planned_mismatch_rejected():
    with pytest.raises(ValueError):
        make_plan(total_planned_minutes=99)


def test_current_allocation_ref_missing_rejected():
    with pytest.raises(ValueError):
        make_plan(current_allocation_ref="allocation_999")


def test_current_allocation_ref_wrong_window_rejected():
    alloc = make_alloc("allocation_001", "day_window_002")
    with pytest.raises(ValueError):
        make_plan(allocations=(alloc,), current_allocation_ref="allocation_001")


def test_current_allocation_ref_without_current_window_rejected():
    with pytest.raises(ValueError):
        make_plan(current_window_ref=None)


def test_later_allocation_not_in_allocations_rejected():
    other = make_alloc("allocation_002", "day_window_002")
    with pytest.raises(ValueError):
        make_plan(later_allocations=(other,))


def test_later_allocation_in_current_window_rejected():
    alloc = make_alloc("allocation_001", "day_window_001")
    with pytest.raises(ValueError):
        make_plan(later_allocations=(alloc,))


def test_negative_unused_capacity_rejected():
    with pytest.raises(ValueError):
        make_plan(unused_capacity_by_window=(("day_window_001", -1),))


def test_empty_warning_rejected():
    with pytest.raises(ValueError):
        make_plan(warnings=("",))


def test_non_allocation_in_allocations_rejected():
    with pytest.raises(TypeError):
        make_plan(allocations=("not-an-allocation",))


def test_plan_summary_properties():
    first = make_alloc("allocation_001", "day_window_001", planned=50)
    second = make_alloc("allocation_002", "day_window_002", planned=70)
    plan = make_plan(
        allocations=(first, second),
        unused_capacity_by_window=(("day_window_001", 0), ("day_window_002", 10)),
        current_allocation_ref="allocation_001",
        later_allocations=(second,),
        total_planned_minutes=120,
    )
    assert plan.planned_minutes_by_task == {"task-1": 120}
    assert plan.planned_minutes_by_window == {"day_window_001": 50, "day_window_002": 70}
    assert plan.remaining_after_plan_by_task == {"task-1": 50}
    assert plan.current_allocation == first
