"""P3c 路线接入 P2 测试：真实寻路、移动扣容量、重规划不调模型、渲染无内部参数（全 mock）。"""
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.p1_models import SourceKind
from src.p1_window_models import AvailabilityLevel, FixedCommitment
from src.p2_models import TaskProgress, TaskState
from src.p2_window_derivation import derive_day_state
from src.p3_location_resolver import (
    LOCATION_RESOLVER_SCHEMA_VERSION,
    resolve_location,
)
from src.p3_map_loader import load_campus_map_data
from src.p3_map_schema import TransportMode
from src.p3_route_planner import (
    MovementRequest,
    MovementStatus,
    extract_destination_text,
    plan_day_with_movements,
    plan_movement,
    render_movement_plan_lines,
    replan_after_movements,
    suggest_movement_mode,
)
from src.p3_time_estimator import TIME_ESTIMATE_SCHEMA_VERSION

REAL_MAP_PATH = Path(__file__).parent.parent / "data" / "beiyangyuan_map.json"
SYNTHETIC_PATH = Path(__file__).parent / "fixtures" / "p3_synthetic_map.json"


def dt(h, m=0):
    return datetime(2026, 9, 1, h, m)


@pytest.fixture(scope="module")
def real_map():
    return load_campus_map_data(REAL_MAP_PATH)


@pytest.fixture(scope="module")
def synthetic_map():
    return load_campus_map_data(SYNTHETIC_PATH)


def make_state(now=None, safety=10):
    now = now or dt(9)
    commitments = (
        FixedCommitment("c1", "上课", "上课", dt(10), dt(11, 30), None, AvailabilityLevel.UNAVAILABLE, {}, ()),
        FixedCommitment("c2", "实验", "实验", dt(14), dt(15, 30), None, AvailabilityLevel.UNAVAILABLE, {}, ()),
    )
    tasks = (
        TaskProgress("t1", "计组实验3", 120, 50, SourceKind.AI_EXTRACTED_FROM_USER_TEXT, TaskState.ACTIVE, True, 30),
        TaskProgress("t2", "背单词", 30, 0, SourceKind.USER_STATED, TaskState.ACTIVE, True, 10),
    )
    return derive_day_state(now, dt(22), commitments, tasks, safety)


class FakeCaller:
    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls = []

    def __call__(self, system, user):
        self.calls.append((system, user))
        if not self.outputs:
            return "{}"
        return self.outputs.pop(0)


def time_json(min_m, max_m):
    return (
        '{"schema_version": "' + TIME_ESTIMATE_SCHEMA_VERSION + '", '
        '"min_minutes": ' + str(min_m) + ', "max_minutes": ' + str(max_m) + ', "reason": null}'
    )


def loc_json(status, node_id=None, display=None, question=None):
    node = "null" if node_id is None else '"' + node_id + '"'
    disp = "null" if display is None else '"' + display + '"'
    q = "null" if question is None else '"' + question + '"'
    return (
        '{"schema_version": "' + LOCATION_RESOLVER_SCHEMA_VERSION + '", "status": "' + status + '", '
        '"matched_node_id": ' + node + ', "display_name": ' + disp + ', "question": ' + q + '}'
    )


def test_exact_31jiao_movement_deducts_window_capacity(real_map):
    state = make_state()
    caller = FakeCaller(time_json(10, 13))
    plan = plan_day_with_movements(
        state, real_map, [MovementRequest("平园", "31教", "walk")], time_caller=caller
    )
    assert len(plan.movements) == 1
    block = plan.movements[0]
    assert block.distance_m == 972
    assert block.estimated_minutes == 11
    assert block.window_ref == "day_window_001"
    # 真实地点切换：移动前默认 5 分钟“收拾东西”，buffer 真实占用窗口容量
    assert block.window_start == dt(9, 5)
    assert block.end_time == dt(9, 16)
    assert block.transition_minutes == 5
    assert block.transition_start == dt(9)
    assert block.mode is TransportMode.WALK
    first = plan.state.windows[0]
    assert first.starts_at == dt(9, 16)
    assert first.capacity_minutes == 34
    # 输入 state 不被修改
    assert state.windows[0].starts_at == dt(9)
    assert state.windows[0].capacity_minutes == 50


