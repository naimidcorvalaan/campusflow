"""P3e 最后一轮：首次全天计划接入 P3 空间理解测试（全 mock，不请求真实 API / 高德）。

覆盖：
- 首次输入 9斋 -> 31教 + bike，移动直接进入首版计划；
- commitment location 被 P3 解析为真实 node；
- current_location 被后续 replanning 继承（后续无起点移动复用当前地点）；
- 未给地点 / map_data=None 时保持原 P2 行为（不调地点/时间模型）；
- 未知地点只产生人话待确认，不编造节点、不阻断 intake；
- 不重复创建 task / commitment；
- 普通 rerun 不重复调用模型；页面无内部 ref / enum。
"""

import re

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.p2_live_main import (
    make_live_session,
    render_live_page_text,
    run_live_intake,
)
from src.p2_session import MOVEMENT_DATA_KEY, START_TEXT
from src.p3_location_resolver import LOCATION_RESOLVER_SCHEMA_VERSION
from src.p3_map_loader import load_campus_map_data
from src.p3_map_schema import TransportMode
from src.p3_time_estimator import TIME_ESTIMATE_SCHEMA_VERSION
from src.p3_route_planner import final_plan_overlap_errors

REAL_MAP_PATH = Path(__file__).parent.parent / "data" / "beiyangyuan_map.json"

INTAKE_9ZHAI_31JIAO_BIKE = (
    '{"schema_version": "p2.day-intake.v1", "day_end": "22:00", '
    '"commitments": [{"title": "上课", "starts_at": "15:00", "ends_at": "16:00", '
    '"starts_in_minutes": null, "duration_minutes": null, "location_text": "31教"}], '
    '"tasks": [{"title": "实验", "total_minutes": 120, "is_splittable": true, '
    '"minimum_slice_minutes": 30, "location_text": null}], '
    '"questions": [], "current_location": "9斋", "transport_mode": "bike"}'
)
INTAKE_NO_LOCATION = (
    '{"schema_version": "p2.day-intake.v1", "day_end": "22:00", '
    '"commitments": [{"title": "上课", "starts_at": "15:00", "ends_at": "16:00", '
    '"starts_in_minutes": null, "duration_minutes": null, "location_text": null}], '
    '"tasks": [{"title": "实验", "total_minutes": 120, "is_splittable": true, '
    '"minimum_slice_minutes": 30, "location_text": null}], '
    '"questions": [], "current_location": null, "transport_mode": null}'
)
INTAKE_UNKNOWN_DEST = (
    '{"schema_version": "p2.day-intake.v1", "day_end": "22:00", '
    '"commitments": [{"title": "上课", "starts_at": "15:00", "ends_at": "16:00", '
    '"starts_in_minutes": null, "duration_minutes": null, "location_text": "一个不认识的地方"}], '
    '"tasks": [{"title": "实验", "total_minutes": 120, "is_splittable": true, '
    '"minimum_slice_minutes": 30, "location_text": null}], '
    '"questions": [], "current_location": "9斋", "transport_mode": null}'
)

PLAN_EMPTY = (
    '{"schema_version": "p2.day-plan-intent.v1", "task_order": [], '
    '"include_low_attention": false, "task_estimates": [], "rationale": null}'
)
REVIEW_ACCEPT = (
    '{"schema_version": "p2.day-review.v1", "decision": "accept", '
    '"reason": "ok", "suggested_task_order": null, "include_low_attention": null}'
)
INTAKE_AUDIT_APPROVE = (
    '{"schema_version": "p4.initial-intake-audit.v1", "decision": "approve", '
    '"issues": [], "repaired_proposal": null}'
)
TASK_PROGRESS_30 = (
    '{"schema_version": "p2.task-reconciliation.v1", "updates": ['
    '{"target_task_ref": "day_task_001", "new_task_title": null, '
    '"progress_delta_minutes": 30, "set_total_minutes": null, '
    '"set_total_source": null, "lifecycle_action": "none", '
    '"is_splittable": null, "minimum_slice_minutes": null}], "questions": []}'
)


def dt(h, m=0):
    return datetime(2026, 9, 1, h, m)


@pytest.fixture(scope="module")
def real_map():
    return load_campus_map_data(REAL_MAP_PATH)


