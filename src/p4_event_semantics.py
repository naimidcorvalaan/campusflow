"""Qwen-facing initial-event semantics, converted to strict Day Intake facts.

This is intentionally a small translation boundary rather than another
planner.  The model identifies events and relationships using proposal-local
IDs; deterministic code validates that graph, performs time arithmetic later
in ``apply_day_intake``, and converts all surviving relations to the existing
Day Intake schema.
"""

import json
from dataclasses import dataclass
from typing import Optional, Tuple

from src.p2_agentic_parser import AgenticParseError, extract_json_object
from src.p2_day_intake import DayIntakeProposal, IntakeCommitment, IntakeTask, INTAKE_SCHEMA_VERSION


RAW_EVENT_SCHEMA_VERSION = "p4.raw-event-extraction.v1"
EVENT_SEMANTIC_SCHEMA_VERSION = "p4.event-semantics.v1"


@dataclass(frozen=True)
class RawEvent:
    local_event_id: str
    event_type: str
    title: str
    order_in_utterance: int
    starts_at: Optional[str] = None
    ends_at: Optional[str] = None
    explicit_duration_minutes: Optional[int] = None
    location_text: Optional[str] = None
    commitment_kind: Optional[str] = None
    raw_evidence: Optional[str] = None

    def __post_init__(self):
        if not isinstance(self.local_event_id, str) or not self.local_event_id.strip():
            raise ValueError("local_event_id 必须非空")
        if self.event_type not in ("task", "fixed_commitment", "meal"):
            raise ValueError("event_type 无效")
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("title 必须非空")
        if isinstance(self.order_in_utterance, bool) or not isinstance(self.order_in_utterance, int) or self.order_in_utterance < 1:
            raise ValueError("order_in_utterance 必须为正整数")
        for field_name, value in (("starts_at", self.starts_at), ("ends_at", self.ends_at)):
            if value is not None and not _is_time(value):
                raise ValueError("{} 必须为 HH:MM 或 null".format(field_name))
        if self.explicit_duration_minutes is not None and (
            isinstance(self.explicit_duration_minutes, bool) or self.explicit_duration_minutes < 1
        ):
            raise ValueError("explicit_duration_minutes 必须为正整数或 null")
        if self.commitment_kind not in (None, "class", "meeting", "appointment", "other"):
            raise ValueError("commitment_kind 无效")


@dataclass(frozen=True)
class EventDurationLink:
    event_id: str
    minutes: int

    def __post_init__(self):
        if not isinstance(self.event_id, str) or not self.event_id.strip():
            raise ValueError("duration event_id 必须非空")
        if isinstance(self.minutes, bool) or not isinstance(self.minutes, int) or self.minutes < 1:
            raise ValueError("duration minutes 必须为正整数")


@dataclass(frozen=True)
class EventRelation:
    event_id: str
    commitment_event_id: str
    relation: str  # before / after

    def __post_init__(self):
        if self.relation not in ("before", "after"):
            raise ValueError("relation 必须为 before 或 after")
        if not isinstance(self.event_id, str) or not self.event_id.strip():
            raise ValueError("event_id 必须非空")
        if not isinstance(self.commitment_event_id, str) or not self.commitment_event_id.strip():
            raise ValueError("commitment_event_id 必须非空")


