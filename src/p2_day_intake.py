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
import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Callable, Mapping, Optional, Sequence, Tuple

from src.p1_models import SourceKind
from src.p1_window_models import AvailabilityLevel, FixedCommitment
from src.p2_agentic_parser import AgenticParseError, extract_json_object
from src.p2_models import DayPlanningState, TaskProgress, TaskState
from src.p2_window_derivation import DEFAULT_SAFETY_BUFFER_MINUTES, derive_day_state
from src.p3_map_schema import CampusMapData
from src.p3_route_planner import run_spatial_intake

INTAKE_SCHEMA_VERSION = "p2.day-intake.v1"
# The default planning horizon is the end of today, not an inferred bedtime.
# An explicit earlier user boundary still takes precedence.
DEFAULT_DAY_END = "24:00"
TASK_TIME_BOUNDARY_SEMANTICS = (
    "开始或到达的时间区间，下界是该任务最早开始，不可遗漏；上界不是完成截止，"
    "不能当作最晚结束，也不能反推其他任务的截止。只有明确要求在某时刻前完成，才记录最晚结束。"
    "每个钟点约束只归属于用户明确指定的事件；先后偏好不使前项继承后项的截止。"
    "先后关系由独立关系字段表达，不能为了表示顺序而复制时间边界。"
)
COMMITMENT_END_FIELDS = ("ends_at", "duration_minutes", "relative_end_minutes")
LOCATION_ROLE_SEMANTICS = (
    "当前位置只来自用户现在所在/出发地点，不能由课程地点、后续任务地点、食堂候选或路线目的地推断。"
    "任务location_text是执行该动作所需地点；用户指定在那里办理或交付，就必须保留那个地点，"
    "不能因为到那里需要移动，就把任务地点改成当前位置。移动由程序连接当前地点与任务地点。"
    "没有明确当前位置时current_location为null；课后吃饭仅建立meal after commitment关系。"
)
CLOCK_RANGE_SEMANTICS = (
    "中文数字与阿拉伯数字的钟点等价；时间范围中首端明确的上午/下午语义延续到另一端。"
    "完整起止时刻都要保留，不把已明确的结束时间标为缺失，不生成额外时长。"
)



