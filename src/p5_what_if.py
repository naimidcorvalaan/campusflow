"""Hypothetical planning that cannot mutate canonical session facts."""

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional, Tuple

from src.p2_agentic_parser import AgenticParseError, extract_json_object
from src.p5_agent_context import AgentDecisionContext
from src.p5_agent_runtime import call_structured_stage

WHAT_IF_PREVIEW_KEY = "p5_what_if_preview"
WHAT_IF_SCHEMA_VERSION = "p5.what-if-intent.v1"
_CHANGE_KINDS = {
    "prefer_task", "cancel_task", "postpone_task", "ordering",
    "location_change", "authorize_concurrency", "revoke_concurrency",
}
logger = logging.getLogger("campusflow.what_if")


class WhatIfApplyError(ValueError):
    """Expected apply rejection with a safe user-facing message."""


class WhatIfInfeasibleError(ValueError):
    """The validated candidate cannot satisfy the requested change."""


class WhatIfOrderingConflict(ValueError):
    """The new hypothetical itself contains contradictory semantic edges."""


@dataclass(frozen=True)
class WhatIfChange:
    kind: str
    task_ref: Optional[str] = None
    related_task_ref: Optional[str] = None
    commitment_ref: Optional[str] = None
    location_text: Optional[str] = None
    requested_minutes: Optional[int] = None

    def __post_init__(self):
        if self.kind not in _CHANGE_KINDS:
            raise ValueError("what-if change kind invalid")
        for value in (self.task_ref, self.related_task_ref, self.commitment_ref, self.location_text):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError("what-if optional text must be non-empty or None")
        if self.requested_minutes is not None and (
            isinstance(self.requested_minutes, bool)
            or not isinstance(self.requested_minutes, int) or self.requested_minutes < 1
        ):
            raise ValueError("requested_minutes invalid")
        if self.kind != "location_change" and self.task_ref is None:
            raise ValueError("what-if task change requires task_ref")
        if self.kind == "ordering" and (
            self.related_task_ref is None or self.related_task_ref == self.task_ref
        ):
            raise ValueError("what-if ordering requires two distinct task refs")
        if self.kind == "location_change" and self.location_text is None:
            raise ValueError("what-if location change requires location_text")
        if self.kind in ("authorize_concurrency", "revoke_concurrency") and self.commitment_ref is None:
            raise ValueError("what-if concurrency requires commitment_ref")
        if self.kind == "authorize_concurrency" and self.requested_minutes is None:
            raise ValueError("what-if authorization requires requested_minutes")


@dataclass(frozen=True)
class WhatIfIntent:
    changes: Tuple[WhatIfChange, ...]
    source: str = "explicit_user_hypothetical"
    clarification_needed: bool = False
    clarification_question: Optional[str] = None

    def __post_init__(self):
        if self.source != "explicit_user_hypothetical":
            raise ValueError("what-if source invalid")
        if self.clarification_needed != bool(self.clarification_question):
            raise ValueError("what-if clarification mismatch")
        if not self.changes and not self.clarification_needed:
            raise ValueError("what-if needs a change or clarification")
        pairs = [(item.task_ref, item.related_task_ref) for item in self.changes if item.kind == "ordering"]
        for left, right in pairs:
            if _reachable(right, left, pairs):
                raise WhatIfOrderingConflict("what-if ordering conflict")


@dataclass(frozen=True)
class WhatIfPreview:
    preview_id: str
    baseline_fingerprint: str
    intent: Optional[WhatIfIntent]
    summary: str
    key_differences: Tuple[str, ...]
    resulting_current_action: Optional[str]
    important_impact: Optional[str]
    feasibility: str
    warning: Optional[str]
    candidate: Optional[object] = None
    source_user_text: Optional[str] = None
    baseline_version: Optional[int] = None

    def __post_init__(self):
        if self.feasibility not in ("feasible", "infeasible", "needs_clarification", "failed"):
            raise ValueError("what-if feasibility invalid")
        if self.feasibility == "feasible" and not (
            isinstance(self.intent, WhatIfIntent) and self.intent.changes
            and not self.intent.clarification_needed and self.candidate is not None
        ):
            raise ValueError("ready preview requires changes and a candidate")
        for value in (self.preview_id, self.baseline_fingerprint, self.summary):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("what-if preview text fields must be non-empty")
        if self.source_user_text is not None and (
            not isinstance(self.source_user_text, str)
            or not self.source_user_text.strip()
        ):
            raise ValueError("what-if source text must be non-empty or None")

    @property
    def status(self):
        return "ready" if self.feasibility == "feasible" else self.feasibility

    @property
    def can_apply(self):
        return bool(self.status == "ready" and self.candidate is not None
                    and self.baseline_fingerprint and self.intent
                    and self.intent.changes and not self.intent.clarification_needed)