@dataclass(frozen=True)
class EventExecutionProfile:
    """Qwen's execution-shape judgement for one ordinary task.

    It deliberately names only a proposal-local event.  ``materialize`` is
    the sole boundary that maps it to a stable task_ref after Day Intake.
    """
    event_id: str
    splittable: bool
    minimum_chunk_minutes: int
    preferred_chunk_minutes: int
    requires_single_session: bool = False
    source: str = "qwen_semantic"

    def __post_init__(self):
        if not isinstance(self.event_id, str) or not self.event_id.strip():
            raise ValueError("profile event_id 必须非空")
        if not isinstance(self.splittable, bool) or not isinstance(self.requires_single_session, bool):
            raise ValueError("profile flags 必须是 bool")
        if self.requires_single_session and self.splittable:
            raise ValueError("single-session task 不能同时 splittable")
        if isinstance(self.minimum_chunk_minutes, bool) or self.minimum_chunk_minutes < 1:
            raise ValueError("minimum chunk 必须为正整数")
        if isinstance(self.preferred_chunk_minutes, bool) or self.preferred_chunk_minutes < self.minimum_chunk_minutes:
            raise ValueError("preferred chunk 必须不小于 minimum chunk")
        if self.source not in ("qwen_semantic", "user_explicit"):
            raise ValueError("profile source 无效")


@dataclass(frozen=True)
class EventSemanticGraph:
    duration_of: Tuple[EventDurationLink, ...] = ()
    commitment_relations: Tuple[EventRelation, ...] = ()
    meal_period_by_event: Tuple[Tuple[str, str], ...] = ()
    explicit_sequence: Tuple[Tuple[str, str], ...] = ()
    conflicts: Tuple[str, ...] = ()
    execution_profiles: Tuple[EventExecutionProfile, ...] = ()

    def __post_init__(self):
        durations = [item.event_id for item in self.duration_of]
        if len(durations) != len(set(durations)):
            raise ValueError("一个事件只能有一个 duration_of")
        relations = [(item.event_id, item.commitment_event_id) for item in self.commitment_relations]
        if len(relations) != len(set(relations)):
            raise ValueError("事件与固定安排关系不能重复")
        relation_by_event = {}
        for item in self.commitment_relations:
            existing = relation_by_event.get((item.event_id, item.relation))
            if existing is not None and existing != item.commitment_event_id:
                raise ValueError("同一事件同类关系只能指向一个固定安排")
            relation_by_event[(item.event_id, item.relation)] = item.commitment_event_id
            opposite = relation_by_event.get((item.event_id, "after" if item.relation == "before" else "before"))
            if opposite is not None and opposite == item.commitment_event_id:
                raise ValueError("同一事件不能同时位于同一固定安排之前和之后")
        periods = [item[0] for item in self.meal_period_by_event]
        if len(periods) != len(set(periods)):
            raise ValueError("一个 meal 只能有一个 period")
        if any(period not in ("lunch", "dinner", "unspecified") for _, period in self.meal_period_by_event):
            raise ValueError("meal period 无效")
        profile_ids = [item.event_id for item in self.execution_profiles]
        if len(profile_ids) != len(set(profile_ids)):
            raise ValueError("一个事件只能有一个 execution profile")


@dataclass(frozen=True)
class RawEventExtraction:
    events: Tuple[RawEvent, ...]
    day_end: Optional[str] = None
    questions: Tuple[str, ...] = ()
    current_location: Optional[str] = None
    transport_mode: Optional[str] = None

    def __post_init__(self):
        ids = [item.local_event_id for item in self.events]
        if len(ids) != len(set(ids)):
            raise ValueError("local_event_id 不能重复")
        if self.day_end is not None and not _is_time(self.day_end):
            raise ValueError("day_end 必须为 HH:MM 或 null")
        if self.current_location is not None and not isinstance(self.current_location, str):
            raise ValueError("current_location 必须为字符串或 null")
        if self.transport_mode not in (None, "walk", "bike"):
            raise ValueError("transport_mode 必须为 walk、bike 或 null")


