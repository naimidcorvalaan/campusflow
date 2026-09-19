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


class BackgroundSemanticError(AgenticParseError):
    """Invalid autonomous-process graph, eligible for one same-stage repair."""


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
    travel_instruction: Optional[str] = None

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
        if self.travel_instruction is not None and not isinstance(self.travel_instruction, str):
            raise ValueError('travel_instruction must be source text or null')


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
    task_dependencies: Tuple[Tuple[str, str], ...] = ()
    background_processes: Tuple[dict, ...] = ()
    departure_dependencies: Tuple[Tuple[str, str], ...] = ()
    task_overlaps: Tuple[Tuple[str, str], ...] = ()

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
        if self.day_end not in (None, "24:00") and not _is_time(self.day_end):
            raise ValueError("day_end 必须为 HH:MM 或 null")
        if self.current_location is not None and not isinstance(self.current_location, str):
            raise ValueError("current_location 必须为字符串或 null")
        if self.transport_mode not in (None, "walk", "bike"):
            raise ValueError("transport_mode 必须为 walk、bike 或 null")


def build_raw_event_extractor_prompt(reference_datetime, user_text, campus_id=None):
    from src.p2_day_intake import TASK_TIME_BOUNDARY_SEMANTICS, DEFAULT_DAY_END, LOCATION_ROLE_SEMANTICS, CLOCK_RANGE_SEMANTICS, EVENT_FACT_SEMANTICS
    from src.task_attention import BACKGROUND_SEMANTICS
    system = (BACKGROUND_SEMANTICS +
        "你是 CampusFlow 的 Raw Event Extractor。只完整抽取用户提到的事件，不安排路线或日程。\n"
        + LOCATION_ROLE_SEMANTICS + CLOCK_RANGE_SEMANTICS + EVENT_FACT_SEMANTICS +
        "识别 task、meal、fixed_commitment；每个事件给稳定的本地 local_event_id、出现顺序、地点、"
        "明确开始/结束时刻、用户明确时长，以及很短的原文证据。遇到‘上一小时半’这类关系，"
        "可保留为相关事件的 explicit_duration_minutes 或 evidence，但不要计算结束时刻。\n"
        "若事件包含用户明确的前往/返回要求，将对应移动原文单独保存在travel_instruction，"
        "包括限制何时出发的原文条件；不要只保留到达后的任务而丢掉移动要求。未提移动可省略。\n"
        "输出 JSON：{\"schema_version\":\"p4.raw-event-extraction.v1\",\"day_end\":\"HH:MM\"|null,"
        "\"current_location\":string|null,\"transport_mode\":\"walk\"|\"bike\"|null,"
        "\"events\":[{\"local_event_id\":string,\"event_type\":\"task\"|\"meal\"|\"fixed_commitment\","
        "\"title\":string,\"order_in_utterance\":int,\"starts_at\":\"HH:MM\"|null,"
        "\"ends_at\":\"HH:MM\"|null,\"explicit_duration_minutes\":int|null,"
        "\"location_text\":string|null,\"commitment_kind\":\"class\"|\"meeting\"|\"appointment\"|\"other\"|null,\"raw_evidence\":string|null,\"travel_instruction\":string|null}],\"questions\":[string,...]}。"
        "task/meal 的 starts_at、ends_at 保留用户明确的最早开始/最晚结束约束，"
        "不是你计算的排程；‘在A到B内完成N分钟工作’分别填 A、B、N。"
        + TASK_TIME_BOUNDARY_SEMANTICS +
        "day_end 仅提取用户明确给出的今日结束边界；未给时返回 null，由程序使用默认 "
        + DEFAULT_DAY_END + "。不得把最后一节课的结束或某项任务的截止当成日终。"
        "‘课前完成/某承诺前完成/出发前完成’保留在该任务 raw_evidence，交给后续关系分析阶段绑定；"
        "不要因为任务在课程后被提到就改成课后任务。偏好词‘最好/尽量’也保留原文，不当成硬截止。"
        "current_location 必须保留已给的位置，同地点简称与‘这里/本楼’应复用同一个地点文本。"
        "questions 默认 []；不追问已给的时间地点，不要求确认是否开始，不为可选字段追问。"
        "普通 task/meal 未给具体开始时刻不是缺必要信息：开始时间由后续规划决定；"
        "只有无法确定任务身份或固定安排的必要时间等影响可行性的信息才提问。"
        "不规划、不猜地图、不创建用户没说的事件。若用户描述启动后自主运行，分别抽取启动和运行过程；"
        "这属于同一请求的真实动作分解，不是添加新目标，未给启动分钟留空交估时。"
    )
    user = "参考时间：{}\nselected campus：{}\n用户输入：\n{}\n只输出 JSON。".format(
        reference_datetime.strftime("%Y-%m-%d %H:%M"), campus_id or "未提供", user_text
    )
    return system, user