def interpret_what_if(context, user_text, caller, repair_caller=None, trace=None):
    if not isinstance(context, AgentDecisionContext):
        raise TypeError("context invalid")
    system, user = build_what_if_prompt(context, user_text)
    def parser(text):
        try:
            result = parse_what_if_intent(text, context)
        except Exception as exc:
            logger.info("what_if stage=parse status=rejected exception=%s", type(exc).__name__)
            raise
        logger.info("what_if stage=parse status=accepted targets=%s clarification=%s",
                    tuple(item.task_ref for item in result.changes if item.task_ref), result.clarification_needed)
        return result
    return call_structured_stage(
        caller, repair_caller, system, user, parser,
        "what_if_interpreter", "understand a hypothetical change", trace,
        repair_system_prompt=system + " 上次输出未通过校验；按完整schema重新输出一次。",
    )


def build_what_if_prompt(context, user_text):
    system = (
        "你是 CampusFlow What-if Interpreter。判断用户提出的只是一个假设预览，不修改当前方案。"
        "所有 task_ref/commitment_ref 必须来自上下文。只输出 JSON："
        "{{schema_version:'{}',changes:[{{kind:prefer_task|cancel_task|postpone_task|ordering|"
        "location_change|authorize_concurrency|revoke_concurrency,task_ref:null|string,"
        "related_task_ref:null|string,commitment_ref:null|string,location_text:null|string,"
        "requested_minutes:null|int}}],clarification_needed:bool,clarification_question:string|null}}。"
        "ordering的task_ref表示先执行的任务，related_task_ref表示随后执行的任务。"
        "假设可以反转旧顺序；这不是歧义，不因与旧偏好不同而追问。"
        "用户说先预览、暂时不要改时保留上述变更，仅供副本模拟。"
        "不相关的change字段可省略；kind和对应目标字段必需。"
        "仅当上下文确实无法唯一绑定任务时提出一个澄清问题。"
    ).format(WHAT_IF_SCHEMA_VERSION)
    return system, "用户假设：{}\n只读上下文：{}".format(user_text, context.to_json())


def parse_what_if_intent(text, context):
    payload = extract_json_object(text)
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version", "changes", "clarification_needed", "clarification_question"
    }:
        raise AgenticParseError("what-if payload invalid")
    if payload.get("schema_version") != WHAT_IF_SCHEMA_VERSION:
        raise AgenticParseError("what-if schema mismatch")
    values = payload.get("changes")
    if not isinstance(values, list):
        raise AgenticParseError("what-if changes must be list")
    task_refs = {item.task_ref for item in context.active_tasks}
    commitment_refs = {item.commitment_ref for item in context.fixed_commitments}
    changes = []
    required = {
        "kind", "task_ref", "related_task_ref", "commitment_ref",
        "location_text", "requested_minutes",
    }
    for item in values:
        if not isinstance(item, dict) or "kind" not in item or set(item) - required:
            raise AgenticParseError("what-if change fields invalid")
        change = WhatIfChange(
            _required(item, "kind"), _optional(item.get("task_ref")),
            _optional(item.get("related_task_ref")), _optional(item.get("commitment_ref")),
            _optional(item.get("location_text")), _optional_minutes(item.get("requested_minutes")),
        )
        if change.task_ref is not None and change.task_ref not in task_refs:
            raise AgenticParseError("what-if contains unknown task_ref")
        if change.related_task_ref is not None and change.related_task_ref not in task_refs:
            raise AgenticParseError("what-if contains unknown related task_ref")
        if change.commitment_ref is not None and change.commitment_ref not in commitment_refs:
            raise AgenticParseError("what-if contains unknown commitment_ref")
        changes.append(change)
    clarification = payload.get("clarification_question")
    return WhatIfIntent(
        tuple(changes), clarification_needed=_bool(payload, "clarification_needed"),
        clarification_question=_optional(clarification),
    )


