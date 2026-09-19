"""Qwen high-level plan strategies with strict reference validation."""

import json
from dataclasses import dataclass
from typing import Tuple

from src.p2_agentic_parser import AgenticParseError, extract_json_object
from src.p5_agent_context import AgentDecisionContext
from src.p5_agent_runtime import call_structured_stage
from src.p5_situation_analyst import SituationAnalysis, situation_payload

STRATEGY_SCHEMA_VERSION = "p5.plan-strategies.v1"


@dataclass(frozen=True)
class PlanStrategy:
    strategy_id: str
    priority_order: Tuple[str, ...]
    protected_preferences: Tuple[str, ...]
    fragmentation_level: str
    slack_preference: str
    meal_preference: str
    concurrency_pairs: Tuple[Tuple[str, str], ...]
    rationale_summary: str
    constraints_to_preserve: Tuple[str, ...]

    def __post_init__(self):
        _text(self.strategy_id, "strategy_id")
        if self.fragmentation_level not in ("low", "medium", "high"):
            raise ValueError("fragmentation_level invalid")
        if self.slack_preference not in ("minimal", "balanced", "more"):
            raise ValueError("slack_preference invalid")
        if self.meal_preference not in ("preserve_window", "recover_soon", "user_explicit", "neutral"):
            raise ValueError("meal_preference invalid")
        if len(self.priority_order) != len(set(self.priority_order)):
            raise ValueError("priority_order cannot repeat refs")
        _text(self.rationale_summary, "rationale_summary")


def generate_plan_strategies(context, analysis, caller, repair_caller=None, trace=None):
    if not isinstance(context, AgentDecisionContext) or not isinstance(analysis, SituationAnalysis):
        raise TypeError("context/analysis types invalid")
    system, user = build_strategy_prompt(context, analysis)
    parser = lambda text: parse_plan_strategies(text, context)
    value, updated = call_structured_stage(
        caller, repair_caller, system, user, parser,
        "plan_strategist", "propose feasible high-level approaches", trace,
        repair_system_prompt="只输出符合 {} 的完整 JSON，不添加不存在的 ref。".format(STRATEGY_SCHEMA_VERSION),
    )
    return (value or fallback_plan_strategies(context)), updated


def build_strategy_prompt(context, analysis):
    system = (
        "你是 CampusFlow Plan Strategist。提出2到4个高层策略，不安排分钟、不计算路线。"
        "策略只能引用输入中的 task_ref/commitment_ref；concurrency_pairs 只能使用已经显式授权的 pair。"
        "偏好优先级为本次明确要求 > day_preferences > personal_defaults > 产品默认；"
        "软偏好不得取消或移动固定安排。"
        "只输出 JSON：{{schema_version:'{}',strategies:[{{strategy_id,priority_order:[task_ref],"
        "protected_preferences:[string],fragmentation_level:low|medium|high,"
        "slack_preference:minimal|balanced|more,"
        "meal_preference:preserve_window|recover_soon|user_explicit|neutral,"
        "concurrency_pairs:[{{task_ref,commitment_ref}}],rationale_summary:string,"
        "constraints_to_preserve:[string]}}]}}。不要输出内部推理。"
    ).format(STRATEGY_SCHEMA_VERSION)
    system += "即使只有一个任务也输出两个策略对象，允许 priority_order 相同但说明取舍；所有列表都必须存在，空值用 []，不能省略字段。"
    user = "上下文：\n{}\n情境分析：\n{}".format(
        context.to_json(), json.dumps(situation_payload(analysis), ensure_ascii=False, sort_keys=True)
    )
    return system, user