def loc_json(status, node_id=None, display=None, question=None):
    node = "null" if node_id is None else '"' + node_id + '"'
    disp = "null" if display is None else '"' + display + '"'
    q = "null" if question is None else '"' + question + '"'
    return (
        '{"schema_version": "' + LOCATION_RESOLVER_SCHEMA_VERSION + '", "status": "'
        + status + '", "matched_node_id": ' + node + ', "display_name": ' + disp
        + ', "question": ' + q + '}'
    )


def time_json(min_m, max_m):
    return (
        '{"schema_version": "' + TIME_ESTIMATE_SCHEMA_VERSION + '", '
        '"min_minutes": ' + str(min_m) + ', "max_minutes": ' + str(max_m)
        + ', "reason": null}'
    )


class MockCaller:
    """按 system 提示分发：intake / 统一反馈 / 地点 / 时间 / P2c pipeline。"""

    def __init__(self, intake=None):
        self.intake = intake if intake is not None else INTAKE_9ZHAI_31JIAO_BIKE
        self.calls = []

    def __call__(self, system, user):
        self.calls.append((system, user))
        if "Initial Intake Semantic Auditor" in system:
            return INTAKE_AUDIT_APPROVE
        if "day-intake" in system:
            return self.intake
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
        if "task-reconciliation" in system:
            if START_TEXT in user:
                return TASK_PROGRESS_30
            return TASK_PROGRESS_30
        return PLAN_EMPTY

    def _unified(self, user):
        import json as _json

        progress_match = re.search(r"又做了(\d+)分钟", user)
        if progress_match is not None:
            delta = int(progress_match.group(1))
            task_updates = _json.loads(TASK_PROGRESS_30).get("updates", [])
            if task_updates:
                task_updates[0]["progress_delta_minutes"] = delta
            movement = {
                "has_movement": False, "origin_text": None,
                "destination_text": None, "mode": None,
                "depart_at": None, "arrive_by": None,
            }
        elif "去图书馆" in user:
            task_updates = []
            movement = {
                "has_movement": True, "origin_text": None,
                "destination_text": "图书馆", "mode": None,
                "depart_at": None, "arrive_by": None,
            }
        else:
            task_updates = []
            movement = {
                "has_movement": False, "origin_text": None,
                "destination_text": None, "mode": None,
                "depart_at": None, "arrive_by": None,
            }
        return _json.dumps(
            {
                "schema_version": "p3.unified-feedback.v1",
                "task_updates": task_updates,
                "commitment_updates": [],
                "movement": movement,
                "current_location": None,
                "questions": [],
                "reason": None,
            },
            ensure_ascii=False,
        )


class FakeAdapter:
    def __init__(self, caller):
        self.agent_caller = caller


class _IntakeStub:
    def __init__(self):
        self.session_state = {}
        self.errors = []
        self.warnings = []
        self.writes = []
        self.markdown_calls = []
        self.rerun_called = 0

    def error(self, *args):
        self.errors.append(args)

    def warning(self, *args):
        self.warnings.append(args)

    def write(self, *args):
        self.writes.append(args)

    def markdown(self, text, **kwargs):
        self.markdown_calls.append(text)

    def rerun(self):
        self.rerun_called += 1

    def experimental_rerun(self):
        self.rerun_called += 1


def intake_and_start(store, caller, real_map, user_text, reference=None):
    """复刻 live 页 _handle_intake_submit 的数据流：intake -> 存移动数据 -> start_day。"""
    reference = reference if reference is not None else dt(9)
    outcome = run_live_intake(store, reference, user_text, caller, map_data=real_map)
    session = make_live_session(store, FakeAdapter(caller), map_data=real_map)
    turn = session.start_day(outcome.applied.state, movement_blocks=outcome.movement_blocks)
    if outcome.current_location_text or outcome.movement_blocks:
        movement_data = store.setdefault(MOVEMENT_DATA_KEY, {})
        if outcome.current_location_text:
            movement_data["current_location_text"] = outcome.current_location_text
        if outcome.movement_blocks:
            movement_data["active_blocks"] = tuple(outcome.movement_blocks)
        if getattr(outcome, "movement_requests", ()):
            movement_data["last_request"] = outcome.movement_requests[0]
    return outcome, session, turn


# ---------------------------------------------------------------------------
# 1. 首次 9斋 -> 31教 + bike：移动直接进入首版计划
# ---------------------------------------------------------------------------