def build_what_if_preview(context, intent, candidate, baseline_fingerprint=None, failure_status=None):
    """Create a preview from a candidate built on cloned canonical inputs."""
    if not isinstance(context, AgentDecisionContext) or (intent is not None and not isinstance(intent, WhatIfIntent)):
        raise TypeError("context/intent invalid")
    fingerprint = baseline_fingerprint or fingerprint_context(context)
    if failure_status == "failed" or intent is None:
        summary = "这次预览未能生成，当前方案没有改变。"
        feasibility, differences, current, impact, warning = "failed", (), None, None, None
        candidate = None
    elif intent.clarification_needed:
        summary = intent.clarification_question or "这个假设还需要一点信息。"
        feasibility = "needs_clarification"
        differences = ()
        current = None
        impact = None
        warning = None
    elif candidate is None:
        summary = "这个调整在当前硬约束下暂时不可行。"
        feasibility = "infeasible"
        differences = ("当前可靠方案保持不变",)
        current = None
        impact = None
        warning = "固定安排、路线或时长限制无法同时满足。"
    else:
        agent = getattr(candidate, "agent_intelligence", None)
        summary_candidate = agent.selected_candidate if agent is not None else candidate
        summary_obj = getattr(summary_candidate, "summary", summary_candidate)
        timeline = tuple(getattr(summary_obj, "timeline", ()) or ())
        plan = getattr(getattr(candidate, "result", None), "allocation_plan", None)
        completion = (tuple(plan.planned_minutes_by_task.items()) if plan is not None
                      else tuple(getattr(summary_obj, "task_completion", ()) or ()))
        title_by_ref = {item.task_ref: item.title for item in context.active_tasks}
        baseline = {}
        for item in context.selected_plan:
            baseline[item.task_ref] = baseline.get(item.task_ref, 0) + item.planned_minutes
        planned = dict(completion)
        differences = _order_differences(context, intent, candidate, summary_obj)
        for ref in sorted(set(baseline) | set(planned)):
            delta = planned.get(ref, 0) - baseline.get(ref, 0)
            if delta:
                direction = "增加" if delta > 0 else "减少"
                differences.append("{}{}{}分钟".format(
                    title_by_ref.get(ref, "这项任务"), direction, abs(delta)
                ))
        differences = tuple(differences) or ("本次预览的任务顺序和计划时长没有变化。",)
        current = _current_action(timeline, title_by_ref)
        summary = "可以先这样安排；采用前，当前方案保持不变。"
        impact = _commitment_impact(context, candidate)
        warning = None
        feasibility = "feasible"
    digest = hashlib.sha256((fingerprint + repr(intent)).encode("utf-8")).hexdigest()[:16]
    return WhatIfPreview(
        "whatif_" + digest, fingerprint, intent, summary, differences,
        current, impact, feasibility, warning, candidate,
    )


def _current_action(timeline, title_by_ref):
    for row in timeline:
        parts = str(row).split(":")
        if len(parts) >= 2 and parts[0] == "task":
            return title_by_ref.get(parts[1], "当前任务")
        text = str(row).strip()
        if "：" in text:
            return text.split("：", 1)[1].strip()
        if text:
            return text
    return None


def apply_what_if_preview(preview, current_context, apply_callback):
    """Apply only after an explicit user action and baseline match."""
    if not isinstance(preview, WhatIfPreview):
        raise TypeError("preview invalid")
    if not isinstance(current_context, AgentDecisionContext):
        raise TypeError("current_context invalid")
    if not preview.can_apply:
        raise WhatIfApplyError("这份预览尚不能采用，当前方案没有改变。")
    if preview.baseline_fingerprint != fingerprint_context(current_context):
        raise WhatIfApplyError("这份预览已过期，请基于当前方案重新预览。")
    if not callable(apply_callback):
        raise TypeError("apply_callback must be callable")
    return apply_callback(preview.intent)


def _reachable(start, end, pairs):
    pending, seen = [start], set()
    while pending:
        node = pending.pop()
        if node == end:
            return True
        if node not in seen:
            seen.add(node)
            pending.extend(right for left, right in pairs if left == node)
    return False


