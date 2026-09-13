"""P3e 移动接入 P2 会话测试（全 mock，不请求真实 API / 高德）。

覆盖：9斋->31教、walk/bike 差异、博学园25斋与三问园28斋不混淆、
北菜 alias 精确命中不调模型、动态修改方式/地点、移动扣窗口容量、
未知地点待确认不修改 state、普通 rerun 不重复调用模型、
移动后任务反馈不丢进度、渲染无内部参数、call budget 有界。
"""
from datetime import datetime
from pathlib import Path

import pytest

from src.p1_models import SourceKind
from src.p1_window_models import AvailabilityLevel, FixedCommitment
from src.p2_main import render_page_text
from src.p2_models import TaskProgress, TaskState
from src.p2_session import P2SessionController
from src.p2_window_derivation import derive_day_state
from src.p3_location_resolver import LOCATION_RESOLVER_SCHEMA_VERSION
from src.p3_map_loader import load_campus_map_data
from src.p3_map_schema import TransportMode
from src.p3_movement_flow import make_movement_handler
from src.p3_movement_intent import MOVEMENT_INTENT_SCHEMA_VERSION
from src.p3_time_estimator import TIME_ESTIMATE_SCHEMA_VERSION

REAL_MAP_PATH = Path(__file__).parent.parent / "data" / "beiyangyuan_map.json"

PLAN_EMPTY = (
    '{"schema_version": "p2.day-plan-intent.v1", "task_order": [], '
    '"include_low_attention": false, "task_estimates": [], "rationale": null}'
)
REVIEW_ACCEPT = (
    '{"schema_version": "p2.day-review.v1", "decision": "accept", '
    '"reason": "ok", "suggested_task_order": null, "include_low_attention": null}'
)
TASK_NONE = (
    '{"schema_version": "p2.task-reconciliation.v1", "updates": [], "questions": []}'
)
TASK_PROGRESS_30 = (
    '{"schema_version": "p2.task-reconciliation.v1", "updates": ['
    '{"target_task_ref": "day_task_001", "new_task_title": null, '
    '"progress_delta_minutes": 30, "set_total_minutes": null, '
    '"set_total_source": null, "lifecycle_action": "none", '
    '"is_splittable": null, "minimum_slice_minutes": null}], "questions": []}'
)
ROUTER_TASK = (
    '{"schema_version": "p2.feedback-router.v1", "route": "task", "reason": null}'
)


def dt(h, m=0):
    return datetime(2026, 9, 1, h, m)


@pytest.fixture(scope="module")
def real_map():
    return load_campus_map_data(REAL_MAP_PATH)


def make_state():
    now = dt(9)
    commitments = (
        FixedCommitment(
            "c1", "上课", "上课", dt(10), dt(11, 30), None,
            AvailabilityLevel.UNAVAILABLE, {}, (),
        ),
    )
    tasks = (
        TaskProgress(
            "day_task_001", "计组实验3", 120, 50,
            SourceKind.AI_EXTRACTED_FROM_USER_TEXT, TaskState.ACTIVE, True, 30,
        ),
        TaskProgress(
            "day_task_002", "背单词", 30, 0,
            SourceKind.USER_STATED, TaskState.ACTIVE, True, 10,
        ),
    )
    return derive_day_state(now, dt(22), commitments, tasks, 10)


def intent_json(has_movement, action, origin=None, dest=None, mode=None):
    def _field(value):
        return "null" if value is None else '"' + value + '"'

    return (
        '{"schema_version": "' + MOVEMENT_INTENT_SCHEMA_VERSION + '", "has_movement": '
        + str(has_movement).lower() + ', "action": "' + action + '", "origin_text": '
        + _field(origin) + ', "destination_text": ' + _field(dest) + ', "mode": '
        + _field(mode) + ', "reason": null}'
    )


def time_json(min_m, max_m):
    return (
        '{"schema_version": "' + TIME_ESTIMATE_SCHEMA_VERSION + '", '
        '"min_minutes": ' + str(min_m) + ', "max_minutes": ' + str(max_m)
        + ', "reason": null}'
    )