def build_semantic_linker_prompt(reference_datetime, user_text, raw):
    from src.p2_day_intake import EVENT_FACT_SEMANTICS
    from src.task_attention import BACKGROUND_SEMANTICS
    system = (BACKGROUND_SEMANTICS +
        "你是 CampusFlow 的 Semantic Linker。根据用户原文和 Raw Event Extractor 事件，判断事件关系，"
        "但绝不规划路线或计算结束时刻。duration_of 表示哪一个事件拥有用户表达的分钟数；"
        "commitment_relations 只连接 task/meal 与 fixed_commitment，relation 是 before 或 after；"
        "meal_period 只能 lunch、dinner、unspecified。\n"
        "例如课程‘上一小时半’应写为该课程 duration_of=90；‘下课以后背单词’是 vocab after course；"
        "‘吃完饭去上课’是 meal before course。不能把分钟数凭感觉绑定到另一件任务。\n"
        "普通任务也必须保留 before：‘课前完成/上课前做完/某承诺前完成’写 task before 对应承诺，"
        "含义是全部剩余工作结束不晚于该承诺开始；实际移动由程序预留。"
        "‘出发前完成’绑定明确的出发承诺，或所前往的固定安排；不虚构出发事件。"
        "只有明确要求才写硬before/after，‘最好/尽量/希望’是偏好，不能写成硬关系。"
        "按指代绑定正确承诺，不按叙述位置或最近时刻猜测；有歧义在conflicts说明。"
        "conflicts 只记录用户已给硬约束之间的真实矛盾。普通任务没有具体开始时刻、"
        "未指定偏好、尚未开始或可以有多个可行排法都不是冲突，不能因此要求补充。"
        "关系只作用于有明确指代的事件：一个任务的课后要求不能传播到相邻的独立任务；"
        "今天必须做完不等于必须在课程前或课程后。事件提及顺序本身不是执行顺序。"
        "execution_profiles 可以为空数组；不知道执行形态时不要生成该事件的 profile。"
        "explicit_sequence 表达用户本轮选择的执行顺序，可由后续明确反馈改变；"
        "task_dependencies表达不可违反的前置边界，包括动作所需的产物以及用户明确要求某动作完成后才出发或开始；可协商偏好和单纯提及顺序不构成这种依赖。"
        "每条task_dependencies的target_boundary说明前项完成限制哪个时刻：action_start表示后项工作开始；"
        "departure表示前往/返回后项地点的出发时刻。用户说前项结束后再前往或返回时用departure，"
        "不只是action_start；程序自动把出发条件也保留为开始条件。普通结果依赖用action_start。"
        "Raw Event中的travel_instruction保留用户移动原文；据此区分移动何时发生与到达后的工作何时开始，"
        "不要把返回原文只当作静态location_text。"
        "task_overlaps表达前台动作在自主后台过程运行期间开始，独立于完成依赖；期间执行不能填成等待该过程完成。"
        "background_processes只用本阶段event_id和launch_event_id连接运行与启动，reason记录无需持续注意的依据，"
        "already_running仅为实际已启动报告；不要使用后续阶段的task索引。"
        "若生成 profile，minimum_chunk_minutes 和 preferred_chunk_minutes 必须是正整数，"
        "不得为 null，preferred 不得小于 minimum；requires_single_session=true 时 splittable=false。"
        "输出 JSON：{\"schema_version\":\"p4.event-semantics.v1\","
        "\"duration_of\":[{\"event_id\":string,\"minutes\":int}],"
        "\"commitment_relations\":[{\"event_id\":string,\"commitment_event_id\":string,\"relation\":\"before\"|\"after\"}],"
        "\"meal_period_by_event\":[{\"event_id\":string,\"meal_period\":\"lunch\"|\"dinner\"|\"unspecified\"}],"
        "\"explicit_sequence\":[{\"before_event_id\":string,\"after_event_id\":string}],"
        "\"task_dependencies\":[{\"before_event_id\":string,\"after_event_id\":string,\"target_boundary\":\"action_start\"|\"departure\"}],"
        "\"task_overlaps\":[{\"background_event_id\":string,\"active_event_id\":string}],"
        "\"background_processes\":[{\"event_id\":string,\"launch_event_id\":string|null,\"reason\":string,\"already_running\":bool}],"
        "\"execution_profiles\":[{\"event_id\":string,\"splittable\":bool,\"minimum_chunk_minutes\":int,\"preferred_chunk_minutes\":int,\"requires_single_session\":bool,\"source\":\"qwen_semantic\"|\"user_explicit\"}],"
        "\"conflicts\":[string,...]}。只输出 JSON。"
        + EVENT_FACT_SEMANTICS +
        "对比：‘有一节课，另有任务要完成’不建立两者的时间关系；"
        "‘任务必须在那节课开始前完成’才建立 before。无法引用用户原文中的关系要求就不建立关系。"
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
            "24:00" if payload.get("day_end") == "24:00" else _optional_time(payload.get("day_end")),
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
    if not allowed.issubset(payload) or set(payload) - allowed - {"task_dependencies", "background_processes", "departure_dependencies", "task_overlaps"}:
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
        from dataclasses import replace
        if 'background_processes' in payload:
            background = _require_list(payload, 'background_processes')
            seen = set()
            for item in background:
                if not isinstance(item, dict) or set(item) != {'event_id', 'launch_event_id', 'reason', 'already_running'}:
                    raise BackgroundSemanticError('invalid background process fields')
                ref, launch = item['event_id'], item['launch_event_id']
                if ref in seen or ref not in known or known[ref].event_type != 'task':
                    raise BackgroundSemanticError('background process must reference a unique task')
                if launch is not None and (launch not in known or launch == ref or known[launch].event_type != 'task'):
                    raise BackgroundSemanticError(
                        'invalid background launch event: process={} launch={}; '
                        'a start action and its autonomous elapsed interval must be distinct raw events; '
                        'preserve the runtime duration on the process, not the start action'.format(ref, launch))
                _require_text(item['reason'], 'background reason')
                if not isinstance(item['already_running'], bool) or (launch is None and not item['already_running']):
                    raise BackgroundSemanticError('background process requires launch or actual running report')
                seen.add(ref)
            graph = replace(graph, background_processes=tuple(background))
        if "task_dependencies" in payload:
            from dataclasses import replace
            dependencies, departures = [], []
            for item in _require_list(payload, 'task_dependencies'):
                if set(item) - {'before_event_id','after_event_id','target_boundary'}:
                    raise ValueError('unknown dependency field')
                boundary = item.get('target_boundary', 'action_start')
                if boundary not in ('action_start', 'departure'):
                    raise ValueError('invalid dependency target boundary')
                pair = (_field(item, 'before_event_id'), _field(item, 'after_event_id'))
                dependencies.append(pair)
                if boundary == 'departure':
                    departures.append(pair)
            graph = replace(graph, task_dependencies=tuple(dependencies), departure_dependencies=tuple(departures))
        if 'departure_dependencies' in payload:
            graph = replace(graph, departure_dependencies=tuple(dict.fromkeys(graph.departure_dependencies + tuple(
                (_field(item, 'before_event_id'), _field(item, 'after_event_id'))
                for item in _require_list(payload, 'departure_dependencies')))))
        if 'task_overlaps' in payload:
            graph = replace(graph, task_overlaps=tuple(
                (_field(item, 'background_event_id'), _field(item, 'active_event_id'))
                for item in _require_list(payload, 'task_overlaps')))
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
        if known[link.event_id].event_type not in ("task", "meal"):
            raise AgenticParseError("relation source 必须是 task 或 meal")
    for event_id, _ in graph.meal_period_by_event:
        if event_id not in known or known[event_id].event_type != "meal":
            raise AgenticParseError("meal period 必须指向 meal")
    for before_id, after_id in graph.explicit_sequence:
        if before_id not in known or after_id not in known or before_id == after_id:
            raise AgenticParseError("explicit sequence 必须连接两个不同的已知 event")
    for profile in graph.execution_profiles:
        if profile.event_id not in known or known[profile.event_id].event_type != "task":
            raise AgenticParseError("execution profile 必须指向普通 task")
    for before_id, after_id in graph.task_dependencies + graph.departure_dependencies + graph.task_overlaps:
        if (before_id not in known or after_id not in known or before_id == after_id
                or known[before_id].event_type == "fixed_commitment"
                or known[after_id].event_type == "fixed_commitment"):
            raise AgenticParseError("task dependency 必须连接两个不同的普通任务")
    return graph


def materialize_day_intake(raw, graph):
    """Convert local model IDs into existing proposal-local indexes safely."""
    events = _semantic_event_order(raw.events, graph.explicit_sequence + graph.task_dependencies + graph.departure_dependencies + graph.task_overlaps)
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
    background_map = {item['event_id']: item for item in graph.background_processes}
    overlap_map = {right: left for left,right in graph.task_overlaps}
    if len(overlap_map) != len(graph.task_overlaps):
        raise AgenticParseError('one active task can name only one overlap target')
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
    task_indexes = {item.local_event_id: index + 1 for index, item in enumerate(
        event for event in events if event.event_type != "fixed_commitment")}
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
        background = background_map.get(item.local_event_id)
        tasks.append(IntakeTask(
            title=item.title,
            total_minutes=duration,
            location_text=item.location_text,
            earliest_start_time=item.starts_at,
            latest_end_time=item.ends_at,
            activity_kind=activity_kind,
            duration_source="user_explicit" if duration is not None else None,
            is_splittable=False if background else (profile.splittable if profile is not None else None),
            minimum_slice_minutes=profile.minimum_chunk_minutes if profile is not None else None,
            preferred_chunk_minutes=profile.preferred_chunk_minutes if profile is not None else None,
            requires_single_session=profile.requires_single_session if profile is not None else None,
            execution_profile_source=profile.source if profile is not None else None,
            after_commitment_index=after_index,
            before_commitment_index=before_index if activity_kind != "meal" else None,
            meal_period=period_map.get(item.local_event_id, "unspecified") if activity_kind == "meal" else None,
            meal_before_commitment_index=before_index if activity_kind == "meal" else None,
            predecessor_task_indexes=tuple(task_indexes[left] for left, right in graph.task_dependencies
                if right == item.local_event_id and left in task_indexes),
            departure_after_task_indexes=tuple(task_indexes[left] for left, right in graph.departure_dependencies
                if right == item.local_event_id and left in task_indexes),
            overlap_task_index=task_indexes.get(overlap_map.get(item.local_event_id)),
            attention_mode='background' if background else 'active',
            launch_task_index=task_indexes.get(background['launch_event_id']) if background else None,
            background_reason=background['reason'] if background else None,
            user_reported_running=background['already_running'] if background else False,
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
                **({'travel_instruction': item.travel_instruction} if item.travel_instruction is not None else {}),
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
        "task_dependencies": [{"before_event_id": left, "after_event_id": right} for left, right in graph.task_dependencies],
        "background_processes": list(graph.background_processes),
        "task_overlaps": [{"background_event_id": left, "active_event_id": right} for left,right in graph.task_overlaps],
        "departure_dependencies": [{"before_event_id": left, "after_event_id": right}
            for left, right in graph.departure_dependencies],
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
    if not required.issubset(item) or set(item) - required - {'travel_instruction'}:
        raise ValueError("raw event 字段不完整或包含未知字段")
    return RawEvent(
        _field(item, "local_event_id"), _field(item, "event_type"), _field(item, "title"),
        _positive(item.get("order_in_utterance"), "order_in_utterance"), _optional_time(item.get("starts_at")),
        _optional_time(item.get("ends_at")), _optional_positive(item.get("explicit_duration_minutes")),
        _optional_text(item.get("location_text")), item.get("commitment_kind"),
        _optional_text(item.get("raw_evidence")),
        _optional_text(item.get('travel_instruction')),
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
