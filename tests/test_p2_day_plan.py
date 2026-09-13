from datetime import datetime

import pytest

from src.p1_models import SourceKind
from src.p1_window_models import AvailabilityLevel, FixedCommitment
from src.p2_allocator import allocate_tasks_across_windows
from src.p2_day_plan import DayPlanSummary, compact_plan_lines, summarize_day_plan
from src.p2_models import DayPlanningState, DayWindow, TaskProgress, TaskState
from src.p2_window_derivation import derive_active_window_ref


def dt(hour, minute=0):
    return datetime(2026, 9, 1, hour, minute)


def window(ref, start, end, capacity, availability=AvailabilityLevel.FULLY_AVAILABLE):
    return DayWindow(ref, start, end, availability, 0, 0, None, capacity)


def make_task(ref, title, total, splittable=True, minimum_slice=15):
    return TaskProgress(
        ref, title, total, 0, SourceKind.AI_ESTIMATED, TaskState.ACTIVE, splittable, minimum_slice
    )


def build_state(windows, tasks, now=None):
    now = now or dt(9)
    active = derive_active_window_ref(tuple(windows), now)
    return DayPlanningState(now, now, dt(22), (), tuple(tasks), tuple(windows), active, (), ())


def make_commitment(ref, title, start, end):
    return FixedCommitment(ref, title, title, start, end, None, AvailabilityLevel.UNAVAILABLE, {}, ())


def test_no_large_transition_filler_replaces_doable_window():
    # P3e-final：唯一剩余窗口约 80 分钟，但 90 分钟任务不可拆分放不下；
    # 不得用“13:35-14:55：准备去上课”这类大段泛化动作填充整个窗口。
    now = dt(13, 35)
    w1 = DayWindow(
        "w1", dt(13, 35), dt(14, 55),
        AvailabilityLevel.FULLY_AVAILABLE, 0, 0, "c1", 80,
    )
    commitments = (make_commitment("c1", "上课", dt(15), dt(16)),)
    task = TaskProgress(
        "t1", "计组实验", 90, 0, SourceKind.AI_ESTIMATED, TaskState.ACTIVE, False, 90
    )
    state = DayPlanningState(now, now, dt(22), commitments, (task,), (w1,), "w1", (), ())
    plan = allocate_tasks_across_windows(state)
    lines = compact_plan_lines(plan, state)
    assert plan.unallocated_task_refs == ("t1",)
    assert not any("准备去上课" in line for line in lines)
    assert any("今天暂未安排：计组实验" in line for line in lines)


def test_remaining_window_not_filled_with_prep_action():
    # P3e-final：剩余窗口没有任务可安排时保持空闲，
    # 不得自动生成“准备去上课”之类的泛化动作填充。
    now = dt(9)
    w1 = DayWindow(
        "w1", dt(9), dt(10), AvailabilityLevel.FULLY_AVAILABLE, 0, 0, "c1", 60
    )
    commitments = (make_commitment("c1", "上课", dt(10), dt(11)),)
    state = DayPlanningState(
        now, now, dt(22), commitments,
        (make_task("t1", "计组实验3", 50),), (w1,), "w1", (), (),
    )
    plan = allocate_tasks_across_windows(state)
    lines = compact_plan_lines(plan, state)
    assert not any("准备去上课" in line for line in lines)
    assert not any("准备去" in line for line in lines)


def test_splittable_small_slice_scheduled_into_short_window():
    # P3e-final 场景 C：背单词 30 分钟，可用窗口只有 9 分钟，
    # Qwen 判断 splittable=true、minimum_slice<=9 时，应安排这 9 分钟。
    now = dt(15, 11)
    w1 = DayWindow(
        "w1", dt(15, 11), dt(15, 20),
        AvailabilityLevel.FULLY_AVAILABLE, 0, 0, "c1", 9,
    )
    commitments = (make_commitment("c1", "上课", dt(15, 20), dt(16, 30)),)
    task = TaskProgress(
        "t1", "背单词", 30, 0, SourceKind.USER_STATED, TaskState.ACTIVE, True, 9
    )
    state = DayPlanningState(now, now, dt(22), commitments, (task,), (w1,), "w1", (), ())
    plan = allocate_tasks_across_windows(state)
    assert plan.total_planned_minutes == 9
    assert plan.allocations[0].task_ref == "t1"
    assert plan.allocations[0].planned_minutes == 9
    # 剩余 21 分钟今天后续窗口仍可继续，不影响本窗口已安排部分
    assert "t1" in plan.unallocated_task_refs
    lines = compact_plan_lines(plan, state)
    assert not any("准备去" in line for line in lines)
    assert any(line.startswith("现在：背单词，做到15:20") for line in lines)


