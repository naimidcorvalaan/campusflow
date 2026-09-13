"""One optional, ref-grounded proactive suggestion."""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from src.p2_agentic_parser import AgenticParseError, extract_json_object
from src.p5_agent_context import AgentDecisionContext
from src.p5_agent_runtime import call_structured_stage
from src.p5_plan_judge import CandidateSummary
from src.p5_situation_analyst import SituationAnalysis, situation_payload

SUGGESTION_SCHEMA_VERSION = "p5.proactive-suggestion.v1"


@dataclass(frozen=True)
class ProactiveSuggestion:
    should_show: bool
    suggestion_type: str = "none"
    text: str = ""
    task_ref: Optional[str] = None
    commitment_ref: Optional[str] = None
    suggested_minutes: Optional[int] = None
    generated: bool = True

    def __post_init__(self):
        if self.suggestion_type not in ("none", "concurrency", "postpone", "meal_timing", "slack"):
            raise ValueError("suggestion_type invalid")
        if self.should_show != bool(self.text.strip()):
            raise ValueError("suggestion text must match should_show")
        if not self.should_show and self.suggestion_type != "none":
            raise ValueError("hidden suggestion must have type none")
        if self.suggested_minutes is not None and (
            isinstance(self.suggested_minutes, bool) or self.suggested_minutes < 1
        ):
            raise ValueError("suggested_minutes invalid")


def propose_suggestion(
    context, analysis, candidate, caller, repair_caller=None, trace=None,
    feasibility_checker=None,
):
    system, user = build_suggestion_prompt(context, analysis, candidate)
    parser = lambda text: parse_suggestion(text, context)
    value, updated = call_structured_stage(
        caller, repair_caller, system, user, parser,
        "proactive_suggestion", "find one optional useful opportunity", trace,
        repair_system_prompt="只输出符合 {} 的 JSON；没有高价值建议就 should_show=false。".format(SUGGESTION_SCHEMA_VERSION),
    )
    value = value or ProactiveSuggestion(False, generated=False)
    checker = feasibility_checker if callable(feasibility_checker) else _basic_feasible
    if value.should_show and not checker(value, context, candidate):
        value = ProactiveSuggestion(False, generated=False)
    return value, updated


def build_suggestion_prompt(context, analysis, candidate):
    system = (
        "你是 CampusFlow Proactive Suggestion。最多提出一个用户值得知道、但系统不应擅自执行的机会。"
        "使用克制的‘如果你愿意’语气；不得评价课程价值，不得建议不存在的任务或地点，"
        "不得重复当前已安排动作，也不得把已有的空档说成缺少缓冲。只输出 JSON：{{schema_version:'{}',should_show:bool,"
        "suggestion_type:none|concurrency|postpone|meal_timing|slack,text:string,"
        "task_ref:string|null,commitment_ref:string|null,suggested_minutes:int|null}}。"
    ).format(SUGGESTION_SCHEMA_VERSION)
    user = "context={}\nanalysis={}\ncandidate={}".format(
        context.to_json(), json.dumps(situation_payload(analysis), ensure_ascii=False),
        json.dumps({
            "candidate_id": candidate.candidate_id,
            "timeline": list(candidate.timeline),
            "remaining_work": list(candidate.remaining_work),
            "concurrency_pairs": list(candidate.concurrency_pairs),
        }, ensure_ascii=False, sort_keys=True),
    )
    return system, user


def parse_suggestion(text, context):
    payload = extract_json_object(text)
    required = {
        "schema_version", "should_show", "suggestion_type", "text",
        "task_ref", "commitment_ref", "suggested_minutes",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise AgenticParseError("suggestion payload invalid")
    if payload.get("schema_version") != SUGGESTION_SCHEMA_VERSION:
        raise AgenticParseError("suggestion schema mismatch")
    should = payload.get("should_show")
    if not isinstance(should, bool):
        raise AgenticParseError("should_show must be bool")
    task_ref = _optional(payload.get("task_ref"))
    commitment_ref = _optional(payload.get("commitment_ref"))
    if task_ref and task_ref not in {item.task_ref for item in context.active_tasks}:
        raise AgenticParseError("unknown task_ref in suggestion")
    if commitment_ref and commitment_ref not in {item.commitment_ref for item in context.fixed_commitments}:
        raise AgenticParseError("unknown commitment_ref in suggestion")
    return ProactiveSuggestion(
        should, _required(payload, "suggestion_type"),
        _text_or_empty(payload.get("text")), task_ref, commitment_ref,
        _optional_minutes(payload.get("suggested_minutes")),
    )


def _basic_feasible(value, context, candidate):
    if not value.should_show:
        return True
    if value.task_ref and value.task_ref not in {item.task_ref for item in context.active_tasks if item.state == "active"}:
        return False
    if value.commitment_ref and value.commitment_ref not in {item.commitment_ref for item in context.fixed_commitments}:
        return False
    if value.suggestion_type == "concurrency":
        if not value.task_ref or not value.commitment_ref or not value.suggested_minutes:
            return False
        if any(pair == (value.task_ref, value.commitment_ref) for pair in candidate.concurrency_pairs):
            return False
        task = next(item for item in context.active_tasks if item.task_ref == value.task_ref)
        commitment = next(
            item for item in context.fixed_commitments
            if item.commitment_ref == value.commitment_ref
        )
        if task.remaining_minutes is not None and value.suggested_minutes > task.remaining_minutes:
            return False
        if commitment.starts_at is None or commitment.ends_at is None:
            return False
        start = datetime.fromisoformat(commitment.starts_at)
        end = datetime.fromisoformat(commitment.ends_at)
        if value.suggested_minutes > int((end - start).total_seconds() // 60):
            return False
        if task.execution_location is not None:
            destination_nodes = {
                item.destination_node_id for item in context.movements
                if item.destination_activity_ref == commitment.commitment_ref
            }
            if task.execution_location.node_id not in destination_nodes:
                return False
    if value.suggestion_type == "slack":
        # ``unused_capacity_by_window`` is a planning capacity accounting
        # value, not proof that a new buffer should be requested.  Until a
        # suggestion carries a concrete, deterministic relocation proposal,
        # suppress generic "leave more slack" copy rather than contradicting
        # a real idle interval already visible in the final timeline.
        return False
    if value.suggestion_type in ("postpone", "meal_timing"):
        if not value.task_ref:
            return False
        remaining = dict(candidate.remaining_work).get(value.task_ref, 0)
        if remaining <= 0:
            return False
        if value.suggestion_type == "meal_timing":
            task = next(item for item in context.active_tasks if item.task_ref == value.task_ref)
            if task.activity_kind != "meal":
                return False
    return True


def _required(payload, name):
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise AgenticParseError("{} must be text".format(name))
    return value.strip()


def _optional(value):
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise AgenticParseError("optional ref invalid")
    return value.strip()


def _text_or_empty(value):
    if not isinstance(value, str):
        raise AgenticParseError("text must be string")
    return value.strip()


def _optional_minutes(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise AgenticParseError("suggested_minutes invalid")
    return value
