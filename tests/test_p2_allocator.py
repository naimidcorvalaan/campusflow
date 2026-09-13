from datetime import datetime

import pytest

from src.p1_models import SourceKind
from src.p1_window_models import AvailabilityLevel, FixedCommitment
from src.p2_allocator import (
    DEFAULT_MINIMUM_SLICE_MINUTES,
    WARNING_LOW_ATTENTION,
    WARNING_UNKNOWN_DURATION,
    allocate_tasks_across_windows,
)
from src.p2_models import DayPlanningState, DayWindow, TaskProgress, TaskState
from src.p2_task_progress import mark_abandoned, mark_skipped_today
from src.p2_window_derivation import derive_active_window_ref, derive_day_state


def dt(hour, minute=0):
    return datetime(2026, 9, 1, hour, minute)


def window(ref, start, end, capacity, availability=AvailabilityLevel.FULLY_AVAILABLE):
    return DayWindow(ref, start, end, availability, 0, 0, None, capacity)


def make_task(
    ref="task-1",
    title="计组实验3",
    total=120,
    completed=0,
    state=TaskState.ACTIVE,
    splittable=True,
    minimum_slice=30,
):
    return TaskProgress(
        ref, title, total, completed, SourceKind.AI_ESTIMATED, state, splittable, minimum_slice
    )


def build_state(windows, tasks, now=None):
    now = now or dt(9)
    active = derive_active_window_ref(tuple(windows), now)
    return DayPlanningState(now, now, dt(22), (), tuple(tasks), tuple(windows), active, (), ())


def plan_of(state, **kwargs):
    return allocate_tasks_across_windows(state, **kwargs)


# ---------------------------------------------------------------------------
# P3e-final：短碎片窗口任务
# ---------------------------------------------------------------------------


def test_short_fragment_window_schedules_when_min_slice_small():
    """available=7、splittable=true、min_slice=5：必须安排（不设全局10分钟门槛）。"""
    state = build_state(
        (window("w1", dt(15, 13), dt(15, 20), 7),),
        (make_task(total=30, splittable=True, minimum_slice=5),),
    )
    plan = plan_of(state)
    assert plan.total_planned_minutes == 7
    assert plan.allocations[0].planned_minutes == 7


def test_short_fragment_window_8_minutes_schedules():
    """available=8、splittable=true、min_slice=5：安排。"""
    state = build_state(
        (window("w1", dt(15, 12), dt(15, 20), 8),),
        (make_task(total=30, splittable=True, minimum_slice=5),),
    )
    plan = plan_of(state)
    assert plan.total_planned_minutes == 8
    assert plan.allocations[0].planned_minutes == 8


def test_short_fragment_window_respects_model_min_slice_10():
    """available=7、splittable=true、min_slice=10：尊重模型，不安排。"""
    state = build_state(
        (window("w1", dt(15, 13), dt(15, 20), 7),),
        (make_task(total=30, splittable=True, minimum_slice=10),),
    )
    plan = plan_of(state)
    assert plan.total_planned_minutes == 0
    assert plan.unallocated_task_refs == ("task-1",)


# ---------------------------------------------------------------------------
# 基础分配
# ---------------------------------------------------------------------------


def test_single_task_fits_window():
    state = build_state((window("w1", dt(9), dt(10), 50),), (make_task(total=40),))
    plan = plan_of(state)
    assert len(plan.allocations) == 1
    allocation = plan.allocations[0]
    assert allocation.planned_minutes == 40
    assert allocation.is_partial is False
    assert allocation.remaining_after == 0
    assert dict(plan.unused_capacity_by_window) == {"w1": 10}


def test_single_task_fits_exactly():
    state = build_state((window("w1", dt(9), dt(10), 50),), (make_task(total=50),))
    plan = plan_of(state)
    assert plan.allocations[0].planned_minutes == 50
    assert dict(plan.unused_capacity_by_window) == {"w1": 0}


def test_effective_duration_override_consumes_the_round_duration_not_task_total():
    state = build_state((window("w1", dt(9), dt(10), 60),), (make_task(total=30),))
    plan = plan_of(state, effective_duration_by_task_ref={"task-1": 40})
    assert plan.total_planned_minutes == 40
    assert plan.allocations[0].remaining_before == 40
    assert state.tasks[0].total_minutes == 30