def test_idle_window_not_filled_with_prep_action():
    # P3e-final 场景 D：没有任何可执行任务、距离出发还有 9 分钟，
    # 宁可留空，也不得虚构“准备去上课”。
    now = dt(15, 11)
    w1 = DayWindow(
        "w1", dt(15, 11), dt(15, 20),
        AvailabilityLevel.FULLY_AVAILABLE, 0, 0, "c1", 9,
    )
    commitments = (make_commitment("c1", "上课", dt(15, 20), dt(16, 30)),)
    state = DayPlanningState(now, now, dt(22), commitments, (), (w1,), "w1", (), ())
    plan = allocate_tasks_across_windows(state)
    lines = compact_plan_lines(plan, state)
    assert not any("准备去" in line for line in lines)
    assert any("10:00" in line or "15:20" in line or "上课" in line for line in lines)


def test_current_action_line_present():
    state = build_state(
        (window("w1", dt(9), dt(10), 60), window("w2", dt(10, 30), dt(12), 90)),
        (make_task("t1", "计组实验3", 120, minimum_slice=30), make_task("t2", "背单词", 30)),
    )
    summary = summarize_day_plan(allocate_tasks_across_windows(state), state)
    assert summary.current_action_line == "现在：计组实验3 60 分钟"


def test_later_lines_in_time_order():
    state = build_state(
        (window("w1", dt(9), dt(10), 60), window("w2", dt(10, 30), dt(11, 30), 60)),
        (make_task("t1", "计组实验3", 100, minimum_slice=30), make_task("t2", "背单词", 30)),
    )
    summary = summarize_day_plan(allocate_tasks_across_windows(state), state)
    assert summary.later_window_lines == (
        "10:30 后：计组实验3 40 分钟",
        "10:30 后：背单词 20 分钟",
    )


def test_later_lines_with_multiple_tasks_in_one_window():
    state = build_state(
        (window("w1", dt(9), dt(10), 60), window("w2", dt(10, 30), dt(12), 120)),
        (make_task("t1", "计组实验3", 60), make_task("t2", "背单词", 30), make_task("t3", "整理报告", 30)),
    )
    summary = summarize_day_plan(allocate_tasks_across_windows(state), state)
    assert summary.current_action_line == "现在：计组实验3 60 分钟"
    assert len(summary.later_window_lines) == 2
    assert summary.later_window_lines[0].startswith("10:30 后：")


def test_no_current_line_when_active_window_empty():
    w1 = window("w1", dt(9), dt(10), 60, AvailabilityLevel.LOW_ATTENTION)
    w2 = window("w2", dt(10, 30), dt(12), 60)
    state = build_state((w1, w2), (make_task("t2", "背单词", 30),))
    summary = summarize_day_plan(allocate_tasks_across_windows(state), state)
    assert summary.current_action_line is None
    # 未来计划不能冒充“现在”
    assert all(not line.startswith("现在") for line in summary.later_window_lines)


def test_unallocated_task_line():
    state = build_state(
        (window("w1", dt(9), dt(10), 60),),
        (make_task("t1", "整理实验报告", 90, splittable=False, minimum_slice=90),),
    )
    summary = summarize_day_plan(allocate_tasks_across_windows(state), state)
    assert summary.unallocated_lines == ("今天暂未安排：整理实验报告",)


def test_no_internal_refs_or_repr_in_text():
    state = build_state(
        (window("w1", dt(9), dt(10), 60), window("w2", dt(10, 30), dt(12), 90)),
        (
            make_task("t1", "计组实验3", 120, minimum_slice=30),
            make_task("t2", "背单词", 30),
            make_task("t3", "整理报告", 90, splittable=False, minimum_slice=90),
        ),
    )
    summary = summarize_day_plan(allocate_tasks_across_windows(state), state)
    lines = [summary.current_action_line] + list(summary.later_window_lines) + list(summary.unallocated_lines)
    text = "\n".join(line for line in lines if line)
    for forbidden in (
        "allocation_",
        "day_window_",
        "task_ref",
        "TaskAllocation",
        "TaskProgress",
        "DayPlanningState",
        "ACTIVE",
        "ai_estimated",
        "fully_available",
        "dataclass",
    ):
        assert forbidden not in text


def test_empty_plan_summary():
    state = build_state((), ())
    summary = summarize_day_plan(allocate_tasks_across_windows(state), state)
    assert summary.current_action_line is None
    assert summary.later_window_lines == ()
    assert summary.unallocated_lines == ()


