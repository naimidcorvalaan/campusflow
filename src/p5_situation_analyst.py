"""Qwen Situation Analyst: high-level judgement, never minute arithmetic."""

import json
from dataclasses import dataclass
from typing import Optional, Tuple

from src.p2_agentic_parser import AgenticParseError, extract_json_object
from src.p5_agent_context import AgentDecisionContext
from src.p5_agent_runtime import StructuredValidationError, call_structured_stage

SITUATION_SCHEMA_VERSION = "p5.situation-analysis.v1"
STRING_LIST_FIELDS = (
    "important_constraints", "user_priority_signals", "timing_pressure",
    "fragmentation_concerns", "flexibility_opportunities", "risk_flags",
    "recommended_strategy_focus",
)


def situation_output_schema(context):
    """One contract for the model prompt and the production parser."""
    text = {"type": "string", "minLength": 1, "pattern": r"\S"}
    assessment = {
        "task_ref": dict(text, enum=[task.task_ref for task in context.active_tasks]),
        "priority": dict(text, enum=["high", "medium", "low"]),
        "reason": text,
    }
    properties = {
        "schema_version": {"type": "string", "const": SITUATION_SCHEMA_VERSION},
        "primary_goal": text,
    }
    properties.update({name: {"type": "array", "items": text} for name in STRING_LIST_FIELDS})
    properties.update({
        "task_priority_assessment": {
            "type": "array", "items": _object_schema(assessment),
            "description": "每个元素是对象；task_ref 只能取 enum 中的值，且不得重复。无任务时为 []。",
        },
        "meal_context": dict(text, type=["string", "null"]),
        "movement_context": dict(text, type=["string", "null"]),
        "clarification_value": dict(text, enum=["none", "low", "medium", "high"]),
    })
    return _object_schema(properties)


def _object_schema(properties):
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def situation_output_example(context):
    example = {name: [] for name in STRING_LIST_FIELDS}
    example.update({
        "schema_version": SITUATION_SCHEMA_VERSION, "primary_goal": "在固定安排内完成任务",
        "task_priority_assessment": [
            {"task_ref": task.task_ref, "priority": "medium", "reason": "保留任务安排"}
            for task in context.active_tasks[:1]
        ],
        "meal_context": None, "movement_context": None, "clarification_value": "none",
    })
    example["important_constraints"] = ["固定安排不可冲突"]
    return example


@dataclass(frozen=True)
class TaskPriorityAssessment:
    task_ref: str
    priority: str
    reason: str

    def __post_init__(self):
        if self.priority not in ("high", "medium", "low"):
            raise ValueError("priority must be high/medium/low")
        _text(self.task_ref, "task_ref")
        _text(self.reason, "reason")


@dataclass(frozen=True)
class SituationAnalysis:
    primary_goal: str
    important_constraints: Tuple[str, ...] = ()
    user_priority_signals: Tuple[str, ...] = ()
    task_priority_assessment: Tuple[TaskPriorityAssessment, ...] = ()
    timing_pressure: Tuple[str, ...] = ()
    fragmentation_concerns: Tuple[str, ...] = ()
    meal_context: Optional[str] = None
    movement_context: Optional[str] = None
    flexibility_opportunities: Tuple[str, ...] = ()
    risk_flags: Tuple[str, ...] = ()
    recommended_strategy_focus: Tuple[str, ...] = ()
    clarification_value: str = "none"
    generated: bool = True

    def __post_init__(self):
        _text(self.primary_goal, "primary_goal")
        if self.clarification_value not in ("none", "low", "medium", "high"):
            raise ValueError("clarification_value invalid")
        known = set()
        for item in self.task_priority_assessment:
            if item.task_ref in known:
                raise ValueError("duplicate task priority assessment")
            known.add(item.task_ref)


