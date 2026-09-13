from datetime import datetime

from src.p1_live_page_state import (
    ERROR_KEY,
    INPUT_KEY,
    INTERACTION_KEY,
    NEXT_INPUT_KEY,
    OPTION_KEY,
    PENDING_INPUT_KEY,
    PENDING_KEY,
    TIME_KEY,
    TIME_TEXT_KEY,
    VIEW_KEY,
    apply_deferred_input,
    build_confirmation_supplement,
    cancel_pending,
    initialize,
    parse_reference_time,
    prepare_confirmation_application,
    reset,
    save_option,
)
from src.p1_view_models import QuickOptionView


def test_live_time_is_initialized_once_with_stable_text():
    state = {}
    now = datetime(2026, 5, 1, 10, 5)
    initialize(state, now)
    assert state[TIME_KEY] == now
    assert state[TIME_TEXT_KEY] == "2026-05-01 10:05"


def test_ordinary_rerun_does_not_overwrite_time_or_user_text():
    state = {}
    initialize(state, datetime(2026, 5, 1, 10, 5))
    state[TIME_TEXT_KEY] = "2026-05-03 08:30"
    state[INPUT_KEY] = "用户修改后的输入"
    initialize(state, datetime(2026, 5, 2, 11, 0))
    assert state[TIME_TEXT_KEY] == "2026-05-03 08:30"
    assert state[INPUT_KEY] == "用户修改后的输入"


def test_valid_time_text_is_parsed_only_when_requested():
    state = {TIME_TEXT_KEY: "2026-05-03 08:30",
             TIME_KEY: datetime(2026, 5, 1, 10, 5)}
    parsed = parse_reference_time(state)
    assert parsed == datetime(2026, 5, 3, 8, 30)
    assert state[TIME_KEY] == parsed


def test_invalid_time_format_does_not_replace_last_valid_time():
    original = datetime(2026, 5, 1, 10, 5)
    state = {TIME_TEXT_KEY: "2026/05/03 08:30", TIME_KEY: original}
    assert parse_reference_time(state) is None
    assert state[TIME_KEY] == original


def test_nonexistent_date_is_rejected():
    state = {TIME_TEXT_KEY: "2026-02-30 08:30"}
    assert parse_reference_time(state) is None


def test_quick_option_saves_action_value_label_and_structured_target():
    state = {}
    save_option(state, QuickOptionView(
        "填写地点", "update_location", "图书馆",
        "current_context", "current_location_text", "当前位置"))
    assert state[OPTION_KEY] == {
        "action": "update_location",
        "value": "图书馆",
        "label": "填写地点",
        "target_ref": "current_context",
        "field_name": "current_location_text",
        "target_label": "当前位置",
    }
    assert state[PENDING_KEY] == state[OPTION_KEY]


def test_confirmation_supplements_cover_estimate_duration_location_and_requirement():
    estimate = {"action": "confirm_estimate", "value": "120",
                "target_label": "完成计组实验3"}
    assert build_confirmation_supplement(estimate) == \
        "补充确认：“完成计组实验3”的预计时长按120分钟。"
    adjust = dict(estimate, action="adjust_duration", value=None)
    assert "预计时长按60分钟" in build_confirmation_supplement(adjust, 60)
    location = {"action": "set_location", "value": None,
                "field_name": "current_location_text", "target_label": "当前位置"}
    assert build_confirmation_supplement(location, "31教学楼") == \
        "补充信息：我现在在31教学楼。"
    requirement = {"action": "set_location_requirement",
                   "value": "no_specific_location", "target_label": "背单词"}
    assert build_confirmation_supplement(requirement) == \
        "补充确认：“背单词”不需要特定地点。"


def test_confirmation_supplements_cover_available_time_task_and_specific_location():
    duration = {"action": "extend_time", "value": "60", "target_label": "当前空档"}
    assert build_confirmation_supplement(duration) == "补充信息：这次可用时间为60分钟。"
    task = {"action": "change_task", "value": None, "target_label": "任务"}
    assert build_confirmation_supplement(task, "整理实验报告") == "补充任务：整理实验报告。"
    location = {"action": "set_location_requirement", "value": "specific_location",
                "target_label": "背单词"}
    assert build_confirmation_supplement(location, "图书馆") == \
        "补充确认：“背单词”需要在图书馆完成。"


def test_prepare_application_keeps_original_input_and_builds_structured_context():
    state = {INPUT_KEY: "原始输入", VIEW_KEY: object(), ERROR_KEY: "old"}
    save_option(state, QuickOptionView(
        "就按这个", "confirm_estimate", "120", "input-task-1",
        "estimated_total_minutes", "完成计组实验3"))
    context = prepare_confirmation_application(state)
    assert state[INPUT_KEY] == "原始输入"
    assert context["original_user_input"] == "原始输入"
    assert context["confirmation"]["selected_value"] == "120"
    assert context["confirmation"]["target_label"] == "完成计组实验3"
    assert "预计时长按120分钟" in context["confirmation"]["supplement_text"]
    assert state[INTERACTION_KEY] is context
    assert VIEW_KEY not in state and PENDING_KEY not in state
    assert OPTION_KEY not in state and ERROR_KEY not in state
    assert NEXT_INPUT_KEY not in state


def test_cancel_pending_does_not_modify_input():
    state = {INPUT_KEY: "原始输入", PENDING_INPUT_KEY: "30"}
    save_option(state, QuickOptionView("调整时长", "adjust_duration"))
    cancel_pending(state)
    assert state[INPUT_KEY] == "原始输入"
    assert PENDING_KEY not in state and OPTION_KEY not in state


def test_reset_clears_all_live_keys_preserves_other_pages_and_uses_new_time():
    new_time = datetime(2026, 5, 4, 9, 15)
    state = {
        "p0_request": "keep",
        "p1_input": "keep-offline",
        INPUT_KEY: "clear",
        TIME_TEXT_KEY: "2026-05-03 08:30",
        VIEW_KEY: object(),
        OPTION_KEY: object(),
        PENDING_KEY: object(),
        PENDING_INPUT_KEY: "value",
        INTERACTION_KEY: {"confirmation": {}},
        ERROR_KEY: "clear",
    }
    reset(state, new_time)
    assert state["p0_request"] == "keep"
    assert state["p1_input"] == "keep-offline"
    assert state[INPUT_KEY] == ""
    assert state[TIME_KEY] == new_time
    assert state[TIME_TEXT_KEY] == "2026-05-04 09:15"
    assert VIEW_KEY not in state and OPTION_KEY not in state and ERROR_KEY not in state
    assert PENDING_KEY not in state and PENDING_INPUT_KEY not in state
    assert INTERACTION_KEY not in state