def commitment_end_contract():
    return (
        "固定安排的" + "、".join(COMMITMENT_END_FIELDS) + "最多一个非null。"
        "用户给了明确结束钟点时保留ends_at，其余两项为null；不要从起止钟点再计算一个duration_minutes。"
        "不能为了消除重复表示而丢失原文已给的结束钟点。"
    )


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
            getattr(self, name) is not None for name in COMMITMENT_END_FIELDS
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
    earliest_start_time: Optional[str] = None
    latest_end_time: Optional[str] = None
    # Hard finish boundary relative to a proposal-local fixed commitment.
    before_commitment_index: Optional[int] = None
    predecessor_task_indexes: Tuple[int, ...] = ()
    attention_mode: str = 'active'
    launch_task_index: Optional[int] = None
    background_reason: Optional[str] = None
    user_reported_running: bool = False
    departure_after_task_indexes: Tuple[int, ...] = ()
    overlap_task_index: Optional[int] = None

    def __post_init__(self):
        _require_text("title", self.title)
        from types import SimpleNamespace
        from src.task_attention import validate_attention
        if self.launch_task_index is not None:
            _require_positive_int('launch_task_index', self.launch_task_index)
        if self.overlap_task_index is not None:
            _require_positive_int('overlap_task_index', self.overlap_task_index)
        validate_attention(SimpleNamespace(task_ref='proposal_task',
            attention_mode=self.attention_mode,
            launch_task_ref=str(self.launch_task_index) if self.launch_task_index else None,
            overlap_task_ref=str(self.overlap_task_index) if self.overlap_task_index else None,
            background_reason=self.background_reason, user_reported_running=self.user_reported_running,
            is_splittable=self.is_splittable))
        if not isinstance(self.predecessor_task_indexes, tuple):
            raise ValueError("predecessor_task_indexes must be a tuple")
        for index in self.predecessor_task_indexes:
            _require_positive_int("predecessor_task_indexes", index)
        if not isinstance(self.departure_after_task_indexes, tuple):
            raise ValueError('departure_after_task_indexes must be a tuple')
        for index in self.departure_after_task_indexes:
            _require_positive_int('departure_after_task_indexes', index)
        for name in ("earliest_start_time", "latest_end_time"):
            if getattr(self, name) is not None:
                _require_time(name, getattr(self, name))
        if self.earliest_start_time and self.latest_end_time:
            if tuple(map(int, self.earliest_start_time.split(":"))) >= tuple(map(int, self.latest_end_time.split(":"))):
                raise ValueError("task time window must have positive duration")
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
        if self.before_commitment_index is not None:
            _require_positive_int("before_commitment_index", self.before_commitment_index)
            if self.before_commitment_index == self.after_commitment_index:
                raise ValueError("task 不能同时位于同一固定安排之前和之后")
            if self.meal_before_commitment_index not in (None, self.before_commitment_index):
                raise ValueError("before commitment 字段不能指向不同固定安排")
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
                (task.before_commitment_index, "before_commitment_index"),
                (task.meal_before_commitment_index, "meal_before_commitment_index"),
            ):
                if index is not None and index > len(self.commitments):
                    raise ValueError("{} 超出 commitments 范围".format(field_name))
        if self.day_end is not None:
            _require_time("day_end", self.day_end)
        for question in self.questions:
            _require_text("question", question)
        from src.task_dependencies import validate_predecessors
        validate_predecessors(tuple(str(index + 1) for index in range(len(self.tasks))), {
            str(index + 1): tuple(dict.fromkeys(str(parent) for parent in
                task.predecessor_task_indexes + task.departure_after_task_indexes
                + ((task.overlap_task_index,) if task.overlap_task_index else ())
                + ((task.launch_task_index,) if task.launch_task_index else ())))
            for index, task in enumerate(self.tasks)})
        for task in self.tasks:
            if task.overlap_task_index and self.tasks[task.overlap_task_index - 1].attention_mode != 'background':
                raise ValueError('overlap target must be a background process')
            if task.overlap_task_index in task.predecessor_task_indexes + task.departure_after_task_indexes:
                raise ValueError('cannot require both overlap and completion of the same process')
            if task.launch_task_index and self.tasks[task.launch_task_index - 1].attention_mode != 'active':
                raise ValueError('background launch must be an active-attention task')
            if task.launch_task_index and task.user_reported_running:
                raise ValueError('initial task list is pending work: already-running process cannot also require a pending launch')
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
    predecessors = item.get("predecessor_task_indexes", [])
    departures = item.get('departure_after_task_indexes', [])
    if not isinstance(departures, list):
        raise AgenticParseError('departure_after_task_indexes must be an array when supplied')
    if not isinstance(predecessors, list):
        raise AgenticParseError("predecessor_task_indexes must be an array when supplied")
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
            before_commitment_index=_optional_positive_int(
                item.get("before_commitment_index"), "before_commitment_index"
            ),
            meal_period=item.get("meal_period"),
            meal_before_commitment_index=_optional_positive_int(
                item.get("meal_before_commitment_index"), "meal_before_commitment_index"
            ),
            meal_time=_optional_time(item.get("meal_time"), "meal_time"),
            earliest_start_time=_optional_time(item.get("earliest_start_time"), "earliest_start_time"),
            latest_end_time=_optional_time(item.get("latest_end_time"), "latest_end_time"),
            predecessor_task_indexes=tuple(predecessors),
            departure_after_task_indexes=tuple(departures),
            overlap_task_index=item.get('overlap_task_index'),
            attention_mode=item.get('attention_mode', 'active'),
            launch_task_index=item.get('launch_task_index'),
            background_reason=item.get('background_reason'),
            user_reported_running=item.get('user_reported_running', False),
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
    semantically_reviewed: bool = False,
) -> IntakeApplied:
    """把解析后的提案转成 DayPlanningState；时间基于 reference_datetime 解析。"""
    if not isinstance(reference_datetime, datetime):
        raise TypeError("reference_datetime must be a datetime")
    if not isinstance(proposal, DayIntakeProposal):
        raise TypeError("proposal must be a DayIntakeProposal")

    if not semantically_reviewed:
        if any(task.attention_mode == 'background' for task in proposal.tasks):
            raise AgenticParseError('background execution requires successful semantic audit')
        proposal = _preserve_explicit_task_durations(proposal, user_text)
    warnings = []
    # Validator-generated questions are required by construction. Model
    # suggestions are filtered separately against the materialized facts.
    questions = []
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
            and not semantically_reviewed
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
                  SourceKind.AI_ESTIMATED if item.duration_source in ("ai_estimated", "semantic_estimate") else
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
                predecessor_task_refs=tuple(dict.fromkeys(_format_ref(TASK_REF_PREFIX, parent)
                    for parent in item.predecessor_task_indexes + item.departure_after_task_indexes)),
                departure_after_task_refs=tuple(_format_ref(TASK_REF_PREFIX, parent)
                    for parent in item.departure_after_task_indexes),
                overlap_task_ref=_format_ref(TASK_REF_PREFIX, item.overlap_task_index) if item.overlap_task_index else None,
                attention_mode=item.attention_mode,
                launch_task_ref=_format_ref(TASK_REF_PREFIX, item.launch_task_index) if item.launch_task_index else None,
                background_reason=item.background_reason,
                user_reported_running=item.user_reported_running,
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
        questions=tuple(dict.fromkeys(questions + list(
            _required_intake_questions(state, proposal.questions, user_text)
        ))),
        new_commitment_refs=tuple(new_commitment_refs),
        new_task_refs=tuple(new_task_refs),
    )