def loc_json(status, node_id=None, display=None, question=None):
    node = "null" if node_id is None else '"' + node_id + '"'
    disp = "null" if display is None else '"' + display + '"'
    q = "null" if question is None else '"' + question + '"'
    return (
        '{"schema_version": "' + LOCATION_RESOLVER_SCHEMA_VERSION + '", "status": "'
        + status + '", "matched_node_id": ' + node + ', "display_name": ' + disp
        + ', "question": ' + q + '}'
    )


class MockCaller:
    """按 system 提示分发的移动/任务 mock；未知地点返回 unresolved，时间按方式区分。"""

    def __init__(self):
        self.calls = []

    def __call__(self, system, user):
        self.calls.append((system, user))
        if "movement-intent" in system:
            return self._intent(user)
        if "p3.location-resolution" in system:
            return loc_json("unresolved", question="地图上暂无可对应地点，请再说明一下。")
        if "p3.travel-time" in system:
            if "交通方式=bike" in user:
                return time_json(4, 6)
            return time_json(10, 13)
        if "feedback-router" in system:
            return ROUTER_TASK
        if "task-reconciliation" in system:
            if "又做了30分钟" in user:
                return TASK_PROGRESS_30
            return TASK_NONE
        if "day-plan-intent" in system:
            return PLAN_EMPTY
        if "day-review" in system:
            return REVIEW_ACCEPT
        return TASK_NONE

    def _intent(self, user):
        if "9斋" in user and "31教" in user and "骑车" in user:
            return intent_json(True, "add", "9斋", "31教", "bike")
        if "9斋" in user and "31教" in user and "走路" in user:
            return intent_json(True, "add", "9斋", "31教", "walk")
        if "博学园25斋" in user and "三问园28斋" in user:
            return intent_json(True, "add", "博学园25斋", "三问园28斋", "walk")
        if "北菜" in user and "图书馆" in user:
            return intent_json(True, "add", "北菜", "图书馆")
        if "改成走路" in user:
            return intent_json(True, "change_mode", mode="walk")
        if "不认识的地方" in user:
            return intent_json(True, "add", "9斋", "一个不认识的地方")
        return intent_json(False, "none")


def make_controller(real_map):
    caller = MockCaller()
    controller = P2SessionController(
        {},
        caller,
        movement_handler=make_movement_handler(real_map, caller),
    )
    controller.start_day(make_state())
    return controller, caller


def make_peak_session_state():
    """now=15:00、16:00 上课：无明确时间移动会落在 15:05-15:10 高峰窗口。"""
    now = dt(15)
    commitments = (
        FixedCommitment(
            "c1", "上课", "上课", dt(16), dt(17, 30), None,
            AvailabilityLevel.UNAVAILABLE, {}, (),
        ),
    )
    tasks = (
        TaskProgress(
            "day_task_001", "计组实验3", 120, 50,
            SourceKind.AI_EXTRACTED_FROM_USER_TEXT, TaskState.ACTIVE, True, 30,
        ),
    )
    return derive_day_state(now, dt(22), commitments, tasks, 10)


def make_peak_controller(real_map):
    caller = MockCaller()
    controller = P2SessionController(
        {},
        caller,
        movement_handler=make_movement_handler(real_map, caller),
    )
    controller.start_day(make_peak_session_state())
    return controller, caller


def test_9zhai_to_31jiao_movement_applied_via_session(real_map):
    controller, caller = make_controller(real_map)
    before_calls = len(caller.calls)
    turn = controller.apply_feedback("我现在在9斋，下午3点去31教，我骑车。")
    assert len(turn.movement_blocks) == 1
    block = turn.movement_blocks[0]
    assert block.mode is TransportMode.BIKE
    assert block.distance_m > 0
    assert block.origin_node_id == "zhengyuan_9zhai"
    assert block.destination_node_id == "building_31"
    # 已提交 state 的窗口起始时间被移动占用
    assert controller.current_state().windows[0].starts_at != make_state().windows[0].starts_at
    # 移动流程调用 = intent(1) + time(1)，地点精确命中不调模型
    assert turn.result.call_count == 2
    assert len(caller.calls) == before_calls + 2