def build_raw_event_extractor_prompt(reference_datetime, user_text, campus_id=None):
    system = (
        "你是 CampusFlow 的 Raw Event Extractor。只完整抽取用户提到的事件，不安排路线或日程。\n"
        "识别 task、meal、fixed_commitment；每个事件给稳定的本地 local_event_id、出现顺序、地点、"
        "明确开始/结束时刻、用户明确时长，以及很短的原文证据。遇到‘上一小时半’这类关系，"
        "可保留为相关事件的 explicit_duration_minutes 或 evidence，但不要计算结束时刻。\n"
        "输出 JSON：{\"schema_version\":\"p4.raw-event-extraction.v1\",\"day_end\":\"HH:MM\"|null,"
        "\"current_location\":string|null,\"transport_mode\":\"walk\"|\"bike\"|null,"
        "\"events\":[{\"local_event_id\":string,\"event_type\":\"task\"|\"meal\"|\"fixed_commitment\","
        "\"title\":string,\"order_in_utterance\":int,\"starts_at\":\"HH:MM\"|null,"
        "\"ends_at\":\"HH:MM\"|null,\"explicit_duration_minutes\":int|null,"
        "\"location_text\":string|null,\"commitment_kind\":\"class\"|\"meeting\"|\"appointment\"|\"other\"|null,\"raw_evidence\":string|null}],\"questions\":[string,...]}。"
        "不规划、不猜地图、不创建用户没说的事件。"
    )
    user = "参考时间：{}\nselected campus：{}\n用户输入：\n{}\n只输出 JSON。".format(
        reference_datetime.strftime("%Y-%m-%d %H:%M"), campus_id or "未提供", user_text
    )
    return system, user


def build_semantic_linker_prompt(reference_datetime, user_text, raw):
    system = (
        "你是 CampusFlow 的 Semantic Linker。根据用户原文和 Raw Event Extractor 事件，判断事件关系，"
        "但绝不规划路线或计算结束时刻。duration_of 表示哪一个事件拥有用户表达的分钟数；"
        "commitment_relations 只连接 task/meal 与 fixed_commitment，relation 是 before 或 after；"
        "meal_period 只能 lunch、dinner、unspecified。\n"
        "例如课程‘上一小时半’应写为该课程 duration_of=90；‘下课以后背单词’是 vocab after course；"
        "‘吃完饭去上课’是 meal before course。不能把分钟数凭感觉绑定到另一件任务。\n"
        "输出 JSON：{\"schema_version\":\"p4.event-semantics.v1\","
        "\"duration_of\":[{\"event_id\":string,\"minutes\":int}],"
        "\"commitment_relations\":[{\"event_id\":string,\"commitment_event_id\":string,\"relation\":\"before\"|\"after\"}],"
        "\"meal_period_by_event\":[{\"event_id\":string,\"meal_period\":\"lunch\"|\"dinner\"|\"unspecified\"}],"
        "\"explicit_sequence\":[{\"before_event_id\":string,\"after_event_id\":string}],"
        "\"execution_profiles\":[{\"event_id\":string,\"splittable\":bool,\"minimum_chunk_minutes\":int,\"preferred_chunk_minutes\":int,\"requires_single_session\":bool,\"source\":\"qwen_semantic\"|\"user_explicit\"}],"
        "\"conflicts\":[string,...]}。只输出 JSON。"
    )
    user = "参考时间：{}\n用户原文：\n{}\n\nRaw events：\n{}".format(
        reference_datetime.strftime("%Y-%m-%d %H:%M"), user_text,
        json.dumps(raw_event_payload(raw), ensure_ascii=False, sort_keys=True),
    )
    return system, user


def parse_raw_event_extraction(text):
    payload = extract_json_object(text)
    if not isinstance(payload, dict) or payload.get("schema_version") != RAW_EVENT_SCHEMA_VERSION:
        raise AgenticParseError("raw event schema_version 不匹配")
    if set(payload) != {"schema_version", "day_end", "current_location", "transport_mode", "events", "questions"}:
        raise AgenticParseError("raw event 字段不完整或包含未知字段")
    try:
        events = tuple(_parse_raw_event(item) for item in _require_list(payload, "events"))
        questions = tuple(_require_text(item, "question") for item in _require_list(payload, "questions"))
        return RawEventExtraction(
            events,
            _optional_time(payload.get("day_end")),
            questions,
            _optional_text(payload.get("current_location")),
            _optional_transport_mode(payload.get("transport_mode")),
        )
    except ValueError as exc:
        raise AgenticParseError(str(exc))


