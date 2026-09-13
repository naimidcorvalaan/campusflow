"""P3e 移动意图解析测试（全 mock，不请求真实 API）。"""
import pytest

from src.p2_agentic_parser import AgenticParseError
from src.p3_movement_intent import (
    MOVEMENT_INTENT_SCHEMA_VERSION,
    WARNING_INTENT_FAILED,
    MovementIntentAction,
    build_movement_intent_prompt,
    detect_movement_intent,
    parse_movement_intent,
)


def intent_json(has_movement, action, origin=None, dest=None, mode=None, reason=None):
    def _field(value):
        return "null" if value is None else '"' + value + '"'

    return (
        '{"schema_version": "' + MOVEMENT_INTENT_SCHEMA_VERSION + '", "has_movement": '
        + str(has_movement).lower() + ', "action": "' + action + '", "origin_text": '
        + _field(origin) + ', "destination_text": ' + _field(dest) + ', "mode": '
        + _field(mode) + ', "reason": ' + _field(reason) + "}"
    )


class FakeCaller:
    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls = []

    def __call__(self, system, user):
        self.calls.append((system, user))
        if not self.outputs:
            return "{}"
        return self.outputs.pop(0)


def test_parse_valid_add():
    intent = parse_movement_intent(intent_json(True, "add", "9斋", "31教", "bike"))
    assert intent.has_movement is True
    assert intent.action == MovementIntentAction.ADD.value
    assert intent.origin_text == "9斋"
    assert intent.destination_text == "31教"
    assert intent.mode == "bike"


def test_parse_valid_change_mode():
    intent = parse_movement_intent(intent_json(True, "change_mode", mode="walk"))
    assert intent.action == MovementIntentAction.CHANGE_MODE.value
    assert intent.mode == "walk"


def test_parse_valid_none():
    intent = parse_movement_intent(intent_json(False, "none"))
    assert intent.has_movement is False
    assert intent.action == "none"


def test_parse_rejects_non_bool_has_movement():
    with pytest.raises(AgenticParseError):
        parse_movement_intent(intent_json("true", "none"))


def test_parse_rejects_unknown_action():
    with pytest.raises(AgenticParseError):
        parse_movement_intent(intent_json(True, "teleport", "a", "b"))


def test_parse_rejects_add_missing_origin():
    with pytest.raises(AgenticParseError):
        parse_movement_intent(intent_json(True, "add", dest="31教"))


def test_parse_rejects_change_mode_without_mode():
    with pytest.raises(AgenticParseError):
        parse_movement_intent(intent_json(True, "change_mode"))


def test_parse_rejects_invalid_mode():
    with pytest.raises(AgenticParseError):
        parse_movement_intent(intent_json(True, "add", "9斋", "31教", "car"))
    with pytest.raises(AgenticParseError):
        parse_movement_intent(intent_json(True, "change_mode", mode="true"))


def test_parse_rejects_has_movement_false_with_action():
    with pytest.raises(AgenticParseError):
        parse_movement_intent(intent_json(False, "add", "9斋", "31教"))


def test_parse_rejects_has_movement_true_none():
    with pytest.raises(AgenticParseError):
        parse_movement_intent(intent_json(True, "none"))


def test_parse_rejects_wrong_schema():
    with pytest.raises(AgenticParseError):
        parse_movement_intent(
            '{"schema_version": "p2.task-reconciliation.v1", "has_movement": false, '
            '"action": "none", "origin_text": null, "destination_text": null, '
            '"mode": null, "reason": null}'
        )


def test_parse_rejects_overlong_location_text():
    with pytest.raises(AgenticParseError):
        parse_movement_intent(intent_json(True, "add", "地" * 90, "31教"))


def test_detect_success_single_call():
    caller = FakeCaller(intent_json(True, "add", "9斋", "31教", "bike"))
    intent, warning = detect_movement_intent("我在9斋，下午3点去31教，我骑车。", caller)
    assert warning is None
    assert intent.has_movement is True
    assert intent.action == "add"
    assert len(caller.calls) == 1


def test_detect_repair_recovers():
    caller = FakeCaller("不是 JSON", intent_json(True, "change_mode", mode="walk"))
    intent, warning = detect_movement_intent("改成走路吧", caller)
    assert warning is None
    assert intent.action == "change_mode"
    assert len(caller.calls) == 2


def test_detect_repair_failure_is_noop_with_warning():
    caller = FakeCaller("坏输出", "还是坏输出")
    intent, warning = detect_movement_intent("今天先不背单词了", caller)
    assert intent.has_movement is False
    assert intent.action == "none"
    assert warning == WARNING_INTENT_FAILED
    assert len(caller.calls) == 2


def test_prompt_contains_examples_no_internal_refs():
    system, user = build_movement_intent_prompt("我在北菜，等会去图书馆。")
    assert "p3.movement-intent.v1" in system
    assert "add" in system and "change_mode" in system
    assert "9斋" in system and "北菜" in system
    assert "我在北菜，等会去图书馆。" in user
    for token in ("task_ref", "day_task_", "ACTIVE", "SKIPPED_TODAY", "COMPLETED"):
        assert token not in system
