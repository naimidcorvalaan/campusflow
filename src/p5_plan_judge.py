"""Qwen selection among already deterministic, validated candidates."""

import json
from dataclasses import dataclass
from typing import Optional, Tuple

from src.p2_agentic_parser import AgenticParseError, extract_json_object
from src.p5_agent_context import AgentDecisionContext
from src.p5_agent_runtime import call_structured_stage
from src.p5_situation_analyst import SituationAnalysis, situation_payload

JUDGE_SCHEMA_VERSION = "p5.plan-judge.v1"


@dataclass(frozen=True)
class CandidateSummary:
    candidate_id: str
    strategy_id: str
    timeline: Tuple[str, ...]
    task_completion: Tuple[Tuple[str, int], ...]
    remaining_work: Tuple[Tuple[str, int], ...]
    movement_count: int
    task_switches: int
    slack_minutes: int
    meal_timing: Tuple[str, ...]
    user_preference_satisfied: bool
    concurrency_pairs: Tuple[Tuple[str, str], ...]
    unresolved_questions: Tuple[str, ...]
    task_sequence: Tuple[str, ...] = ()

    def __post_init__(self):
        _text(self.candidate_id, "candidate_id")
        _text(self.strategy_id, "strategy_id")
        for name, value in (
            ("movement_count", self.movement_count),
            ("task_switches", self.task_switches),
            ("slack_minutes", self.slack_minutes),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("{} must be non-negative int".format(name))


@dataclass(frozen=True)
class PlanJudgeDecision:
    selected_candidate_id: str
    concise_selection_reason: str
    concern: Optional[str]
    confidence: str
    generated: bool = True

    def __post_init__(self):
        _text(self.selected_candidate_id, "selected_candidate_id")
        _text(self.concise_selection_reason, "concise_selection_reason")
        if self.confidence not in ("high", "medium", "low"):
            raise ValueError("confidence invalid")


def judge_candidates(context, analysis, candidates, caller, repair_caller=None, trace=None):
    if not isinstance(context, AgentDecisionContext) or not isinstance(analysis, SituationAnalysis):
        raise TypeError("context/analysis types invalid")
    candidates = tuple(candidates)
    if not candidates or any(not isinstance(item, CandidateSummary) for item in candidates):
        raise ValueError("at least one candidate summary is required")
    system, user = build_judge_prompt(context, analysis, candidates)
    parser = lambda text: parse_plan_judge(text, candidates)
    value, updated = call_structured_stage(
        caller, repair_caller, system, user, parser,
        "plan_judge", "select the most human-aligned feasible candidate", trace,
        repair_system_prompt="只输出符合 {} 的 JSON，candidate id 必须来自候选列表。".format(JUDGE_SCHEMA_VERSION),
    )
    return (value or deterministic_judge(candidates, context)), updated


def build_judge_prompt(context, analysis, candidates):
    system = (
        "你是 CampusFlow Plan Judge。候选都已通过程序硬校验；从中选择最符合用户意图和人类直觉的一份。"
        "关注最新明确偏好、无意义拖延、过度碎片、任务切换、饭点、叙事顺序和早期窗口利用。"
        "不能修改时间路线，不能选择不存在的 candidate。只输出 JSON："
        "{{schema_version:'{}',selected_candidate_id:string,concise_selection_reason:string,"
        "concern:string|null,confidence:high|medium|low}}。置信度仅内部使用。"
    ).format(JUDGE_SCHEMA_VERSION)
    payload = [_candidate_payload(item) for item in candidates]
    user = "用户上下文：\n{}\n情境：\n{}\n候选：\n{}".format(
        context.to_json(), json.dumps(situation_payload(analysis), ensure_ascii=False),
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
    )
    return system, user


def parse_plan_judge(text, candidates):
    payload = extract_json_object(text)
    allowed = {
        "schema_version", "selected_candidate_id", "concise_selection_reason",
        "concern", "confidence",
    }
    if not isinstance(payload, dict) or set(payload) != allowed:
        raise AgenticParseError("judge payload invalid")
    if payload.get("schema_version") != JUDGE_SCHEMA_VERSION:
        raise AgenticParseError("judge schema mismatch")
    result = PlanJudgeDecision(
        _required_text(payload, "selected_candidate_id"),
        _required_text(payload, "concise_selection_reason"),
        _optional_text(payload.get("concern")),
        _required_text(payload, "confidence"),
    )
    if result.selected_candidate_id not in {item.candidate_id for item in candidates}:
        raise AgenticParseError("judge selected unknown candidate")
    return result


def deterministic_judge(candidates, context=None):
    preferences = dict(getattr(context, "day_preferences", ()) or ())
    prefer_more_slack = preferences.get("slack_preference") == "more"
    prefer_less_slack = preferences.get("slack_preference") == "less"
    prefer_fewer_switches = (
        preferences.get("task_switching_tolerance") == "fewer"
    )

    def key(item):
        slack_rank = (
            -item.slack_minutes if prefer_more_slack
            else item.slack_minutes if prefer_less_slack
            else 0
        )
        switch_rank = item.task_switches if prefer_fewer_switches else 0
        return (
            0 if item.user_preference_satisfied else 1,
            slack_rank,
            switch_rank,
            item.task_switches,
            item.movement_count,
            -sum(value for _, value in item.task_completion),
            item.slack_minutes if not prefer_more_slack else 0,
            item.candidate_id,
        )
    selected = min(candidates, key=key)
    return PlanJudgeDecision(
        selected.candidate_id,
        "模型评审不可用，采用满足明确偏好且切换较少的确定性候选",
        None, "low", generated=False,
    )


def _candidate_payload(item):
    return {
        "candidate_id": item.candidate_id,
        "strategy_id": item.strategy_id,
        "timeline": list(item.timeline),
        "task_completion": list(item.task_completion),
        "remaining_work": list(item.remaining_work),
        "movement_count": item.movement_count,
        "task_switches": item.task_switches,
        "slack_minutes": item.slack_minutes,
        "meal_timing": list(item.meal_timing),
        "user_preference_satisfied": item.user_preference_satisfied,
        "concurrency_pairs": list(item.concurrency_pairs),
        "unresolved_questions": list(item.unresolved_questions),
        "task_sequence": list(item.task_sequence),
    }


def _required_text(payload, name):
    value = payload.get(name)
    _text(value, name)
    return value.strip()


def _optional_text(value):
    if value is None:
        return None
    _text(value, "concern")
    return value.strip()


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("{} must be non-empty text".format(name))