def test_replan_after_movements_needs_no_model(real_map):
    state = make_state()
    origin = resolve_location(real_map, "平园")
    destination = resolve_location(real_map, "31教")
    resolved = plan_movement(real_map, origin, destination, "walk", FakeCaller(time_json(10, 13)))
    assert resolved.status is MovementStatus.OK
    plan = replan_after_movements(state, (resolved,))
    assert len(plan.movements) == 1
    assert plan.state.windows[0].capacity_minutes == 34
    assert plan.state is not state


def test_walk_vs_bike_estimated_minutes_differ(real_map):
    state = make_state()
    walk_plan = plan_day_with_movements(state, real_map, [MovementRequest("平园", "31教", "walk")])
    bike_plan = plan_day_with_movements(state, real_map, [MovementRequest("平园", "31教", "bike")])
    walk_block = walk_plan.movements[0]
    bike_block = bike_plan.movements[0]
    assert walk_block.mode is TransportMode.WALK
    assert bike_block.mode is TransportMode.BIKE
    assert walk_block.distance_m == bike_block.distance_m > 0
    assert bike_block.estimated_minutes < walk_block.estimated_minutes


def test_qwen_nonexistent_node_question_kept(real_map):
    state = make_state()
    loc_caller = FakeCaller(loc_json("resolved", "ghost_node", "幽灵楼"))
    plan = plan_day_with_movements(
        state, real_map, [MovementRequest("平园", "去幽灵地点", "walk")], location_caller=loc_caller
    )
    assert len(plan.movements) == 0
    assert len(plan.unhandled) == 1
    assert plan.questions
    assert plan.allocation_plan is not None


def test_jiu_zhai_routes_to_independent_node(real_map):
    # P3d 官方图补充后：9 斋可解析到独立正园9斋，直接生成真实路线
    state = make_state()
    plan = plan_day_with_movements(state, real_map, [MovementRequest("平园", "9斋", "walk")])
    assert len(plan.movements) == 1
    assert plan.movements[0].approximate is False
    assert not plan.questions
    lines = render_movement_plan_lines(plan)
    assert any("步行前往" in line for line in lines)
    for token in ("building", "node", "day_window"):
        assert all(token not in line for line in lines)


def test_finer_granularity_approximate_question_preserved(real_map):
    # 更细粒度（9斋甲座）未命中节点：Qwen 近似映射必须保留待确认问题
    state = make_state()
    loc_caller = FakeCaller(
        loc_json(
            "approximate",
            "zhengyuan_9zhai",
            "天津大学北洋园校区正园9斋",
            "正园9斋甲座更细，地图上只有正园9斋，是否按正园9斋处理？",
        )
    )
    plan = plan_day_with_movements(
        state, real_map, [MovementRequest("平园", "正园9斋甲座", "walk")], location_caller=loc_caller
    )
    assert len(plan.movements) == 1
    assert plan.movements[0].approximate is True
    assert plan.questions
    lines = render_movement_plan_lines(plan)
    assert any("地点待确认" in line for line in lines)


def test_no_route_unhandled_synthetic(synthetic_map):
    state = make_state()
    plan = plan_day_with_movements(state, synthetic_map, [MovementRequest("东门", "宿舍", "walk")])
    assert len(plan.movements) == 0
    assert len(plan.unhandled) == 1
    assert any("无可用路线" in w for w in plan.warnings)


