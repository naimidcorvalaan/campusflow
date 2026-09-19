"""Semantic completeness audit for one parsed initial Day Intake.

The extraction pass remains responsible for producing the existing
``DayIntakeProposal``.  This module gives a second, narrowly-scoped model pass
the original wording and that parsed proposal.  The auditor may approve it or
return one complete repaired proposal; it never plans routes or time windows.

Python 3.8 compatible.
"""

import json
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Callable, Optional, Tuple

from src.p2_agentic_parser import AgenticParseError, extract_json_object
from src.p2_day_intake import (
    DayIntakeProposal, parse_day_intake, TASK_TIME_BOUNDARY_SEMANTICS, commitment_end_contract,
    LOCATION_ROLE_SEMANTICS, CLOCK_RANGE_SEMANTICS, day_intake_field_contract, EVENT_FACT_SEMANTICS,
    INTAKE_SCHEMA_VERSION,
)


INTAKE_AUDIT_SCHEMA_VERSION = "p4.initial-intake-audit.v1"


@dataclass(frozen=True)
class InitialIntakeAuditResult:
    proposal: DayIntakeProposal
    decision: str
    issues: Tuple[str, ...] = ()
    repaired: bool = False
    audit_failed: bool = False
    call_count: int = 1


def audit_initial_intake(
    reference_datetime: datetime,
    user_text: str,
    proposal: DayIntakeProposal,
    caller: Callable[[str, str], str],
    campus_id: Optional[str] = None,
    raw_events=None,
    semantic_graph=None,
    confirmed_proposal: Optional[DayIntakeProposal] = None,
    _retry_error: Optional[str] = None,
    map_data=None,
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

    if (raw_events is not None and semantic_graph is not None
            and semantic_graph.background_processes and not semantic_graph.conflicts):
        from src.p4_event_semantics import materialize_day_intake
        # The Semantic Linker already reviews raw evidence for autonomy and
        # causal/overlap relationships. Preserve that exact validated result;
        # a second model must not reclassify its tasks or invent actual progress.
        if materialize_day_intake(raw_events, semantic_graph) == proposal:
            return InitialIntakeAuditResult(proposal, 'approve', call_count=0)

    system, user = build_initial_intake_audit_prompt(
        reference_datetime, user_text, proposal, campus_id=campus_id,
        raw_events=raw_events, semantic_graph=semantic_graph,
    )
    if _retry_error:
        user += '\n上次审核未通过正式契约：' + _retry_error + '。重新审核原候选；只列真正错误，不列已通过的字段。'

    def failed(issues=(), error='invalid audit JSON/schema'):
        # Background permissions require successful independent review. One
        # correction of an invalid audit, never acceptance of malformed data.
        if _retry_error is None and any(t.attention_mode == 'background' for t in proposal.tasks):
            from dataclasses import replace
            result = audit_initial_intake(reference_datetime, user_text, proposal, caller,
                campus_id, raw_events, semantic_graph, confirmed_proposal, error, map_data)
            return replace(result, call_count=1 + result.call_count)
        return InitialIntakeAuditResult(proposal, 'fallback', tuple(issues), audit_failed=True)
    raw = caller(system, user)
    try:
        payload = _parse_audit_payload(raw)
    except (AgenticParseError, TypeError, ValueError):
        return failed()

    decision = payload["decision"]
    issues = tuple(payload["issues"])
    if decision == "approve":
        return InitialIntakeAuditResult(proposal=proposal, decision="approve", issues=issues)

    repaired_payload = payload["repaired_proposal"]
    try:
        # An audit revision is a root-field patch against its exact input,
        # not a new intake. Omitted fields retain their existing facts; arrays
        # supplied by the model replace whole arrays and still pass the formal
        # parser. No natural-language matching, value invention or JSON repair.
        original = _proposal_payload(proposal)
        if not repaired_payload or set(repaired_payload) - set(original):
            raise ValueError("audit revision must contain known proposal fields")
        merged = dict(original)
        merged.update(repaired_payload)
        if merged.get("schema_version") != INTAKE_SCHEMA_VERSION:
            raise ValueError("audit revision has incompatible proposal version")
        repaired = parse_day_intake(json.dumps(merged, ensure_ascii=False))
        # Pass A is a candidate, not a user-confirmed fact. Protect only an
        # explicitly supplied trusted baseline; otherwise an invented deadline
        # would become impossible for the semantic auditor to correct.
        if confirmed_proposal is not None and (
            _before_relation_facts(confirmed_proposal) - _before_relation_facts(repaired)
        ):
            raise ValueError("intake revision dropped an established before relation")
    except (AgenticParseError, TypeError, ValueError) as exc:
        return failed(issues, str(exc))
    checks = _changed_place_questions(proposal, repaired)
    rejected = set()
    if checks:
        # Before/after values and the auditor's justification are deliberately
        # absent: this pass extracts source facts, not agreement with a patch.
        rejected = _unsupported_place_revisions(user_text, checks, caller, map_data)
        # A place disagreement cannot undo an independent valid correction,
        # such as removal of an invented deadline. Roll back only that slot.
        if 'current' in rejected:
            repaired = replace(repaired, current_location=proposal.current_location)
        for kind in ('tasks', 'commitments'):
            values = list(getattr(repaired, kind))
            for index, item in enumerate(values):
                if kind + ':' + str(index) in rejected:
                    old = next(old for old in getattr(proposal, kind) if old.title == item.title)
                    values[index] = replace(item, location_text=old.location_text)
            repaired = replace(repaired, **{kind: tuple(values)})
        if rejected:
            issues += ('unverified location slots retained; other audit changes preserved',)
    changed = repaired != proposal
    return InitialIntakeAuditResult(
        proposal=repaired,
        decision="repair" if changed else "approve",
        issues=issues,
        repaired=changed,
        audit_failed=bool(rejected and not changed),
        call_count=2 if checks else 1,
    )


def _changed_place_questions(original, repaired):
    """Only audit replacement of existing locations, never invent identity.

    At intake, array entries have not yet received stable task refs. A place
    replacement is recognized only for an unambiguous unchanged activity
    title; array reordering is harmless. The temporary question ref belongs
    to this one validation call and is never persisted as a task identity.
    """
    checks = []
    if original.current_location and original.current_location != repaired.current_location:
        checks.append(('current', '用户', 'current_location', repaired.current_location))
    for kind in ('tasks', 'commitments'):
        before = getattr(original, kind)
        after = getattr(repaired, kind)
        for index, item in enumerate(after):
            matches = [old for old in before if old.title == item.title]
            if (len(matches) == 1 and matches[0].location_text
                    and sum(other.title == item.title for other in after) == 1
                    and matches[0].location_text != item.location_text):
                checks.append((kind + ':' + str(index), item.title,
                               'execution_location', item.location_text))
    return checks


def _unsupported_place_revisions(user_text, checks, caller, map_data):
    system = (
        '你是 CampusFlow 的 Source Place Extractor。从用户原文独立抽取每个问题对应的地点事实。'
        '任务执行地点是人实际做那个动作的地方，不是前往该处之前的出发地。'
        '结合原文理解指代，没有依据则null。evidence引用原文支持片段，不改写。'
        '只返回JSON：{"answers":[{"ref":string,"location":string|null,"evidence":string|null}]}。'
    )
    user = json.dumps(dict(user=user_text, questions=[dict(ref=ref, subject=subject, role=role)
        for ref, subject, role, _ in checks]), ensure_ascii=False)
    expected = {ref: location for ref, _, _, location in checks}
    def same_place(left, right):
        if left == right:
            return True
        if not left or not right or map_data is None:
            return False
        from src.p3_location_resolver import resolve_location
        a, b = resolve_location(map_data, left), resolve_location(map_data, right)
        return bool(a.node_id and a.node_id == b.node_id)
    try:
        payload = extract_json_object(caller(system, user))
        if not isinstance(payload, dict) or set(payload) != {'answers'}:
            return set(expected)
        answers = payload['answers']
        if not isinstance(answers, list) or len(answers) != len(expected):
            return set(expected)
        seen, rejected = set(), set()
        for answer in answers:
            if not isinstance(answer, dict) or set(answer) != {'ref', 'location', 'evidence'}:
                return set(expected)
            ref, location, evidence = answer['ref'], answer['location'], answer['evidence']
            if not isinstance(ref, str) or ref not in expected or ref in seen:
                return set(expected)
            seen.add(ref)
            if location is not None and (not isinstance(location, str) or not location.strip()):
                return set(expected)
            if location is not None and (not isinstance(evidence, str) or not evidence or evidence not in user_text):
                return set(expected)
            if location is None and evidence is not None:
                return set(expected)
            if not same_place(location, expected[ref]):
                rejected.add(ref)
        return rejected
    except (AgenticParseError, TypeError, ValueError):
        return set(expected)


def _before_relation_facts(proposal):
    """Reject an audit rewrite that erases or rebinds an established deadline.

    Reordered arrays are fine; identity must still be supported by the same
    task and fixed-commitment facts. Ambiguous renaming keeps parsed Pass A.
    Stable refs are assigned later at apply, never inferred by this guard.
    """
    result = Counter()
    for task in proposal.tasks:
        index = task.before_commitment_index
        if index is not None:
            commitment = proposal.commitments[index - 1]
            result[(task.title, commitment.title, commitment.starts_at,
                    commitment.starts_in_minutes)] += 1
    return result


def build_initial_intake_audit_prompt(
    reference_datetime: datetime,
    user_text: str,
    proposal: DayIntakeProposal,
    campus_id: Optional[str] = None,
    raw_events=None,
    semantic_graph=None,
):
    """Build the Pass-B prompt without asking the model to do time arithmetic."""
    include_process = _uses_process_fields(proposal)
    system = (
        "你是 CampusFlow 的 Initial Intake Semantic Auditor。审核候选是否忠实表达用户，"
        "不规划路线、时间窗口，不判断当天能否做完。\n"
        "先独立理解用户原文，再逐项核对候选：漏任务、漏明确时长、固定安排与截止混淆、"
        "地点指代丢失、无来源的硬约束。候选字段和候选图均不是已确认事实。\n"
        "只修影响事实或可执行性的具体错误；不要因为字段冗余、标题风格或另一个表达也合法就重写候选。"
        "量化工作描述本身就是工作量事实，不需用户另说‘总时长’才有效。"
        "不可拆任务的最小片段等于整个任务时长是合法的；不要删除正确的工作量和来源。\n"
        "只保留用户确实要求的硬关系；没有关系也是完整的语义结果。任务与固定安排并列出现，"
        "不是任务必须在该安排之前结束。可排的时间不等于必须执行的时间。\n"
        "predecessor_task_indexes是不能违反的完成依赖：因果上需要前项产物，或用户明确禁止逆序。"
        "只是更想先做、叙述顺序或可协商偏好，不应升级成这种硬依赖；它们由后续顺序规划读取原文。"
        "不以赶截止为由新增依赖，也不为了容纳某种顺序而给独立任务传播deadline。\n"
        "任务地点中的指代由你结合完整原文理解，保留对应用户地点，不根据最近地点猜测。"
        "标题仅描述活动本身；时刻、地点、时长存入对应字段。\n"
        "可选偏好、未知普通任务分钟、尚未选定排程不是必要追问；固定承诺必要时间或"
        "路线所必需的用户地点确实缺失时才提问。\n"
        + LOCATION_ROLE_SEMANTICS + CLOCK_RANGE_SEMANTICS + TASK_TIME_BOUNDARY_SEMANTICS
        + commitment_end_contract() + EVENT_FACT_SEMANTICS
        + '输出单个JSON且完整包含四字段：{"schema_version":"' + INTAKE_AUDIT_SCHEMA_VERSION
        + '","decision":"approve或repair","issues":[],"repaired_proposal":null}。'
        "issues只列实际错误，每项一句；不要列正确字段、反复自问或推理过程。完全忠实才approve并令repaired_proposal=null；"
        "否则repair：repaired_proposal是对给定proposal的顶层字段修订，未提供的顶层字段保持原值；"
        "提供tasks或commitments时必须给完整数组及其中的正式字段，不得靠省略数组元素删除已有事实。"
        "嵌套版本由审核契约固定为p2.day-intake.v1，无需重复输出；若输出则必须一致。"
        "修复对象字段契约：\n" + day_intake_field_contract(include_process=include_process)
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
        proposal=json.dumps(_audit_proposal_payload(proposal, include_process), ensure_ascii=False, sort_keys=True),
    )
    if raw_events is not None:
        from src.p4_event_semantics import raw_event_payload
        user += "\n\nRaw Events：\n" + json.dumps(raw_event_payload(raw_events), ensure_ascii=False, sort_keys=True)
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
                "before_commitment_index": item.before_commitment_index,
                "meal_period": item.meal_period,
                "meal_before_commitment_index": item.meal_before_commitment_index,
                "meal_time": item.meal_time,
                "earliest_start_time": item.earliest_start_time,
                "latest_end_time": item.latest_end_time,
                "predecessor_task_indexes": list(item.predecessor_task_indexes),
                "departure_after_task_indexes": list(item.departure_after_task_indexes),
                "overlap_task_index": item.overlap_task_index,
                "attention_mode": item.attention_mode, "launch_task_index": item.launch_task_index,
                "background_reason": item.background_reason, "user_reported_running": item.user_reported_running,
            }
            for item in proposal.tasks
        ],
        "questions": list(proposal.questions),
        "current_location": proposal.current_location,
        "transport_mode": proposal.transport_mode,
    }


_PROCESS_DEFAULTS = {
    "departure_after_task_indexes": [], "overlap_task_index": None,
    "attention_mode": "active", "launch_task_index": None,
    "background_reason": None, "user_reported_running": False,
}

def _uses_process_fields(proposal):
    return any(any(task[key] != default for key, default in _PROCESS_DEFAULTS.items())
               for task in _proposal_payload(proposal)["tasks"])

def _audit_proposal_payload(proposal, include_process):
    """Omit only unused default-valued extension fields; never discard facts.

    The full parser and mutation payload remain unchanged. Projection is
    selected from formal values, not input language or scenario identifiers.
    """
    payload = _proposal_payload(proposal)
    if not include_process:
        if _uses_process_fields(proposal):
            raise ValueError("non-default process facts cannot be projected out")
        for task in payload["tasks"]:
            for key in _PROCESS_DEFAULTS:
                del task[key]
    return payload
