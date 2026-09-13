"""P2d 第二阶段：首次全天计划自然语言抽取（Day Intake）（Python 3.8 兼容）。

输入：程序提供的 reference_datetime + 用户第一次描述“今天安排 + 任务”的自然语言。
流程：Day Intake Agent（可注入 callable，最多 1 次格式 repair）
      -> 程序解析（schema/时间/数值严格校验）
      -> 程序构造 DayPlanningState（ref 由程序生成，时间基于 reference_datetime 解析）
      -> 交由 P2c / P2SessionController 继续规划。

程序硬边界：
- 模型不能制造 completed / remaining / window capacity / task_ref / commitment_ref；
- 相对时间（starts_in_minutes）基于 reference_datetime，模型不自行猜测“现在”；
- 用户明确时长 -> SourceKind.AI_EXTRACTED_FROM_USER_TEXT；未知时长 -> None（由 P2c 暂估）；
- 时间不合法 / 缺失结束时间 -> warning + question，不构造非法 commitment。
"""

import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Callable, Mapping, Optional, Sequence, Tuple

from src.p1_models import SourceKind
from src.p1_window_models import AvailabilityLevel, FixedCommitment
from src.p2_agentic_parser import AgenticParseError, extract_json_object
from src.p2_agentic_prompt_builder import build_repair_prompt
from src.p2_models import DayPlanningState, TaskProgress, TaskState
from src.p2_window_derivation import DEFAULT_SAFETY_BUFFER_MINUTES, derive_day_state
from src.p3_map_schema import CampusMapData
from src.p3_route_planner import run_spatial_intake

INTAKE_SCHEMA_VERSION = "p2.day-intake.v1"
DEFAULT_DAY_END = "22:00"
MAX_TITLE_LENGTH = 100
MAX_QUESTION_LENGTH = 200
MAX_LOCATION_LENGTH = 200
COMMITMENT_REF_PREFIX = "day_commitment_"
TASK_REF_PREFIX = "day_task_"
WARNING_INTAKE_FAILED = "首次全天信息暂未结构化应用，请补充更明确的时间后重试。"

_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?$")

AgentCaller = Callable[[str, str], str]


@dataclass(frozen=True)
class IntakeCommitment:
    """Agent 提议的固定安排（时间表达为绝对 HH:MM 或相对参考时间的分钟数）。"""

    title: str
    starts_at: Optional[str] = None
    ends_at: Optional[str] = None
    starts_in_minutes: Optional[int] = None
    duration_minutes: Optional[int] = None
    # Relative to this commitment's resolved start.  The model binds the
    # phrase to the event; deterministic code performs the datetime addition.
    relative_end_minutes: Optional[int] = None
    location_text: Optional[str] = None
    commitment_kind: Optional[str] = None
    class_arrival_lead_minutes: Optional[int] = None

    def __post_init__(self):
        _require_text("title", self.title)
        if self.starts_at is not None and self.starts_in_minutes is not None:
            raise ValueError("starts_at 与 starts_in_minutes 不能同时出现")
        if self.starts_at is None and self.starts_in_minutes is None:
            raise ValueError("commitment 必须提供 starts_at 或 starts_in_minutes")
        if self.starts_at is not None:
            _require_time("starts_at", self.starts_at)
        if self.ends_at is not None:
            _require_time("ends_at", self.ends_at)
        end_forms = sum(
            value is not None
            for value in (self.ends_at, self.duration_minutes, self.relative_end_minutes)
        )
        if end_forms > 1:
            raise ValueError("ends_at、duration_minutes、relative_end_minutes 只能出现一个")
        if self.duration_minutes is not None:
            _require_positive_int("duration_minutes", self.duration_minutes)
        if self.relative_end_minutes is not None:
            _require_positive_int("relative_end_minutes", self.relative_end_minutes)
        if self.starts_in_minutes is not None:
            _require_positive_int("starts_in_minutes", self.starts_in_minutes)
        if self.location_text is not None:
            _require_text("location_text", self.location_text)
        if self.commitment_kind is not None and self.commitment_kind not in ("class", "meeting", "appointment", "other"):
            raise ValueError("commitment_kind 无效")
        if self.class_arrival_lead_minutes is not None:
            _require_positive_int("class_arrival_lead_minutes", self.class_arrival_lead_minutes)