def _required_intake_questions(state, questions, user_text):
    """Publish questions only for an unresolved hard fact or event identity.

    Task start times are allocation decisions; unknown estimates and soft
    preferences have their own defaults/estimation path. A model's generic
    conflict note is not evidence that one of these fields is required.
    Spatial resolution still owns its independently validated questions.
    """
    if not state.tasks and not state.commitments:
        return tuple(dict.fromkeys(questions))
    source = user_text or ""
    required = []
    fixed = state.commitments
    overlaps = any(
        left.starts_at and left.ends_at and right.starts_at and right.ends_at
        and left.ends_at > state.now and right.ends_at > state.now
        and left.starts_at < right.ends_at and right.starts_at < left.ends_at
        for index, left in enumerate(fixed) for right in fixed[index + 1:]
    )
    for question in questions:
        # Identity ambiguity cannot be resolved from a valid duration/window.
        if (re.search(r"指的是|指哪|同名|无法区分", question)
                or (re.search(r"哪(?:一)?(?:份|项|门)", question)
                    and not re.search(r"偏好|习惯|喜欢|优先|先做", question))):
            required.append(question)
            continue
        temporal = bool(re.search(r"几点|时间|开始|结束|下课|多久|多长|时长|持续|冲突|重叠", question))
        if temporal and overlaps:
            required.append(question)
            continue
        for item in fixed:
            if item.title not in question:
                continue
            asks_start = "开始" in question
            asks_end = bool(re.search(r"结束|下课|多久|多长|时长|持续", question))
            if temporal and (
                (item.starts_at is None and (asks_start or not asks_end))
                or (item.ends_at is None and (asks_end or not asks_start))
            ):
                required.append(question)
                break
            if (item.location_text is None and re.search(r"地点|哪里|在哪", question)
                    and re.search(r"前往|去", source) and item.title in source):
                required.append(question)
                break
        else:
            # A fixed event with no start may have been omitted by the intake
            # parser. Preserve its concrete time question, not optional task
            # scheduling questions or an unbound "time relation" note.
            unbound_question = question
            for task in state.tasks:
                unbound_question = unbound_question.replace(task.title, "")
            if temporal and any(
                marker in unbound_question and marker in source
                and not any(marker in item.title or (
                    marker in ("上课", "课程") and item.commitment_kind == "class"
                ) for item in fixed)
                for marker in ("上课", "课程", "组会", "开会", "会议", "预约", "约会")
            ):
                required.append(question)
    return tuple(dict.fromkeys(required))


def _preserve_explicit_task_durations(proposal, user_text):
    """Keep proposal and applied facts aligned at the intake boundary.

    Restore omitted explicit durations or their source, never overwrite an
    existing numeric total, infer a task, or change its other semantics.
    """
    tasks = []
    for item in proposal.tasks:
        explicit = _explicit_task_duration_minutes(item.title, user_text)
        if explicit is not None and item.total_minutes in (None, explicit):
            item = replace(item, total_minutes=explicit, duration_source="user_explicit")
        tasks.append(item)
    return replace(proposal, tasks=tuple(tasks))