def parse_event_semantic_graph(text, raw):
    payload = extract_json_object(text)
    if not isinstance(payload, dict) or payload.get("schema_version") != EVENT_SEMANTIC_SCHEMA_VERSION:
        raise AgenticParseError("event semantic schema_version 不匹配")
    allowed = {"schema_version", "duration_of", "commitment_relations", "meal_period_by_event", "explicit_sequence", "execution_profiles", "conflicts"}
    if set(payload) != allowed:
        raise AgenticParseError("event semantic 字段不完整或包含未知字段")
    known = {item.local_event_id: item for item in raw.events}
    try:
        durations = tuple(EventDurationLink(_field(item, "event_id"), _positive(item.get("minutes"), "minutes")) for item in _require_list(payload, "duration_of"))
        relations = tuple(EventRelation(_field(item, "event_id"), _field(item, "commitment_event_id"), _field(item, "relation")) for item in _require_list(payload, "commitment_relations"))
        periods = tuple((_field(item, "event_id"), _field(item, "meal_period")) for item in _require_list(payload, "meal_period_by_event"))
        sequence = tuple((_field(item, "before_event_id"), _field(item, "after_event_id")) for item in _require_list(payload, "explicit_sequence"))
        profiles = tuple(_parse_execution_profile(item) for item in _require_list(payload, "execution_profiles"))
        conflicts = tuple(_require_text(item, "conflict") for item in _require_list(payload, "conflicts"))
        graph = EventSemanticGraph(durations, relations, periods, sequence, conflicts, profiles)
    except ValueError as exc:
        raise AgenticParseError(str(exc))
    for link in graph.duration_of:
        if link.event_id not in known:
            raise AgenticParseError("duration 指向未知 event")
    for link in graph.commitment_relations:
        if link.event_id not in known or link.commitment_event_id not in known:
            raise AgenticParseError("relation 指向未知 event")
        if known[link.commitment_event_id].event_type != "fixed_commitment":
            raise AgenticParseError("relation target 必须是 fixed_commitment")
    for event_id, _ in graph.meal_period_by_event:
        if event_id not in known or known[event_id].event_type != "meal":
            raise AgenticParseError("meal period 必须指向 meal")
    for before_id, after_id in graph.explicit_sequence:
        if before_id not in known or after_id not in known or before_id == after_id:
            raise AgenticParseError("explicit sequence 必须连接两个不同的已知 event")
    for profile in graph.execution_profiles:
        if profile.event_id not in known or known[profile.event_id].event_type != "task":
            raise AgenticParseError("execution profile 必须指向普通 task")
    return graph