def analyze_situation(context, caller, repair_caller=None, trace=None):
    if not isinstance(context, AgentDecisionContext):
        raise TypeError("context must be AgentDecisionContext")
    system, user = build_situation_prompt(context)
    parser = lambda text: parse_situation_analysis(text, context)
    value, updated = call_structured_stage(
        caller, repair_caller, system, user, parser,
        "situation_analyst", "identify the planning tension", trace,
        repair_system_prompt=(
            "只输出符合 {} 的完整 JSON；保留有效 task_ref，不输出分钟级计划。".format(
                SITUATION_SCHEMA_VERSION
            )
        ),
    )
    return (value or fallback_situation_analysis(context)), updated


def build_situation_prompt(context):
    system = (
        "你是 CampusFlow Situation Analyst。只判断当前规划矛盾和高层重点，不安排具体分钟，"
        "不计算路线，不修改事实。所有 task_ref 必须来自输入。只输出 JSON，schema_version={}。"
        "偏好优先级固定为：latest_user_text/latest_feedback_text 中的本次明确要求，"
        "高于 day_preferences，后者高于 personal_defaults；任何软偏好都不能覆盖固定安排。"
        "不要输出思维过程，只给系统可执行的简短结论。"
    ).format(SITUATION_SCHEMA_VERSION)
    system += (
        "所有字段必须出现，不增加字段。字符串数组即使只有一项也必须用 [\"结论\"]，"
        "不能输出单个字符串、对象或 null；只有 meal_context/movement_context 可用 null。"
        "task_priority_assessment 是对象数组，不是以 task_ref 为键的字典，也不是字符串数组。"
        "禁止使用任务标题、序号、fixed commitment/ref 或自行缩写替代 task_ref。每项结论保持简短。"
        "\n正式输出 JSON Schema：\n" + json.dumps(situation_output_schema(context), ensure_ascii=False)
        + "\n有效结构示例（只演示类型，不照抄结论）：\n"
        + json.dumps(situation_output_example(context), ensure_ascii=False)
    )
    facts = context.to_payload()
    facts.pop("schema_version")  # Input projection version is not an output field.
    return system, (
        "允许的 task_ref 完整集合：\n"
        + json.dumps([task.task_ref for task in context.active_tasks], ensure_ascii=False)
        + "\n只读决策上下文（不是输出模板）：\n" + json.dumps(facts, ensure_ascii=False)
    )


def parse_situation_analysis(text, context):
    payload = extract_json_object(text)
    issues = _contract_issues(payload, situation_output_schema(context))
    if issues:
        raise StructuredValidationError(issues[:16])
    assessments = []
    seen = set()
    for item in _list(payload, "task_priority_assessment"):
        assessment = TaskPriorityAssessment(
            _required_text(item, "task_ref"), _required_text(item, "priority"),
            _required_text(item, "reason"),
        )
        if assessment.task_ref in seen:
            raise StructuredValidationError(((
                "$.task_priority_assessment[{}].task_ref".format(len(assessments)),
                "duplicate_task_ref", "unique allowed task_ref", "string",
            ),))
        seen.add(assessment.task_ref)
        assessments.append(assessment)
    return SituationAnalysis(
        primary_goal=_required_text(payload, "primary_goal"),
        important_constraints=_strings(payload, "important_constraints"),
        user_priority_signals=_strings(payload, "user_priority_signals"),
        task_priority_assessment=tuple(assessments),
        timing_pressure=_strings(payload, "timing_pressure"),
        fragmentation_concerns=_strings(payload, "fragmentation_concerns"),
        meal_context=_optional_text(payload.get("meal_context")),
        movement_context=_optional_text(payload.get("movement_context")),
        flexibility_opportunities=_strings(payload, "flexibility_opportunities"),
        risk_flags=_strings(payload, "risk_flags"),
        recommended_strategy_focus=_strings(payload, "recommended_strategy_focus"),
        clarification_value=_required_text(payload, "clarification_value"),
    )