def test_intake_9zhai_to_31jiao_bike_movement_in_first_plan(real_map):
    caller = MockCaller()
    store = {}
    outcome, session, turn = intake_and_start(
        store, caller, real_map, "我现在在9斋，下午3点去31教上课，我骑车。今天还要做实验。"
    )
    assert outcome.applied is not None
    assert len(outcome.movement_blocks) == 1
    block = outcome.movement_blocks[0]
    assert block.mode is TransportMode.BIKE
    assert block.distance_m > 0
    # 移动真实进入窗口：9斋->31教 bike 骑行，14:55 出发 15:00 前到
    assert block.origin_node_id == "zhengyuan_9zhai"
    assert block.destination_node_id == "building_31"
    assert block.window_start.strftime("%H:%M") == "14:55"
    assert block.end_time.strftime("%H:%M") == "15:00"
    assert block.estimated_minutes == 5
    # 首版计划渲染包含移动行，无内部参数
    text = render_live_page_text(turn)
    assert "骑行前往31教，约5分钟（AI暂估）" in text
    assert "14:50–14:55：收拾东西" in text
    assert "当前方案" in text
    for marker in ("day_task_", "day_commitment_", "building_31", "zhengyuan_", "TaskProgress("):
        assert marker not in text
    # Legacy mock cannot answer the new raw-event schema, so the bounded
    # semantic pass falls back once before extraction + audit + travel estimate.
    assert outcome.call_count == 4


def test_intake_movement_deducts_window_capacity(real_map):
    caller = MockCaller()
    store = {}
    outcome, session, turn = intake_and_start(
        store, caller, real_map, "我现在在9斋，下午3点去31教上课，我骑车。今天还要做实验。"
    )
    # 15:00 上课前：14:50-14:55 收拾东西，14:55-15:00 骑行，任务最晚做到 14:50
    before = None
    for window in outcome.applied.state.windows:
        if window.ends_at.hour == 14:
            before = window
    assert before is not None
    assert before.ends_at.strftime("%H:%M") == "14:50"
    assert before.capacity_minutes >= 0


def test_commitment_end_feedback_replays_movement_into_capacity(real_map):
    """Regression: 13:35 的 90 分钟实验必须按真实 75 + 15 分钟分段。"""
    class ScenarioCaller(MockCaller):
        def __init__(self):
            intake = (
                '{"schema_version": "p2.day-intake.v1", "day_end": "22:00", '
                '"commitments": [{"title": "上课", "starts_at": "15:00", "ends_at": null, '
                '"starts_in_minutes": null, "duration_minutes": null, "location_text": "31教"}], '
                '"tasks": [{"title": "计组实验", "total_minutes": 90, "is_splittable": true, '
                '"minimum_slice_minutes": 15, "location_text": null}], '
                '"questions": [], "current_location": "9斋", "transport_mode": "bike"}'
            )
            super().__init__(intake=intake)

        def _unified(self, user):
            import json as _json
            return _json.dumps({
                "schema_version": "p3.unified-feedback.v1",
                "task_updates": [],
                "commitment_updates": [{
                    "target_commitment_ref": "day_commitment_001",
                    "action": "update_time", "title": None,
                    "starts_at": None, "ends_at": "17:00", "delay_minutes": None,
                }],
                "movement": {"has_movement": False, "origin_text": None,
                             "destination_text": None, "mode": None,
                             "depart_at": None, "arrive_by": None},
                "current_location": None, "questions": [], "reason": None,
            }, ensure_ascii=False)

    caller = ScenarioCaller()
    store = {}
    outcome, session, initial = intake_and_start(
        store, caller, real_map, "我现在在9斋，下午3点去31教上课，我骑车。",
        reference=dt(13, 35),
    )
    initial_window = next(window for window in initial.result.updated_state.windows
                          if window.starts_at == dt(13, 35))
    assert initial_window.ends_at == dt(14, 50)

    turn = session.apply_feedback("上课17点结束")
    state = turn.result.updated_state
    block = turn.movement_blocks[0]
    assert block.transition_start == dt(14, 50)
    assert block.window_start == dt(14, 55)
    assert block.end_time == dt(15)

    task_segments = []
    for window in state.windows:
        cursor = window.starts_at
        for allocation in sorted(
            (item for item in turn.result.allocation_plan.allocations
             if item.window_ref == window.window_ref),
            key=lambda item: (item.sequence_index, item.allocation_ref),
        ):
            end = cursor + timedelta(minutes=allocation.planned_minutes)
            if allocation.task_title == "计组实验":
                task_segments.append((cursor, end, allocation.planned_minutes))
            cursor = end
    assert task_segments == [(dt(13, 35), dt(14, 50), 75), (dt(17), dt(17, 15), 15)]
    assert sum(minutes for _, _, minutes in task_segments) == 90
    assert final_plan_overlap_errors(state, turn.result.allocation_plan, turn.movement_blocks) == ()

    from src.p2_companion_copy import extract_change_facts
    changes = extract_change_facts(initial.result.updated_state, state)
    assert any("已补充结束时间为17:00" in line for line in changes["commitment_changes"])
    assert not any("开始时间改为15:00" in line for line in changes["commitment_changes"])