@dataclass(frozen=True)
class IntakeTask:
    """Agent 提议的任务（时长未知时 total_minutes 为 None，交由 P2c 暂估）。"""

    title: str
    total_minutes: Optional[int] = None
    is_splittable: Optional[bool] = None
    minimum_slice_minutes: Optional[int] = None
    # P4d-2 execution semantics.  TaskProgress keeps only the durable
    # splittable/minimum facts; these optional planning preferences are
    # transferred to the task_ref-bound execution context after apply.
    preferred_chunk_minutes: Optional[int] = None
    requires_single_session: Optional[bool] = None
    execution_profile_source: Optional[str] = None
    location_text: Optional[str] = None
    activity_kind: Optional[str] = None
    duration_source: Optional[str] = None
    # One-based index into this proposal's commitments.  It is intentionally
    # proposal-local: apply_day_intake converts it to the stable commitment
    # ref before planning, so task titles never become dependency keys.
    after_commitment_index: Optional[int] = None
    # Meal-only temporal semantics remain proposal-local for the same reason
    # as ``after_commitment_index``.  The enrichment boundary converts them
    # to stable commitment refs after apply_day_intake has made those refs.
    meal_period: Optional[str] = None
    meal_before_commitment_index: Optional[int] = None
    meal_time: Optional[str] = None

    def __post_init__(self):
        _require_text("title", self.title)
        if self.total_minutes is not None:
            _require_positive_int("total_minutes", self.total_minutes)
        if self.is_splittable is not None and not isinstance(self.is_splittable, bool):
            raise ValueError("is_splittable 必须是 bool 或 null")
        if self.minimum_slice_minutes is not None:
            _require_positive_int("minimum_slice_minutes", self.minimum_slice_minutes)
        if self.preferred_chunk_minutes is not None:
            _require_positive_int("preferred_chunk_minutes", self.preferred_chunk_minutes)
        if (
            self.minimum_slice_minutes is not None
            and self.preferred_chunk_minutes is not None
            and self.preferred_chunk_minutes < self.minimum_slice_minutes
        ):
            raise ValueError("preferred_chunk_minutes 不得小于 minimum_slice_minutes")
        if self.requires_single_session is not None and not isinstance(self.requires_single_session, bool):
            raise ValueError("requires_single_session 必须是 bool 或 null")
        if self.requires_single_session and self.is_splittable is True:
            raise ValueError("single-session task 不能同时为 splittable")
        if self.execution_profile_source not in (None, "qwen_semantic", "user_explicit"):
            raise ValueError("execution_profile_source 无效")
        if self.location_text is not None:
            _require_text("location_text", self.location_text)
        if self.activity_kind is not None and self.activity_kind not in ("generic", "meal"):
            raise ValueError("activity_kind 必须是 generic、meal 或 null")
        if self.duration_source is not None and self.duration_source not in (
            "user_explicit", "semantic_estimate", "ai_estimated",
        ):
            raise ValueError("duration_source 无效")
        if self.duration_source is not None and self.total_minutes is None:
            raise ValueError("duration_source 需要 total_minutes")
        if self.after_commitment_index is not None:
            _require_positive_int("after_commitment_index", self.after_commitment_index)
        if self.meal_period is not None and self.meal_period not in ("lunch", "dinner", "unspecified"):
            raise ValueError("meal_period 必须是 lunch、dinner、unspecified 或 null")
        if self.meal_before_commitment_index is not None:
            _require_positive_int("meal_before_commitment_index", self.meal_before_commitment_index)
        if self.meal_time is not None:
            _require_time("meal_time", self.meal_time)
        if self.activity_kind != "meal" and any(
            value is not None
            for value in (self.meal_period, self.meal_before_commitment_index, self.meal_time)
        ):
            raise ValueError("meal temporal fields 只能用于 meal")
        if (
            self.meal_before_commitment_index is not None
            and self.after_commitment_index is not None
            and self.meal_before_commitment_index == self.after_commitment_index
        ):
            raise ValueError("meal 不能同时位于同一固定安排之前和之后")


@dataclass(frozen=True)
class DayIntakeProposal:
    """Day Intake Agent 的解析后结构化提案（时间仍为程序可解析的表达）。

    current_location / transport_mode 为 P3 空间理解字段（可空）：
    - current_location：用户“我现在在X/我从X出发”的原话；
    - transport_mode：用户明确的交通偏好 walk/bike，未提为 None。
    """

    schema_version: str
    commitments: Tuple[IntakeCommitment, ...]
    tasks: Tuple[IntakeTask, ...]
    day_end: Optional[str]
    questions: Tuple[str, ...]
    current_location: Optional[str] = None
    transport_mode: Optional[str] = None

    def __post_init__(self):
        if self.schema_version != INTAKE_SCHEMA_VERSION:
            raise ValueError("schema_version 不匹配")
        for commitment in self.commitments:
            if not isinstance(commitment, IntakeCommitment):
                raise TypeError("commitments 必须包含 IntakeCommitment")
        for task in self.tasks:
            if not isinstance(task, IntakeTask):
                raise TypeError("tasks 必须包含 IntakeTask")
            for index, field_name in (
                (task.after_commitment_index, "after_commitment_index"),
                (task.meal_before_commitment_index, "meal_before_commitment_index"),
            ):
                if index is not None and index > len(self.commitments):
                    raise ValueError("{} 超出 commitments 范围".format(field_name))
        if self.day_end is not None:
            _require_time("day_end", self.day_end)
        for question in self.questions:
            _require_text("question", question)
        if self.current_location is not None:
            if not isinstance(self.current_location, str) or not self.current_location.strip():
                raise ValueError("current_location 必须是非空字符串或 null")
            if len(self.current_location) > MAX_LOCATION_LENGTH:
                raise ValueError("current_location 过长")
        if self.transport_mode is not None:
            _require_transport_mode("transport_mode", self.transport_mode)


