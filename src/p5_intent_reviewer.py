"""Intent Compliance Reviewer 2.0 for initial, feedback and preview flows."""

import json
from dataclasses import dataclass
from typing import Optional, Tuple

from src.p2_agentic_parser import AgenticParseError, extract_json_object
from src.p5_agent_context import AgentDecisionContext
from src.p5_agent_runtime import call_structured_stage
from src.p5_plan_judge import CandidateSummary
from src.p5_situation_analyst import SituationAnalysis, situation_payload

REVIEW_SCHEMA_VERSION = "p5.intent-compliance.v2"
_DIRECTIVE_KINDS = {
    "prefer_task", "preserve_order", "remove_unapproved_concurrency",
    "honor_single_session", "honor_location", "honor_meal_temporal",
    "honor_latest_location", "reduce_fragmentation",
}


@dataclass(frozen=True)
class IntentRepairDirective:
    kind: str
    task_ref: Optional[str] = None
    commitment_ref: Optional[str] = None
    related_task_ref: Optional[str] = None

    def __post_init__(self):
        if self.kind not in _DIRECTIVE_KINDS:
            raise ValueError("repair directive kind invalid")
        for value in (self.task_ref, self.commitment_ref, self.related_task_ref):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError("repair directive refs must be text or None")


@dataclass(frozen=True)
class IntentComplianceReviewV2:
    decision: str
    reason: str
    repair_directives: Tuple[IntentRepairDirective, ...] = ()
    generated: bool = True

    def __post_init__(self):
        if self.decision not in ("approve", "reject"):
            raise ValueError("review decision invalid")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("review reason must be non-empty")
        if self.decision == "approve" and self.repair_directives:
            raise ValueError("approved review cannot contain repair directives")


def review_candidate_intent(
    context,
    latest_user_text,
    extracted_semantics,
    analysis,
    candidate,
    deterministic_facts,
    caller,
    repair_caller=None,
    trace=None,
    flow="initial",
):
    if flow not in ("initial", "feedback", "concurrency", "what_if"):
        raise ValueError("flow invalid")
    if not isinstance(context, AgentDecisionContext):
        raise TypeError("context invalid")
    if not isinstance(analysis, SituationAnalysis) or not isinstance(candidate, CandidateSummary):
        raise TypeError("analysis/candidate invalid")
    system, user = build_intent_review_prompt(
        context, latest_user_text, extracted_semantics, analysis,
        candidate, deterministic_facts, flow,
    )
    parser = lambda text: parse_intent_review(text, context)
    value, updated = call_structured_stage(
        caller, repair_caller, system, user, parser,
        "intent_compliance_reviewer", "verify the chosen plan against latest intent", trace,
        repair_system_prompt="只输出符合 {} 的完整 JSON，不修改任何时间或路线事实。".format(REVIEW_SCHEMA_VERSION),
    )
    if value is None:
        value = deterministic_intent_review(context, candidate)
    return value, updated


def build_intent_review_prompt(
    context, latest_user_text, extracted_semantics, analysis,
    candidate, deterministic_facts, flow,
):
    system = (
        "你是 CampusFlow Intent Compliance Reviewer 2.0。只审核候选是否违背用户最新明确意图；"
        "不得修改分钟、路线、地点或固定安排。检查 before/after、preferred-next、single-session、"
        "显式并行授权/撤销、earliest feasible、过度碎片、location correction 和 meal temporal。"
        "硬事实不允许用户偏好覆盖。只输出 JSON：{{schema_version:'{}',decision:approve|reject,"
        "reason:string,repair_directives:[{{kind,task_ref:null|string,commitment_ref:null|string,"
        "related_task_ref:null|string}}]}}。approve 时 directives 为空。"
    ).format(REVIEW_SCHEMA_VERSION)
    user = "flow={}\nlatest={}\ncontext={}\nsemantics={}\nanalysis={}\ncandidate={}\nhard_facts={}".format(
        flow, latest_user_text or "", context.to_json(),
        json.dumps(extracted_semantics or {}, ensure_ascii=False, sort_keys=True),
        json.dumps(situation_payload(analysis), ensure_ascii=False, sort_keys=True),
        json.dumps(_candidate_payload(candidate), ensure_ascii=False, sort_keys=True),
        json.dumps(deterministic_facts or {}, ensure_ascii=False, sort_keys=True),
    )
    return system, user


