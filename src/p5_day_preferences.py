"""Explicit, day-scoped user preferences extracted by Qwen."""

import json
from dataclasses import asdict, dataclass, replace
from typing import Optional, Tuple

from src.p2_agentic_parser import AgenticParseError, extract_json_object
from src.p5_agent_context import AgentDecisionContext
from src.p5_agent_runtime import call_structured_stage

DAY_PREFERENCE_KEY = "p5_day_preference_profile"
PREFERENCE_SCHEMA_VERSION = "p5.day-preference.v1"


@dataclass(frozen=True)
class TaskSpecificPreference:
    task_ref: str
    preference: str

    def __post_init__(self):
        _text(self.task_ref, "task_ref")
        _text(self.preference, "preference")


@dataclass(frozen=True)
class DayPreferenceProfile:
    slack_preference: str = "default"
    meal_timing_preference: str = "default"
    fragmentation_tolerance: str = "default"
    task_switching_tolerance: str = "default"
    concurrency_suggestion_preference: str = "neutral"
    movement_buffer_preference: str = "default"
    task_specific_preferences: Tuple[TaskSpecificPreference, ...] = ()
    provenance: str = "explicit_user"
    last_updated_turn: int = 0

    def __post_init__(self):
        _choice(self.slack_preference, {"default", "more", "less"}, "slack_preference")
        _choice(self.meal_timing_preference, {"default", "earlier", "later"}, "meal_timing_preference")
        _choice(self.fragmentation_tolerance, {"default", "lower", "higher"}, "fragmentation_tolerance")
        _choice(self.task_switching_tolerance, {"default", "fewer", "flexible"}, "task_switching_tolerance")
        _choice(self.concurrency_suggestion_preference, {"neutral", "allow", "suppress"}, "concurrency_suggestion_preference")
        _choice(self.movement_buffer_preference, {"default", "more", "less"}, "movement_buffer_preference")
        if self.provenance != "explicit_user":
            raise ValueError("day preference provenance must be explicit_user")
        if isinstance(self.last_updated_turn, bool) or self.last_updated_turn < 0:
            raise ValueError("last_updated_turn invalid")
        refs = [item.task_ref for item in self.task_specific_preferences]
        if len(refs) != len(set(refs)):
            raise ValueError("task-specific preferences cannot repeat refs")

    def to_payload(self):
        value = asdict(self)
        value["task_specific_preferences"] = [asdict(item) for item in self.task_specific_preferences]
        return value


@dataclass(frozen=True)
class PreferenceExtraction:
    should_update: bool
    profile: DayPreferenceProfile


def extract_day_preferences(
    context, feedback_text, caller, repair_caller=None, existing=None,
    turn_version=0, trace=None,
):
    if not isinstance(context, AgentDecisionContext):
        raise TypeError("context invalid")
    base = existing if isinstance(existing, DayPreferenceProfile) else DayPreferenceProfile()
    system, user = build_preference_prompt(context, feedback_text, base, turn_version)
    parser = lambda text: parse_preference_extraction(text, context, base, turn_version)
    value, updated = call_structured_stage(
        caller, repair_caller, system, user, parser,
        "preference_extractor", "update explicit day-scoped preferences", trace,
        repair_system_prompt="只输出符合 {} 的 JSON，不推断长期人格。".format(PREFERENCE_SCHEMA_VERSION),
    )
    return (value or PreferenceExtraction(False, base)), updated


def build_preference_prompt(context, feedback_text, existing, turn_version):
    system = (
        "你是 CampusFlow Day Preference Extractor。只提取用户对今天后续规划明确表达的稳定偏好，"
        "不要建立跨天人格，不要把一次任务指令误当长期偏好。没有偏好就 should_update=false。"
        "只输出 JSON：{{schema_version:'{}',should_update:bool,profile:{{slack_preference:default|more|less,"
        "meal_timing_preference:default|earlier|later,fragmentation_tolerance:default|lower|higher,"
        "task_switching_tolerance:default|fewer|flexible,concurrency_suggestion_preference:neutral|allow|suppress,"
        "movement_buffer_preference:default|more|less,task_specific_preferences:[{{task_ref,preference}}]}}}}。"
        "task_ref 必须来自输入；未被本句修改的字段保留 existing。"
    ).format(PREFERENCE_SCHEMA_VERSION)
    user = "feedback={}\nexisting={}\ncontext={}".format(
        feedback_text,
        json.dumps(existing.to_payload(), ensure_ascii=False, sort_keys=True),
        context.to_json(),
    )
    return system, user


def parse_preference_extraction(text, context, existing, turn_version):
    payload = extract_json_object(text)
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "should_update", "profile"}:
        raise AgenticParseError("preference payload invalid")
    if payload.get("schema_version") != PREFERENCE_SCHEMA_VERSION:
        raise AgenticParseError("preference schema mismatch")
    should = payload.get("should_update")
    if not isinstance(should, bool):
        raise AgenticParseError("should_update must be bool")
    data = payload.get("profile")
    required = {
        "slack_preference", "meal_timing_preference", "fragmentation_tolerance",
        "task_switching_tolerance", "concurrency_suggestion_preference",
        "movement_buffer_preference", "task_specific_preferences",
    }
    if not isinstance(data, dict) or set(data) != required:
        raise AgenticParseError("profile fields invalid")
    known = {item.task_ref for item in context.active_tasks}
    task_specific = []
    raw_specific = data.get("task_specific_preferences")
    if not isinstance(raw_specific, list):
        raise AgenticParseError("task_specific_preferences must be list")
    for item in raw_specific:
        if not isinstance(item, dict) or set(item) != {"task_ref", "preference"}:
            raise AgenticParseError("task specific preference invalid")
        pref = TaskSpecificPreference(_required(item, "task_ref"), _required(item, "preference"))
        if pref.task_ref not in known:
            raise AgenticParseError("unknown task_ref in preference")
        task_specific.append(pref)
    profile = DayPreferenceProfile(
        slack_preference=_required(data, "slack_preference"),
        meal_timing_preference=_required(data, "meal_timing_preference"),
        fragmentation_tolerance=_required(data, "fragmentation_tolerance"),
        task_switching_tolerance=_required(data, "task_switching_tolerance"),
        concurrency_suggestion_preference=_required(data, "concurrency_suggestion_preference"),
        movement_buffer_preference=_required(data, "movement_buffer_preference"),
        task_specific_preferences=tuple(task_specific),
        last_updated_turn=turn_version if should else existing.last_updated_turn,
    )
    return PreferenceExtraction(should, profile if should else existing)


def load_day_preferences(store):
    value = store.get(DAY_PREFERENCE_KEY)
    return value if isinstance(value, DayPreferenceProfile) else DayPreferenceProfile()


def save_day_preferences(store, profile):
    if not isinstance(profile, DayPreferenceProfile):
        raise TypeError("profile must be DayPreferenceProfile")
    store[DAY_PREFERENCE_KEY] = profile


def _required(payload, name):
    value = payload.get(name)
    _text(value, name)
    return value.strip()


def _choice(value, values, name):
    if value not in values:
        raise ValueError("{} invalid".format(name))


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("{} must be non-empty text".format(name))
