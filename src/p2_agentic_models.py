"""P2c Agentic 任务决策模型（Python 3.8 兼容）。

P2c 重新引入大模型决策，但只保留“建议/意图”形态：模型输出由程序解析、
校验并安全应用到 P2a 状态与 P2b allocator 之上。本模块只定义解析后的
结构化对象，不保存原始模型 JSON / prompt / 响应。
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from src.p2_allocation_models import DayAllocationPlan
from src.p2_day_plan import DayPlanSummary
from src.p2_models import DayPlanningState

RECONCILIATION_SCHEMA_VERSION = "p2.task-reconciliation.v1"
DAY_PLAN_INTENT_SCHEMA_VERSION = "p2.day-plan-intent.v1"
REVIEW_SCHEMA_VERSION = "p2.day-review.v1"

MAX_CALLS_PER_ROUND = 24
MAX_QUESTION_LENGTH = 200
MAX_REASON_LENGTH = 400
MAX_TITLE_LENGTH = 100


class LifecycleAction(str, Enum):
    """Reconciliation 允许的最小生命周期动作集合。"""

    NONE = "none"
    SKIP_TODAY = "skip_today"
    ABANDON = "abandon"
    COMPLETE = "complete"
    RESUME_TODAY = "resume_today"


class TotalSourceChoice(str, Enum):
    """set_total_source 的简化选择；程序映射为 P1 SourceKind。"""

    USER_TEXT = "user_text"
    AI_ESTIMATE = "ai_estimate"


@dataclass(frozen=True)
class ReconciliationUpdate:
    """模型对单个任务的反馈建议。

    target_task_ref 为 null 表示新增任务（必须给出 new_task_title）；
    否则必须引用当天 state 中真实存在的 task_ref（程序校验）。
    """

    target_task_ref: Optional[str]
    new_task_title: Optional[str]
    progress_delta_minutes: Optional[int]
    set_total_minutes: Optional[int]
    set_total_source: Optional[TotalSourceChoice]
    lifecycle_action: LifecycleAction
    is_splittable: Optional[bool]
    minimum_slice_minutes: Optional[int]
    user_reported_running: Optional[bool] = None

    def __post_init__(self):
        if self.user_reported_running is not None and not isinstance(self.user_reported_running, bool):
            raise ValueError('user_reported_running must be bool or None')
        if self.target_task_ref is not None:
            _require_non_empty("target_task_ref", self.target_task_ref)
        if self.new_task_title is not None:
            _require_non_empty("new_task_title", self.new_task_title)
            if len(self.new_task_title) > MAX_TITLE_LENGTH:
                raise ValueError("new_task_title too long")
        if self.target_task_ref is not None and self.new_task_title is not None:
            raise ValueError("cannot set both target_task_ref and new_task_title")
        if self.target_task_ref is None and self.new_task_title is None:
            raise ValueError("update must reference an existing task or define a new task")
        if self.progress_delta_minutes is not None:
            _require_int_min("progress_delta_minutes", self.progress_delta_minutes, minimum=1)
        if self.set_total_minutes is not None:
            _require_int_min("set_total_minutes", self.set_total_minutes, minimum=1)
        if self.set_total_source is not None and not isinstance(self.set_total_source, TotalSourceChoice):
            raise ValueError("set_total_source must be a TotalSourceChoice or None")
        if (self.set_total_minutes is None) != (self.set_total_source is None):
            raise ValueError("set_total_minutes and set_total_source must be set together")
        if not isinstance(self.lifecycle_action, LifecycleAction):
            raise ValueError("lifecycle_action must be a LifecycleAction")
        if self.is_splittable is not None and not isinstance(self.is_splittable, bool):
            raise ValueError("is_splittable must be a bool or None")
        if self.minimum_slice_minutes is not None:
            _require_int_min("minimum_slice_minutes", self.minimum_slice_minutes, minimum=1)


@dataclass(frozen=True)
class ReconciliationResult:
    """Reconciliation Agent 解析后的结构化结果。"""

    schema_version: str
    updates: Tuple[ReconciliationUpdate, ...]
    questions: Tuple[str, ...]

    def __post_init__(self):
        if self.schema_version != RECONCILIATION_SCHEMA_VERSION:
            raise ValueError("reconciliation schema_version mismatch")
        for update in self.updates:
            if not isinstance(update, ReconciliationUpdate):
                raise TypeError("updates must contain ReconciliationUpdate instances")
        for question in self.questions:
            _require_non_empty("question", question)


@dataclass(frozen=True)
class TaskEstimate:
    """Day Plan Agent 对缺失属性的暂估建议。"""

    task_ref: str
    estimated_total_minutes: int
    is_splittable: Optional[bool]
    minimum_slice_minutes: Optional[int]

    def __post_init__(self):
        _require_non_empty("task_ref", self.task_ref)
        _require_int_min("estimated_total_minutes", self.estimated_total_minutes, minimum=1)
        if self.is_splittable is not None and not isinstance(self.is_splittable, bool):
            raise ValueError("is_splittable must be a bool or None")
        if self.minimum_slice_minutes is not None:
            _require_int_min("minimum_slice_minutes", self.minimum_slice_minutes, minimum=1)


@dataclass(frozen=True)
class DayPlanIntent:
    """Day Plan Agent 输出：只决定顺序与缺失信息，不输出具体 allocation。"""

    schema_version: str
    task_order: Tuple[str, ...]
    include_low_attention: bool
    task_estimates: Tuple[TaskEstimate, ...]
    rationale: Optional[str]

    def __post_init__(self):
        if self.schema_version != DAY_PLAN_INTENT_SCHEMA_VERSION:
            raise ValueError("day plan intent schema_version mismatch")
        for ref in self.task_order:
            _require_non_empty("task_order entry", ref)
        if not isinstance(self.include_low_attention, bool):
            raise ValueError("include_low_attention must be a bool")
        for estimate in self.task_estimates:
            if not isinstance(estimate, TaskEstimate):
                raise TypeError("task_estimates must contain TaskEstimate instances")
        if self.rationale is not None:
            _require_non_empty("rationale", self.rationale)


@dataclass(frozen=True)
class ReviewResult:
    """Review Agent 输出。"""

    schema_version: str
    decision: str
    reason: Optional[str]
    suggested_task_order: Optional[Tuple[str, ...]]
    include_low_attention: Optional[bool]

    def __post_init__(self):
        if self.schema_version != REVIEW_SCHEMA_VERSION:
            raise ValueError("review schema_version mismatch")
        if self.decision not in ("accept", "revise"):
            raise ValueError("decision must be accept or revise")
        if self.reason is not None:
            _require_non_empty("reason", self.reason)
        if self.suggested_task_order is not None:
            for ref in self.suggested_task_order:
                _require_non_empty("suggested_task_order entry", ref)
        if self.include_low_attention is not None and not isinstance(self.include_low_attention, bool):
            raise ValueError("include_low_attention must be a bool or None")


@dataclass(frozen=True)
class P2AgenticDayResult:
    """一轮 P2c Agentic 规划的结果快照（不含任何 raw model JSON）。"""

    original_state: DayPlanningState
    updated_state: DayPlanningState
    reconciliation_result: Optional[ReconciliationResult]
    day_plan_intent: Optional[DayPlanIntent]
    allocation_plan: DayAllocationPlan
    day_summary: DayPlanSummary
    review_result: Optional[ReviewResult]
    revision_used: bool
    call_count: int
    warnings: Tuple[str, ...]
    questions: Tuple[str, ...]

    def __post_init__(self):
        if not isinstance(self.original_state, DayPlanningState):
            raise TypeError("original_state must be a DayPlanningState")
        if not isinstance(self.updated_state, DayPlanningState):
            raise TypeError("updated_state must be a DayPlanningState")
        if self.reconciliation_result is not None and not isinstance(
            self.reconciliation_result, ReconciliationResult
        ):
            raise TypeError("reconciliation_result must be a ReconciliationResult or None")
        if self.day_plan_intent is not None and not isinstance(self.day_plan_intent, DayPlanIntent):
            raise TypeError("day_plan_intent must be a DayPlanIntent or None")
        if not isinstance(self.allocation_plan, DayAllocationPlan):
            raise TypeError("allocation_plan must be a DayAllocationPlan")
        if not isinstance(self.day_summary, DayPlanSummary):
            raise TypeError("day_summary must be a DayPlanSummary")
        if self.review_result is not None and not isinstance(self.review_result, ReviewResult):
            raise TypeError("review_result must be a ReviewResult or None")
        if not isinstance(self.revision_used, bool):
            raise ValueError("revision_used must be a bool")
        if isinstance(self.call_count, bool) or not isinstance(self.call_count, int):
            raise ValueError("call_count must be an integer")
        if self.call_count < 0:
            raise ValueError("call_count must be >= 0")
        if self.call_count > MAX_CALLS_PER_ROUND:
            raise ValueError("call_count must not exceed MAX_CALLS_PER_ROUND")
        for warning in self.warnings:
            _require_non_empty("warning", warning)
        for question in self.questions:
            _require_non_empty("question", question)


def _require_int_min(name, value, minimum):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("{} must be an integer, got {}".format(name, type(value).__name__))
    if value < minimum:
        raise ValueError("{} must be >= {}".format(name, minimum))


def _require_non_empty(name, value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("{} must be a non-empty string".format(name))
