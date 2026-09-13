"""P2e 反馈路由测试（Python 3.8 兼容，全部 mock，不请求 API）。"""

from datetime import datetime

import pytest

from src.p1_models import SourceKind
from src.p2_feedback_router import (
    ALLOWED_ROUTES,
    ROUTE_BOTH,
    ROUTE_COMMITMENT,
    ROUTER_SCHEMA_VERSION,
    ROUTE_TASK,
    ROUTE_UNKNOWN,
    WARNING_ROUTER_FAILED,
    build_router_prompt,
    parse_router_output,
    route_feedback,
)
from src.p2_agentic_parser import AgenticParseError
from src.p1_window_models import AvailabilityLevel, FixedCommitment
from src.p2_models import TaskProgress, TaskState
from src.p2_window_derivation import derive_day_state



def dt(h, m=0):
    return datetime(2026, 9, 1, h, m)


def make_task(ref, title):
    return TaskProgress(ref, title, 60, 0, SourceKind.AI_ESTIMATED, TaskState.ACTIVE, True, 15)


def build_state():
    commitments = (
        FixedCommitment("c1", "上课", "上课", dt(10), dt(11), None, AvailabilityLevel.UNAVAILABLE, {}, ()),
    )
    tasks = (make_task("t1", "计组实验"), make_task("t2", "背单词"))
    return derive_day_state(dt(9), dt(22), commitments, tasks, 0, {})


def router_json(route, reason=None):
    payload = '{"schema_version": "' + ROUTER_SCHEMA_VERSION + '", "route": "' + route + '", "reason": '
    payload += "null" if reason is None else '"' + reason + '"'
    return payload + "}"


class FakeCaller:
    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls = []

    def __call__(self, system, user):
        self.calls.append((system, user))
        if not self.outputs:
            return "{}"
        return self.outputs.pop(0)


def test_parse_router_output_valid():
    parsed = parse_router_output(router_json("task", "进度反馈"))
    assert parsed.route == "task"
    assert parsed.reason == "进度反馈"
    assert parsed.schema_version == ROUTER_SCHEMA_VERSION


def test_parse_accepts_all_routes():
    for route in ALLOWED_ROUTES:
        assert parse_router_output(router_json(route)).route == route


def test_parse_rejects_bad_route():
    with pytest.raises(AgenticParseError):
        parse_router_output(router_json("everything"))


def test_parse_rejects_missing_route():
    with pytest.raises(AgenticParseError):
        parse_router_output('{"schema_version": "' + ROUTER_SCHEMA_VERSION + '", "reason": null}')


def test_parse_rejects_wrong_schema():
    with pytest.raises(AgenticParseError):
        parse_router_output(
            '{"schema_version": "p2.task-reconciliation.v1", "route": "task", "reason": null}'
        )


def test_parse_tolerates_code_fence():
    text = "```json\n" + router_json("commitment") + "\n```"
    assert parse_router_output(text).route == "commitment"


def test_route_feedback_success():
    caller = FakeCaller(router_json("commitment"))
    state = build_state()
    route, warning = route_feedback("组会推迟到3点。", state, caller)
    assert route == ROUTE_COMMITMENT
    assert warning is None
    assert len(caller.calls) == 1


def test_route_feedback_repair_recovers():
    caller = FakeCaller("不是 JSON", router_json("task"))
    state = build_state()
    route, warning = route_feedback("我又做了30分钟实验。", state, caller)
    assert route == ROUTE_TASK
    assert warning is None
    assert len(caller.calls) == 2


def test_route_feedback_fallback_both():
    caller = FakeCaller("不是 JSON", "还是不是 JSON")
    state = build_state()
    route, warning = route_feedback("今天感觉不错。", state, caller)
    assert route == ROUTE_BOTH
    assert warning == WARNING_ROUTER_FAILED
    assert len(caller.calls) == 2


def test_route_feedback_unclear_passthrough():
    caller = FakeCaller(router_json(ROUTE_UNKNOWN))
    state = build_state()
    route, warning = route_feedback("今天感觉不错。", state, caller)
    assert route == ROUTE_UNKNOWN
    assert warning is None


def test_router_prompt_contains_titles_and_user_text():
    state = build_state()
    system, user = build_router_prompt(state, "我刚又做了30分钟实验。")
    assert "task|commitment|both|unclear" in system
    assert "计组实验" in user
    assert "背单词" in user
    assert "我刚又做了30分钟实验。" in user
    assert "day_task_" not in user


def test_route_feedback_rejects_empty_text():
    state = build_state()
    with pytest.raises(ValueError):
        route_feedback("   ", state, FakeCaller(router_json("task")))