def parse_intent_review(text, context):
    payload = extract_json_object(text)
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version", "decision", "reason", "repair_directives"
    }:
        raise AgenticParseError("intent review payload invalid")
    if payload.get("schema_version") != REVIEW_SCHEMA_VERSION:
        raise AgenticParseError("intent review schema mismatch")
    decision = _required_text(payload, "decision")
    directives_payload = payload.get("repair_directives")
    if not isinstance(directives_payload, list):
        raise AgenticParseError("repair_directives must be list")
    known_tasks = {item.task_ref for item in context.active_tasks}
    known_commitments = {item.commitment_ref for item in context.fixed_commitments}
    directives = []
    for item in directives_payload:
        if not isinstance(item, dict) or set(item) != {
            "kind", "task_ref", "commitment_ref", "related_task_ref"
        }:
            raise AgenticParseError("repair directive fields invalid")
        directive = IntentRepairDirective(
            _required_text(item, "kind"),
            _optional_text(item.get("task_ref")),
            _optional_text(item.get("commitment_ref")),
            _optional_text(item.get("related_task_ref")),
        )
        if directive.task_ref is not None and directive.task_ref not in known_tasks:
            raise AgenticParseError("unknown task_ref in repair directive")
        if directive.related_task_ref is not None and directive.related_task_ref not in known_tasks:
            raise AgenticParseError("unknown related_task_ref in repair directive")
        if directive.commitment_ref is not None and directive.commitment_ref not in known_commitments:
            raise AgenticParseError("unknown commitment_ref in repair directive")
        directives.append(directive)
    return IntentComplianceReviewV2(
        decision, _required_text(payload, "reason"), tuple(directives)
    )


def deterministic_intent_review(context, candidate):
    sequence = list(candidate.task_sequence)
    if context.preferred_next_task_ref and (
        not sequence or sequence[0] != context.preferred_next_task_ref
    ):
        return IntentComplianceReviewV2(
            "reject", "候选没有优先满足最新明确任务偏好",
            (IntentRepairDirective("prefer_task", context.preferred_next_task_ref),),
            generated=False,
        )
    positions = {ref: index for index, ref in enumerate(sequence)}
    for before, after in context.ordering_constraints:
        if before in positions and after in positions and positions[before] > positions[after]:
            return IntentComplianceReviewV2(
                "reject", "候选违反用户明确任务顺序",
                (IntentRepairDirective("preserve_order", before, related_task_ref=after),),
                generated=False,
            )
    authorized = {
        (item.task_ref, item.commitment_ref) for item in context.concurrency_authorizations
    }
    if any(pair not in authorized for pair in candidate.concurrency_pairs):
        return IntentComplianceReviewV2(
            "reject", "候选包含未授权并行",
            (IntentRepairDirective("remove_unapproved_concurrency"),),
            generated=False,
        )
    return IntentComplianceReviewV2("approve", "候选符合已知明确意图", generated=False)


def _candidate_payload(item):
    return {
        "candidate_id": item.candidate_id,
        "timeline": list(item.timeline),
        "task_completion": list(item.task_completion),
        "remaining_work": list(item.remaining_work),
        "concurrency_pairs": list(item.concurrency_pairs),
        "user_preference_satisfied": item.user_preference_satisfied,
        "task_sequence": list(item.task_sequence),
    }


def _required_text(payload, name):
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise AgenticParseError("{} must be text".format(name))
    return value.strip()


def _optional_text(value):
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise AgenticParseError("optional ref must be text")
    return value.strip()
