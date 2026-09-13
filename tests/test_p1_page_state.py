from datetime import datetime

from src.p1_page_state import (ERROR_KEY, INPUT_KEY, OPTION_KEY, TIME_KEY, VIEW_KEY,
                               initialize_state, load_example, save_option, start_new_plan)
from src.p1_view_models import QuickOptionView


def test_time_initializes_once_and_user_value_survives_reruns():
    state = {}; first = datetime(2026, 5, 1, 10, 5)
    initialize_state(state, first); state[TIME_KEY] = datetime(2026, 5, 2, 9, 0)
    initialize_state(state, datetime(2026, 5, 3, 8, 0))
    assert state[TIME_KEY] == datetime(2026, 5, 2, 9, 0)


def test_example_option_and_new_plan_only_touch_p1_keys():
    state = {"p0_key":"keep", VIEW_KEY:"view", ERROR_KEY:"error"}
    initialize_state(state, datetime(2026, 5, 1, 10, 5)); load_example(state, "样例")
    save_option(state, QuickOptionView("确认", "confirm", "x"))
    assert state[INPUT_KEY] == "样例" and state[OPTION_KEY]["action"] == "confirm"
    start_new_plan(state, datetime(2026, 5, 2, 10, 5))
    assert state["p0_key"] == "keep" and VIEW_KEY not in state and ERROR_KEY not in state
    assert state[TIME_KEY] == datetime(2026, 5, 2, 10, 5)


def test_first_initialization_sets_time_and_input():
    state = {}; now = datetime(2026, 5, 1, 10, 5)
    initialize_state(state, now)
    assert state[TIME_KEY] == now and state[INPUT_KEY] == ""


def test_load_example_only_updates_input_value():
    state = {TIME_KEY: datetime(2026,5,1,10,5), "p1_result_view":"old"}
    load_example(state, "样例二")
    assert state[INPUT_KEY] == "样例二" and state["p1_result_view"] == "old"


def test_option_preserves_label_action_and_value():
    state = {}; save_option(state, QuickOptionView("30 分钟", "set_duration", "30"))
    assert state[OPTION_KEY] == {"label":"30 分钟", "action":"set_duration", "value":"30"}
