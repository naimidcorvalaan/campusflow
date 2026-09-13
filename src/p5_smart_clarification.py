"""Select at most one high-information clarification for the existing UI."""

import json
from dataclasses import dataclass
from typing import Tuple

from src.p2_agentic_parser import AgenticParseError, extract_json_object
from src.p5_agent_context import AgentDecisionContext
from src.p5_agent_runtime import call_structured_stage
from src.p5_situation_analyst import SituationAnalysis, situation_payload

CLARIFICATION_SCHEMA_VERSION = "p5.smart-clarification.v1"


@dataclass(frozen=True)
class SmartClarification:
    should_ask: bool
    question: str = ""
    reason_for_system: str = ""
    affected_decisions: Tuple[str, ...] = ()
    information_gain: str = "none"
    generated: bool = True

    def __post_init__(self):
        if self.information_gain not in ("none", "low", "medium", "high"):
            raise ValueError("information_gain invalid")
        if self.should_ask != bool(self.question.strip()):
            raise ValueError("question must match should_ask")
        if self.should_ask and not self.reason_for_system.strip():
            raise ValueError("asked clarification needs an internal reason")


def choose_smart_clarification(context, analysis, caller, repair_caller=None, trace=None):
    if not isinstance(context, AgentDecisionContext) or not isinstance(analysis, SituationAnalysis):
        raise TypeError("context/analysis invalid")
    system, user = build_clarification_prompt(context, analysis)
    value, updated = call_structured_stage(
        caller, repair_caller, system, user, parse_smart_clarification,
        "smart_clarification", "choose the single most valuable question", trace,
        repair_system_prompt="只输出符合 {} 的 JSON，最多一个问题。".format(CLARIFICATION_SCHEMA_VERSION),
    )
    # Questions affect the page's canonical confirmation card.  The model can
    # rank uncertainty, but it must not manufacture a concern after the final
    # timeline already answers it (for example, calling a completed meal
    # "tight" despite a real post-meal gap).  The displayed question therefore
    # comes only from an unresolved structured fact in this exact context.
    grounded = fallback_clarification(context)
    if grounded.should_ask:
        return grounded, updated
    return SmartClarification(False, generated=False), updated


def build_clarification_prompt(context, analysis):
    system = (
        "你是 CampusFlow Smart Clarification。最多选择一个真正会改变当前计划的问题；"
        "不要把所有未知都问一遍，不重复已回答问题。只输出 JSON："
        "{{schema_version:'{}',should_ask:bool,question:string,reason_for_system:string,"
        "affected_decisions:[string],information_gain:none|low|medium|high}}。"
        "reason_for_system 不会展示给用户。"
    ).format(CLARIFICATION_SCHEMA_VERSION)
    return system, "context={}\nanalysis={}".format(
        context.to_json(), json.dumps(situation_payload(analysis), ensure_ascii=False, sort_keys=True)
    )


def parse_smart_clarification(text):
    payload = extract_json_object(text)
    required = {
        "schema_version", "should_ask", "question", "reason_for_system",
        "affected_decisions", "information_gain",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise AgenticParseError("clarification payload invalid")
    if payload.get("schema_version") != CLARIFICATION_SCHEMA_VERSION:
        raise AgenticParseError("clarification schema mismatch")
    should = payload.get("should_ask")
    if not isinstance(should, bool):
        raise AgenticParseError("should_ask must be bool")
    values = payload.get("affected_decisions")
    if not isinstance(values, list) or any(not isinstance(item, str) or not item.strip() for item in values):
        raise AgenticParseError("affected_decisions invalid")
    return SmartClarification(
        should,
        _text_or_empty(payload.get("question")),
        _text_or_empty(payload.get("reason_for_system")),
        tuple(item.strip() for item in values),
        _required(payload, "information_gain"),
    )


def fallback_clarification(context):
    if not context.unresolved_confirmations:
        return SmartClarification(False, generated=False)
    first = context.unresolved_confirmations[0]
    if first.startswith("current_location_assumed:") and context.current_location is not None:
        return SmartClarification(
            True,
            "你现在是在{}吗？如果不是，告诉我你现在的位置，我会重新调整路上的时间。".format(
                context.current_location.display_name
            ),
            "当前位置会影响后续移动",
            ("movement", "meal_route"), "high", generated=False,
        )
    if first.startswith("meal_location_context_required:"):
        return SmartClarification(
            True, "你现在大概在哪里？我可以帮你选一个顺路的食堂。",
            "缺少选择食堂的路线依据", ("meal_location",), "high", generated=False,
        )
    if first.startswith("feedback:"):
        question = first.split(":", 1)[1].strip()
        if question:
            return SmartClarification(
                True, question, "反馈语义尚需确认", ("feedback",), "high",
                generated=False,
            )
    return SmartClarification(False, generated=False)


def _required(payload, name):
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise AgenticParseError("{} must be text".format(name))
    return value.strip()


def _text_or_empty(value):
    if not isinstance(value, str):
        raise AgenticParseError("text field required")
    return value.strip()
