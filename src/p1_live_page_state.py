"""State helpers for the independent P1 live page."""
from datetime import datetime


PREFIX = "p1_live_"
INPUT_KEY = PREFIX + "input"
TIME_TEXT_KEY = PREFIX + "time_text"
TIME_KEY = PREFIX + "time"
VIEW_KEY = PREFIX + "view"
OPTION_KEY = PREFIX + "option"
PENDING_KEY = PREFIX + "pending_confirmation"
PENDING_INPUT_KEY = PREFIX + "pending_value"
NEXT_INPUT_KEY = PREFIX + "next_input"
INTERACTION_KEY = PREFIX + "interaction_context"
ERROR_KEY = PREFIX + "error"


def initialize(state, now):
    """Initialize once; ordinary reruns never overwrite user edits."""
    state.setdefault(INPUT_KEY, "")
    state.setdefault(TIME_KEY, now)
    state.setdefault(TIME_TEXT_KEY, now.strftime("%Y-%m-%d %H:%M"))


def parse_reference_time(state):
    """Parse editable text only on submission and retain the last valid time."""
    value = state.get(TIME_TEXT_KEY)
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return None
    state[TIME_KEY] = parsed
    return parsed


def save_option(state, option):
    selected = {
        "action": option.action,
        "value": option.value,
        "label": option.label,
        "target_ref": option.target_ref,
        "field_name": option.field_name,
        "target_label": option.target_label,
    }
    state[OPTION_KEY] = dict(selected)
    state[PENDING_KEY] = selected
    state.pop(PENDING_INPUT_KEY, None)


def cancel_pending(state):
    state.pop(PENDING_KEY, None)
    state.pop(PENDING_INPUT_KEY, None)
    state.pop(OPTION_KEY, None)


def _positive_minutes(value):
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return None
    return minutes if minutes > 0 else None


def build_confirmation_supplement(pending, supplied_value=None):
    if not isinstance(pending, dict):
        return None
    action = pending.get("action")
    target_label = pending.get("target_label") or "任务"
    selected_value = pending.get("value")
    value = selected_value if supplied_value in (None, "") else supplied_value
    if action in ("confirm_estimate", "adjust_duration"):
        minutes = _positive_minutes(value)
        if minutes is None:
            return None
        return "补充确认：“%s”的预计时长按%d分钟。" % (target_label, minutes)
    if action in ("set_duration", "extend_time"):
        minutes = _positive_minutes(value)
        if minutes is None:
            return None
        return "补充信息：这次可用时间为%d分钟。" % minutes
    if action in ("set_location", "update_location"):
        if not isinstance(value, str) or not value.strip():
            return None
        if pending.get("field_name") == "current_location_text":
            return "补充信息：我现在在%s。" % value.strip()
        return "补充信息：“%s”的地点是%s。" % (target_label, value.strip())
    if action in ("change_task", "add_task"):
        if not isinstance(value, str) or not value.strip():
            return None
        return "补充任务：%s。" % value.strip().rstrip("。")
    if action == "agent_choice":
        if not isinstance(value, str) or not value.strip():
            return None
        return "补充确认：我选择“%s”。" % value.strip()
    if action == "set_location_requirement":
        if selected_value == "no_specific_location":
            return "补充确认：“%s”不需要特定地点。" % target_label
        if selected_value == "location_requirement_unknown":
            return "补充确认：“%s”的地点要求暂不确定。" % target_label
        if selected_value == "specific_location":
            if not isinstance(supplied_value, str) or not supplied_value.strip():
                return None
            return "补充确认：“%s”需要在%s完成。" % (
                target_label, supplied_value.strip())
    return None


def prepare_confirmation_application(state, supplied_value=None):
    supplement = build_confirmation_supplement(state.get(PENDING_KEY), supplied_value)
    if supplement is None:
        return None
    pending = state.get(PENDING_KEY)
    view = state.get(VIEW_KEY)
    previous = None
    if view is not None:
        steps = []
        card = getattr(view, "primary_card", None)
        if card is not None:
            steps = [
                {"title": item.title, "minutes": item.planned_minutes}
                for item in card.steps
            ]
        previous = {"headline": getattr(view, "headline", None), "steps": steps}
    context = {
        "original_user_input": state.get(INPUT_KEY, ""),
        "previous_suggestion": previous,
        "confirmation": {
            "action": pending.get("action"),
            "selected_value": pending.get("value"),
            "target_ref": pending.get("target_ref"),
            "field_name": pending.get("field_name"),
            "target_label": pending.get("target_label"),
            "user_value": supplied_value,
            "supplement_text": supplement,
        },
    }
    state[INTERACTION_KEY] = context
    state.pop(VIEW_KEY, None)
    state.pop(ERROR_KEY, None)
    cancel_pending(state)
    return context


def apply_deferred_input(state):
    if NEXT_INPUT_KEY in state:
        state[INPUT_KEY] = state.pop(NEXT_INPUT_KEY)


def reset(state, now):
    for key in tuple(state.keys()):
        if key.startswith(PREFIX):
            state.pop(key, None)
    initialize(state, now)