def test_trivial_same_node_warning(real_map):
    state = make_state()
    plan = plan_day_with_movements(state, real_map, [MovementRequest("平园", "平园宿舍", "walk")])
    assert len(plan.movements) == 0
    assert len(plan.unhandled) == 1
    assert any("相同" in w for w in plan.warnings)
    # 同地点不产生移动块，也不产生“收拾东西”buffer
    lines = render_movement_plan_lines(plan)
    assert all("收拾东西" not in line for line in lines)


def test_movement_too_large_unhandled(real_map):
    # 临近一天结束：唯一未来窗口 21:50-22:00 容量 10，装不下步行兜底 14 分钟
    tasks = (
        TaskProgress("t1", "计组实验3", 120, 50, SourceKind.AI_EXTRACTED_FROM_USER_TEXT, TaskState.ACTIVE, True, 30),
    )
    state = derive_day_state(dt(21, 50), dt(22), (), tasks, 0)
    plan = plan_day_with_movements(state, real_map, [MovementRequest("平园", "31教", "walk")])
    assert len(plan.movements) == 0
    assert len(plan.unhandled) == 1
    assert any("没有足够窗口" in w for w in plan.warnings)


def test_multiple_movements_sequential_windows(real_map):
    state = make_state()
    plan = plan_day_with_movements(
        state, real_map,
        [MovementRequest("平园", "31教", "walk"), MovementRequest("31教", "郑东图书馆", "walk")],
    )
    assert len(plan.movements) == 2
    first, second = plan.movements
    assert first.window_start < second.window_start
    assert first.window_ref == second.window_ref == "day_window_001"
    assert first.distance_m > 0 and second.distance_m > 0
    assert plan.state.windows[0].capacity_minutes < 50


def test_render_uses_real_time_slots_no_internal_refs(real_map):
    state = make_state()
    caller = FakeCaller(time_json(10, 13))
    plan = plan_day_with_movements(
        state, real_map, [MovementRequest("平园", "31教", "walk")], time_caller=caller
    )
    lines = render_movement_plan_lines(plan)
    assert lines[0].startswith("现在：")
    joined = "\n".join(lines)
    assert "09:16–09:50" in joined
    # now=9:00 正处于 buffer 内：第一行是“现在：收拾东西”
    assert lines[0] == "现在：收拾东西"
    assert "09:05–09:16：步行前往31教" in joined
    for token in (
        "day_window", "window_ref", "building_31", "node_id", "MovementBlock",
        "ACTIVE", "COMPLETED", "SKIPPED_TODAY", "ABANDONED", "schema_version",
        "TravelTimeEstimate", "LocationResolution", "MovementStatus",
        "transition_minutes", "transition_buffer", "buffer_minutes",
    ):
        assert token not in joined


def test_suggest_movement_mode():
    assert suggest_movement_mode("我骑车去31教") is TransportMode.BIKE
    assert suggest_movement_mode("我走路去图书馆") is TransportMode.WALK
    assert suggest_movement_mode("随便走走") is None
    assert extract_destination_text("我要去31教") == "31教"
    assert extract_destination_text("今天吃什么") is None


def test_movement_request_validates():
    with pytest.raises(ValueError):
        MovementRequest("", "31教", "walk")
    with pytest.raises(ValueError):
        MovementRequest("平园", "", "walk")
    with pytest.raises(ValueError):
        MovementRequest("平园", "31教", "car")


def test_include_low_attention_passthrough(real_map):
    state = make_state()
    plan = plan_day_with_movements(
        state, real_map, [MovementRequest("平园", "31教", "walk")], include_low_attention=True
    )
    assert plan.allocation_plan is not None
    assert len(plan.movements) == 1


# ---------------------------------------------------------------------------
# 确定性校园骑行高峰规则（程序事实，不交给 Qwen）：只影响 bike，左闭右开。
# ---------------------------------------------------------------------------