# ---------------------------------------------------------------------------
# 2. commitment location 被 P3 解析为真实 node
# ---------------------------------------------------------------------------


def test_intake_commitment_location_resolved_via_p3(real_map):
    caller = MockCaller()
    store = {}
    outcome, session, turn = intake_and_start(
        store, caller, real_map, "我现在在9斋，下午3点去31教上课，我骑车。今天还要做实验。"
    )
    block = outcome.movement_blocks[0]
    assert block.destination_node_id == "building_31"
    assert block.origin_node_id == "zhengyuan_9zhai"
    assert block.distance_m > 0


# ---------------------------------------------------------------------------
# 3. current_location 被后续 replanning 继承
# ---------------------------------------------------------------------------


def test_intake_current_location_inherited_by_later_replanning(real_map):
    caller = MockCaller()
    store = {}
    outcome, session, turn = intake_and_start(
        store, caller, real_map, "我现在在9斋，下午3点去31教上课，我骑车。今天还要做实验。"
    )
    assert store[MOVEMENT_DATA_KEY]["current_location_text"] == "9斋"

    # 后续任务反馈：进度 +30，当前地点仍保留
    task_turn = session.apply_feedback("我刚又做了30分钟实验。")
    assert task_turn.result.updated_state.tasks[0].completed_minutes == 30
    assert store[MOVEMENT_DATA_KEY]["current_location_text"] == "9斋"

    # 无起点移动“等会去图书馆”复用继承的当前地点 9斋
    move_turn = session.apply_feedback("我从9斋出发，等会去图书馆。")
    move_blocks = [b for b in move_turn.movement_blocks if b.destination_node_id == "zhengdong_library"]
    assert len(move_blocks) == 1
    assert move_blocks[0].origin_node_id == "zhengyuan_9zhai"


# ---------------------------------------------------------------------------
# 4. 未给地点 / map_data=None 保持原 P2 行为
# ---------------------------------------------------------------------------


def test_intake_without_location_keeps_original_p2_behavior(real_map):
    caller = MockCaller(intake=INTAKE_NO_LOCATION)
    store = {}
    outcome, session, turn = intake_and_start(
        store, caller, real_map, "下午3点上课，今天做实验。"
    )
    assert outcome.applied is not None
    assert outcome.movement_blocks == ()
    assert outcome.movement_questions == ()
    assert outcome.current_location_text is None
    assert outcome.call_count == 3  # raw fallback + extraction + semantic audit
    text = render_live_page_text(turn)
    assert "前往" not in text
    assert "AI暂估）" not in text


def test_intake_without_map_data_keeps_original_p2_behavior():
    caller = MockCaller()
    outcome = run_live_intake(
        {}, dt(9), "我现在在9斋，下午3点去31教上课，我骑车。今天还要做实验。",
        caller, map_data=None,
    )
    assert outcome.applied is not None
    assert outcome.movement_blocks == ()
    assert outcome.movement_questions == ()
    # map_data 未提供时不进入 P3 空间理解，current_location_text 保持空
    assert outcome.current_location_text is None
    # 没有调用地点/时间模型（只有 intake 一次）
    location_or_time_calls = [
        item for item in caller.calls
        if "p3.location-resolution" in item[0] or "p3.travel-time" in item[0]
    ]
    assert location_or_time_calls == []


# ---------------------------------------------------------------------------
# 5. 未知地点只产生人话待确认
# ---------------------------------------------------------------------------


def test_intake_unknown_location_asks_human_question_only(real_map):
    caller = MockCaller(intake=INTAKE_UNKNOWN_DEST)
    store = {}
    outcome, session, turn = intake_and_start(
        store, caller, real_map, "我在9斋，下午3点去一个不认识的地方。"
    )
    assert outcome.applied is not None
    assert outcome.movement_blocks == ()  # 不编造路线
    assert len(outcome.movement_questions) >= 1
    for question in outcome.movement_questions:
        assert "一个不认识的地方" in question or "地点" in question
    # 人话待确认进入页面，无内部 ref / enum
    text = render_live_page_text(turn, intake_questions=outcome.movement_questions)
    assert "待确认问题" in text
    for marker in ("day_task_", "day_commitment_", "TaskProgress(", "schema_version", "UNRESOLVED"):
        assert marker not in text
    # intake 仍成功应用（task/commitment 已建立），只是移动待确认
    assert len(outcome.applied.state.tasks) == 1
    assert len(outcome.applied.state.commitments) == 1