@dataclass(frozen=True)
class IntakeApplied:
    """程序构造完成的结果；state 已按 P2a 派生全天窗口。"""

    state: DayPlanningState
    warnings: Tuple[str, ...]
    questions: Tuple[str, ...]
    new_commitment_refs: Tuple[str, ...]
    new_task_refs: Tuple[str, ...]


@dataclass(frozen=True)
class DayIntakeOutcome:
    """一次 Day Intake 轮次的结果；applied 为 None 表示最终失败（不污染状态）。

    movement_blocks / current_location_text / movement_questions / movement_warnings
    为 P3 空间增强产物（map_data 提供时填充；不提供时保持空，原 P2 行为）。
    """

    applied: Optional[IntakeApplied]
    warnings: Tuple[str, ...]
    questions: Tuple[str, ...]
    call_count: int
    repair_used: bool
    movement_blocks: Tuple[object, ...] = ()
    movement_requests: Tuple[object, ...] = ()
    current_location_text: Optional[str] = None
    movement_questions: Tuple[str, ...] = ()
    movement_warnings: Tuple[str, ...] = ()
    # The parsed proposal is retained for the live execution-enrichment
    # boundary.  It is optional so failed/legacy outcomes remain compatible.
    proposal: Optional[DayIntakeProposal] = None
    semantic_audit_used: bool = False
    semantic_repair_used: bool = False


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------


def parse_day_intake(text: str) -> DayIntakeProposal:
    """解析 Day Intake Agent 输出（复用 P2c 的 JSON 提取器）。"""
    payload = extract_json_object(text)
    if not isinstance(payload, dict):
        raise AgenticParseError("输出必须是 JSON 对象")
    if payload.get("schema_version") != INTAKE_SCHEMA_VERSION:
        raise AgenticParseError("schema_version 不匹配")
    commitments = _require_list(payload, "commitments")
    tasks = _require_list(payload, "tasks")
    questions = _require_list(payload, "questions")
    day_end = _optional_time(payload.get("day_end"), "day_end")
    current_location = _optional_text(payload.get("current_location"), "current_location", MAX_LOCATION_LENGTH)
    transport_mode = _optional_transport_mode(payload.get("transport_mode"))
    try:
        proposal = DayIntakeProposal(
            schema_version=INTAKE_SCHEMA_VERSION,
            commitments=tuple(_parse_commitment(item) for item in commitments),
            tasks=tuple(_parse_task(item) for item in tasks),
            day_end=day_end,
            questions=tuple(_parse_question(item) for item in questions),
            current_location=current_location,
            transport_mode=transport_mode,
        )
    except ValueError as exc:
        raise AgenticParseError(str(exc))
    return proposal


def _parse_commitment(item) -> IntakeCommitment:
    if not isinstance(item, dict):
        raise AgenticParseError("commitments 中的每一项必须是对象")
    try:
        return IntakeCommitment(
            title=_field_text(item.get("title"), "title"),
            starts_at=_optional_time(item.get("starts_at"), "starts_at"),
            ends_at=_optional_time(item.get("ends_at"), "ends_at"),
            starts_in_minutes=_optional_positive_int(item.get("starts_in_minutes"), "starts_in_minutes"),
            duration_minutes=_optional_positive_int(item.get("duration_minutes"), "duration_minutes"),
            relative_end_minutes=_optional_positive_int(
                item.get("relative_end_minutes"), "relative_end_minutes"
            ),
            location_text=_optional_text(item.get("location_text"), "location_text", MAX_LOCATION_LENGTH),
            commitment_kind=item.get("commitment_kind"),
            class_arrival_lead_minutes=item.get("class_arrival_lead_minutes"),
        )
    except ValueError as exc:
        raise AgenticParseError(str(exc))


def _parse_task(item) -> IntakeTask:
    if not isinstance(item, dict):
        raise AgenticParseError("tasks 中的每一项必须是对象")
    try:
        return IntakeTask(
            title=_field_text(item.get("title"), "title"),
            total_minutes=_optional_positive_int(item.get("total_minutes"), "total_minutes"),
            is_splittable=_optional_bool(item.get("is_splittable"), "is_splittable"),
            minimum_slice_minutes=_optional_positive_int(
                item.get("minimum_slice_minutes"), "minimum_slice_minutes"
            ),
            preferred_chunk_minutes=_optional_positive_int(
                item.get("preferred_chunk_minutes"), "preferred_chunk_minutes"
            ),
            requires_single_session=_optional_bool(
                item.get("requires_single_session"), "requires_single_session"
            ),
            execution_profile_source=_optional_execution_profile_source(
                item.get("execution_profile_source")
            ),
            location_text=_optional_text(item.get("location_text"), "location_text", MAX_LOCATION_LENGTH),
            activity_kind=item.get("activity_kind"),
            duration_source=item.get("duration_source"),
            after_commitment_index=_optional_positive_int(
                item.get("after_commitment_index"), "after_commitment_index"
            ),
            meal_period=item.get("meal_period"),
            meal_before_commitment_index=_optional_positive_int(
                item.get("meal_before_commitment_index"), "meal_before_commitment_index"
            ),
            meal_time=_optional_time(item.get("meal_time"), "meal_time"),
        )
    except ValueError as exc:
        raise AgenticParseError(str(exc))


# ---------------------------------------------------------------------------
# 程序构造 DayPlanningState
# ---------------------------------------------------------------------------


