"""P1j 可离线测试的 session 状态转换。"""
from datetime import datetime


INPUT_KEY = "p1_input"
TIME_KEY = "p1_assumed_datetime"
VIEW_KEY = "p1_result_view"
OPTION_KEY = "p1_selected_option"
ERROR_KEY = "p1_error"


def initialize_state(state, now):
    if TIME_KEY not in state:
        state[TIME_KEY] = now
    state.setdefault(INPUT_KEY, "")


def load_example(state, text):
    state[INPUT_KEY] = text


def save_option(state, option):
    state[OPTION_KEY] = {"action": option.action, "value": option.value, "label": option.label}


def start_new_plan(state, now):
    for key in (INPUT_KEY, VIEW_KEY, OPTION_KEY, ERROR_KEY, TIME_KEY, "p1_assumed_datetime_text"):
        state.pop(key, None)
    initialize_state(state, now)