# ---------------------------------------------------------------------------
# 6. 不重复创建 task / commitment
# ---------------------------------------------------------------------------


def test_intake_does_not_duplicate_tasks_or_commitments(real_map):
    caller = MockCaller()
    store = {}
    outcome, session, turn = intake_and_start(
        store, caller, real_map, "我现在在9斋，下午3点去31教上课，我骑车。今天还要做实验。"
    )
    assert len(outcome.applied.state.tasks) == 1
    assert len(outcome.applied.state.commitments) == 1
    # 后续任务反馈不新增任务/安排
    task_turn = session.apply_feedback("我刚又做了30分钟实验。")
    state = task_turn.result.updated_state
    assert len(state.tasks) == 1
    assert len(state.commitments) == 1
    # 再次基于同一 intake state 重新规划也不新增任务/安排
    again = session.start_day(outcome.applied.state, movement_blocks=outcome.movement_blocks)
    assert len(again.result.updated_state.tasks) == 1
    assert len(again.result.updated_state.commitments) == 1


# ---------------------------------------------------------------------------
# 7. 普通 rerun 不重复调用模型
# ---------------------------------------------------------------------------


def test_intake_rerun_does_not_repeat_model_calls(real_map):
    caller = MockCaller()
    store = {}
    outcome, session, turn = intake_and_start(
        store, caller, real_map, "我现在在9斋，下午3点去31教上课，我骑车。今天还要做实验。"
    )
    calls_after_start = len(caller.calls)
    # 模拟普通 rerun：只读 last_turn 并渲染，不触发任何模型调用
    for _ in range(3):
        turn = session.last_turn()
        render_live_page_text(turn)
    assert len(caller.calls) == calls_after_start
# ---------------------------------------------------------------------------
# 8. 页面级：_handle_intake_submit 真实路径（含 movement 数据落 store）
# ---------------------------------------------------------------------------


def test_live_handle_intake_submit_with_movement(real_map):
    from src.p2_live_main import _handle_intake_submit

    caller = MockCaller()
    stub = _IntakeStub()
    session = make_live_session(stub.session_state, FakeAdapter(caller), map_data=real_map)
    ok = _handle_intake_submit(
        stub,
        session,
        (),
        dt(9),
        "我现在在9斋，下午3点去31教上课，我骑车。今天还要做实验。",
        map_data=real_map,
    )
    assert ok
    assert stub.rerun_called == 1
    # 当前地点与移动块持久化到 session store，供后续 replanning 继承
    assert stub.session_state[MOVEMENT_DATA_KEY]["current_location_text"] == "9斋"
    assert len(stub.session_state[MOVEMENT_DATA_KEY]["active_blocks"]) == 1
    turn = session.last_turn()
    assert len(turn.movement_blocks) == 1
    # 页面 markdown 逐行渲染移动，无内部参数
    from src.p2_main import render_page_streamlit

    rendered = []
    stub.markdown_calls = rendered
    render_page_streamlit(stub, turn)
    assert any("骑行前往31教" in line for line in rendered)
    for marker in ("day_task_", "day_commitment_", "building_31", "zhengyuan_", "TaskProgress("):
        assert not any(marker in line for line in rendered)
# ---------------------------------------------------------------------------
# P3e 真实 Qwen 容错：不完整但合理的模型输出
# ---------------------------------------------------------------------------

INTAKE_PARTIAL_NO_END = (
    '{"schema_version": "p2.day-intake.v1", "day_end": "22:00", '
    '"commitments": [{"title": "上课", "starts_at": "15:00", "ends_at": null, '
    '"starts_in_minutes": null, "duration_minutes": null, "location_text": "31教"}], '
    '"tasks": [{"title": "计组实验", "total_minutes": 90, "is_splittable": true, '
    '"minimum_slice_minutes": 30, "location_text": null}], '
    '"questions": [], "current_location": "9斋", "transport_mode": "bike"}'
)
INTAKE_TRANSPORT_ALIAS = (
    '{"schema_version": "p2.day-intake.v1", "day_end": "22:00", '
    '"commitments": [{"title": "上课", "starts_at": "15:00", "ends_at": "16:00", '
    '"starts_in_minutes": null, "duration_minutes": null, "location_text": "31教"}], '
    '"tasks": [{"title": "计组实验", "total_minutes": 90, "is_splittable": true, '
    '"minimum_slice_minutes": 30, "location_text": null}], '
    '"questions": [], "current_location": "9斋", "transport_mode": "骑行"}'
)