def apply_day_intake(
    reference_datetime: datetime,
    proposal: DayIntakeProposal,
    default_day_end: str = DEFAULT_DAY_END,
    default_safety_buffer_minutes: int = DEFAULT_SAFETY_BUFFER_MINUTES,
    travel_minutes_by_commitment: Optional[Mapping[str, int]] = None,
    history: Tuple[str, ...] = (),
    user_text: Optional[str] = None,
) -> IntakeApplied:
    """把解析后的提案转成 DayPlanningState；时间基于 reference_datetime 解析。"""
    if not isinstance(reference_datetime, datetime):
        raise TypeError("reference_datetime must be a datetime")
    if not isinstance(proposal, DayIntakeProposal):
        raise TypeError("proposal must be a DayIntakeProposal")

    warnings = []
    questions = list(proposal.questions)
    commitments = []
    new_commitment_refs = []
    for index, item in enumerate(proposal.commitments):
        ref = _format_ref(COMMITMENT_REF_PREFIX, index + 1)
        starts = _resolve_start(item, reference_datetime)
        ends = _resolve_end(item, starts)
        # A class end may be unknown.  Do not turn a model's convenient
        # one-hour guess into a hard user fact: without matching end-time or
        # duration wording in the original input, preserve the known start
        # and explicitly ask for the end later.
        if (
            ends is not None
            and item.commitment_kind == "class"
            and user_text is not None
            and not _class_end_is_explicitly_supported(item, user_text)
        ):
            ends = None
        if starts is None:
            warnings.append("首次输入中“{}”的开始时间无法确定，已跳过".format(item.title))
            questions.append("请补充“{}”的开始时间。".format(item.title))
            continue
        if ends is not None and not (starts < ends):
            warnings.append("首次输入中“{}”的时间不合法（开始不早于结束），已跳过".format(item.title))
            questions.append("请重新确认“{}”的时间。".format(item.title))
            continue
        if ends is None:
            warnings.append("首次输入中“{}”的结束时间未知，已保留开始时间与地点，结束时间待确认".format(item.title))
            questions.append("“{}”大约几点结束？".format(item.title))
        commitments.append(
            FixedCommitment(
                commitment_ref=ref,
                title=item.title,
                original_text=item.title,
                starts_at=starts,
                ends_at=ends,
                location_text=item.location_text,
                availability_during=AvailabilityLevel.UNAVAILABLE,
                field_evidence={},
                needs_confirmation=(),
                commitment_kind=item.commitment_kind,
                class_arrival_lead_minutes=item.class_arrival_lead_minutes,
            )
        )
        new_commitment_refs.append(ref)

    tasks = []
    new_task_refs = []
    for index, item in enumerate(proposal.tasks):
        ref = _format_ref(TASK_REF_PREFIX, index + 1)
        source = (None if item.total_minutes is None else
                  SourceKind.AI_ESTIMATED if item.duration_source == "ai_estimated" else
                  SourceKind.AI_EXTRACTED_FROM_USER_TEXT)
        tasks.append(
            TaskProgress(
                task_ref=ref,
                title=item.title,
                total_minutes=item.total_minutes,
                completed_minutes=0,
                total_source=source,
                state=TaskState.ACTIVE,
                is_splittable=item.is_splittable,
                minimum_slice_minutes=item.minimum_slice_minutes,
            )
        )
        new_task_refs.append(ref)

    day_end = _resolve_day_end(proposal.day_end, default_day_end, reference_datetime, warnings)
    if day_end is None or not (reference_datetime < day_end):
        raise ValueError("day_end 无法解析为晚于参考时间的有效时间")

    state = derive_day_state(
        now=reference_datetime,
        day_end=day_end,
        commitments=tuple(commitments),
        tasks=tuple(tasks),
        default_safety_buffer_minutes=default_safety_buffer_minutes,
        travel_minutes_by_commitment=travel_minutes_by_commitment or {},
        history=tuple(history),
        reference_datetime=reference_datetime,
    )
    return IntakeApplied(
        state=state,
        warnings=tuple(warnings),
        questions=tuple(questions),
        new_commitment_refs=tuple(new_commitment_refs),
        new_task_refs=tuple(new_task_refs),
    )


# ---------------------------------------------------------------------------
# 编排：build prompt -> call -> (最多 1 次 repair) -> apply
# ---------------------------------------------------------------------------