def test_effective_duration_override_honors_explicit_twenty_minutes():
    state = build_state((window("w1", dt(9), dt(10), 60),), (make_task(total=120),))
    assert plan_of(state, effective_duration_by_task_ref={"task-1": 20}).total_planned_minutes == 20


def test_latest_end_caps_splittable_task_and_keeps_residual_unallocated():
    state = build_state(
        (
            window("w1", dt(9), dt(10), 60),
            window("w2", dt(10), dt(12), 120),
        ),
        (make_task(total=180, splittable=True, minimum_slice=5),),
    )
    plan = plan_of(state, latest_end_by_task_ref={"task-1": dt(9, 40)})
    assert [(item.window_ref, item.planned_minutes) for item in plan.allocations] == [("w1", 40)]
    assert plan.unallocated_task_refs == ("task-1",)
    assert plan.allocations[0].remaining_after == 140


def test_latest_end_does_not_split_an_unsplittable_location_task():
    state = build_state(
        (window("w1", dt(9), dt(11), 120),),
        (make_task(total=60, splittable=False, minimum_slice=None),),
    )
    plan = plan_of(state, latest_end_by_task_ref={"task-1": dt(9, 40)})
    assert plan.allocations == ()
    assert plan.unallocated_task_refs == ("task-1",)


def test_missing_effective_duration_override_preserves_existing_allocator_behavior():
    state = build_state((window("w1", dt(9), dt(10), 60),), (make_task(total=30),))
    assert plan_of(state).total_planned_minutes == 30


def test_effective_duration_override_respects_progress_and_inactive_lifecycle():
    active = make_task(ref="active", total=30, completed=10)
    skipped = make_task(ref="skipped", total=30, state=TaskState.SKIPPED_TODAY)
    state = build_state((window("w1", dt(9), dt(11), 120),), (active, skipped))
    plan = plan_of(state, effective_duration_by_task_ref={"active": 40, "skipped": 40})
    assert [(item.task_ref, item.planned_minutes) for item in plan.allocations] == [("active", 30)]


def test_effective_duration_override_keeps_existing_splittable_rules():
    state = build_state(
        (window("w1", dt(9), dt(9, 20), 20), window("w2", dt(10), dt(10, 20), 20)),
        (make_task(total=120, splittable=True, minimum_slice=15),),
    )
    plan = plan_of(state, effective_duration_by_task_ref={"task-1": 40})
    assert [(item.window_ref, item.planned_minutes) for item in plan.allocations] == [("w1", 20), ("w2", 20)]


def test_preferred_meal_duration_compresses_to_one_safe_contiguous_slice():
    """P4 default meal prefers 40 minutes but may safely use 20--39."""
    state = build_state(
        (window("w1", dt(16, 4), dt(16, 34), 30), window("w2", dt(16, 40), dt(16, 49), 9)),
        (make_task(total=120, splittable=True, minimum_slice=5),),
        now=dt(16, 4),
    )
    plan = plan_of(
        state,
        effective_duration_by_task_ref={"task-1": 40},
        protected_duration_by_task_ref={"task-1": 40},
    )
    assert [item.planned_minutes for item in plan.allocations] == [30]
    assert plan.unallocated_task_refs == ("task-1",)


def test_protected_effective_duration_allocates_full_single_block_when_it_fits():
    state = build_state(
        (window("w1", dt(16), dt(17), 45),),
        (make_task(total=120, splittable=True, minimum_slice=5),),
        now=dt(16),
    )
    plan = plan_of(
        state,
        effective_duration_by_task_ref={"task-1": 40},
        protected_duration_by_task_ref={"task-1": 40},
    )
    assert [(item.task_ref, item.planned_minutes) for item in plan.allocations] == [("task-1", 40)]
    assert plan.unallocated_task_refs == ()


# ---------------------------------------------------------------------------
# 场景 A：计组实验跨两个窗口
# ---------------------------------------------------------------------------


def test_scenario_a_splittable_task_across_two_windows():
    state = build_state(
        (window("w1", dt(9), dt(9, 50), 50), window("w2", dt(10), dt(11, 20), 80)),
        (make_task(total=120, completed=0, splittable=True, minimum_slice=30),),
    )
    plan = plan_of(state)
    assert [(a.window_ref, a.planned_minutes) for a in plan.allocations] == [
        ("w1", 50),
        ("w2", 70),
    ]
    assert plan.total_planned_minutes == 120
    assert plan.allocations[0].is_partial is True
    assert plan.allocations[1].is_partial is False
    assert plan.allocations[1].remaining_after == 0
    assert dict(plan.unused_capacity_by_window) == {"w1": 0, "w2": 10}
    assert plan.unallocated_task_refs == ()


