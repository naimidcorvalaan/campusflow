from datetime import datetime
import pytest

from src.p1_window_prompt_builder import build_p1_window_prompt, build_p1_window_system_prompt


def test_system_user_prompt_are_separate_and_forbid_routes():
    text = "我现在在31教学楼，11:30去46楼上课"
    system, user = build_p1_window_prompt(text, datetime(2026, 1, 1, 10))
    assert text not in system and text in user
    assert "p1.window-extraction.v2" in system
    assert "不要猜测用户没有提供的课程时间、地点、空闲时长" in system
    assert "确认问题都由程序确定性生成" in system
    assert "路线" in system and "推荐" in system


def test_window_prompt_has_complete_relative_time_and_cross_midnight_examples():
    prompt = build_p1_window_system_prompt()
    assert "90分钟后" in prompt and "2026-08-16T11:30:00" in prompt
    assert "2小时后" in prompt and "2026-08-16T12:00:00" in prompt
    assert "半小时后" in prompt and "增加 30 分钟" in prompt
    assert "跨午夜" in prompt and "2026-08-17T01:00:00" in prompt


def test_user_prompt_includes_trusted_reference_time():
    _, user = build_p1_window_prompt("空闲两小时", datetime(2026, 1, 1, 10, 5))
    assert "2026-01-01T10:05:00" in user


@pytest.mark.parametrize("text,reference", [(None, datetime.now()), ("", datetime.now()), ("输入", "bad")])
def test_invalid_prompt_inputs_rejected(text, reference):
    with pytest.raises((TypeError, ValueError)):
        build_p1_window_prompt(text, reference)