def make_peak_state(now=None):
    """无固定安排的一天：单窗口 [now, 22:00]，便于精确控制 arrive_by / depart_at。"""
    now = now if now is not None else dt(9)
    tasks = (
        TaskProgress(
            "t1", "计组实验3", 120, 50,
            SourceKind.AI_EXTRACTED_FROM_USER_TEXT, TaskState.ACTIVE, True, 30,
        ),
    )
    return derive_day_state(now, dt(22), (), tasks, 0)


def test_bike_1455_1500_before_peak_not_doubled(real_map):
    # 14:55-15:00 骑行：不撞 15:05-15:25 高峰，保持 5 分钟
    state = make_peak_state()
    plan = plan_day_with_movements(
        state, real_map,
        [MovementRequest("9斋", "31教", "bike", arrive_by=dt(15))],
        time_caller=FakeCaller(time_json(4, 6)),
    )
    block = plan.movements[0]
    assert block.estimated_minutes == 5
    assert block.peak_bike is False
    assert block.window_start == dt(14, 55)
    assert block.end_time == dt(15)


def test_bike_1505_1510_peak_doubled(real_map):
    # arrive_by=15:10，base 5：候选区间 15:05-15:10 撞高峰 => 有效 10
    state = make_peak_state()
    plan = plan_day_with_movements(
        state, real_map,
        [MovementRequest("9斋", "31教", "bike", arrive_by=dt(15, 10))],
        time_caller=FakeCaller(time_json(4, 6)),
    )
    block = plan.movements[0]
    assert block.estimated_minutes == 10
    assert block.peak_bike is True
    assert block.window_start == dt(15)
    assert block.end_time == dt(15, 10)
    assert block.transition_start == dt(14, 55)


def test_bike_nominal_1503_1508_overlap_doubled(real_map):
    # 正常骑行 5 分钟：15:03-15:08 与 15:05-15:25 重叠 => 10 分钟
    state = make_peak_state()
    plan = plan_day_with_movements(
        state, real_map,
        [MovementRequest("9斋", "31教", "bike", depart_at=dt(15, 3))],
        time_caller=FakeCaller(time_json(4, 6)),
    )
    block = plan.movements[0]
    assert block.estimated_minutes == 10
    assert block.peak_bike is True
    assert block.window_start == dt(15, 3)
    assert block.end_time == dt(15, 13)


def test_arrive_by_1520_peak_bike_buffer(real_map):
    # arrive_by=15:20，base=5：高峰 bike=10，movement 15:10-15:20，buffer 15:05-15:10
    state = make_peak_state()
    plan = plan_day_with_movements(
        state, real_map,
        [MovementRequest("9斋", "31教", "bike", arrive_by=dt(15, 20))],
        time_caller=FakeCaller(time_json(4, 6)),
    )
    block = plan.movements[0]
    assert block.estimated_minutes == 10
    assert block.peak_bike is True
    assert block.window_start == dt(15, 10)
    assert block.end_time == dt(15, 20)
    assert block.transition_start == dt(15, 5)
    # 前一个任务窗口最晚做到 buffer 起点 15:05
    target = next(w for w in plan.state.windows if w.window_ref == block.window_ref)
    assert target.ends_at == dt(15, 5)
    lines = render_movement_plan_lines(plan)
    assert "15:05–15:10：收拾东西" in "\n".join(lines)
    assert "15:10–15:20：高峰期骑行前往31教，约10分钟（AI暂估）" in "\n".join(lines)
    for token in ("peak_multiplier", "base_travel_minutes", "effective_travel_minutes", "transition_minutes"):
        assert all(token not in line for line in lines)


def test_depart_at_1510_peak_bike_buffer(real_map):
    # depart_at=15:10，base=5：高峰 bike=10，movement 15:10-15:20，buffer 15:05-15:10
    state = make_peak_state()
    plan = plan_day_with_movements(
        state, real_map,
        [MovementRequest("9斋", "31教", "bike", depart_at=dt(15, 10))],
        time_caller=FakeCaller(time_json(4, 6)),
    )
    block = plan.movements[0]
    assert block.estimated_minutes == 10
    assert block.peak_bike is True
    assert block.window_start == dt(15, 10)
    assert block.end_time == dt(15, 20)
    assert block.transition_start == dt(15, 5)


