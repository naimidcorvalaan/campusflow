from datetime import datetime

import pytest

from src.p1_models import SourceKind
from src.p1_window_models import AvailabilityLevel, FixedCommitment
from src.p2_models import DayPlanningState, TaskProgress, TaskState
from src.p2_task_progress import is_task_schedulable_today
from src.p2_window_derivation import (
    DEFAULT_SAFETY_BUFFER_MINUTES,
    derive_active_window_ref,
    derive_day_state,
    derive_day_windows,
)


def dt(hour, minute=0):
    return datetime(2026, 9, 1, hour, minute)


def commit(
    ref,
    start=None,
    end=None,
    availability=AvailabilityLevel.UNAVAILABLE,
    commitment_kind=None,
):
    return FixedCommitment(
        ref, ref, ref, start, end, None, availability, {}, (), commitment_kind
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
# A. 单个未来 commitment
# ---------------------------------------------------------------------------


def test_single_future_commitment():
    result = derive_day_windows(
        dt(9),
        (commit("class", dt(10), dt(11, 30)),),
        dt(18),
        10,
        {"class": 15},
    )
    assert len(result.windows) == 2
    first, second = result.windows
    assert first.starts_at == dt(9)
    assert first.ends_at == dt(10)
    assert first.capacity_minutes == 35
    assert first.travel_minutes == 15
    assert first.next_commitment_ref == "class"
    assert first.availability == AvailabilityLevel.FULLY_AVAILABLE
    assert second.starts_at == dt(11, 30)
    assert second.ends_at == dt(18)
    assert second.capacity_minutes == 380
    assert second.next_commitment_ref is None


# ---------------------------------------------------------------------------
# B. 三个 fixed commitments
# ---------------------------------------------------------------------------


def test_three_fixed_commitments():
    result = derive_day_windows(
        dt(9, 10),
        (
            commit("class", dt(10), dt(11, 30)),
            commit("lab", dt(14), dt(15, 30)),
            commit("meeting", dt(19), dt(20)),
        ),
        dt(22),
        10,
        {"class": 15, "lab": 15, "meeting": 15},
    )
    windows = result.windows
    assert [w.window_ref for w in windows] == [
        "day_window_001",
        "day_window_002",
        "day_window_003",
        "day_window_004",
    ]
    assert [w.starts_at for w in windows] == [dt(9, 10), dt(11, 30), dt(15, 30), dt(20)]
    assert [w.ends_at for w in windows] == [dt(10), dt(14), dt(19), dt(22)]
    assert windows[1].capacity_minutes == 125
    for left, right in zip(windows, windows[1:]):
        assert left.ends_at <= right.starts_at


# ---------------------------------------------------------------------------
# C. travel + buffer
# ---------------------------------------------------------------------------


def test_travel_and_buffer_reduce_capacity():
    result = derive_day_windows(
        dt(9),
        (commit("class", dt(10), dt(11)),),
        dt(12),
        10,
        {"class": 15},
    )
    assert len(result.windows) == 2
    assert result.windows[0].starts_at == dt(9)
    assert result.windows[0].ends_at == dt(10)
    assert result.windows[0].capacity_minutes == 35


# ---------------------------------------------------------------------------
# D. capacity <= 0
# ---------------------------------------------------------------------------


def test_zero_or_negative_capacity_window_dropped():
    result = derive_day_windows(
        dt(9),
        (commit("class", dt(9, 20), dt(11)),),
        dt(18),
        10,
        {"class": 15},
    )
    assert len(result.windows) == 1
    assert result.windows[0].starts_at == dt(11)
    assert result.windows[0].capacity_minutes == 410


# ---------------------------------------------------------------------------
# E. 已经过期 commitment
# ---------------------------------------------------------------------------


def test_expired_commitment_ignored():
    result = derive_day_windows(
        dt(9),
        (
            commit("old", dt(8), dt(8, 30)),
            commit("ending_now", dt(8), dt(9)),
            commit("class", dt(10), dt(11, 30)),
        ),
        dt(18),
    )
    assert len(result.windows) == 2
    assert result.windows[0].starts_at == dt(9)
    assert result.windows[0].next_commitment_ref == "class"
    assert result.unresolved_commitment_refs == ()


# ---------------------------------------------------------------------------
# F. 当前正在上课（UNAVAILABLE）
# ---------------------------------------------------------------------------


def test_in_progress_unavailable_commitment():
    result = derive_day_windows(
        dt(10, 15),
        (commit("class", dt(10), dt(11, 30)),),
        dt(18),
    )
    assert all(not (w.starts_at <= dt(10, 15) < w.ends_at) for w in result.windows)
    assert result.windows[0].starts_at == dt(11, 30)


# ---------------------------------------------------------------------------
# G. LOW_ATTENTION
# ---------------------------------------------------------------------------


def test_low_attention_future_commitment():
    result = derive_day_windows(
        dt(9),
        (commit("lab", dt(10), dt(11, 30), AvailabilityLevel.LOW_ATTENTION),),
        dt(18),
        10,
    )
    assert len(result.windows) == 3
    assert result.windows[1].availability == AvailabilityLevel.LOW_ATTENTION
    assert result.windows[1].starts_at == dt(10)
    assert result.windows[1].ends_at == dt(11, 30)
    assert result.windows[1].capacity_minutes == 80
    assert result.windows[1].travel_minutes == 0
    assert result.windows[0].availability == AvailabilityLevel.FULLY_AVAILABLE
    assert result.windows[2].availability == AvailabilityLevel.FULLY_AVAILABLE


def test_low_attention_in_progress():
    result = derive_day_windows(
        dt(10, 15),
        (commit("lab", dt(10), dt(11, 30), AvailabilityLevel.LOW_ATTENTION),),
        dt(18),
        10,
    )
    assert len(result.windows) == 2
    assert result.windows[0].availability == AvailabilityLevel.LOW_ATTENTION
    assert result.windows[0].starts_at == dt(10, 15)
    assert result.windows[0].ends_at == dt(11, 30)
    assert result.windows[0].capacity_minutes == 65


def test_low_attention_tail_travel_to_next_commitment():
    result = derive_day_windows(
        dt(9),
        (
            commit("lab", dt(10), dt(11, 30), AvailabilityLevel.LOW_ATTENTION),
            commit("next", dt(13), dt(14)),
        ),
        dt(18),
        10,
        {"next": 10},
    )
    low_window = [w for w in result.windows if w.availability == AvailabilityLevel.LOW_ATTENTION][0]
    assert low_window.next_commitment_ref == "next"
    assert low_window.travel_minutes == 10
    assert low_window.capacity_minutes == 70


# ---------------------------------------------------------------------------
# H. 重叠 commitment
# ---------------------------------------------------------------------------


def test_overlapping_commitments_merged():
    result = derive_day_windows(
        dt(9),
        (
            commit("a", dt(10), dt(11, 30)),
            commit("b", dt(11), dt(12)),
        ),
        dt(18),
    )
    windows = result.windows
    assert [w.starts_at for w in windows] == [dt(9), dt(12)]
    assert [w.ends_at for w in windows] == [dt(10), dt(18)]
    for left, right in zip(windows, windows[1:]):
        assert left.ends_at <= right.starts_at


def test_touching_commitments_do_not_overlap():
    result = derive_day_windows(
        dt(9),
        (
            commit("a", dt(10), dt(11, 30)),
            commit("b", dt(11, 30), dt(12)),
        ),
        dt(18),
    )
    assert [w.starts_at for w in result.windows] == [dt(9), dt(12)]
    assert [w.ends_at for w in result.windows] == [dt(10), dt(18)]
    for left, right in zip(result.windows, result.windows[1:]):
        assert left.ends_at <= right.starts_at


# ---------------------------------------------------------------------------
# I. starts_at 未知
# ---------------------------------------------------------------------------


def test_unknown_start_unresolved():
    result = derive_day_windows(
        dt(9),
        (
            commit("mystery", None, None),
            commit("class", dt(10), dt(11, 30)),
        ),
        dt(18),
    )
    assert "mystery" in result.unresolved_commitment_refs
    assert len(result.windows) == 2


# ---------------------------------------------------------------------------
# J. future commitment end 未知
# ---------------------------------------------------------------------------


def test_future_unknown_end_stops_derivation():
    result = derive_day_windows(
        dt(9),
        (
            commit("class", dt(10), dt(11, 30)),
            commit("mystery", dt(14), None),
        ),
        dt(22),
    )
    assert "mystery" in result.unresolved_commitment_refs
    assert [w.ends_at for w in result.windows] == [dt(10), dt(14)]
    assert all(w.ends_at <= dt(14) for w in result.windows)


def test_future_unknown_class_end_reserves_prep_as_hard_capacity():
    result = derive_day_windows(
        dt(14),
        (commit("class", dt(17), None, commitment_kind="class"),),
        dt(22),
        0,
    )
    assert result.unresolved_commitment_refs == ("class",)
    assert len(result.windows) == 1
    assert result.windows[0].starts_at == dt(14)
    assert result.windows[0].ends_at == dt(16, 50)
    assert result.windows[0].capacity_minutes == 170


def test_future_unknown_end_gap_uses_travel():
    result = derive_day_windows(
        dt(9),
        (
            commit("class", dt(10), dt(11, 30)),
            commit("mystery", dt(14), None),
        ),
        dt(22),
        10,
        {"mystery": 20},
    )
    last = result.windows[-1]
    assert last.ends_at == dt(14)
    assert last.next_commitment_ref == "mystery"
    assert last.travel_minutes == 20
    assert last.capacity_minutes == 120


# ---------------------------------------------------------------------------
# K. 当前 commitment end 未知
# ---------------------------------------------------------------------------


def test_current_unknown_end_no_windows():
    result = derive_day_windows(
        dt(10, 30),
        (commit("mystery", dt(10), None),),
        dt(22),
    )
    assert result.windows == ()
    assert "mystery" in result.unresolved_commitment_refs


# ---------------------------------------------------------------------------
# L. day_end 截断
# ---------------------------------------------------------------------------


def test_day_end_truncation():
    result = derive_day_windows(
        dt(9),
        (commit("late", dt(19), dt(23)),),
        dt(22),
    )
    assert all(w.ends_at <= dt(22) for w in result.windows)
    assert result.windows[-1].ends_at == dt(19)


def test_next_day_commitment_ignored():
    result = derive_day_windows(
        dt(9),
        (
            commit("class", dt(10), dt(11, 30)),
            commit("tomorrow", dt(23), None),
        ),
        dt(22),
    )
    assert "tomorrow" not in result.unresolved_commitment_refs
    assert result.windows[-1].ends_at == dt(22)


# ---------------------------------------------------------------------------
# M. window_ref 稳定
# ---------------------------------------------------------------------------


def test_window_refs_stable_across_calls():
    args = (
        dt(9, 10),
        (
            commit("class", dt(10), dt(11, 30)),
            commit("lab", dt(14), dt(15, 30)),
        ),
        dt(22),
    )
    first = derive_day_windows(*args, 10, {"class": 15, "lab": 15})
    second = derive_day_windows(*args, 10, {"class": 15, "lab": 15})
    assert [w.window_ref for w in first.windows] == [w.window_ref for w in second.windows]


# ---------------------------------------------------------------------------
# travel 输入形式
# ---------------------------------------------------------------------------


def test_callable_travel_input():
    result = derive_day_windows(
        dt(9),
        (commit("class", dt(10), dt(11)),),
        dt(12),
        10,
        lambda ref: {"class": 15}.get(ref, 0),
    )
    assert result.windows[0].travel_minutes == 15
    assert result.windows[0].capacity_minutes == 35


def test_invalid_travel_value_rejected():
    with pytest.raises(ValueError):
        derive_day_windows(
            dt(9),
            (commit("class", dt(10), dt(11)),),
            dt(12),
            10,
            {"class": -5},
        )


def test_invalid_travel_type_rejected():
    with pytest.raises(ValueError):
        derive_day_windows(
            dt(9),
            (commit("class", dt(10), dt(11)),),
            dt(12),
            10,
            {"class": True},
        )


# ---------------------------------------------------------------------------
# 派生输入校验
# ---------------------------------------------------------------------------


def test_derivation_rejects_day_end_before_now():
    with pytest.raises(ValueError):
        derive_day_windows(dt(10), (), dt(9))


def test_derivation_rejects_cross_midnight():
    with pytest.raises(ValueError):
        derive_day_windows(dt(23), (), datetime(2026, 9, 2, 1))


def test_derivation_rejects_invalid_buffer():
    with pytest.raises(ValueError):
        derive_day_windows(dt(9), (), dt(18), True)
    with pytest.raises(ValueError):
        derive_day_windows(dt(9), (), dt(18), -1)


def test_derivation_rejects_non_commitment():
    with pytest.raises(TypeError):
        derive_day_windows(dt(9), ("not-a-commitment",), dt(18))


# ---------------------------------------------------------------------------
# derive_active_window_ref / derive_day_state
# ---------------------------------------------------------------------------


def test_derive_active_window_ref():
    result = derive_day_windows(
        dt(9),
        (commit("class", dt(10), dt(11, 30)),),
        dt(18),
    )
    assert derive_active_window_ref(result.windows, dt(9)) == "day_window_001"
    assert derive_active_window_ref(result.windows, dt(10)) is None
    assert derive_active_window_ref(result.windows, dt(11, 45)) == "day_window_002"
    assert derive_active_window_ref(result.windows, dt(18)) is None


def test_derive_day_state_builds_state():
    task = make_task()
    state = derive_day_state(
        dt(9),
        dt(22),
        (commit("class", dt(10), dt(11, 30)),),
        (task,),
        10,
        {"class": 15},
    )
    assert isinstance(state, DayPlanningState)
    assert state.active_window_ref == "day_window_001"
    assert state.windows[0].capacity_minutes == 35


def test_derive_day_state_history_capped():
    state = derive_day_state(
        dt(9),
        dt(22),
        (),
        (),
        history=tuple("e{}".format(i) for i in range(10)),
    )
    assert len(state.history) == 10


# ---------------------------------------------------------------------------
# P2a 组合验收场景
# ---------------------------------------------------------------------------


def test_scenario_a_cross_window_long_task_foundation():
    commitments = (
        commit("class", dt(10), dt(11, 30)),
        commit("lab", dt(14), dt(15, 30)),
    )
    task = make_task(total_minutes=120, completed_minutes=0)
    state = derive_day_state(
        dt(9), dt(22), commitments, (task,), 10, {"class": 15, "lab": 15}
    )
    capacities = [w.capacity_minutes for w in state.windows]
    assert capacities == [35, 125, 380]
    remaining = state.tasks[0].remaining_minutes
    assert remaining == 120
    assert is_task_schedulable_today(state.tasks[0]) is True
    # P2b 将具备做跨窗口分配所需的全部输入信息
    assert all(cap > 0 for cap in capacities)


def test_scenario_b_user_already_did_80_minutes():
    task = make_task(total_minutes=120, completed_minutes=80)
    state = derive_day_state(
        dt(9),
        dt(22),
        (commit("class", dt(10), dt(11, 30)),),
        (task,),
    )
    assert state.tasks[0].remaining_minutes == 40
    assert state.tasks[0].remaining_minutes != 120


def test_scenario_c_skipped_task_remains_in_ledger():
    task = make_task()
    from src.p2_task_progress import mark_skipped_today

    skipped = mark_skipped_today(task)
    state = derive_day_state(dt(9), dt(22), (), (skipped,))
    assert any(t.task_ref == "task-1" for t in state.tasks)
    assert is_task_schedulable_today(state.tasks[0]) is False


def test_scenario_d_time_advance_rederive_preserves_progress():
    commitments = (
        commit("class", dt(10), dt(11, 30)),
        commit("lab", dt(14), dt(15, 30)),
    )
    task = make_task(completed_minutes=50)
    morning = derive_day_state(dt(9), dt(22), commitments, (task,))
    assert morning.active_window_ref == "day_window_001"

    noon = derive_day_state(dt(12), dt(22), commitments, (task,))
    assert noon.active_window_ref == "day_window_001"
    assert noon.windows[0].starts_at == dt(12)
    assert noon.windows[0].ends_at == dt(14)
    # 过去窗口不再成为 active
    assert all(w.starts_at >= dt(12) for w in noon.windows)
    # 任务进度不因时间推进被修改
    assert noon.tasks == morning.tasks
    assert noon.tasks[0].completed_minutes == 50