def test_walk_vs_bike_estimated_minutes_differ(real_map):
    walk_controller, _ = make_controller(real_map)
    walk_turn = walk_controller.apply_feedback("我从9斋走路去31教。")
    bike_controller, _ = make_controller(real_map)
    bike_turn = bike_controller.apply_feedback("我从9斋骑车去31教。")
    walk_block = walk_turn.movement_blocks[0]
    bike_block = bike_turn.movement_blocks[0]
    assert walk_block.mode is TransportMode.WALK
    assert bike_block.mode is TransportMode.BIKE
    assert walk_block.distance_m == bike_block.distance_m > 0
    assert walk_block.estimated_minutes > bike_block.estimated_minutes


def test_boxueyuan_25zhai_vs_sanwenyuan_28zhai_not_confused(real_map):
    controller, _ = make_controller(real_map)
    turn = controller.apply_feedback("我从博学园25斋去三问园28斋。")
    assert len(turn.movement_blocks) == 1
    block = turn.movement_blocks[0]
    assert block.origin_node_id == "boxueyuan_25zhai"
    assert block.destination_node_id == "sanwenyuan_28zhai"
    # 两园相距较远，路线距离明显大于同园区内移动，不能视为邻近
    assert block.distance_m > 800


def test_beicai_alias_exact_no_location_model_call(real_map):
    controller, caller = make_controller(real_map)
    turn = controller.apply_feedback("我在北菜，等会去图书馆。")
    assert len(turn.movement_blocks) == 1
    block = turn.movement_blocks[0]
    assert block.origin_node_id == "beiyangyuan_north_cainiao"
    assert block.destination_node_id == "zhengdong_library"
    # 北菜/图书馆均为精确 alias 命中，没有 location 模型调用
    location_calls = [
        call for call in caller.calls if "p3.location-resolution" in call[0]
    ]
    assert location_calls == []
    assert turn.result.call_count == 2


def test_change_mode_replans_last_movement_no_double_deduct(real_map):
    controller, _ = make_controller(real_map)
    first = controller.apply_feedback("我从9斋骑车去31教。")
    first_block = first.movement_blocks[0]
    first_state = controller.current_state()
    second = controller.apply_feedback("改成走路吧。")
    assert len(second.movement_blocks) == 1
    changed = second.movement_blocks[0]
    assert changed.mode is TransportMode.WALK
    assert changed.origin_node_id == "zhengyuan_9zhai"
    assert changed.destination_node_id == "building_31"
    # 重规划基于移动前基础 state：按步行 11 分钟整体重排，不叠加在已扣过的窗口上；
    # 5 分钟“收拾东西”buffer 只在重排时出现一份，不随 walk/bike 切换重复扣除
    assert first_block.window_start == changed.window_start == dt(9, 5)
    assert first_block.transition_minutes == changed.transition_minutes == 5
    assert first_block.transition_start == changed.transition_start == dt(9)
    assert second.result.updated_state.windows[0].starts_at == dt(9, 16)
    # 若错误地在已扣窗口上再扣一次 buffer+移动，容量会更小；重排后为 34
    assert second.result.updated_state.windows[0].capacity_minutes == 34
    assert second.result.updated_state.windows[0].capacity_minutes > first_state.windows[0].capacity_minutes - 11


def test_unknown_location_question_no_state_change(real_map):
    controller, _ = make_controller(real_map)
    state_before = controller.current_state()
    turn = controller.apply_feedback("我从9斋去一个不认识的地方。")
    assert turn.movement_blocks == ()
    assert turn.movement_questions
    # 地点未解析：窗口/任务/固定安排完全不变（history 追加是常规规划快照行为）
    after = controller.current_state()
    assert after.windows == state_before.windows
    assert after.tasks == state_before.tasks
    assert after.commitments == state_before.commitments
    text = render_page_text(turn)
    assert "## 待确认问题" in text