def test_partial_commitment_start_location_kept_movement_planned(real_map):
    caller = MockCaller(intake=INTAKE_PARTIAL_NO_END)
    store = {}
    outcome, session, turn = intake_and_start(
        store, caller, real_map, "我现在在9斋，下午3点去31教上课，我骑车。这之前想做一下计组实验，大概还要一个半小时。"
    )
    assert outcome.applied is not None
    # 只有 starts_at + location 的 commitment 被保留
    commitment = outcome.applied.state.commitments[0]
    assert commitment.title == "上课"
    assert commitment.starts_at == dt(15)
    assert commitment.ends_at is None
    assert commitment.location_text == "31教"
    # 移动不依赖 end time：9斋 -> 31教 bike，15:00 前到达
    assert len(outcome.movement_blocks) == 1
    block = outcome.movement_blocks[0]
    assert block.origin_node_id == "zhengyuan_9zhai"
    assert block.destination_node_id == "building_31"
    assert block.end_time == dt(15)
    # 待确认问题只问缺失的结束时间，不再问开始时间
    assert outcome.questions
    assert "结束" in outcome.questions[0]
    assert "开始" not in outcome.questions[0]
    # 页面渲染：有骑行移动、有“起”格式的部分安排，无内部参数
    text = render_live_page_text(turn)
    assert "骑行前往31教" in text
    assert "15:00起：上课" in text
    for marker in ("day_task_", "day_commitment_", "building_31", "zhengyuan_", "TaskProgress("):
        assert marker not in text


def test_combined_feedback_preserves_progress_and_movement_context(real_map):
    caller = MockCaller(intake=INTAKE_PARTIAL_NO_END)
    store = {}
    outcome, session, turn = intake_and_start(
        store, caller, real_map, "我现在在9斋，下午3点去31教上课，我骑车。这之前想做一下计组实验，大概还要一个半小时。"
    )
    assert store[MOVEMENT_DATA_KEY]["current_location_text"] == "9斋"
    assert store[MOVEMENT_DATA_KEY]["last_request"] is not None
    # 组合反馈：还是骑车吧 + progress +20
    follow = session.apply_feedback("算了还是骑车吧，另外实验我刚又做了20分钟。")
    # 不出现“从哪里出发”等错误问题
    assert follow.all_questions == ()
    # progress 生效
    assert follow.result.updated_state.tasks[0].completed_minutes == 20
    # 已有 9斋->31教 移动上下文保留，且仍落在 15:00 前
    assert len(follow.movement_blocks) == 1
    block = follow.movement_blocks[0]
    assert block.origin_node_id == "zhengyuan_9zhai"
    assert block.destination_node_id == "building_31"
    assert block.end_time == dt(15)
    # 页面只渲染一个“当前方案”主区块，无内部参数
    text = render_live_page_text(follow)
    assert text.count("# 当前方案") == 1
    assert "骑行前往31教" in text
    for marker in ("day_task_", "day_commitment_", "building_31", "zhengyuan_"):
        assert marker not in text


def test_transport_mode_alias_parsed_tolerantly(real_map):
    caller = MockCaller(intake=INTAKE_TRANSPORT_ALIAS)
    store = {}
    outcome, session, turn = intake_and_start(
        store, caller, real_map, "我现在在9斋，下午3点去31教上课，我骑车。"
    )
    assert len(outcome.movement_blocks) == 1
    assert outcome.movement_blocks[0].mode is TransportMode.BIKE


def test_partial_commitment_question_not_repeated_after_followup(real_map):
    # 结束后问题只在首次出现一次；后续 feedback 不再重复询问 starts_at
    caller = MockCaller(intake=INTAKE_PARTIAL_NO_END)
    store = {}
    outcome, session, turn = intake_and_start(
        store, caller, real_map, "我现在在9斋，下午3点去31教上课，我骑车。"
    )
    assert outcome.questions == ("“上课”大约几点结束？",)
    follow = session.apply_feedback("我刚又做了20分钟实验。")
    assert "开始" not in "".join(follow.all_questions)