def materialize_day_intake(raw, graph):
    """Convert local model IDs into existing proposal-local indexes safely."""
    events = _semantic_event_order(raw.events, graph.explicit_sequence)
    commitments_raw = [item for item in events if item.event_type == "fixed_commitment"]
    commitment_index = {item.local_event_id: index + 1 for index, item in enumerate(commitments_raw)}
    duration_map = {item.event_id: item.minutes for item in graph.duration_of}
    before_relation_map = {
        item.event_id: item for item in graph.commitment_relations if item.relation == "before"
    }
    after_relation_map = {
        item.event_id: item for item in graph.commitment_relations if item.relation == "after"
    }
    period_map = dict(graph.meal_period_by_event)
    profile_map = {item.event_id: item for item in graph.execution_profiles}
    commitments = []
    for item in commitments_raw:
        linked_duration = duration_map.get(item.local_event_id)
        duration = linked_duration if linked_duration is not None else item.explicit_duration_minutes
        commitments.append(IntakeCommitment(
            title=item.title, starts_at=item.starts_at, ends_at=item.ends_at,
            duration_minutes=duration if item.ends_at is None and linked_duration is None else None,
            relative_end_minutes=linked_duration if item.ends_at is None else None,
            location_text=item.location_text, commitment_kind=item.commitment_kind or "other",
        ))
    tasks = []
    for item in events:
        if item.event_type == "fixed_commitment":
            continue
        before_relation = before_relation_map.get(item.local_event_id)
        after_relation = after_relation_map.get(item.local_event_id)
        before_index = commitment_index.get(before_relation.commitment_event_id) if before_relation else None
        after_index = commitment_index.get(after_relation.commitment_event_id) if after_relation else None
        activity_kind = "meal" if item.event_type == "meal" else "generic"
        duration = duration_map.get(item.local_event_id, item.explicit_duration_minutes)
        profile = profile_map.get(item.local_event_id)
        tasks.append(IntakeTask(
            title=item.title,
            total_minutes=duration,
            location_text=item.location_text,
            activity_kind=activity_kind,
            duration_source="user_explicit" if duration is not None else None,
            is_splittable=profile.splittable if profile is not None else None,
            minimum_slice_minutes=profile.minimum_chunk_minutes if profile is not None else None,
            preferred_chunk_minutes=profile.preferred_chunk_minutes if profile is not None else None,
            requires_single_session=profile.requires_single_session if profile is not None else None,
            execution_profile_source=profile.source if profile is not None else None,
            after_commitment_index=after_index,
            meal_period=period_map.get(item.local_event_id, "unspecified") if activity_kind == "meal" else None,
            meal_before_commitment_index=before_index if activity_kind == "meal" else None,
        ))
    questions = list(raw.questions)
    if graph.conflicts:
        questions.append("有一处时间关系需要确认后才能准确安排。")
    return DayIntakeProposal(
        schema_version=INTAKE_SCHEMA_VERSION,
        commitments=tuple(commitments), tasks=tuple(tasks), day_end=raw.day_end,
        questions=tuple(questions),
        current_location=raw.current_location,
        transport_mode=raw.transport_mode,
    )


def raw_event_payload(raw):
    return {
        "schema_version": RAW_EVENT_SCHEMA_VERSION,
        "day_end": raw.day_end,
        "current_location": raw.current_location,
        "transport_mode": raw.transport_mode,
        "events": [
            {
                "local_event_id": item.local_event_id, "event_type": item.event_type,
                "title": item.title, "order_in_utterance": item.order_in_utterance,
                "starts_at": item.starts_at, "ends_at": item.ends_at,
                "explicit_duration_minutes": item.explicit_duration_minutes,
                "location_text": item.location_text, "commitment_kind": item.commitment_kind,
                "raw_evidence": item.raw_evidence,
            } for item in raw.events
        ],
        "questions": list(raw.questions),
    }


def graph_payload(graph):
    return {
        "schema_version": EVENT_SEMANTIC_SCHEMA_VERSION,
        "duration_of": [{"event_id": item.event_id, "minutes": item.minutes} for item in graph.duration_of],
        "commitment_relations": [
            {"event_id": item.event_id, "commitment_event_id": item.commitment_event_id, "relation": item.relation}
            for item in graph.commitment_relations
        ],
        "meal_period_by_event": [{"event_id": key, "meal_period": value} for key, value in graph.meal_period_by_event],
        "explicit_sequence": [{"before_event_id": left, "after_event_id": right} for left, right in graph.explicit_sequence],
        "execution_profiles": [
            {"event_id": item.event_id, "splittable": item.splittable,
             "minimum_chunk_minutes": item.minimum_chunk_minutes,
             "preferred_chunk_minutes": item.preferred_chunk_minutes,
             "requires_single_session": item.requires_single_session, "source": item.source}
            for item in graph.execution_profiles
        ],
        "conflicts": list(graph.conflicts),
    }