def _explicit_task_duration_minutes(title, user_text):
    """Read only an unambiguous numeric duration adjacent to the exact title.

    The model still identifies the task. A number elsewhere in the input,
    a progress amount, a bound or a conflicting mention is not evidence.
    """
    title = re.escape(title.strip())
    number = r"(?<![\d.])(?P<number>\d+(?:\.\d+)?)\s*(?P<unit>分钟|小时)"
    patterns = (
        number + r"\s*的\s*" + title,
        title + r"\s*(?:时长(?:为|是)?|需要|用时|大约|大概|预计|约|要|需|用)?\s*[:：]?\s*" + number,
    )
    durations = []
    for clause in re.split(r"[，,。；;！？!?\n]", user_text or ""):
        if re.search(r"不是|并非|不要|不用|无需|不需要|至少|最多|至多|不到|超过|已经|已做|做了|还剩|剩余|或者|或", clause):
            continue
        if re.search(r"(?:\d+(?:\.\d+)?\s*(?:到|至|[-~～—])\s*|[-−]\s*)\d+(?:\.\d+)?\s*(?:分钟|小时)", clause):
            continue
        for pattern in patterns:
            for match in re.finditer(pattern, clause):
                minutes = float(match.group("number")) * (60 if match.group("unit") == "小时" else 1)
                durations.append(minutes)
    if len(set(durations)) == 1 and durations[0] > 0 and durations[0].is_integer():
        return int(durations[0])
    return None


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
            BackgroundSemanticError,
        )
        raw_system, raw_user = build_raw_event_extractor_prompt(
            reference_datetime, user_text, campus_id=campus_id or (map_data.campus_id if map_data is not None else None)
        )
        failure = None
        failed_response = None
        background_failure = False
        for attempt in range(2):
            retry_user = raw_user
            if attempt:
                repair_used = True
                retry_user += '\n上一轮事件/关系未通过正式校验：' + failure
                retry_user += ('。重新检查完整原文中的事件是否遗漏或混合，使用同一事件契约修复；'
                               '不要用不存在的事件、自己引用自己或把待做动作改为已执行来满足关系。')
                retry_user += '\n上次事件抽取（待修订，不是可信事实）：\n' + (failed_response or '')
            semantic_calls += 1
            try:
                failed_response = raw_event_caller(raw_system, retry_user)
                raw_events = parse_raw_event_extraction(failed_response)
                linker_system, linker_user = build_semantic_linker_prompt(reference_datetime, user_text, raw_events)
                semantic_calls += 1
                semantic_graph = parse_event_semantic_graph(semantic_linker_caller(linker_system, linker_user), raw_events)
                proposal = materialize_day_intake(raw_events, semantic_graph)
                break
            except (AgenticParseError, TypeError, ValueError) as exc:
                failure = str(exc)
                retryable = (isinstance(exc, BackgroundSemanticError) or
                    (semantic_graph is not None and bool(semantic_graph.background_processes)))
                background_failure = background_failure or retryable
                semantic_graph = None
                if not retryable:
                    break
        if proposal is None:
            if background_failure:
                # A recognized autonomy relation that failed its bounded
                # repair must not silently become a different serial task.
                return DayIntakeOutcome(applied=None, warnings=(WARNING_INTAKE_FAILED,),
                    questions=(), call_count=semantic_calls, repair_used=repair_used)
            # The raw/linker pass is advisory semantic understanding.  A bad
            # model response never gets deterministically guessed into task
            # facts; the existing strict Day Intake path remains a safe
            # fallback and the auditor can still inspect its explicit facts.
            # A linker failure does not invalidate independently parsed raw
            # facts. Keep them as evidence for the bounded semantic auditor.
            semantic_graph = None
            if raw_events is not None:
                # A rejected relationship graph does not invalidate parsed
                # event identities, absolute boundaries or places. Hand those
                # facts to the existing semantic audit without guessed edges.
                from src.p4_event_semantics import EventSemanticGraph
                try:
                    proposal = materialize_day_intake(raw_events, EventSemanticGraph())
                except (AgenticParseError, TypeError, ValueError):
                    proposal = None

    if proposal is None:
        system, user = build_day_intake_prompt(reference_datetime, user_text, default_day_end)
        text = caller(system, user)
        semantic_calls += 1
        try:
            proposal = parse_day_intake(text)
        except AgenticParseError as exc:
            # Same complete contract and user facts as the initial pass.
            # A generic version-only repair lost time/location semantics and
            # could not recover facts omitted from the malformed candidate.
            repair_user = json.dumps({
                "request_stage": "day_intake_repair",
                "original_context": user,
                "previous_response": text,
                "validation_error": str(exc),
                "instruction": "按同一正式契约修复失败字段，保留用户已经提供的起止时间、时长、地点和任务；只返回一个完整JSON对象。",
            }, ensure_ascii=False)
            repaired = repair_caller(system, repair_user)
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
    semantically_reviewed = False
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
            map_data=map_data,
        )
        proposal = audit.proposal
        semantic_repair_used = audit.repaired
        # A version-only approval of the legacy fallback is not a grounded
        # extraction. Only the parsed event + semantic audit path replaces
        # the older language-specific evidence check.
        semantically_reviewed = raw_events is not None and not audit.audit_failed

    # Enrichment consumes the proposal while allocation consumes applied
    # state. Both must see the same preserved explicit duration/source.
    if not semantically_reviewed:
        proposal = _preserve_explicit_task_durations(proposal, user_text)
    applied = apply_day_intake(
        reference_datetime,
        proposal,
        default_day_end=default_day_end,
        default_safety_buffer_minutes=default_safety_buffer_minutes,
        travel_minutes_by_commitment=travel_minutes_by_commitment,
        user_text=user_text,
        semantically_reviewed=semantically_reviewed,
    )
    total_calls = semantic_calls + (audit.call_count if semantic_audit_used else 0)
    if map_data is not None:
        venue_questions = tuple(
            "“{}”在哪个地点上课？".format(item.title)
            for item in applied.state.commitments
            if item.commitment_kind == "class" and not item.location_text
        )
        applied = replace(applied, questions=tuple(dict.fromkeys(applied.questions + venue_questions)))
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