def parse_plan_strategies(text, context):
    payload = extract_json_object(text)
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "strategies"}:
        raise AgenticParseError("strategy payload invalid")
    if payload.get("schema_version") != STRATEGY_SCHEMA_VERSION:
        raise AgenticParseError("strategy schema mismatch")
    values = payload.get("strategies")
    if not isinstance(values, list) or not (2 <= len(values) <= 4):
        raise AgenticParseError("strategies must contain two to four items")
    known_tasks = {item.task_ref for item in context.active_tasks}
    authorized = {
        (item.task_ref, item.commitment_ref) for item in context.concurrency_authorizations
    }
    result = []
    for item in values:
        required = {
            "strategy_id", "priority_order", "protected_preferences",
            "fragmentation_level", "slack_preference", "meal_preference",
            "concurrency_pairs", "rationale_summary", "constraints_to_preserve",
        }
        if not isinstance(item, dict) or set(item) != required:
            raise AgenticParseError("strategy fields invalid")
        order = _strings(item, "priority_order")
        if any(ref not in known_tasks for ref in order):
            raise AgenticParseError("strategy contains unknown task_ref")
        pairs = []
        for pair in _list(item, "concurrency_pairs"):
            if not isinstance(pair, dict) or set(pair) != {"task_ref", "commitment_ref"}:
                raise AgenticParseError("concurrency pair invalid")
            value = (_required_text(pair, "task_ref"), _required_text(pair, "commitment_ref"))
            if value not in authorized:
                raise AgenticParseError("strategy cannot invent concurrency authorization")
            pairs.append(value)
        result.append(PlanStrategy(
            _required_text(item, "strategy_id"), order,
            _strings(item, "protected_preferences"),
            _required_text(item, "fragmentation_level"),
            _required_text(item, "slack_preference"),
            _required_text(item, "meal_preference"), tuple(pairs),
            _required_text(item, "rationale_summary"),
            _strings(item, "constraints_to_preserve"),
        ))
    if len({item.strategy_id for item in result}) != len(result):
        raise AgenticParseError("strategy ids must be unique")
    return tuple(result)


def fallback_plan_strategies(context):
    active = tuple(item.task_ref for item in context.active_tasks if item.state == "active")
    preferred = context.preferred_next_task_ref
    if preferred in active:
        active = (preferred,) + tuple(ref for ref in active if ref != preferred)
    authorized = tuple(
        (item.task_ref, item.commitment_ref) for item in context.concurrency_authorizations
    )
    preferences = dict(context.day_preferences)
    fragmentation = (
        "low" if preferences.get("fragmentation_tolerance") == "lower"
        else "high" if preferences.get("fragmentation_tolerance") == "higher"
        else "medium"
    )
    slack = (
        "more" if preferences.get("slack_preference") == "more"
        else "minimal" if preferences.get("slack_preference") == "less"
        else "balanced"
    )
    meal = (
        "user_explicit"
        if preferences.get("meal_timing_preference") in ("earlier", "later")
        else "preserve_window" if context.meals else "neutral"
    )
    balanced = PlanStrategy(
        "deterministic_balanced", active,
        ("hard_facts", "latest_explicit_intent"), fragmentation, slack,
        meal, authorized,
        "保持当前确定性顺序与硬约束", ("fixed_commitments", "locations", "durations"),
    )
    conservative = PlanStrategy(
        "deterministic_conservative", active,
        ("hard_facts", "minimum_chunks"), "low", "more",
        "preserve_window" if context.meals else "neutral", authorized,
        "减少切换并保留更多余量", ("fixed_commitments", "locations", "durations"),
    )
    return (balanced, conservative)


def baseline_plan_strategy(context):
    """Describe the existing deterministic order without claiming Qwen chose it."""
    active = tuple(item.task_ref for item in context.active_tasks if item.state == "active")
    authorized = tuple(
        (item.task_ref, item.commitment_ref) for item in context.concurrency_authorizations
    )
    return PlanStrategy(
        "current_deterministic", active,
        ("hard_facts", "current_valid_plan"), "medium", "balanced",
        "preserve_window" if context.meals else "neutral", authorized,
        "保留当前已经通过硬校验的确定性方案",
        ("fixed_commitments", "locations", "durations"),
    )


def strategy_payload(item):
    return {
        "strategy_id": item.strategy_id,
        "priority_order": list(item.priority_order),
        "protected_preferences": list(item.protected_preferences),
        "fragmentation_level": item.fragmentation_level,
        "slack_preference": item.slack_preference,
        "meal_preference": item.meal_preference,
        "concurrency_pairs": [
            {"task_ref": task_ref, "commitment_ref": commitment_ref}
            for task_ref, commitment_ref in item.concurrency_pairs
        ],
        "rationale_summary": item.rationale_summary,
        "constraints_to_preserve": list(item.constraints_to_preserve),
    }


def _list(payload, name):
    value = payload.get(name)
    if not isinstance(value, list):
        raise AgenticParseError("{} must be list".format(name))
    return value


def _strings(payload, name):
    value = _list(payload, name)
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise AgenticParseError("{} must contain strings".format(name))
    return tuple(item.strip() for item in value)


def _required_text(payload, name):
    value = payload.get(name)
    _text(value, name)
    return value.strip()


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("{} must be non-empty text".format(name))