def _parse_raw_event(item):
    if not isinstance(item, dict):
        raise ValueError("event 必须是对象")
    required = {"local_event_id", "event_type", "title", "order_in_utterance", "starts_at", "ends_at", "explicit_duration_minutes", "location_text", "commitment_kind", "raw_evidence"}
    if set(item) != required:
        raise ValueError("raw event 字段不完整或包含未知字段")
    return RawEvent(
        _field(item, "local_event_id"), _field(item, "event_type"), _field(item, "title"),
        _positive(item.get("order_in_utterance"), "order_in_utterance"), _optional_time(item.get("starts_at")),
        _optional_time(item.get("ends_at")), _optional_positive(item.get("explicit_duration_minutes")),
        _optional_text(item.get("location_text")), item.get("commitment_kind"),
        _optional_text(item.get("raw_evidence")),
    )


def _parse_execution_profile(item):
    if not isinstance(item, dict):
        raise ValueError("execution profile 必须是对象")
    required = {"event_id", "splittable", "minimum_chunk_minutes", "preferred_chunk_minutes", "requires_single_session", "source"}
    if set(item) != required:
        raise ValueError("execution profile 字段不完整或包含未知字段")
    splittable = item.get("splittable")
    single = item.get("requires_single_session")
    if not isinstance(splittable, bool) or not isinstance(single, bool):
        raise ValueError("execution profile flags 必须是 bool")
    return EventExecutionProfile(
        _field(item, "event_id"), splittable,
        _positive(item.get("minimum_chunk_minutes"), "minimum_chunk_minutes"),
        _positive(item.get("preferred_chunk_minutes"), "preferred_chunk_minutes"),
        single, _field(item, "source"),
    )


def _is_time(value):
    import re
    return isinstance(value, str) and re.match(r"^([01]?\d|2[0-3]):[0-5]\d$", value) is not None


def _optional_time(value):
    if value is None:
        return None
    if not _is_time(value):
        raise ValueError("time 无效")
    return value


def _optional_positive(value):
    if value is None:
        return None
    return _positive(value, "minutes")


def _optional_transport_mode(value):
    if value is None:
        return None
    if value not in ("walk", "bike"):
        raise ValueError("transport_mode 无效")
    return value


def _semantic_event_order(events, explicit_sequence):
    """Apply model-linked explicit order without turning local IDs into state.

    Raw utterance order remains the stable tie-breaker.  A cycle is rejected
    instead of silently picking an arbitrary interpretation.
    """
    ordered = tuple(sorted(events, key=lambda item: (item.order_in_utterance, item.local_event_id)))
    by_id = {item.local_event_id: item for item in ordered}
    outgoing = {item.local_event_id: set() for item in ordered}
    indegree = {item.local_event_id: 0 for item in ordered}
    for before_id, after_id in explicit_sequence:
        if after_id not in outgoing[before_id]:
            outgoing[before_id].add(after_id)
            indegree[after_id] += 1
    rank = {item.local_event_id: index for index, item in enumerate(ordered)}
    ready = sorted((event_id for event_id, degree in indegree.items() if degree == 0), key=rank.__getitem__)
    result = []
    while ready:
        event_id = ready.pop(0)
        result.append(by_id[event_id])
        for child in sorted(outgoing[event_id], key=rank.__getitem__):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
                ready.sort(key=rank.__getitem__)
    if len(result) != len(ordered):
        raise ValueError("explicit sequence 存在循环")
    return tuple(result)


def _positive(value, field):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("{} 必须为正整数".format(field))
    return value


def _field(item, field):
    if not isinstance(item, dict):
        raise ValueError("link 必须是对象")
    value = item.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("{} 必须非空字符串".format(field))
    return value


def _optional_text(value):
    if value is None:
        return None
    return _require_text(value, "text")


def _require_text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("{} 必须非空字符串".format(field))
    return value


def _require_list(payload, field):
    value = payload.get(field)
    if not isinstance(value, list):
        raise ValueError("{} 必须是数组".format(field))
    return value