def test_walk_same_interval_not_doubled(real_map):
    # walk 同一时段不翻倍（高峰只影响 bike）
    state = make_peak_state()
    plan = plan_day_with_movements(
        state, real_map,
        [MovementRequest("9斋", "31教", "walk", arrive_by=dt(15, 20))],
        time_caller=FakeCaller(time_json(10, 13)),
    )
    block = plan.movements[0]
    assert block.peak_bike is False
    assert block.end_time == dt(15, 20)
    assert block.window_start == dt(15, 20) - timedelta(minutes=block.estimated_minutes)


def test_0830_departure_not_in_peak(real_map):
    # 08:30 恰好出发：不在 [08:10, 08:30) 高峰内，不翻倍
    state = make_peak_state(dt(8))
    plan = plan_day_with_movements(
        state, real_map,
        [MovementRequest("9斋", "31教", "bike", depart_at=dt(8, 30))],
        time_caller=FakeCaller(time_json(4, 6)),
    )
    block = plan.movements[0]
    assert block.estimated_minutes == 5
    assert block.peak_bike is False
    assert block.window_start == dt(8, 30)
    assert block.end_time == dt(8, 35)


def test_0825_departure_inside_peak(real_map):
    # 08:25 出发：骑行区间 [08:25, 08:30) 在高峰内，翻倍为 10
    state = make_peak_state(dt(8))
    plan = plan_day_with_movements(
        state, real_map,
        [MovementRequest("9斋", "31教", "bike", depart_at=dt(8, 25))],
        time_caller=FakeCaller(time_json(4, 6)),
    )
    block = plan.movements[0]
    assert block.estimated_minutes == 10
    assert block.peak_bike is True
    assert block.window_start == dt(8, 25)
    assert block.end_time == dt(8, 35)


def test_peak_boundary_left_closed_right_open():
    # 左闭右开：08:30 不属高峰，08:29:59 边界点属高峰
    from src.p3_route_planner import bike_peak_overlap

    assert bike_peak_overlap(dt(8, 10), dt(8, 20)) is True
    assert bike_peak_overlap(dt(8, 30), dt(8, 35)) is False
    assert bike_peak_overlap(dt(8, 25), dt(8, 30)) is True
    assert bike_peak_overlap(dt(14, 55), dt(15, 5)) is False
    assert bike_peak_overlap(dt(15, 5), dt(15, 10)) is True
    assert bike_peak_overlap(dt(15, 25), dt(15, 35)) is False


# ---------------------------------------------------------------------------
# P3e：旧“固定安排前默认预留10分钟”在 live 链路已停用（safety=0），
# 窗口只被真实 movement（5分钟收拾 + 路线通勤 + peak 倍率）扣除一次。
# ---------------------------------------------------------------------------


def _zero_buffer_state(commit_start):
    """now=13:00，固定安排 commit_start 在 31教；safety=0（P3 live intake 产物）。"""
    now = dt(13)
    commitments = (
        FixedCommitment(
            "c1", "上课", "上课", commit_start, dt(16, 30), "31教",
            AvailabilityLevel.UNAVAILABLE, {}, (),
        ),
    )
    tasks = (
        TaskProgress(
            "t1", "计组实验", 120, 0,
            SourceKind.AI_EXTRACTED_FROM_USER_TEXT, TaskState.ACTIVE, True, 30,
        ),
    )
    return derive_day_state(now, dt(22), commitments, tasks, 0)


def test_same_location_commitment_no_old_10min_truncation():
    """同地点上课：safety=0 时窗口容量=完整 120 分钟，不被旧10分钟截断。"""
    state = _zero_buffer_state(dt(15))
    window = state.windows[0]
    assert window.starts_at == dt(13)
    assert window.ends_at == dt(15)
    assert window.capacity_minutes == 120
    legacy = derive_day_state(dt(13), dt(22), state.commitments, state.tasks, 10)
    assert legacy.windows[0].capacity_minutes == 110


