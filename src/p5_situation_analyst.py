"""Qwen Situation Analyst: high-level judgement, never minute arithmetic."""

import json
from dataclasses import dataclass
from typing import Optional, Tuple

from src.p2_agentic_parser import AgenticParseError, extract_json_object
from src.p5_agent_context import AgentDecisionContext
from src.p5_agent_runtime import AgentCallTrace, call_structured_stage

SITUATION_SCHEMA_VERSION = "p5.situation-analysis.v1"


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
        "字段：primary_goal:string, important_constraints:[string], user_priority_signals:[string],"
        "task_priority_assessment:[{{task_ref,priority:high|medium|low,reason}}],"
        "timing_pressure:[string], fragmentation_concerns:[string], meal_context:string|null,"
        "movement_context:string|null, flexibility_opportunities:[string], risk_flags:[string],"
        "recommended_strategy_focus:[string], clarification_value:none|low|medium|high。"
        "不要输出思维过程，只给系统可执行的简短结论。"
    ).format(SITUATION_SCHEMA_VERSION)
    return system, "只读决策上下文：\n{}".format(context.to_json())


def parse_situation_analysis(text, context):
    payload = extract_json_object(text)
    allowed = {
        "schema_version", "primary_goal", "important_constraints",
        "user_priority_signals", "task_priority_assessment", "timing_pressure",
        "fragmentation_concerns", "meal_context", "movement_context",
        "flexibility_opportunities", "risk_flags", "recommended_strategy_focus",
        "clarification_value",
    }
    if not isinstance(payload, dict) or set(payload) != allowed:
        raise AgenticParseError("situation analysis fields invalid")
    if payload.get("schema_version") != SITUATION_SCHEMA_VERSION:
        raise AgenticParseError("situation schema mismatch")
    known = {item.task_ref for item in context.active_tasks}
    assessments = []
    for item in _list(payload, "task_priority_assessment"):
        if not isinstance(item, dict) or set(item) != {"task_ref", "priority", "reason"}:
            raise AgenticParseError("task priority assessment invalid")
        assessment = TaskPriorityAssessment(
            _required_text(item, "task_ref"), _required_text(item, "priority"),
            _required_text(item, "reason"),
        )
        if assessment.task_ref not in known:
            raise AgenticParseError("unknown task_ref in situation analysis")
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
