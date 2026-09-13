from unittest.mock import Mock

import pytest

from src.main import (
    build_planning_display,
    run_planning_request,
    validate_planning_inputs,
)


@pytest.mark.parametrize("user_input", ["", "   ", None])
def test_empty_user_input_returns_chinese_prompt(user_input):
    message = validate_planning_inputs(user_input, "09:00")

    assert message == "请输入任务信息后再开始规划。"


@pytest.mark.parametrize("user_input", ["", "   ", None, 123])
def test_invalid_user_input_stops_before_map_and_planning(user_input):
    map_loader = Mock()
    planner = Mock()
    formatter = Mock()

    text = build_planning_display(
        user_input,
        "09:00",
        map_loader=map_loader,
        planner=planner,
        formatter=formatter,
    )

    assert text.strip()
    assert any("\u4e00" <= character <= "\u9fff" for character in text)
    map_loader.assert_not_called()
    planner.assert_not_called()
    formatter.assert_not_called()


@pytest.mark.parametrize(
    "current_time",
    ["", "9:00", "09:60", "24:00", "invalid", None],
)
def test_invalid_time_returns_chinese_prompt(current_time):
    message = validate_planning_inputs("从宿舍去校门", current_time)

    assert message is not None
    assert "时间" in message
    assert "HH:MM" in message


@pytest.mark.parametrize("current_time", ["", "9:00", "24:00", "09:60", None, 900])
def test_invalid_time_stops_before_map_and_planning(current_time):
    map_loader = Mock()
    planner = Mock()
    formatter = Mock()

    text = build_planning_display(
        "从宿舍去校门",
        current_time,
        map_loader=map_loader,
        planner=planner,
        formatter=formatter,
    )

    assert text.strip()
    assert any("\u4e00" <= character <= "\u9fff" for character in text)
    map_loader.assert_not_called()
    planner.assert_not_called()
    formatter.assert_not_called()


def test_valid_inputs_have_no_validation_error():
    assert validate_planning_inputs("从宿舍去校门", "09:30") is None


def test_planning_action_calls_pipeline_with_inputs():
    campus_map = Mock(name="campus_map")
    pipeline_result = Mock(name="pipeline_result")
    planner = Mock(return_value=pipeline_result)
    formatter = Mock(return_value="推荐路线：dormitory → gate")

    text = run_planning_request(
        campus_map,
        "从宿舍去校门",
        "09:30",
        planner=planner,
        formatter=formatter,
    )

    planner.assert_called_once_with(campus_map, "从宿舍去校门", "09:30")
    formatter.assert_called_once_with(pipeline_result)
    assert text == "推荐路线：dormitory → gate"


def test_display_text_is_exact_formatter_output():
    expected_text = "规划成功\n预计步行：20 分钟"
    planner = Mock(return_value=Mock())
    formatter = Mock(return_value=expected_text)

    text = run_planning_request(
        Mock(),
        "规划路线",
        "10:00",
        planner=planner,
        formatter=formatter,
    )

    assert text == expected_text


def test_valid_submission_calls_planner_and_formatter():
    campus_map = Mock(name="campus_map")
    pipeline_result = Mock(name="pipeline_result")
    planner = Mock(return_value=pipeline_result)
    formatter = Mock(return_value="完整中文规划结果")
    map_loader = Mock(return_value=campus_map)

    text = build_planning_display(
        "  从宿舍去校门  ",
        " 09:30 ",
        map_loader=map_loader,
        planner=planner,
        formatter=formatter,
    )

    map_loader.assert_called_once_with()
    planner.assert_called_once_with(campus_map, "从宿舍去校门", "09:30")
    formatter.assert_called_once_with(pipeline_result)
    assert text == "完整中文规划结果"


def test_map_load_failure_returns_safe_chinese_error_without_planning():
    planner = Mock()
    formatter = Mock()

    text = build_planning_display(
        "从宿舍去校门",
        "09:30",
        map_loader=Mock(side_effect=ValueError("secret traceback detail")),
        planner=planner,
        formatter=formatter,
    )

    assert text == "测试地图加载失败，请稍后重试。"
    assert "secret" not in text
    assert "traceback" not in text.casefold()
    planner.assert_not_called()
    formatter.assert_not_called()


def test_planner_exception_returns_safe_error_without_formatting():
    formatter = Mock()
    planner = Mock(
        side_effect=RuntimeError('Bearer test-secret {"api_key":"secret"}')
    )

    text = build_planning_display(
        "从宿舍去校门",
        "09:30",
        map_loader=Mock(return_value=Mock()),
        planner=planner,
        formatter=formatter,
    )

    assert text == "规划过程中发生错误，请检查输入信息后重试。"
    formatter.assert_not_called()
    for sensitive_text in (
        "Bearer",
        "test-secret",
        "api_key",
        "{",
        "}",
        "RuntimeError",
        "traceback",
    ):
        assert sensitive_text.casefold() not in text.casefold()


def test_formatter_exception_returns_safe_error_without_leaking_details():
    pipeline_result = Mock()
    planner = Mock(return_value=pipeline_result)
    formatter = Mock(
        side_effect=RuntimeError(
            'Authorization: Bearer formatter-secret ["raw-json"]'
        )
    )

    text = build_planning_display(
        "从宿舍去校门",
        "09:30",
        map_loader=Mock(return_value=Mock()),
        planner=planner,
        formatter=formatter,
    )

    planner.assert_called_once()
    formatter.assert_called_once_with(pipeline_result)
    assert text == "规划过程中发生错误，请检查输入信息后重试。"
    for sensitive_text in (
        "Authorization",
        "Bearer",
        "formatter-secret",
        "raw-json",
        "[",
        "]",
        "RuntimeError",
        "traceback",
    ):
        assert sensitive_text.casefold() not in text.casefold()