def test_bike_arrive_by_deducts_only_transition_and_travel(real_map):
    """9斋→31教 bike=5 arrive_by=15:00：只扣 5 收拾 + 5 骑行，无额外10分钟。"""
    state = _zero_buffer_state(dt(15))
    plan = plan_day_with_movements(
        state, real_map,
        [MovementRequest("9斋", "31教", "bike", arrive_by=dt(15))],
        time_caller=FakeCaller(time_json(4, 6)),
    )
    block = plan.movements[0]
    assert block.estimated_minutes == 5
    assert block.peak_bike is False
    assert block.transition_minutes == 5
    assert block.transition_start == dt(14, 50)
    assert block.window_start == dt(14, 55)
    assert block.end_time == dt(15)
    window = plan.state.windows[0]
    assert window.ends_at == dt(14, 50)
    assert window.capacity_minutes == 110


def test_peak_bike_arrive_by_deducts_only_transition_and_effective(real_map):
    """高峰 bike 5→10 arrive_by=15:20：只扣 5 收拾 + 10 高峰骑行。"""
    state = _zero_buffer_state(dt(15, 20))
    plan = plan_day_with_movements(
        state, real_map,
        [MovementRequest("9斋", "31教", "bike", arrive_by=dt(15, 20))],
        time_caller=FakeCaller(time_json(4, 6)),
    )
    block = plan.movements[0]
    assert block.estimated_minutes == 10
    assert block.peak_bike is True
    assert block.transition_start == dt(15, 5)
    assert block.window_start == dt(15, 10)
    assert block.end_time == dt(15, 20)
    window = plan.state.windows[0]
    assert window.ends_at == dt(15, 5)
    assert window.capacity_minutes == 125


def test_walk_arrive_by_deducts_only_transition_and_travel(real_map):
    """walk=7 arrive_by=15:20：只扣 5 收拾 + 7 步行。"""
    state = _zero_buffer_state(dt(15, 20))
    plan = plan_day_with_movements(
        state, real_map,
        [MovementRequest("9斋", "31教", "walk", arrive_by=dt(15, 20))],
        time_caller=FakeCaller(time_json(5, 9)),
    )
    block = plan.movements[0]
    assert block.estimated_minutes == 7
    assert block.peak_bike is False
    assert block.transition_start == dt(15, 8)
    assert block.window_start == dt(15, 13)
    assert block.end_time == dt(15, 20)
    window = plan.state.windows[0]
    assert window.ends_at == dt(15, 8)
    assert window.capacity_minutes == 128


def test_no_movement_no_fixed_10min_hole(real_map):
    """无 movement：窗口容量=完整时长，不产生固定10分钟空洞。"""
    state = _zero_buffer_state(dt(15))
    plan = plan_day_with_movements(state, real_map, [])
    assert plan.movements == ()
    assert plan.state.windows[0].capacity_minutes == 120


def test_mode_switch_replan_zero_buffer_no_double_deduct(real_map):
    """P3 live（safety=0）：bike→walk 重规划基于原 state，只扣一次收拾+最新耗时。"""
    state = _zero_buffer_state(dt(15))
    origin = resolve_location(real_map, "9斋")
    destination = resolve_location(real_map, "31教")
    bike = plan_movement(real_map, origin, destination, "bike", FakeCaller(time_json(4, 6)))
    walk = plan_movement(real_map, origin, destination, "walk", FakeCaller(time_json(5, 9)))
    bike_plan = replan_after_movements(state, (bike,))
    walk_plan = replan_after_movements(state, (walk,))
    assert bike_plan.state.windows[0].capacity_minutes == 110
    assert walk_plan.state.windows[0].capacity_minutes == 108