EVENT_FACT_SEMANTICS = (
    "事件边界：任务需要多少分钟是工作量，不是距现在多少分钟开始；剩余工作量不产生固定开始时刻。"
    "只有用户给出固定开始/结束或真正的固定承诺才建 commitment。某时刻前交付是 task 的最晚完成边界，"
    "不是该时刻才开始的 appointment；时长未知由后续估时，不追问交付任务的固定结束时刻。"
    "独立任务与课程并列出现，不构成课前/课后硬关系，也不让任务继承课程时间作为截止。"
    "课前/课后修饰哪项动作，就只绑定该动作，不能传播到相邻独立任务。"
    "返回某处继续工作的地点属于对应任务，必须保留，不是只放进解释文字；"
    "同名任务的不同片段按各自时长、地点及证据区分。标题只写事件主题，不拼接地点、钟点、时长；"
    "这些事实使用各自正式字段，避免地点修改后标题仍显示旧地点。标题不得借用另一事件的主题。\n"
)


def process_field_contract() -> str:
    """Shared optional process/departure declarations for intake and audit."""
    return (
        "departure_after_task_indexes（可省略int数组；仅用户要求前项完成后才出发/返回时填写；普通任务开始依赖不自动限制路上移动）、"
        "overlap_task_index（可省略int，1-based；主动任务要在指定后台过程运行期间开始，不是等它完成后开始；不能同时把该过程列为predecessor）、"
        "attention_mode（可省略，默认active；background仅自主运行过程）、"
        "launch_task_index（只属于background过程，指向另一个active启动任务的1-based索引；不是并行搭档索引）、"
        "background_reason（只属于background，说明无需持续人工操作的依据）、"
        "user_reported_running（可省略bool，仅明确已启动事实）。active任务不携带launch_task_index/background_reason；"
        "兼容前台任务保持active；用户要求在后台运行期间执行时填写overlap_task_index；取结果任务依赖background过程的索引，不仅依赖启动。"
    )


def day_intake_field_contract(include_process: bool = True) -> str:
    """One field declaration shared by extraction and semantic repair."""
    return (
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
        "before_commitment_index（int|null，表示必须在指定固定安排开始前完成）、"
        "earliest_start_time/latest_end_time（HH:MM|null，仅用户明确硬时间窗）、"
        "predecessor_task_indexes（可省略的int数组，引用本proposal从1开始的task；仅真实因果前置，后项依赖前项产出；不把可更改的用户排序、叙述顺序或偏好变成因果依赖）、"
        + (process_field_contract() if include_process else "") +
        "meal_period（lunch|dinner|unspecified|null）、meal_before_commitment_index（int|null）、"
        "meal_time（HH:MM|null）。meal temporal 字段只用于 activity_kind=meal。\n\n"
    )