def test_scenario_a_derived_windows_product_check():
    commitments = (
        FixedCommitment("class", "class", "class", dt(10), dt(11, 30), None, AvailabilityLevel.UNAVAILABLE, {}, ()),
        FixedCommitment("lab", "lab", "lab", dt(14), dt(15, 30), None, AvailabilityLevel.UNAVAILABLE, {}, ()),
    )
    state = derive_day_state(dt(9), dt(22), commitments, (make_task(total=120),), 10, {"class": 15, "lab": 15})
    plan = plan_of(state)
    assert plan.total_planned_minutes == 120
    assert plan.allocations[0].is_partial is True
    assert plan.allocations[-1].is_partial is False
    assert plan.allocations[-1].remaining_after == 0
    assert plan.unallocated_task_refs == ()


# ---------------------------------------------------------------------------
# 场景 B：已经做 80 分钟
# ---------------------------------------------------------------------------


def test_scenario_b_completed_progress_limits_plan():
    state = build_state((window("w1", dt(9), dt(10, 30), 150),), (make_task(total=120, completed=80),))
    plan = plan_of(state)
    assert sum(a.planned_minutes for a in plan.allocations) == 40
    assert plan.allocations[0].planned_minutes == 40
    assert plan.allocations[0].remaining_before == 40
    assert plan.unallocated_task_refs == ()


# ---------------------------------------------------------------------------
# 场景 C：不可拆分任务
# ---------------------------------------------------------------------------


def test_scenario_c_indivisible_skips_small_window():
    state = build_state(
        (window("w1", dt(9), dt(9, 40), 40), window("w2", dt(10), dt(11, 10), 70)),
        (make_task(total=60, splittable=False, minimum_slice=60),),
    )
    plan = plan_of(state)
    assert [(a.window_ref, a.planned_minutes) for a in plan.allocations] == [("w2", 60)]
    assert plan.unallocated_task_refs == ()


def test_indivisible_too_big_for_all_windows_unallocated():
    state = build_state(
        (window("w1", dt(9), dt(10), 60), window("w2", dt(10, 30), dt(11), 30)),
        (make_task(total=80, splittable=False, minimum_slice=80),),
    )
    plan = plan_of(state)
    assert plan.allocations == ()
    assert plan.unallocated_task_refs == ("task-1",)


def test_splittable_remaining_exceeds_single_window_schedules_partial():
    # P3e-final：remaining=90 > 单窗口 80，splittable=true、minimum_slice<=80
    # 时必须在窗口内先安排一部分，不能整项 unallocated。
    state = build_state(
        (window("w1", dt(9), dt(10, 20), 80), window("w2", dt(10, 30), dt(11), 30)),
        (make_task(total=90, splittable=True, minimum_slice=20),),
    )
    plan = plan_of(state)
    assert [(a.window_ref, a.planned_minutes) for a in plan.allocations] == [
        ("w1", 80),
        ("w2", 10),
    ]
    assert plan.allocations[0].is_partial is True
    assert plan.unallocated_task_refs == ()


def test_nonsplittable_remaining_exceeds_single_window_may_be_unallocated():
    # P3e-final：splittable=false 且完整任务放不进任何窗口时才允许整项不安排。
    state = build_state(
        (window("w1", dt(9), dt(10, 20), 80),),
        (make_task(total=90, splittable=False, minimum_slice=90),),
    )
    plan = plan_of(state)
    assert plan.allocations == ()
    assert plan.unallocated_task_refs == ("task-1",)


def test_splittable_none_treated_as_indivisible():
    state = build_state(
        (window("w1", dt(9), dt(9, 40), 40), window("w2", dt(10), dt(11, 10), 70)),
        (make_task(total=60, splittable=None, minimum_slice=None),),
    )
    plan = plan_of(state)
    assert [(a.window_ref, a.planned_minutes) for a in plan.allocations] == [("w2", 60)]


# ---------------------------------------------------------------------------
# 场景 D：minimum slice
# ---------------------------------------------------------------------------