def test_summarize_rejects_unknown_window():
    state = build_state((window("w1", dt(9), dt(10), 60),), (make_task("t1", "A", 30),))
    plan = allocate_tasks_across_windows(state)
    other_state = build_state(
        (window("w2", dt(11), dt(12), 60),),
        (make_task("t1", "A", 30),),
        now=dt(10),
    )
    with pytest.raises(ValueError):
        summarize_day_plan(plan, other_state)


def test_summarize_rejects_unknown_task():
    from src.p2_allocator import allocate_tasks_across_windows as allocate

    state = build_state(
        (window("w1", dt(9), dt(10), 60),),
        (
            make_task("t1", "A", 30),
            make_task("t2", "B", 90, splittable=False, minimum_slice=90),
        ),
    )
    plan = allocate(state)
    # fake 状态缺少 t2，而 plan.unallocated_task_refs 引用 t2
    fake = build_state((window("w1", dt(9), dt(10), 60),), (make_task("t1", "A", 30),))
    with pytest.raises(ValueError):
        summarize_day_plan(plan, fake)


def test_summary_is_frozen_dataclass():
    summary = DayPlanSummary(None, (), ())
    assert summary.current_action_line is None
    assert summary.later_window_lines == ()


def test_no_current_task_shows_next_arrangement_start():
    """当前无进行中安排、后面还有下一项计划时，动态显示下一项真实开始时间。"""
    now = dt(15, 0)
    state = build_state(
        (window("w1", dt(15, 5), dt(15, 35), 30),),
        (make_task("t1", "计组实验3", 30),),
    )
    plan = allocate_tasks_across_windows(state)
    lines = compact_plan_lines(plan, state)
    assert lines[0] == "现在暂时没有安排哦，先休息一下～下一项安排从15:05开始："
    assert any(line.startswith("15:05–") for line in lines)
    # 不再出现旧的机械文案
    assert "当前没有进行中的任务安排。" not in "\n".join(lines)


def test_current_task_line_unchanged():
    """当前正在执行任务时，仍显示“现在：XXX，做到HH:MM”。"""
    now = dt(9)
    state = build_state(
        (window("w1", dt(9), dt(10), 60),),
        (make_task("t1", "计组实验3", 60),),
    )
    plan = allocate_tasks_across_windows(state)
    lines = compact_plan_lines(plan, state)
    assert any(line.startswith("现在：计组实验3，做到") for line in lines)
    assert not any(line.startswith("现在暂时没有安排") for line in lines)


def test_no_current_task_and_no_future_shows_idle_text():
    """今天后续完全无安排时，只显示休息文案，不硬生成“下一项安排”。"""
    state = build_state((), ())
    plan = allocate_tasks_across_windows(state)
    lines = compact_plan_lines(plan, state)
    assert lines[0] == "现在暂时没有安排啦，可以先休息一下～"
    assert "下一项安排" not in lines[0]
    assert "当前没有进行中的任务安排。" not in "\n".join(lines)


def test_no_current_task_next_is_fixed_commitment():
    """下一项就是固定安排（如 15:20 上课）时，时间来自真实固定安排。"""
    now = dt(15, 0)
    commitments = (make_commitment("c1", "上课", dt(15, 20), dt(16, 30)),)
    state = DayPlanningState(
        now, now, dt(22), commitments, (), (), None, (), (),
    )
    plan = allocate_tasks_across_windows(state)
    lines = compact_plan_lines(plan, state)
    assert lines[0] == "现在暂时没有安排哦，先休息一下～下一项安排从15:20开始："
    assert any(line.startswith("15:20–") and "上课" in line for line in lines)


def test_compact_plan_lines_show_minutes_and_ai_estimate():
    """30/60/120 分钟等时长统一带“分钟”，AI 暂估内联为“N 分钟（AI暂估）”。"""
    from src.p1_window_models import FixedCommitment

    commitments = (
        FixedCommitment(
            "c1", "上课", "上课", dt(10), dt(11, 30), None,
            AvailabilityLevel.UNAVAILABLE, {}, (),
        ),
    )
    windows = (window("w1", dt(11, 30), dt(15), 210),)
    tasks = (
        make_task("t1", "甲任务", 120),
        make_task("t2", "乙任务", 60),
        make_task("t3", "丙任务", 30),
    )
    state = DayPlanningState(
        dt(10, 30), dt(10, 30), dt(22), commitments, tuple(tasks), windows, None, (), (),
    )
    plan = allocate_tasks_across_windows(state)
    lines = compact_plan_lines(plan, state)
    text = "\n".join(lines)
    assert "甲任务 120 分钟（AI暂估）" in text
    assert "乙任务 60 分钟（AI暂估）" in text
    assert "丙任务 30 分钟（AI暂估）" in text
