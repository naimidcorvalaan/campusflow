"""Semantic completeness audit for one parsed initial Day Intake.

The extraction pass remains responsible for producing the existing
``DayIntakeProposal``.  This module gives a second, narrowly-scoped model pass
the original wording and that parsed proposal.  The auditor may approve it or
return one complete repaired proposal; it never plans routes or time windows.

Python 3.8 compatible.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional, Tuple

from src.p2_agentic_parser import AgenticParseError, extract_json_object
from src.p2_day_intake import DayIntakeProposal, parse_day_intake


INTAKE_AUDIT_SCHEMA_VERSION = "p4.initial-intake-audit.v1"


@dataclass(frozen=True)
class InitialIntakeAuditResult:
    proposal: DayIntakeProposal
    decision: str
    issues: Tuple[str, ...] = ()
    repaired: bool = False
    audit_failed: bool = False


def audit_initial_intake(
    reference_datetime: datetime,
    user_text: str,
    proposal: DayIntakeProposal,
    caller: Callable[[str, str], str],
    campus_id: Optional[str] = None,
    raw_events=None,
    semantic_graph=None,
) -> InitialIntakeAuditResult:
    """Run one bounded semantic audit; malformed output keeps parsed Pass A.

    Keeping Pass A is the safe fallback: the auditor cannot erase a proposal
    that already passed the strict intake parser, and no raw model payload is
    exposed to the presentation layer.
    """
    if not isinstance(reference_datetime, datetime):
        raise TypeError("reference_datetime must be a datetime")
    if not isinstance(user_text, str) or not user_text.strip():
        raise ValueError("user_text must be a non-empty string")
    if not isinstance(proposal, DayIntakeProposal):
        raise TypeError("proposal must be a DayIntakeProposal")
    if not callable(caller):
        raise TypeError("caller must be callable")

    system, user = build_initial_intake_audit_prompt(
        reference_datetime, user_text, proposal, campus_id=campus_id,
        raw_events=raw_events, semantic_graph=semantic_graph,
    )
    raw = caller(system, user)
    try:
        payload = _parse_audit_payload(raw)
    except (AgenticParseError, TypeError, ValueError):
        return InitialIntakeAuditResult(
            proposal=proposal,
            decision="fallback",
            audit_failed=True,
        )

    decision = payload["decision"]
    issues = tuple(payload["issues"])
    if decision == "approve":
        return InitialIntakeAuditResult(proposal=proposal, decision="approve", issues=issues)

    repaired_payload = payload["repaired_proposal"]
    try:
        repaired = parse_day_intake(json.dumps(repaired_payload, ensure_ascii=False))
    except (AgenticParseError, TypeError, ValueError):
        return InitialIntakeAuditResult(
            proposal=proposal,
            decision="fallback",
            issues=issues,
            audit_failed=True,
        )
    return InitialIntakeAuditResult(
        proposal=repaired,
        decision="repair",
        issues=issues,
        repaired=True,
    )


def build_initial_intake_audit_prompt(
    reference_datetime: datetime,
    user_text: str,
    proposal: DayIntakeProposal,
    campus_id: Optional[str] = None,
    raw_events=None,
    semantic_graph=None,
):
    """Build the Pass-B prompt without asking the model to do time arithmetic."""
    system = (
        "你是 CampusFlow 的 Initial Intake Semantic Auditor。\n"
        "schema_version 必须为 " + INTAKE_AUDIT_SCHEMA_VERSION + "。\n"
        "你只审核首次输入抽取的语义完整性，不规划路线、食堂、可行窗口或任务时间。\n"
        "检查：是否漏任务、漏用户明确时长、漏固定安排、漏地点、漏‘然后’后的内容，"
        "以及相对结束时长是否绑定最近一个语义上可结束的 fixed commitment。\n"
        "若用户在某个固定安排结束后说‘然后做X’，检查 X 的 after_commitment_index 是否指向该"
        "固定安排（从 1 开始）；程序会使用该安排的真实结束时刻作为最早开始时间。\n"
        "重要：after_commitment_index 只标记该结束语义之后明确出现的任务后缀；固定安排之前已经"
        "出现的任务绝不能被一并标记。逐项核对 tasks 数组，不能把整个 proposal.tasks 套同一约束。\n"
        "还要审核 meal 的 event-relative 语义：如果用户把吃饭明确放在某固定安排之前，"
        "在该 meal task 填 meal_before_commitment_index；若明确放在安排结束之后，填 "
        "after_commitment_index。索引都从 commitments 数组的 1 开始，且必须只绑定正确的一项。"
        "meal_period 只能是 lunch、dinner 或 unspecified；用户给出明确吃饭时刻时填 meal_time=HH:MM。"
        "叙述中的 before/after 关系优先于按当前钟点推断午饭或晚饭；不要把固定安排前已出现的"
        "报告或吃饭错误放入 after-commitment 后缀。\n"
        "若同时提供 Raw Events 与 Semantic Graph，逐项检查 duration 是否绑定正确事件、每个"
        "after/before 是否指向正确 fixed commitment、以及图中的关系是否已完整落到 proposal。\n"
        "相对结束语义只写 relative_end_minutes，例如‘21点上课，一个半小时后下课’写 90；"
        "不要自行计算或输出 22:30，时间加法由程序完成。\n"
        "不得补充用户没有表达的任务、时长、地点或承诺。\n"
        "输出 JSON：{\"schema_version\":\"p4.initial-intake-audit.v1\","
        "\"decision\":\"approve\"|\"repair\",\"issues\":[string,...],"
        "\"repaired_proposal\":object|null}。\n"
        "approve 时 repaired_proposal 必须为 null；repair 时必须返回完整、可独立解析的 "
        "p2.day-intake.v1 proposal。最多修复一次。"
    )
    user = (
        "参考时间：{reference}\n"
        "selected campus：{campus}\n"
        "用户原始完整输入：\n{raw}\n\n"
        "Pass A 已解析结构：\n{proposal}\n\n"
        "请只输出审核 JSON。"
    ).format(
        reference=reference_datetime.strftime("%Y-%m-%d %H:%M"),
        campus=campus_id or "未提供",
        raw=user_text,
        proposal=json.dumps(_proposal_payload(proposal), ensure_ascii=False, sort_keys=True),
    )
    if raw_events is not None:
        from src.p4_event_semantics import raw_event_payload
        user += "\n\nRaw Events：\n" + json.dumps(raw_event_payload(raw_events), ensure_ascii=False, sort_keys=True)
    if semantic_graph is not None:
        from src.p4_event_semantics import graph_payload
        user += "\n\nSemantic Graph：\n" + json.dumps(graph_payload(semantic_graph), ensure_ascii=False, sort_keys=True)
    return system, user


def _parse_audit_payload(text):
    payload = extract_json_object(text)
    if not isinstance(payload, dict):
        raise AgenticParseError("audit 输出必须是 JSON 对象")
    allowed = {"schema_version", "decision", "issues", "repaired_proposal"}
    if set(payload) != allowed:
        raise AgenticParseError("audit 字段不完整或包含未知字段")
    if payload.get("schema_version") != INTAKE_AUDIT_SCHEMA_VERSION:
        raise AgenticParseError("audit schema_version 不匹配")
    decision = payload.get("decision")
    if decision not in ("approve", "repair"):
        raise AgenticParseError("audit decision 无效")
    issues = payload.get("issues")
    if not isinstance(issues, list) or any(
        not isinstance(item, str) or not item.strip() for item in issues
    ):
        raise AgenticParseError("audit issues 必须是字符串数组")
    repaired = payload.get("repaired_proposal")
    if decision == "approve" and repaired is not None:
        raise AgenticParseError("approve 不得包含 repaired_proposal")
    if decision == "repair" and not isinstance(repaired, dict):
        raise AgenticParseError("repair 必须包含完整 repaired_proposal")
    return payload


def _proposal_payload(proposal):
    return {
        "schema_version": proposal.schema_version,
        "day_end": proposal.day_end,
        "commitments": [
            {
                "title": item.title,
                "starts_at": item.starts_at,
                "ends_at": item.ends_at,
                "starts_in_minutes": item.starts_in_minutes,
                "duration_minutes": item.duration_minutes,
                "relative_end_minutes": item.relative_end_minutes,
                "location_text": item.location_text,
                "commitment_kind": item.commitment_kind,
                "class_arrival_lead_minutes": item.class_arrival_lead_minutes,
            }
            for item in proposal.commitments
        ],
        "tasks": [
            {
                "title": item.title,
                "total_minutes": item.total_minutes,
                "is_splittable": item.is_splittable,
                "minimum_slice_minutes": item.minimum_slice_minutes,
                "preferred_chunk_minutes": item.preferred_chunk_minutes,
                "requires_single_session": item.requires_single_session,
                "execution_profile_source": item.execution_profile_source,
                "location_text": item.location_text,
                "activity_kind": item.activity_kind,
                "duration_source": item.duration_source,
                "after_commitment_index": item.after_commitment_index,
                "meal_period": item.meal_period,
                "meal_before_commitment_index": item.meal_before_commitment_index,
                "meal_time": item.meal_time,
            }
            for item in proposal.tasks
        ],
        "questions": list(proposal.questions),
        "current_location": proposal.current_location,
        "transport_mode": proposal.transport_mode,
    }