def test_scenario_d_minimum_slice_respected():
    state = build_state(
        (window("w1", dt(9), dt(9, 20), 20), window("w2", dt(10), dt(11, 40), 100)),
        (make_task(total=120, splittable=True, minimum_slice=30),),
    )
    plan = plan_of(state)
    assert [(a.window_ref, a.planned_minutes) for a in plan.allocations] == [("w2", 100)]
    assert plan.unallocated_task_refs == ("task-1",)


def test_default_minimum_slice_used_when_missing():
    assert DEFAULT_MINIMUM_SLICE_MINUTES == 15
    state = build_state(
        (window("w1", dt(9), dt(9, 10), 10), window("w2", dt(10), dt(11, 40), 100)),
        (make_task(total=120, splittable=True, minimum_slice=None),),
    )
    plan = plan_of(state)
    assert [(a.window_ref, a.planned_minutes) for a in plan.allocations] == [("w2", 100)]


# ---------------------------------------------------------------------------
# 场景 E：最后收尾片段小于 minimum slice
# ---------------------------------------------------------------------------


def test_scenario_e_tail_fragment_below_minimum_allowed():
    state = build_state((window("w1", dt(9), dt(9, 25), 25),), (make_task(total=20, splittable=True, minimum_slice=30),))
    plan = plan_of(state)
    assert [(a.window_ref, a.planned_minutes) for a in plan.allocations] == [("w1", 20)]
    assert plan.allocations[0].is_partial is False
    assert plan.unallocated_task_refs == ()


# ---------------------------------------------------------------------------
# 场景 F / J：生命周期防复活
# ---------------------------------------------------------------------------


def test_scenario_f_skipped_today_not_allocated():
    skipped = mark_skipped_today(make_task(ref="words", title="背单词", total=30))
    state = build_state((window("w1", dt(9), dt(10), 120),), (skipped,))
    plan = plan_of(state)
    assert plan.allocations == ()
    assert plan.unallocated_task_refs == ()


def test_scenario_j_lifecycle_tasks_never_allocated():
    completed = make_task(ref="c", title="已完成", total=120, completed=120)
    skipped = mark_skipped_today(make_task(ref="s", title="跳过", total=30))
    abandoned = mark_abandoned(make_task(ref="a", title="放弃", total=45))
    active = make_task(ref="ok", title="正常", total=30)
    state = build_state(
        (window("w1", dt(9), dt(10), 200),),
        (completed, skipped, abandoned, active),
    )
    plan = plan_of(state, task_order=["c", "s", "a", "ok"])
    assert [a.task_ref for a in plan.allocations] == ["ok"]
    assert plan.unallocated_task_refs == ()


# ---------------------------------------------------------------------------
# 场景 G：多个任务
# ---------------------------------------------------------------------------


def test_scenario_g_multiple_tasks():
    state = build_state(
        (window("w1", dt(9), dt(10, 30), 90),),
        (
            make_task(ref="A", title="A", total=30),
            make_task(ref="B", title="B", total=40, splittable=False, minimum_slice=40),
            make_task(ref="C", title="C", total=50, splittable=True, minimum_slice=15),
        ),
    )
    plan = plan_of(state)
    assert [(a.task_ref, a.planned_minutes) for a in plan.allocations] == [
        ("A", 30),
        ("B", 40),
        ("C", 20),
    ]
    assert plan.unallocated_task_refs == ("C",)


# ---------------------------------------------------------------------------
# 场景 H：任务顺序改变
# ---------------------------------------------------------------------------


def test_scenario_h_task_order_reorders_plan():
    state = build_state(
        (window("w1", dt(9), dt(10), 60),),
        (
            make_task(ref="A", title="A", total=40, splittable=True, minimum_slice=15),
            make_task(ref="B", title="B", total=40, splittable=True, minimum_slice=15),
        ),
    )
    default = plan_of(state)
    reordered = plan_of(state, task_order=["B", "A"])
    assert [a.task_ref for a in default.allocations] == ["A", "B"]
    assert [a.task_ref for a in reordered.allocations] == ["B", "A"]
    assert default.planned_minutes_by_task == {"A": 40, "B": 20}
    assert reordered.planned_minutes_by_task == {"A": 20, "B": 40}
    assert state == build_state(
        (window("w1", dt(9), dt(10), 60),),
        (
            make_task(ref="A", title="A", total=40, splittable=True, minimum_slice=15),
            make_task(ref="B", title="B", total=40, splittable=True, minimum_slice=15),
        ),
    )