def test_same_movement_feedback_cached_no_extra_calls(real_map):
    controller, caller = make_controller(real_map)
    turn1 = controller.apply_feedback("我从9斋骑车去31教。")
    calls_after_first = len(caller.calls)
    turn2 = controller.apply_feedback("我从9斋骑车去31教。")
    assert turn2 is turn1
    assert len(caller.calls) == calls_after_first


def test_movement_then_task_feedback_keeps_progress_and_blocks(real_map):
    controller, _ = make_controller(real_map)
    movement_turn = controller.apply_feedback("我从9斋骑车去31教。")
    assert movement_turn.movement_blocks
    task_turn = controller.apply_feedback("我刚又做了30分钟实验")
    updated = task_turn.result.updated_state
    task1 = {t.task_ref: t for t in updated.tasks}["day_task_001"]
    assert task1.completed_minutes == 80
    # 移动块在任务反馈后仍然保留并参与渲染
    assert task_turn.movement_blocks
    text = render_page_text(task_turn)
    assert "骑行前往" in text


def test_render_movement_lines_no_current_task_shows_next_movement_start():
    """当前无进行中任务、后面有 movement 时，首行“下一项安排”时间必须来自 movement 前第一项（buffer）。"""
    from src.p2_allocator import allocate_tasks_across_windows
    from src.p2_models import DayPlanningState, DayWindow
    from src.p3_route_planner import MovementBlock, render_movement_lines
    from src.p3_time_estimator import TimeEstimateMethod

    now = dt(15, 0)
    windows = (
        DayWindow(
            "w1", dt(15, 0), dt(15, 20),
            AvailabilityLevel.FULLY_AVAILABLE, 0, 0, None, 20,
        ),
    )
    commitments = (
        FixedCommitment(
            "c1", "上课", "上课", dt(15, 20), dt(16, 30), None,
            AvailabilityLevel.UNAVAILABLE, {}, (),
        ),
    )
    state = DayPlanningState(now, now, dt(22), commitments, (), windows, "w1", (), ())
    plan = allocate_tasks_across_windows(state)
    block = MovementBlock(
        window_ref="w1", window_start=dt(15, 10),
        origin_text="9斋", destination_text="31教",
        origin_node_id="node_9zhai", destination_node_id="node_31",
        origin_name="9斋", destination_name="31教",
        mode=TransportMode.BIKE, distance_m=800, estimated_minutes=10,
        low_minutes=8, high_minutes=12,
        method=TimeEstimateMethod.PROGRAM_FALLBACK, approximate=False,
        destination_short_name="31教",
        transition_minutes=5, peak_bike=True,
    )
    lines = render_movement_lines((block,), plan, state)
    assert lines[0] == "现在暂时没有安排哦，先休息一下～下一项安排从15:05开始："
    assert any(line.startswith("15:05–15:10：收拾东西") for line in lines)
    assert any("15:10–15:20：高峰期骑行前往31教" in line for line in lines)
    assert "当前没有进行中的任务安排。" not in "\n".join(lines)


def test_render_movement_lines_no_current_task_anywhere_idle_text():
    """movement 之外今天后续也完全无安排时，只显示休息文案。"""
    from src.p2_allocator import allocate_tasks_across_windows
    from src.p2_models import DayPlanningState
    from src.p3_route_planner import render_movement_lines

    now = dt(15, 0)
    state = DayPlanningState(now, now, dt(22), (), (), (), None, (), ())
    plan = allocate_tasks_across_windows(state)
    lines = render_movement_lines((), plan, state)
    assert lines[0] == "现在暂时没有安排啦，可以先休息一下～"
    assert "下一项安排" not in lines[0]


def test_render_movement_lines_no_internal_refs(real_map):
    controller, _ = make_controller(real_map)
    turn = controller.apply_feedback("我从9斋骑车去31教。")
    text = render_page_text(turn)
    assert "# 当前方案" in text
    assert "现在：" in text
    for token in (
        "day_task_", "day_window_", "day_commitment_", "allocation_", "node_id",
        "zhengyuan", "building_31", "MovementBlock", "schema_version", "ACTIVE",
        "COMPLETED", "SKIPPED_TODAY", "ABANDONED", "window_ref", "task_ref",
    ):
        assert token not in text