def replace_hypothetical_preferences(previous, current):
    """Replace conflicting old soft edges only, on the isolated decision."""
    from dataclasses import replace
    from src.p4_feedback_decision import FeedbackDecision
    if not isinstance(previous, FeedbackDecision):
        return current
    edges = list(previous.ordering_constraints)
    new_edges = list(current.ordering_constraints)
    # Any old edge lying on a reverse path contradicts the new relation.
    # Remove those edges, preserving unrelated preferences and all hard facts.
    for new in new_edges:
        pairs = [(edge.before_task_ref, edge.after_task_ref) for edge in edges]
        edges = [edge for edge in edges if not (
            _reachable(new.after_task_ref, edge.before_task_ref, pairs)
            and _reachable(edge.after_task_ref, new.before_task_ref, pairs)
        )]
    for edge in new_edges:
        if edge not in edges:
            edges.append(edge)
    touched = set(current.target_task_refs)
    preferred = current.preferred_next_task_ref
    if preferred is None and previous.preferred_next_task_ref not in touched:
        preferred = previous.preferred_next_task_ref
    if preferred:
        edges = [edge for edge in edges if edge.after_task_ref != preferred or edge in new_edges]
    return replace(
        current, ordering_constraints=tuple(edges), preferred_next_task_ref=preferred,
        priority_changes=tuple(x for x in previous.priority_changes if x.task_ref not in touched) + current.priority_changes,
        postponed_task_refs=tuple(dict.fromkeys(
            tuple(ref for ref in previous.postponed_task_refs if ref not in touched) + current.postponed_task_refs
        )),
    )


def _order_differences(context, intent, candidate, summary):
    titles = {task.task_ref: task.title for task in context.active_tasks}
    rows = []
    intervals = {}
    result = getattr(candidate, "result", None)
    if result is not None:
        for window in result.updated_state.windows:
            cursor = window.starts_at
            for allocation in sorted((a for a in result.allocation_plan.allocations if a.window_ref == window.window_ref),
                                     key=lambda a: (a.sequence_index, a.allocation_ref)):
                cursor = allocation.starts_at or cursor
                end = cursor + timedelta(minutes=allocation.planned_minutes)
                intervals.setdefault(allocation.task_ref, []).append((cursor, end))
                cursor = end
    sequence = list(dict.fromkeys(getattr(summary, "task_sequence", ()) or tuple(intervals)))
    old_sequence = list(dict.fromkeys(item.task_ref for item in context.selected_plan))
    for change in intent.changes:
        left, right = change.task_ref, change.related_task_ref
        if change.kind == "ordering" and left in sequence and right in sequence:
            if old_sequence == sequence:
                rows.append("{}仍排在{}之前，顺序没有变化。".format(titles[left], titles[right]))
            else:
                rows.append("{}将排到{}之前。".format(titles[left], titles[right]))
        refs = (left, right) if change.kind == "ordering" else (left,)
        for ref in refs:
            if ref in intervals:
                row = "{}：{}".format(titles[ref], "、".join("{}–{}".format(s.strftime("%H:%M"), e.strftime("%H:%M")) for s, e in intervals[ref]))
                if row not in rows:
                    rows.append(row)
    return rows


def _commitment_impact(context, candidate):
    state = getattr(candidate, "state", None)
    if state is None:
        return None
    old = {item.commitment_ref: (item.starts_at, item.ends_at) for item in context.fixed_commitments}
    rows = []
    for item in state.commitments:
        if item.ends_at is not None and item.ends_at <= state.now:
            continue  # Historical timetable records are not upcoming impacts.
        times = (item.starts_at.isoformat() if item.starts_at else None,
                 item.ends_at.isoformat() if item.ends_at else None)
        if old.get(item.commitment_ref) == times and item.starts_at:
            end = item.ends_at.strftime("%H:%M") if item.ends_at else "结束时间待确认"
            rows.append("{} {}–{}不变".format(item.title, item.starts_at.strftime("%H:%M"), end))
    return "；".join(rows) or None


def fingerprint_context(context):
    if not isinstance(context, AgentDecisionContext):
        raise TypeError("context invalid")
    return hashlib.sha256(context.to_json().encode("utf-8")).hexdigest()


def load_what_if_preview(store):
    value = store.get(WHAT_IF_PREVIEW_KEY)
    return value if isinstance(value, WhatIfPreview) else None


def save_what_if_preview(store, preview):
    if not isinstance(preview, WhatIfPreview):
        raise TypeError("preview invalid")
    store[WHAT_IF_PREVIEW_KEY] = preview


def clear_what_if_preview(store):
    store.pop(WHAT_IF_PREVIEW_KEY, None)


def _required(payload, name):
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise AgenticParseError("{} must be text".format(name))
    return value.strip()


def _optional(value):
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise AgenticParseError("optional text invalid")
    return value.strip()


def _optional_minutes(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise AgenticParseError("requested_minutes invalid")
    return value


def _bool(payload, name):
    value = payload.get(name)
    if not isinstance(value, bool):
        raise AgenticParseError("{} must be bool".format(name))
    return value