def test_task_order_unknown_ref_rejected():
    state = build_state((window("w1", dt(9), dt(10), 60),), (make_task(),))
    with pytest.raises(ValueError):
        plan_of(state, task_order=["ghost"])


def test_task_order_duplicate_ref_rejected():
    state = build_state(
        (window("w1", dt(9), dt(10), 60),),
        (make_task(ref="A"), make_task(ref="B")),
    )
    with pytest.raises(ValueError):
        plan_of(state, task_order=["A", "A"])


def test_task_order_empty_uses_default_order():
    state = build_state(
        (window("w1", dt(9), dt(10), 60),),
        (make_task(ref="A", total=30), make_task(ref="B", total=30)),
    )
    assert [a.task_ref for a in plan_of(state, task_order=[]).allocations] == ["A", "B"]


# ---------------------------------------------------------------------------
# 场景 I：延误后重新分配
# ---------------------------------------------------------------------------


def test_scenario_i_reallocation_after_capacity_change():
    task = make_task(total=120, splittable=True, minimum_slice=30)
    before = build_state(
        (window("w1", dt(9), dt(10), 60), window("w2", dt(10, 30), dt(12), 90)),
        (task,),
    )
    after = build_state(
        (window("w1", dt(9), dt(9, 40), 40), window("w2", dt(10, 30), dt(12), 90)),
        (task,),
    )
    plan_before = plan_of(before)
    plan_after = plan_of(after)
    assert plan_before.allocations[0].planned_minutes == 60
    assert plan_after.allocations[0].planned_minutes == 40
    assert plan_after.allocations[1].planned_minutes == 80
    # 只遵守新容量，不继承旧 planned
    assert sum(a.planned_minutes for a in plan_after.allocations) == 120
    assert after == build_state(
        (window("w1", dt(9), dt(9, 40), 40), window("w2", dt(10, 30), dt(12), 90)),
        (task,),
    )


# ---------------------------------------------------------------------------
# total 重估后的 remaining 数据链
# ---------------------------------------------------------------------------


def test_total_reestimated_remaining_limits_plan():
    task = make_task(total=150, completed=40)
    state = build_state((window("w1", dt(9), dt(11), 200),), (task,))
    plan = plan_of(state)
    assert task.remaining_minutes == 110
    assert sum(a.planned_minutes for a in plan.allocations) == 110


# ---------------------------------------------------------------------------
# duration unknown
# ---------------------------------------------------------------------------


def test_duration_unknown_task_unallocated_with_warning():
    task = make_task(total=None, splittable=None, minimum_slice=None)
    state = build_state((window("w1", dt(9), dt(10), 120),), (task,))
    plan = plan_of(state)
    assert plan.allocations == ()
    assert plan.unallocated_task_refs == ("task-1",)
    assert any(WARNING_UNKNOWN_DURATION in w for w in plan.warnings)


# ---------------------------------------------------------------------------
# 过去窗口
# ---------------------------------------------------------------------------


def test_expired_window_skipped():
    state = build_state(
        (window("w1", dt(7), dt(8), 60), window("w2", dt(12), dt(13), 60)),
        (make_task(total=30),),
        now=dt(9),
    )
    plan = plan_of(state)
    assert [a.window_ref for a in plan.allocations] == ["w2"]


# ---------------------------------------------------------------------------
# LOW_ATTENTION
# ---------------------------------------------------------------------------


def test_low_attention_excluded_by_default():
    low = window("w1", dt(9), dt(11), 100, AvailabilityLevel.LOW_ATTENTION)
    state = build_state((low,), (make_task(total=40),))
    plan = plan_of(state)
    assert plan.allocations == ()
    assert dict(plan.unused_capacity_by_window) == {}


def test_low_attention_included_when_enabled_with_warning():
    low = window("w1", dt(9), dt(11), 100, AvailabilityLevel.LOW_ATTENTION)
    state = build_state((low,), (make_task(total=40),))
    plan = plan_of(state, include_low_attention=True)
    assert [(a.window_ref, a.planned_minutes) for a in plan.allocations] == [("w1", 40)]
    assert any(WARNING_LOW_ATTENTION in w for w in plan.warnings)


def test_include_low_attention_must_be_bool():
    state = build_state((window("w1", dt(9), dt(10), 60),), (make_task(total=30),))
    with pytest.raises(ValueError):
        plan_of(state, include_low_attention="yes")