def _intake_system_prompt(default_day_end: str) -> str:
    from src.task_attention import BACKGROUND_SEMANTICS
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
        + commitment_end_contract() +
        "例如‘21点上课，一个半小时后下课’填 starts_at=21:00、relative_end_minutes=90，"
        "不要自行计算22:30。确实不知道则三者都为 null。\n"
        "10. 如果用户说‘上课/开会结束后，然后做X、Y’，把这些后续任务的 after_commitment_index 填为"
        "对应固定安排在 commitments 数组中的一开始的序号；程序会按该安排的真实结束时间限制任务，"
        "不要自行做时间加法。\n"
        "11. meal 的时间语义：用户明确说午饭/晚饭时填 meal_period=lunch/dinner；未说明填 unspecified。"
        "用户明确说某顿饭在某个固定安排前/后时，分别填 meal_before_commitment_index 或 after_commitment_index；"
        "二者都是 commitments 数组从 1 开始的序号。用户明确给出吃饭时刻时填 meal_time=HH:MM。"
        "不要依据当前钟点猜测这一顿饭在课程前还是后。\n"
        "12. 每个 task 可填 earliest_start_time/latest_end_time（HH:MM|null），仅表示用户明确的"
        "可执行时间窗或截止时刻；不是你安排的时间。时长与时间窗宽度不同。"
        + TASK_TIME_BOUNDARY_SEMANTICS +
        "‘课前完成/上课前做完/某固定承诺前完成/出发前完成’是硬截止：填该任务的"
        "before_commitment_index，指向对应固定安排（从1开始）；含义是任务结束不晚于该安排开始。"
        "出发若是独立固定安排则绑定出发；若是去上课等安排的移动则绑定该安排，程序预留移动。"
        "不能把‘课前’填成 after_commitment_index，也不能仅按事件在原文中出现的先后推断前后。"
        "‘最好/尽量/希望课前’只是偏好，不填硬截止；未要求承诺前完成的任务不加此字段。"
        "有多个承诺时按明确指代绑定，歧义放 questions，不猜最近一项；程序按该承诺完整日期计算截止。"
        "信息足够时 questions=[]，不要要求确认开始执行，也不要追问已给的地点或截止时间。"
        "只输出 JSON，不要解释。\n\n"
        "输出对象：{\"schema_version\": \"...\", \"day_end\": \"HH:MM\"|null, "
        "\"commitments\": [...], \"tasks\": [...], \"questions\": [...], "
        "\"current_location\": string|null, \"transport_mode\": \"walk\"|\"bike\"|null}\n"
        + LOCATION_ROLE_SEMANTICS + CLOCK_RANGE_SEMANTICS + EVENT_FACT_SEMANTICS + BACKGROUND_SEMANTICS +
        day_intake_field_contract() +
        "示例1（地点 + 交通方式 + 任务时长）：用户说“我现在在9斋，下午3点去31教上课，我骑车。"
        "今天还要做实验。”输出："
        '{"schema_version": "p2.day-intake.v1", "day_end": null, '
        '"commitments": [{"title": "上课", "starts_at": "15:00", "ends_at": null, '
        '"starts_in_minutes": null, "duration_minutes": null, "location_text": "31教"}], '
        '"tasks": [{"title": "实验", "total_minutes": null, "is_splittable": null, '
        '"minimum_slice_minutes": null, "preferred_chunk_minutes": null, "requires_single_session": null, '
        '"execution_profile_source": null, "location_text": null, "activity_kind": "generic"}], '
        '"questions": ["这节课大约几点结束？"], "current_location": "9斋", "transport_mode": "bike"}\n'
        "示例2（绝对时间 + 任务时长）：用户说“我现在在宿舍，10点到11点半上课，下午2点到3点有组会。"
        "今天要做计组实验、背30分钟单词、整理实验报告，实验大概要2小时。”输出："
        '{"schema_version": "p2.day-intake.v1", "day_end": null, '
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
        from src.intake_time_evidence import supports_commitment_end
        return supports_commitment_end(item.starts_at, item.ends_at, text)
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
    if text.strip() == "24:00":
        return base.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
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
    if name == "day_end" and value == "24:00":
        return value
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
    if name == "day_end" and value == "24:00":
        return
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