def run_day_intake(
    reference_datetime: datetime,
    user_text: str,
    caller: AgentCaller,
    repair_caller: Optional[AgentCaller] = None,
    default_day_end: str = DEFAULT_DAY_END,
    default_safety_buffer_minutes: int = DEFAULT_SAFETY_BUFFER_MINUTES,
    travel_minutes_by_commitment: Optional[Mapping[str, int]] = None,
    map_data: Optional[CampusMapData] = None,
    defer_spatial_intake: bool = False,
    semantic_auditor_caller: Optional[AgentCaller] = None,
    raw_event_caller: Optional[AgentCaller] = None,
    semantic_linker_caller: Optional[AgentCaller] = None,
    campus_id: Optional[str] = None,
) -> DayIntakeOutcome:
    """执行一轮 Day Intake；格式失败时最多一次 repair，最终失败返回 applied=None。

    map_data 提供且首次输入包含地点信息（current_location 或带 location 的固定安排）时，
    接入 P3 空间理解：真实路线移动进入首版计划；地点未知只产生人话待确认，不阻断 intake。
    """
    if not isinstance(reference_datetime, datetime):
        raise TypeError("reference_datetime must be a datetime")
    if not isinstance(user_text, str) or not user_text.strip():
        raise ValueError("user_text must be a non-empty string")
    if not callable(caller):
        raise TypeError("caller must be callable")
    if map_data is not None and not isinstance(map_data, CampusMapData):
        raise TypeError("map_data must be a CampusMapData or None")
    repair_caller = repair_caller if repair_caller is not None else caller

    raw_events = None
    semantic_graph = None
    semantic_calls = 0
    proposal = None
    repair_used = False
    if raw_event_caller is not None and semantic_linker_caller is not None:
        from src.p4_event_semantics import (
            build_raw_event_extractor_prompt,
            build_semantic_linker_prompt,
            materialize_day_intake,
            parse_event_semantic_graph,
            parse_raw_event_extraction,
        )
        raw_system, raw_user = build_raw_event_extractor_prompt(
            reference_datetime, user_text, campus_id=campus_id or (map_data.campus_id if map_data is not None else None)
        )
        semantic_calls += 1
        try:
            raw_events = parse_raw_event_extraction(raw_event_caller(raw_system, raw_user))
            linker_system, linker_user = build_semantic_linker_prompt(reference_datetime, user_text, raw_events)
            semantic_calls += 1
            semantic_graph = parse_event_semantic_graph(semantic_linker_caller(linker_system, linker_user), raw_events)
            proposal = materialize_day_intake(raw_events, semantic_graph)
        except (AgenticParseError, TypeError, ValueError):
            # The raw/linker pass is advisory semantic understanding.  A bad
            # model response never gets deterministically guessed into task
            # facts; the existing strict Day Intake path remains a safe
            # fallback and the auditor can still inspect its explicit facts.
            raw_events = None
            semantic_graph = None

    if proposal is None:
        system, user = build_day_intake_prompt(reference_datetime, user_text, default_day_end)
        text = caller(system, user)
        semantic_calls += 1
        try:
            proposal = parse_day_intake(text)
            repair_used = False
        except AgenticParseError:
            repair_system, repair_user = build_repair_prompt("day_intake", text, INTAKE_SCHEMA_VERSION)
            repair_user += (
                "\nDay Intake 的每个 task 可选字段为 activity_kind（generic|meal|null）和 "
                "location_text（string|null）、duration_source（user_explicit|semantic_estimate|ai_estimated|null）。"
                "commitment 的 ends_at、duration_minutes、relative_end_minutes 三者最多保留一个；"
                "‘开始后多久结束/下课’使用 relative_end_minutes。"
                "只保留原内容已经表达的语义；缺失时输出 null。"
            )
            repaired = repair_caller(repair_system, repair_user)
            semantic_calls += 1
            try:
                proposal = parse_day_intake(repaired)
                repair_used = True
            except AgenticParseError:
                return DayIntakeOutcome(
                    applied=None,
                    warnings=(WARNING_INTAKE_FAILED,),
                    questions=(),
                    call_count=semantic_calls,
                    repair_used=True,
                )

    semantic_audit_used = semantic_auditor_caller is not None
    semantic_repair_used = False
    if semantic_auditor_caller is not None:
        from src.p4_intake_auditor import audit_initial_intake
        audit = audit_initial_intake(
            reference_datetime,
            user_text,
            proposal,
            semantic_auditor_caller,
            campus_id=campus_id or (map_data.campus_id if map_data is not None else None),
            raw_events=raw_events,
            semantic_graph=semantic_graph,
        )
        proposal = audit.proposal
        semantic_repair_used = audit.repaired

    applied = apply_day_intake(
        reference_datetime,
        proposal,
        default_day_end=default_day_end,
        default_safety_buffer_minutes=default_safety_buffer_minutes,
        travel_minutes_by_commitment=travel_minutes_by_commitment,
        user_text=user_text,
    )
    total_calls = semantic_calls + (1 if semantic_audit_used else 0)
    movement_blocks = ()
    movement_requests = ()
    current_location_text = None
    movement_questions = ()
    movement_warnings = ()
    if not isinstance(defer_spatial_intake, bool):
        raise TypeError("defer_spatial_intake must be a bool")
    if map_data is not None and not defer_spatial_intake and _proposal_has_spatial_info(proposal):
        spatial = run_spatial_intake(
            applied.state,
            proposal.current_location,
            proposal.transport_mode,
            map_data,
            caller,
            repair_caller,
        )
        applied = replace(applied, state=spatial.state)
        movement_blocks = tuple(spatial.blocks)
        movement_requests = tuple(spatial.requests)
        current_location_text = spatial.current_location_text
        movement_questions = tuple(spatial.questions)
        movement_warnings = tuple(spatial.warnings)
        total_calls += spatial.call_count
    return DayIntakeOutcome(
        applied=applied,
        warnings=applied.warnings,
        questions=applied.questions,
        call_count=total_calls,
        repair_used=repair_used,
        movement_blocks=movement_blocks,
        movement_requests=movement_requests,
        current_location_text=current_location_text,
        movement_questions=movement_questions,
        movement_warnings=movement_warnings,
        proposal=proposal,
        semantic_audit_used=semantic_audit_used,
        semantic_repair_used=semantic_repair_used,
    )