def test_movement_blocks_survive_refresh(real_map):
    controller, _ = make_controller(real_map)
    controller.apply_feedback("我从9斋骑车去31教。")
    turn = controller.refresh()
    assert turn.movement_blocks
    text = render_page_text(turn)
    assert "骑行前往" in text


def test_movement_turn_call_budget_bounded(real_map):
    controller, caller = make_controller(real_map)
    turn = controller.apply_feedback("我从9斋去一个不认识的地方。")
    # 未应用分支：P2c 计划 + review = 2
    assert turn.result.call_count == 2
    # 移动流程调用 = 意图(1) + 终点 location(1)（起点“9斋”精确命中不调模型）
    movement_calls = [
        call for call in caller.calls
        if "p3.location-resolution" in call[0] or "movement-intent" in call[0]
    ]
    assert len(movement_calls) == 2
    assert turn.result.call_count <= 10


def test_refresh_does_not_reapply_transition_buffer(real_map):
    """刷新只重排任务，不重新扣除移动 / 5 分钟 buffer 容量。"""
    controller, _ = make_controller(real_map)
    turn1 = controller.apply_feedback("我从9斋骑车去31教。")
    block1 = turn1.movement_blocks[0]
    assert block1.transition_minutes == 5
    first_state = controller.current_state()
    turn2 = controller.refresh()
    after = controller.current_state()
    # 窗口起始/容量完全不变 => buffer 与移动都没有被二次扣除
    assert after.windows == first_state.windows
    assert len(turn2.movement_blocks) == 1
    assert turn2.movement_blocks[0].transition_minutes == 5
    text = render_page_text(turn2)
    assert "收拾东西" in text
    for token in ("transition_minutes", "buffer_minutes", "day_window_"):
        assert token not in text



def test_bike_peak_removed_when_switch_to_walk_no_double_deduct(real_map):
    """bike 高峰翻倍后改成 walk：高峰倍率移除、walk 重新估时、buffer 只留一份、不重复扣容量。"""
    controller, _ = make_peak_controller(real_map)
    first = controller.apply_feedback("我从9斋骑车去31教。")
    first_block = first.movement_blocks[0]
    # 15:05-15:10 骑行撞高峰：base 5 => 10
    assert first_block.peak_bike is True
    assert first_block.estimated_minutes == 10
    assert first_block.window_start == dt(15, 5)
    assert first_block.end_time == dt(15, 15)
    second = controller.apply_feedback("改成走路吧。")
    changed = second.movement_blocks[0]
    # walk 不翻倍：base 11，从同一基础 state 重排（单份 buffer）
    assert changed.peak_bike is False
    assert changed.estimated_minutes == 11
    assert changed.window_start == dt(15, 5)
    assert changed.end_time == dt(15, 16)
    # 若在已扣窗口上再扣一次，窗口会从 15:15 之后才开始；单次重排为 15:16
    assert second.result.updated_state.windows[0].starts_at == dt(15, 16)
    text = render_page_text(second)
    # now=15:00 正处于 walk 的 buffer 内：第一行是“现在：收拾东西”
    assert "现在：收拾东西" in text
    assert "15:05–15:16：步行前往31教，约11分钟（AI暂估）" in text
    for token in ("peak", "multiplier", "transition_minutes", "buffer_minutes"):
        assert token not in text


def test_refresh_does_not_reapply_peak_multiplier(real_map):
    """refresh 只重排任务：高峰倍率与移动/buffer 容量都不被二次应用。"""
    controller, _ = make_peak_controller(real_map)
    turn1 = controller.apply_feedback("我从9斋骑车去31教。")
    block1 = turn1.movement_blocks[0]
    assert block1.peak_bike is True
    assert block1.estimated_minutes == 10
    first_state = controller.current_state()
    turn2 = controller.refresh()
    after = controller.current_state()
    assert after.windows == first_state.windows
    assert len(turn2.movement_blocks) == 1
    refreshed = turn2.movement_blocks[0]
    assert refreshed.peak_bike is True
    assert refreshed.estimated_minutes == 10