def _contract_issues(value, schema, path="$"):
    """Validate the closed subset used by situation_output_schema.

    Paths only use schema-owned field names and numeric indices. In
    particular, neither unknown model keys nor invalid ref values are logged.
    """
    actual = ("null" if value is None else "boolean" if isinstance(value, bool) else
              "string" if isinstance(value, str) else "array" if isinstance(value, list) else
              "object" if isinstance(value, dict) else "number")
    expected = schema["type"]
    types = expected if isinstance(expected, list) else [expected]
    if actual not in types:
        code = "list_type" if "array" in types else "type_mismatch"
        return [(path, code, "|".join(types), actual)]
    if actual == "null":
        return []
    issues = []
    if actual == "object":
        properties = schema["properties"]
        for name in schema["required"]:
            if name not in value:
                issues.append((path + "." + name, "missing_field", "required", "missing"))
        if not schema["additionalProperties"] and set(value) - set(properties):
            issues.append((path, "field_set", "only declared fields", "extra_fields"))
        for name, child in properties.items():
            if name in value:
                issues.extend(_contract_issues(value[name], child, path + "." + name))
    elif actual == "array":
        for index, item in enumerate(value):
            issues.extend(_contract_issues(item, schema["items"], path + "[{}]".format(index)))
    elif actual == "string":
        if schema.get("minLength") and not value.strip():
            issues.append((path, "text_type", "non-empty string", "empty_string"))
        if "const" in schema and value != schema["const"]:
            issues.append((path, "schema_version", "declared output version", actual))
        if "enum" in schema and value.strip() not in schema["enum"]:
            is_ref = path.endswith(".task_ref")
            issues.append((path, "unknown_task_ref" if is_ref else "enum_value",
                           "allowed task_ref" if is_ref else "declared enum", actual))
    return issues


def fallback_situation_analysis(context):
    constraints = []
    if context.fixed_commitments:
        constraints.append("固定安排及其移动、准备时间不可冲突")
    if context.meals:
        constraints.append("保留已存在的吃饭任务及确定性时长策略")
    if context.ordering_constraints:
        constraints.append("保持用户明确任务顺序")
    assessments = tuple(
        TaskPriorityAssessment(
            item.task_ref,
            "high" if item.explicitly_preferred else "medium",
            "最新明确偏好" if item.explicitly_preferred else "保持现有确定性顺序",
        )
        for item in context.active_tasks if item.state == "active"
    )
    return SituationAnalysis(
        primary_goal="在全部硬约束内尽早安排仍可执行的任务",
        important_constraints=tuple(constraints),
        task_priority_assessment=assessments,
        recommended_strategy_focus=("earliest_feasible", "preserve_hard_facts"),
        clarification_value="medium" if context.unresolved_confirmations else "none",
        generated=False,
    )


def situation_payload(value):
    return {
        "primary_goal": value.primary_goal,
        "important_constraints": list(value.important_constraints),
        "user_priority_signals": list(value.user_priority_signals),
        "task_priority_assessment": [
            {"task_ref": item.task_ref, "priority": item.priority, "reason": item.reason}
            for item in value.task_priority_assessment
        ],
        "timing_pressure": list(value.timing_pressure),
        "fragmentation_concerns": list(value.fragmentation_concerns),
        "meal_context": value.meal_context,
        "movement_context": value.movement_context,
        "flexibility_opportunities": list(value.flexibility_opportunities),
        "risk_flags": list(value.risk_flags),
        "recommended_strategy_focus": list(value.recommended_strategy_focus),
        "clarification_value": value.clarification_value,
    }


def _strings(payload, name):
    values = _list(payload, name)
    if any(not isinstance(item, str) or not item.strip() for item in values):
        raise AgenticParseError("{} must contain text".format(name))
    return tuple(item.strip() for item in values)


def _list(payload, name):
    value = payload.get(name)
    if not isinstance(value, list):
        raise AgenticParseError("{} must be list".format(name))
    return value


def _required_text(payload, name):
    value = payload.get(name)
    _text(value, name)
    return value.strip()


def _optional_text(value):
    if value is None:
        return None
    _text(value, "optional text")
    return value.strip()


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("{} must be non-empty text".format(name))