# ---------------------------------------------------------------------------
# current / later allocations
# ---------------------------------------------------------------------------


def test_current_allocation_is_first_in_active_window():
    state = build_state(
        (window("w1", dt(9), dt(9, 50), 50), window("w2", dt(10, 30), dt(12), 90)),
        (make_task(ref="A", total=40), make_task(ref="B", total=60)),
    )
    plan = plan_of(state)
    assert plan.current_window_ref == "w1"
    assert plan.current_allocation.task_ref == "A"
    assert [a.window_ref for a in plan.later_allocations] == ["w2"]


def test_no_current_allocation_when_active_window_empty():
    # 让 active window 没有 allocation：w1 是 LOW_ATTENTION，默认不进入分配
    w1 = window("w1", dt(9), dt(10), 90, AvailabilityLevel.LOW_ATTENTION)
    w2 = window("w2", dt(10, 30), dt(12), 90)
    state = build_state((w1, w2), (make_task(ref="A", total=40),))
    plan = plan_of(state)
    assert plan.current_window_ref == "w1"
    assert plan.current_allocation is None
    assert [a.window_ref for a in plan.later_allocations] == ["w2"]


def test_no_active_window_all_allocations_are_later():
    state = build_state(
        (window("w1", dt(13), dt(14), 60),),
        (make_task(total=30),),
        now=dt(12),
    )
    plan = plan_of(state)
    assert plan.current_window_ref is None
    assert plan.current_allocation is None
    assert [a.window_ref for a in plan.later_allocations] == ["w1"]


# ---------------------------------------------------------------------------
# deterministic / immutability
# ---------------------------------------------------------------------------


def test_deterministic_same_input_same_result():
    state = build_state(
        (window("w1", dt(9), dt(9, 50), 50), window("w2", dt(10), dt(11, 20), 80)),
        (make_task(total=120), make_task(ref="B", title="B", total=40)),
    )
    first = plan_of(state)
    second = plan_of(state)
    assert first == second
    assert [a.allocation_ref for a in first.allocations] == [a.allocation_ref for a in second.allocations]


def test_allocator_does_not_modify_state_or_tasks():
    windows = (window("w1", dt(9), dt(9, 50), 50), window("w2", dt(10), dt(11, 20), 80))
    tasks = (make_task(total=120), make_task(ref="B", title="B", total=40))
    state = build_state(windows, tasks)
    state_before = build_state(windows, tasks)
    task_before = tasks[0]
    plan_of(state)
    assert state == state_before
    assert state.tasks == tasks
    assert tasks[0] == task_before
    assert tasks[0].completed_minutes == 0


# ---------------------------------------------------------------------------
# 统计边界
# ---------------------------------------------------------------------------


def test_per_task_planned_never_exceeds_remaining():
    tasks = (
        make_task(ref="A", title="A", total=120, splittable=True, minimum_slice=30),
        make_task(ref="B", title="B", total=40, splittable=False, minimum_slice=40),
    )
    state = build_state(
        (window("w1", dt(9), dt(9, 50), 50), window("w2", dt(10), dt(12), 120)),
        tasks,
    )
    plan = plan_of(state)
    by_task = plan.planned_minutes_by_task
    assert by_task["A"] == 120
    assert by_task["B"] == 40
    assert by_task["A"] <= tasks[0].remaining_minutes
    assert by_task["B"] <= tasks[1].remaining_minutes


def test_per_window_planned_never_exceeds_capacity():
    state = build_state(
        (window("w1", dt(9), dt(9, 50), 50), window("w2", dt(10), dt(11, 20), 80)),
        (make_task(total=120), make_task(ref="B", title="B", total=40)),
    )
    plan = plan_of(state)
    by_window = plan.planned_minutes_by_window
    assert by_window["w1"] <= 50
    assert by_window["w2"] <= 80
    for window_ref, unused in plan.unused_capacity_by_window:
        assert 0 <= unused <= dict(
            (w.window_ref, w.capacity_minutes) for w in state.windows
        )[window_ref]


def test_allocation_refs_stable_across_runs():
    state = build_state(
        (window("w1", dt(9), dt(9, 50), 50), window("w2", dt(10), dt(11, 20), 80)),
        (make_task(total=120),),
    )
    refs = [[a.allocation_ref for a in plan_of(state).allocations] for _ in range(3)]
    assert refs[0] == refs[1] == refs[2]