def _proposal_has_spatial_info(proposal: DayIntakeProposal) -> bool:
    """首次输入是否包含可参与 P3 空间理解的地点信息。"""
    if proposal.current_location:
        return True
    return any(commitment.location_text for commitment in proposal.commitments)


# ---------------------------------------------------------------------------
# prompt
# ---------------------------------------------------------------------------


def build_day_intake_prompt(
    reference_datetime: datetime, user_text: str, default_day_end: str = DEFAULT_DAY_END
) -> Tuple[str, str]:
    """构建 Day Intake prompt：参考时间由程序提供，模型只表达相对/绝对时间意图。"""
    system = _intake_system_prompt(default_day_end)
    user = (
        "参考时间（程序提供，不要自行猜测“现在”）：{reference}\n"
        "默认 day_end：{day_end}\n\n"
        "用户首次输入：\n{user_text}\n\n"
        "只输出一个符合 schema 的 JSON 对象。"
    ).format(
        reference=reference_datetime.strftime("%Y-%m-%d %H:%M"),
        day_end=default_day_end,
        user_text=user_text,
    )
    return system, user


def _intake_system_prompt(default_day_end: str) -> str:
    return (
        "你是 CampusFlow 的“首次全天计划抽取（day intake）”助手。\n"
        "你的任务：把用户第一次描述“今天安排 + 任务”的自然语言，转成结构化 JSON。\n\n"
        "规则：\n"
        "1. 输出 schema 版本：" + INTAKE_SCHEMA_VERSION + "\n"
        "2. 绝对时间用 24 小时制 HH:MM（例如 10:00）；相对时间用 starts_in_minutes"
        "（距参考时间的分钟数，正整数）。参考时间由程序提供，你不得自行猜测“现在”。\n"
        "3. day_end 缺省为 " + default_day_end + "；只有用户明确提到更晚/更早的日终时间才给出 day_end。\n"
        "4. 任务时长：用户明确说出的时长放 total_minutes（整数分钟）并填 duration_source=user_explicit；"
        "只有“快速吃点/慢慢吃”等明确时长语义才可填 semantic_estimate；不要自行估计时长（ai_estimated 保留给兼容旧输出）。"
        "没说就 null，由后续 Agent 暂估。\n"
        "5. 任务是否可拆分 / 最小片段：用户没提就 null；"
        "minimum_slice_minutes 表示“至少做多久才开始产生实际价值”，preferred_chunk_minutes 表示理想单段时长，"
        "requires_single_session 表示用户明确要求一次做完；这些是任务执行语义，不是 completed/进度。"
        "用户说“做一会儿 / 背会儿 / 看一会儿”时可给 5~10 分钟小片段。\n"
        "6. 任务 activity_kind：普通任务填 generic；吃饭/吃午饭/吃晚饭填 meal。地点与交通：用户提到就填 location_text，没提到就 null；用户说“我现在在X / 我从X出发”"
        "时把 X 填到顶层 current_location（原话，不解释）；用户明确骑车/骑行时 transport_mode 填"
        " \"bike\"，走路/步行填 \"walk\"，未提为 null。\n"
        "7. 你不得输出 completed / remaining / window capacity / task_ref / commitment_ref，"
        "这些全部由程序生成。\n"
        "8. 固定安排的开始时间无法确定、或身份/时间有歧义时，把问题放进 questions，不要编造时间。\n"
        "9. 固定安排必须有开始时间（starts_at 或 starts_in_minutes）；结束时间三选一：明确结束时刻用 ends_at，"
        "明确持续时长用 duration_minutes；‘开始后多久结束/下课’用 relative_end_minutes。"
        "例如‘21点上课，一个半小时后下课’填 starts_at=21:00、relative_end_minutes=90，"
        "不要自行计算22:30。确实不知道则三者都为 null。\n"
        "10. 如果用户说‘上课/开会结束后，然后做X、Y’，把这些后续任务的 after_commitment_index 填为"
        "对应固定安排在 commitments 数组中的一开始的序号；程序会按该安排的真实结束时间限制任务，"
        "不要自行做时间加法。\n"
        "11. meal 的时间语义：用户明确说午饭/晚饭时填 meal_period=lunch/dinner；未说明填 unspecified。"
        "用户明确说某顿饭在某个固定安排前/后时，分别填 meal_before_commitment_index 或 after_commitment_index；"
        "二者都是 commitments 数组从 1 开始的序号。用户明确给出吃饭时刻时填 meal_time=HH:MM。"
        "不要依据当前钟点猜测这一顿饭在课程前还是后。\n"
        "12. 只输出 JSON，不要解释。\n\n"
        "输出对象：{\"schema_version\": \"...\", \"day_end\": \"HH:MM\"|null, "
        "\"commitments\": [...], \"tasks\": [...], \"questions\": [...], "
        "\"current_location\": string|null, \"transport_mode\": \"walk\"|\"bike\"|null}\n"
        "commitment 字段：title、starts_at（HH:MM|null）、ends_at（HH:MM|null）、"
        "starts_in_minutes（int|null）、duration_minutes（int|null）、location_text（string|null）、"
        "relative_end_minutes（int|null，只表示相对该安排开始后的结束分钟数）、"
        "commitment_kind（class|meeting|appointment|other|null）、class_arrival_lead_minutes（int|null）。\n"
        "明确上课/课程填 commitment_kind=class；只有用户明确说提前几分钟到楼时才填 class_arrival_lead_minutes，否则 null。\n"
        "task 字段：title、total_minutes（int|null）、is_splittable（bool|null）、"
        "minimum_slice_minutes（int|null）、preferred_chunk_minutes（int|null）、requires_single_session（bool|null）、"
        "execution_profile_source（qwen_semantic|user_explicit|null）、location_text（string|null）、activity_kind（generic|meal|null）、"
        "duration_source（user_explicit|semantic_estimate|ai_estimated|null）、"
        "after_commitment_index（int|null，表示必须在本 proposal 第几个固定安排结束后执行）、"
        "meal_period（lunch|dinner|unspecified|null）、meal_before_commitment_index（int|null）、"
        "meal_time（HH:MM|null）。meal temporal 字段只用于 activity_kind=meal。\n\n"
        "示例1（地点 + 交通方式 + 任务时长）：用户说“我现在在9斋，下午3点去31教上课，我骑车。"
        "今天还要做实验。”输出："
        '{"schema_version": "p2.day-intake.v1", "day_end": "22:00", '
        '"commitments": [{"title": "上课", "starts_at": "15:00", "ends_at": "16:00", '
        '"starts_in_minutes": null, "duration_minutes": null, "location_text": "31教"}], '
        '"tasks": [{"title": "实验", "total_minutes": null, "is_splittable": null, '
        '"minimum_slice_minutes": null, "preferred_chunk_minutes": null, "requires_single_session": null, '
        '"execution_profile_source": null, "location_text": null, "activity_kind": "generic"}], '
        '"questions": [], "current_location": "9斋", "transport_mode": "bike"}\n'
        "示例2（绝对时间 + 任务时长）：用户说“我现在在宿舍，10点到11点半上课，下午2点到3点有组会。"
        "今天要做计组实验、背30分钟单词、整理实验报告，实验大概要2小时。”输出："
        '{"schema_version": "p2.day-intake.v1", "day_end": "22:00", '
        '"commitments": [{"title": "上课", "starts_at": "10:00", "ends_at": "11:30", '
        '"starts_in_minutes": null, "duration_minutes": null, "location_text": null}, '
        '{"title": "组会", "starts_at": "14:00", "ends_at": "15:00", '
        '"starts_in_minutes": null, "duration_minutes": null, "location_text": null}], '
        '"tasks": [{"title": "计组实验", "total_minutes": 120, "is_splittable": true, '
        '"minimum_slice_minutes": 30, "location_text": null, "activity_kind": "generic"}, '
        '{"title": "背单词", "total_minutes": 30, "is_splittable": false, '
        '"minimum_slice_minutes": null, "location_text": null, "activity_kind": "generic"}, '
        '{"title": "整理实验报告", "total_minutes": null, "is_splittable": null, '
        '"minimum_slice_minutes": null, "location_text": null, "activity_kind": "generic"}], '
        '"questions": [], "current_location": "宿舍", "transport_mode": null}\n'
        "示例3（相对时间）：用户说“90分钟后上课，大概1小时，今天还要写实验报告。”输出："
        '{"schema_version": "p2.day-intake.v1", "day_end": null, '
        '"commitments": [{"title": "上课", "starts_at": null, "ends_at": null, '
        '"starts_in_minutes": 90, "duration_minutes": 60, "location_text": null}], '
        '"tasks": [{"title": "写实验报告", "total_minutes": null, "is_splittable": null, '
        '"minimum_slice_minutes": null, "location_text": null, "activity_kind": "generic"}], '
        '"questions": [], "current_location": null, "transport_mode": null}\n'
        "示例4（歧义进 questions）：列表中存在多个相似安排且时间无法判断时，不要编造，"
        "把需要确认的问题放进 questions，并且不要把该安排放进 commitments。"
        "示例5（部分固定安排）：用户说“下午3点去31教上课，我骑车，地点和开始时间确定但不知道几点下课。”"
        "输出 ends_at/duration_minutes 为 null，仍保留 starts_at 与 location_text，"
        "并在 questions 里只问“这节课大约几点结束？”。"
    )


