"""P3e 第二轮：统一反馈理解 Agent 测试（全 mock，不请求真实 API / 高德）。

覆盖：task+movement、commitment+movement、task+commitment+movement 三者同句、
arrive_by 倒推出发、depart_at 放置移动、普通单一 task / commitment / movement、
ambiguous 人话问题不修改 state、同句各意图都生效不互相吞掉、
不重复应用 progress / movement、页面仅“当前方案 + 待确认问题”、call budget 有界。
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.p1_models import SourceKind
from src.p1_window_models import AvailabilityLevel, FixedCommitment
from src.p2_main import render_page_text
from src.p2_models import TaskProgress, TaskState
from src.p2_session import MOVEMENT_DATA_KEY, P2SessionController
from src.p2_window_derivation import derive_day_state
from src.p3_location_resolver import LOCATION_RESOLVER_SCHEMA_VERSION
from src.p3_map_loader import load_campus_map_data
from src.p3_map_schema import TransportMode
from src.p3_time_estimator import TIME_ESTIMATE_SCHEMA_VERSION
from src.p3_route_planner import MovementRequest
from src.p3_unified_feedback import UNIFIED_FEEDBACK_SCHEMA_VERSION, make_unified_feedback_handler

REAL_MAP_PATH = Path(__file__).parent.parent / "data" / "beiyangyuan_map.json"

PLAN_EMPTY = (
    '{"schema_version": "p2.day-plan-intent.v1", "task_order": [], '
    '"include_low_attention": false, "task_estimates": [], "rationale": null}'
)
REVIEW_ACCEPT = (
    '{"schema_version": "p2.day-review.v1", "decision": "accept", '
    '"reason": "ok", "suggested_task_order": null, "include_low_attention": null}'
)


def dt(h, m=0):
    return datetime(2026, 9, 1, h, m)


@pytest.fixture(scope="module")
def real_map():
    return load_campus_map_data(REAL_MAP_PATH)


def make_state(meeting_start=14):
    """meeting_start=None 表示当天没有组会；否则组会从 meeting_start:00 开始 1 小时。"""
    now = dt(9)
    commitments = [
        FixedCommitment(
            "c1", "上课", "上课", dt(10), dt(11, 30), None,
            AvailabilityLevel.UNAVAILABLE, {}, (),
        ),
    ]
    if meeting_start is not None:
        commitments.append(
            FixedCommitment(
                "c2", "组会", "组会", dt(meeting_start), dt(meeting_start + 1),
                "31教", AvailabilityLevel.UNAVAILABLE, {}, (),
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
    return derive_day_state(now, dt(22), tuple(commitments), tasks, 10)


def task_update(ref=None, title=None, progress=None, total=None, source=None,
                lifecycle="none", splittable=None, min_slice=None):
    return {
        "target_task_ref": ref,
        "new_task_title": title,
        "progress_delta_minutes": progress,
        "set_total_minutes": total,
        "set_total_source": source,
        "lifecycle_action": lifecycle,
        "is_splittable": splittable,
        "minimum_slice_minutes": min_slice,
    }


def commitment_update(action, ref=None, title=None, starts=None, ends=None, delay=None):
    return {
        "target_commitment_ref": ref,
        "action": action,
        "title": title,
        "starts_at": starts,
        "ends_at": ends,
        "delay_minutes": delay,
    }


def movement_json(origin=None, dest=None, mode=None, depart=None, arrive=None, has=True):
    return {
        "has_movement": has,
        "origin_text": origin,
        "destination_text": dest,
        "mode": mode,
        "depart_at": depart,
        "arrive_by": arrive,
    }


def unified_json(task_updates=None, commitment_updates=None, movement=None,
                 current_location=None, questions=None):
    payload = {
        "schema_version": UNIFIED_FEEDBACK_SCHEMA_VERSION,
        "task_updates": task_updates if task_updates is not None else [],
        "commitment_updates": commitment_updates if commitment_updates is not None else [],
        "movement": movement if movement is not None else movement_json(has=False),
        "current_location": current_location,
        "questions": questions if questions is not None else [],
        "reason": None,
    }
    return json.dumps(payload, ensure_ascii=False)


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


class UnifiedCaller:
    """按用户文本返回统一反馈 JSON；地点精确命中不调模型，时间按方式区分。"""

    def __init__(self):
        self.calls = []

    def __call__(self, system, user):
        self.calls.append((system, user))
        if "统一反馈理解器" in system:
            return self._unified(user)
        if "p3.location-resolution" in system:
            return loc_json("unresolved", question="地图上暂无可对应地点，请再说明一下。")
        if "p3.travel-time" in system:
            if "交通方式=bike" in user:
                return time_json(4, 6)
            return time_json(10, 13)
        if "day-plan-intent" in system:
            return PLAN_EMPTY
        if "day-review" in system:
            return REVIEW_ACCEPT
        return PLAN_EMPTY

    def _unified(self, user):
        if "组会改到3点" in user and "不背单词" in user and "骑车" in user:
            # task + commitment + movement 三者同句
            return unified_json(
                task_updates=[task_update(ref="day_task_002", lifecycle="skip_today")],
                commitment_updates=[commitment_update("update_time", ref="c2", starts="15:00")],
                movement=movement_json(origin="北菜", dest="组会", mode="bike", arrive="15:00"),
                current_location="北菜",
            )
        if "组会改到3点" in user and "骑车" in user:
            # commitment + movement 同句
            return unified_json(
                commitment_updates=[commitment_update("update_time", ref="c2", starts="15:00")],
                movement=movement_json(origin="北菜", dest="组会", mode="bike", arrive="15:00"),
                current_location="北菜",
            )
        if "哪个实验" in user:
            return unified_json(questions=["你说的是计组实验还是物理实验报告？"])
        if "又做了30分钟" in user and "骑车" in user:
            # task + movement 同句
            return unified_json(
                task_updates=[task_update(ref="day_task_001", progress=30)],
                movement=movement_json(origin="9斋", dest="31教", mode="bike"),
            )
        if "15点前到" in user:
            return unified_json(
                movement=movement_json(origin="9斋", dest="31教", mode="bike", arrive="15:00")
            )
        if "15:20前到" in user:
            return unified_json(
                movement=movement_json(origin="9斋", dest="31教", mode="bike", arrive="15:20")
            )
        if "改到15:20" in user:
            return unified_json(
                commitment_updates=[commitment_update("update_time", ref="c2", starts="15:20")],
                movement=movement_json(mode="bike"),
            )
        if "改到14点" in user:
            return unified_json(
                commitment_updates=[commitment_update("update_time", ref="c2", starts="14:00")],
                movement=movement_json(mode="bike"),
            )
        if "14:40" in user:
            return unified_json(
                movement=movement_json(origin="图书馆", dest="9斋", depart="14:40")
            )
        if "8:40" in user:
            return unified_json(
                movement=movement_json(origin="图书馆", dest="9斋", depart="08:40")
            )
        if "又做了30分钟" in user:
            return unified_json(task_updates=[task_update(ref="day_task_001", progress=30)])
        if "组会改到3点" in user:
            return unified_json(
                commitment_updates=[commitment_update("update_time", ref="c2", starts="15:00")]
            )
        if "背会儿单词" in user and "15:20" in user:
            return unified_json(
                task_updates=[task_update(ref="day_task_002", splittable=True, min_slice=9)]
            )
        if "不背单词" in user:
            return unified_json(task_updates=[task_update(ref="day_task_002", lifecycle="skip_today")])
        if "骑车去31教" in user:
            return unified_json(
                movement=movement_json(origin="9斋", dest="31教", mode="bike")
            )
        if "去一个不认识的地方" in user:
            return unified_json(movement=movement_json(origin="9斋", dest="一个不认识的地方"))
        return unified_json()


def make_controller(real_map, state=None):
    caller = UnifiedCaller()
    controller = P2SessionController(
        {},
        caller,
        unified_handler=make_unified_feedback_handler(real_map, caller),
    )
    controller.start_day(state if state is not None else make_state())
    return controller, caller


def task_by_ref(state, ref):
    return {task.task_ref: task for task in state.tasks}[ref]


def commitment_by_ref(state, ref):
    return {c.commitment_ref: c for c in state.commitments}[ref]


# ---------------------------------------------------------------------------
# 同句多意图：task + movement
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# P3e-final：partial commitment 同轮 commitment + movement + task
# ---------------------------------------------------------------------------


def make_partial_state():
    """上课只有 starts_at + location（ends_at=None），计组实验剩余 90 分钟。"""
    now = dt(13, 35)
    commitments = (
        FixedCommitment(
            "c1", "上课", "上课", dt(15), None, "31教",
            AvailabilityLevel.UNAVAILABLE, {}, (),
        ),
    )
    tasks = (
        TaskProgress(
            "day_task_001", "计组实验", 90, 0,
            SourceKind.AI_EXTRACTED_FROM_USER_TEXT, TaskState.ACTIVE, True, 30,
        ),
    )
    return derive_day_state(now, dt(22), tuple(commitments), tasks, 10)


class PartialRescheduleCaller:
    """真实模型容错：commitment 只改 starts_at，movement 只给 mode，task 只给 skip。"""

    def __init__(self):
        self.calls = []

    def __call__(self, system, user):
        self.calls.append((system, user))
        if "统一反馈理解器" in system:
            return unified_json(
                task_updates=[task_update(ref="day_task_001", lifecycle="skip_today")],
                commitment_updates=[
                    commitment_update("update_time", ref="c1", starts="15:30")
                ],
                movement=movement_json(mode="walk"),
            )
        if "p3.travel-time" in system:
            if "交通方式=bike" in user:
                return time_json(4, 6)
            return time_json(10, 13)
        if "day-plan-intent" in system:
            return PLAN_EMPTY
        if "day-review" in system:
            return REVIEW_ACCEPT
        return PLAN_EMPTY


def test_partial_commitment_same_round_reschedule_movement_and_skip(real_map):
    caller = PartialRescheduleCaller()
    store = {}
    controller = P2SessionController(
        store, caller, unified_handler=make_unified_feedback_handler(real_map, caller)
    )
    # 模拟 intake 已保存的移动上下文：9斋 -> 31教 bike，arrive_by=15:00
    store[MOVEMENT_DATA_KEY] = {
        "current_location_text": "9斋",
        "last_request": MovementRequest(
            "9斋", "31教", TransportMode.BIKE,
            arrive_by=datetime(2026, 9, 1, 15, 0),
        ),
        "active_blocks": (),
    }
    controller.start_day(make_partial_state())
    turn = controller.apply_feedback(
        "上课改到下午3点半，我先走过去吧，实验今天就先做到这里。"
    )
    updated = turn.result.updated_state
    # 1) commitment：starts_at 15:00 -> 15:30，ends_at 仍 None，location 保留
    commitment = commitment_by_ref(updated, "c1")
    assert commitment.starts_at == dt(15, 30)
    assert commitment.ends_at is None
    assert commitment.location_text == "31教"
    # 2) task：计组实验 SKIPPED_TODAY，保留已有进度，不误标 COMPLETED / ABANDONED
    task = task_by_ref(updated, "day_task_001")
    assert task.state is TaskState.SKIPPED_TODAY
    assert task.state is not TaskState.COMPLETED
    assert task.state is not TaskState.ABANDONED
    assert task.completed_minutes == 0
    # 3) movement：bike -> walk，终点仍 31教，arrive_by 跟随更新后的 15:30
    assert len(turn.movement_blocks) == 1
    block = turn.movement_blocks[0]
    assert block.origin_node_id == "zhengyuan_9zhai"
    assert block.destination_node_id == "building_31"
    assert block.mode is TransportMode.WALK
    assert block.end_time == dt(15, 30)
    # 5 分钟“收拾东西”buffer 跟随新的 15:30 到达时间一起重排
    assert block.transition_minutes == 5
    assert block.transition_start == dt(15, 14)
    assert block.window_start == dt(15, 19)
    # 页面只有一个主区块，无内部参数
    text = render_page_text(turn)
    assert text.count("# 当前方案") == 1


def test_transition_buffer_rendered_as_human_lines(real_map):
    """arrive_by=15:00 + 骑行5分钟 => 14:50-14:55 收拾东西 + 14:55-15:00 骑行；
    页面不暴露任何内部 buffer 字段。"""
    controller, _ = make_controller(real_map, state=make_state(meeting_start=15))
    turn = controller.apply_feedback("我从9斋骑车去31教，15点前到。")
    block = turn.movement_blocks[0]
    assert block.transition_minutes == 5
    assert block.transition_start == dt(14, 50)
    assert block.window_start == dt(14, 55)
    assert block.end_time == dt(15)
    text = render_page_text(turn)
    assert "14:50–14:55：收拾东西" in text
    assert "14:55–15:00：骑行前往31教，约5分钟（AI暂估）" in text
    for token in ("transition", "buffer", "day_task_", "day_commitment_", "building_31", "zhengyuan_"):
        assert token not in text

    for marker in ("day_commitment_", "day_task_", "building_31", "zhengyuan_"):
        assert marker not in text


def test_task_and_movement_same_feedback_both_apply(real_map):
    controller, caller = make_controller(real_map)
    turn = controller.apply_feedback("我刚又做了30分钟实验，我从9斋骑车去31教。")
    updated = turn.result.updated_state
    assert task_by_ref(updated, "day_task_001").completed_minutes == 80
    assert len(turn.movement_blocks) == 1
    block = turn.movement_blocks[0]
    assert block.origin_node_id == "zhengyuan_9zhai"
    assert block.destination_node_id == "building_31"
    assert block.mode is TransportMode.BIKE
    # 统一理解(1) + 时间估计(1)，地点精确命中不调模型
    assert turn.result.call_count == 2


def test_task_and_movement_do_not_swallow_each_other(real_map):
    controller, _ = make_controller(real_map)
    turn = controller.apply_feedback("我刚又做了30分钟实验，我从9斋骑车去31教。")
    assert task_by_ref(turn.result.updated_state, "day_task_001").completed_minutes == 80
    assert len(turn.movement_blocks) == 1


# ---------------------------------------------------------------------------
# 同句多意图：commitment + movement
# ---------------------------------------------------------------------------


def test_commitment_and_movement_same_feedback_both_apply(real_map):
    controller, _ = make_controller(real_map)
    turn = controller.apply_feedback("组会改到3点，我从北菜骑车过去。")
    updated = turn.result.updated_state
    assert commitment_by_ref(updated, "c2").starts_at == dt(15)
    assert len(turn.movement_blocks) == 1
    block = turn.movement_blocks[0]
    # 终点“组会”被程序映射到组会 location_text=“31教”
    assert block.destination_node_id == "building_31"
    assert block.origin_node_id == "beiyangyuan_north_cainiao"
    # arrive_by=15:00 被落实：移动结束于组会开始时
    assert block.end_time == dt(15)


# ---------------------------------------------------------------------------
# 同句多意图：task + commitment + movement 三者同句
# ---------------------------------------------------------------------------


def test_task_commitment_movement_all_in_one(real_map):
    controller, _ = make_controller(real_map)
    turn = controller.apply_feedback("组会改到3点，我从北菜骑车过去，今天先不背单词了。")
    updated = turn.result.updated_state
    assert commitment_by_ref(updated, "c2").starts_at == dt(15)
    assert task_by_ref(updated, "day_task_002").state == TaskState.SKIPPED_TODAY
    assert len(turn.movement_blocks) == 1
    block = turn.movement_blocks[0]
    assert block.origin_node_id == "beiyangyuan_north_cainiao"
    assert block.destination_node_id == "building_31"
    assert block.mode is TransportMode.BIKE
    assert block.end_time == dt(15)


# ---------------------------------------------------------------------------
# 明确时间移动：arrive_by / depart_at
# ---------------------------------------------------------------------------


def test_arrive_by_backcomputes_departure(real_map):
    controller, _ = make_controller(real_map, state=make_state(meeting_start=15))
    turn = controller.apply_feedback("我从9斋骑车去31教，15点前到。")
    assert len(turn.movement_blocks) == 1
    block = turn.movement_blocks[0]
    assert block.end_time == dt(15)
    assert block.window_start == dt(15) - timedelta(minutes=block.estimated_minutes)
    # 移动前的 5 分钟“收拾东西”：任务窗口在 buffer 开始时截止
    assert block.transition_minutes == 5
    assert block.transition_start == dt(14, 50)
    assert block.window_start == dt(14, 55)
    updated = turn.result.updated_state
    target_window = next(w for w in updated.windows if w.window_ref == block.window_ref)
    assert target_window.ends_at == block.transition_start


def test_depart_at_places_movement(real_map):
    controller, _ = make_controller(real_map, state=make_state(meeting_start=15))
    turn = controller.apply_feedback("我14:40从图书馆出发去9斋。")
    assert len(turn.movement_blocks) == 1
    block = turn.movement_blocks[0]
    assert block.window_start == dt(14, 40)
    assert block.origin_node_id == "zhengdong_library"
    assert block.destination_node_id == "zhengyuan_9zhai"
    # 14:40 出发：5 分钟“收拾东西”在出发前 14:35-14:40，不能加到出发时间之后
    assert block.transition_minutes == 5
    assert block.transition_start == dt(14, 35)
    # 14:40 出发的移动不占用 14:40 之前的任务容量
    assert block.end_time > block.window_start
    text = render_page_text(turn)
    assert "14:35–14:40：收拾东西" in text
    assert "14:40–" in text and "步行前往9斋" in text


def test_depart_at_in_past_clamped_to_now(real_map):
    controller, _ = make_controller(real_map)
    turn = controller.apply_feedback("我8:40从图书馆出发去9斋。")
    # 出发时间已过：程序按“现在出发”处理，绝不排到过去
    assert len(turn.movement_blocks) == 1
    block = turn.movement_blocks[0]
    assert block.window_start >= dt(9)
    assert block.window_start < dt(9, 30)


# ---------------------------------------------------------------------------
# 普通单一反馈
# ---------------------------------------------------------------------------


def test_single_task_feedback_still_applies(real_map):
    controller, _ = make_controller(real_map)
    turn = controller.apply_feedback("我刚又做了30分钟实验。")
    assert task_by_ref(turn.result.updated_state, "day_task_001").completed_minutes == 80
    assert turn.movement_blocks == ()
    assert commitment_by_ref(turn.result.updated_state, "c2").starts_at == dt(14)
    # 理解(1) + Day Plan(1) + Review(1)
    assert turn.result.call_count == 3


def test_single_commitment_feedback_still_applies(real_map):
    controller, _ = make_controller(real_map)
    turn = controller.apply_feedback("下午的组会改到3点。")
    assert commitment_by_ref(turn.result.updated_state, "c2").starts_at == dt(15)
    assert turn.movement_blocks == ()
    assert task_by_ref(turn.result.updated_state, "day_task_001").completed_minutes == 50


def test_movement_only_feedback(real_map):
    controller, _ = make_controller(real_map)
    turn = controller.apply_feedback("我从9斋骑车去31教。")
    assert len(turn.movement_blocks) == 1
    updated = turn.result.updated_state
    assert task_by_ref(updated, "day_task_001").completed_minutes == 50
    assert commitment_by_ref(updated, "c2").starts_at == dt(14)


# ---------------------------------------------------------------------------
# ambiguous / 未知地点
# ---------------------------------------------------------------------------


def test_ambiguous_question_no_state_change(real_map):
    controller, _ = make_controller(real_map)
    state_before = controller.current_state()
    turn = controller.apply_feedback("我刚又做了30分钟，你说的是哪个实验？")
    assert turn.result.questions
    assert "计组实验" in turn.result.questions[0]
    after = controller.current_state()
    assert after.tasks == state_before.tasks
    assert after.commitments == state_before.commitments
    assert after.windows == state_before.windows
    text = render_page_text(turn)
    assert "## 待确认问题" in text


def test_unknown_location_asks_question_no_movement(real_map):
    controller, _ = make_controller(real_map)
    state_before = controller.current_state()
    turn = controller.apply_feedback("我从9斋去一个不认识的地方。")
    assert turn.movement_blocks == ()
    assert turn.movement_questions
    after = controller.current_state()
    assert after.tasks == state_before.tasks
    assert after.commitments == state_before.commitments


# ---------------------------------------------------------------------------
# 幂等 / 不重复应用
# ---------------------------------------------------------------------------


def test_same_feedback_cached_no_double_apply(real_map):
    controller, caller = make_controller(real_map)
    turn1 = controller.apply_feedback("我刚又做了30分钟实验，我从9斋骑车去31教。")
    calls_after_first = len(caller.calls)
    turn2 = controller.apply_feedback("我刚又做了30分钟实验，我从9斋骑车去31教。")
    assert turn2 is turn1
    assert len(caller.calls) == calls_after_first
    assert task_by_ref(controller.current_state(), "day_task_001").completed_minutes == 80
    assert len(controller.current_state().tasks) == 2


def test_followup_task_feedback_does_not_reapply_movement(real_map):
    controller, _ = make_controller(real_map)
    movement_turn = controller.apply_feedback("我从9斋骑车去31教。")
    assert len(movement_turn.movement_blocks) == 1
    task_turn = controller.apply_feedback("我刚又做了30分钟实验。")
    assert task_by_ref(task_turn.result.updated_state, "day_task_001").completed_minutes == 80
    assert len(task_turn.movement_blocks) == 1
    # 移动没有被重复应用：容量只被扣一次
    updated = task_turn.result.updated_state
    task_block = movement_turn.movement_blocks[0]
    block_after = task_turn.movement_blocks[0]
    assert block_after.estimated_minutes == task_block.estimated_minutes


# ---------------------------------------------------------------------------
# 页面输出 / call budget
# ---------------------------------------------------------------------------


def test_page_only_current_plan_and_questions_no_internal_refs(real_map):
    controller, _ = make_controller(real_map)
    turn = controller.apply_feedback("组会改到3点，我从北菜骑车过去，今天先不背单词了。")
    text = render_page_text(turn)
    assert "# 当前方案" in text
    assert "骑行前往" in text
    for token in (
        "day_task_", "day_window_", "day_commitment_", "allocation_", "node_id",
        "zhengyuan", "building_31", "MovementBlock", "schema_version", "ACTIVE",
        "COMPLETED", "SKIPPED_TODAY", "ABANDONED", "window_ref", "task_ref",
        "commitment_ref",
    ):
        assert token not in text


def test_combined_turn_call_budget_within_cap(real_map):
    controller, _ = make_controller(real_map)
    turn = controller.apply_feedback("组会改到3点，我从北菜骑车过去，今天先不背单词了。")
    assert turn.result.call_count <= 10
    assert turn.result.call_count > 0


def test_splittable_task_cannot_cross_transition_buffer(real_map):
    """arrive_by=15:00 + bike(5) + buffer(5)：前一窗口的 splittable 任务最晚只能做到 14:50。"""
    controller, _ = make_controller(real_map, state=make_state(meeting_start=15))
    turn = controller.apply_feedback("我从9斋骑车去31教，15点前到。")
    block = turn.movement_blocks[0]
    updated = turn.result.updated_state
    target_window = next(w for w in updated.windows if w.window_ref == block.window_ref)
    assert target_window.ends_at == dt(14, 50)
    limit = dt(14, 50).time()
    for line in render_page_text(turn).splitlines():
        if len(line) < 12 or line[2] != ":":
            continue
        start_text, rest = line.split("–", 1)
        end_text = rest.split("：", 1)[0]
        try:
            start = datetime.strptime(start_text, "%H:%M").time()
            end = datetime.strptime(end_text, "%H:%M").time()
        except ValueError:
            continue
        if start < limit < end:
            # 只有“收拾东西 / 移动”允许跨越 14:50，任务行一律不允许
            assert ("收拾东西" in line) or ("前往" in line), line



def make_state_at_1520():
    """组会 15:20-16:20（高峰内），位于 31教。"""
    now = dt(9)
    commitments = (
        FixedCommitment(
            "c1", "上课", "上课", dt(10), dt(11, 30), None,
            AvailabilityLevel.UNAVAILABLE, {}, (),
        ),
        FixedCommitment(
            "c2", "组会", "组会", dt(15, 20), dt(16, 20), "31教",
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
    return derive_day_state(now, dt(22), tuple(commitments), tasks, 10)


def test_commitment_time_change_into_peak_doubles_bike(real_map):
    """组会 15:00（非高峰）改到 15:20（高峰）：bike 5 分钟 => 10 分钟。"""
    controller, _ = make_controller(real_map, state=make_state(meeting_start=15))
    turn1 = controller.apply_feedback("我从9斋骑车去31教，15点前到。")
    block1 = turn1.movement_blocks[0]
    assert block1.estimated_minutes == 5
    assert block1.peak_bike is False
    turn2 = controller.apply_feedback("组会改到15:20，我骑车过去。")
    updated = turn2.result.updated_state
    assert commitment_by_ref(updated, "c2").starts_at == dt(15, 20)
    assert len(turn2.movement_blocks) == 1
    block2 = turn2.movement_blocks[0]
    assert block2.peak_bike is True
    assert block2.estimated_minutes == 10
    assert block2.window_start == dt(15, 10)
    assert block2.end_time == dt(15, 20)
    assert block2.transition_start == dt(15, 5)
    text = render_page_text(turn2)
    assert "15:05–15:10：收拾东西" in text
    assert "高峰期骑行前往31教，约10分钟（AI暂估）" in text
    for token in ("peak", "multiplier", "base_travel", "effective_travel"):
        assert token not in text


def test_commitment_time_change_out_of_peak_restores_bike_base(real_map):
    """组会 15:20（高峰）改到 14:00（非高峰）：bike 10 分钟 => 5 分钟。"""
    controller, _ = make_controller(real_map, state=make_state_at_1520())
    turn1 = controller.apply_feedback("我从9斋骑车去31教，15:20前到。")
    block1 = turn1.movement_blocks[0]
    assert block1.estimated_minutes == 10
    assert block1.peak_bike is True
    turn2 = controller.apply_feedback("组会改到14点，我骑车过去。")
    updated = turn2.result.updated_state
    assert commitment_by_ref(updated, "c2").starts_at == dt(14)
    block2 = turn2.movement_blocks[0]
    assert block2.peak_bike is False
    assert block2.estimated_minutes == 5
    assert block2.window_start == dt(13, 55)
    assert block2.end_time == dt(14)
    assert block2.transition_start == dt(13, 50)


def test_vocab_splittable_judgment_applied_through_feedback(real_map):
    """场景 C：Qwen 判断 splittable=true、min_slice<=9 时，反馈应用后任务可安排。"""
    controller, _ = make_controller(real_map, state=make_state_at_1520())
    turn = controller.apply_feedback("这之前有空的话背会儿单词。")
    updated = turn.result.updated_state
    task = task_by_ref(updated, "day_task_002")
    assert task.is_splittable is True
    assert task.minimum_slice_minutes == 9
    planned = turn.result.allocation_plan.planned_minutes_by_task
    assert planned.get("day_task_002") == 30
    text = render_page_text(turn)
    assert "准备去" not in text
    assert "背单词" in text