# ---------------------------------------------------------------------------
# 程序时间解析与工具
# ---------------------------------------------------------------------------


def _resolve_start(item: IntakeCommitment, reference: datetime) -> Optional[datetime]:
    if item.starts_in_minutes is not None:
        return reference + timedelta(minutes=item.starts_in_minutes)
    if item.starts_at is not None:
        return _parse_hhmm(item.starts_at, reference)
    return None


def _resolve_end(item: IntakeCommitment, starts: Optional[datetime]) -> Optional[datetime]:
    if item.ends_at is not None:
        return _parse_hhmm(item.ends_at, starts)
    if item.duration_minutes is not None and starts is not None:
        return starts + timedelta(minutes=item.duration_minutes)
    if item.relative_end_minutes is not None and starts is not None:
        return starts + timedelta(minutes=item.relative_end_minutes)
    return None


def _class_end_is_explicitly_supported(item: IntakeCommitment, user_text: str) -> bool:
    """Require a concrete user cue before fixing a class's end time.

    A bare “21:00 去上课” establishes only the start. Numeric end/duration
    wording is enough; otherwise retain an open-ended commitment rather than
    assuming one hour.
    """
    text = str(user_text or "")
    if item.relative_end_minutes is not None:
        # This field exists specifically for an extraction/auditor-bound
        # relative-end phrase.  Arithmetic remains deterministic in
        # ``_resolve_end`` rather than being delegated to the model.
        return True
    if item.ends_at is not None:
        hour = item.ends_at.split(":", 1)[0].lstrip("0") or "0"
        return item.ends_at in text or (hour + "点") in text
    if item.duration_minutes is not None:
        minutes = str(item.duration_minutes)
        return minutes + "分钟" in text or (
            item.duration_minutes % 60 == 0
            and str(item.duration_minutes // 60) + "小时" in text
        )
    return False


def _resolve_day_end(
    proposed: Optional[str],
    default: str,
    reference: datetime,
    warnings: list,
) -> Optional[datetime]:
    resolved = _parse_hhmm(proposed, reference) if proposed is not None else None
    if resolved is not None and reference < resolved:
        return resolved
    if proposed is not None:
        warnings.append("首次输入中的 day_end 不合法，已使用默认 {}".format(default))
    resolved_default = _parse_hhmm(default, reference)
    if resolved_default is not None and reference < resolved_default:
        return resolved_default
    return None


def _parse_hhmm(text: Optional[str], base: Optional[datetime]) -> Optional[datetime]:
    if text is None or base is None:
        return None
    match = _TIME_RE.match(text.strip())
    if match is None:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _format_ref(prefix: str, number: int) -> str:
    return "{}{:03d}".format(prefix, number)


# -- parser helpers (AgenticParseError) --

def _require_list(payload: dict, name: str) -> list:
    value = payload.get(name)
    if value is None:
        return []
    if not isinstance(value, list):
        raise AgenticParseError("{} 必须是数组".format(name))
    return value


def _field_text(value, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AgenticParseError("{} 必须是非空字符串".format(name))
    return value.strip()


def _optional_text(value, name: str, max_len: Optional[int] = None) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise AgenticParseError("{} 必须是非空字符串".format(name))
    stripped = value.strip()
    if max_len is not None and len(stripped) > max_len:
        stripped = stripped[:max_len]
    return stripped


def _optional_time(value, name: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or _TIME_RE.match(value.strip()) is None:
        raise AgenticParseError("{} 必须是 HH:MM 时间".format(name))
    return value.strip()


def _optional_positive_int(value, name: str) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise AgenticParseError("{} 必须是整数".format(name))
    if value <= 0:
        raise AgenticParseError("{} 必须 > 0".format(name))
    return value


def _optional_bool(value, name: str) -> Optional[bool]:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise AgenticParseError("{} 必须是布尔值或 null".format(name))
    return value


def _optional_execution_profile_source(value) -> Optional[str]:
    if value is None:
        return None
    if value not in ("qwen_semantic", "user_explicit"):
        raise AgenticParseError("execution_profile_source 必须是 qwen_semantic、user_explicit 或 null")
    return value


def _optional_transport_mode(value) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise AgenticParseError("transport_mode 必须是字符串或 null")
    stripped = value.strip().lower()
    if stripped in ("walk", "步行", "走路"):
        return "walk"
    if stripped in ("bike", "骑行", "骑车", "自行车"):
        return "bike"
    raise AgenticParseError("transport_mode 必须是 walk、bike 或 null")


def _parse_question(item) -> str:
    if not isinstance(item, str) or not item.strip():
        raise AgenticParseError("questions 中的每一项必须是非空字符串")
    text = item.strip()
    if len(text) > MAX_QUESTION_LENGTH:
        text = text[:MAX_QUESTION_LENGTH]
    return text


# -- dataclass helpers (ValueError) --

def _require_text(name: str, value) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("{} 必须是非空字符串".format(name))


def _require_time(name: str, value) -> None:
    if not isinstance(value, str) or _TIME_RE.match(value.strip()) is None:
        raise ValueError("{} 必须是 HH:MM 时间".format(name))


def _require_transport_mode(name: str, value) -> None:
    if not isinstance(value, str):
        raise ValueError("{} 必须是 walk、bike 或 null".format(name))
    stripped = value.strip().lower()
    if stripped in ("walk", "步行", "走路", "bike", "骑行", "骑车", "自行车"):
        return
    raise ValueError("{} 必须是 walk、bike 或 null".format(name))


def _require_positive_int(name: str, value) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("{} 必须是整数".format(name))
    if value <= 0:
        raise ValueError("{} 必须 > 0".format(name))
# probe
