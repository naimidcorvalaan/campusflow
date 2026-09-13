"""Immutable execution-location facts, deliberately separate from TaskProgress."""
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from src.p3_location_resolver import LocationResolution
from src.p4_concurrency import ConcurrentExecutionAuthorization

EXECUTION_PLAN_CONTEXT_KEY = "p4_execution_plan_context"


class ExecutionLocationSource(str, Enum):
    EXPLICIT_TASK_LOCATION = "explicit_task_location"
    AUTO_SELECTED_MEAL = "auto_selected_meal"
    USER_CURRENT_LOCATION = "user_current_location"


@dataclass(frozen=True)
class TaskExecutionProfile:
    """Stable, round-local execution shape for a single formal task_ref."""
    task_ref: str
    splittable: bool
    minimum_chunk_minutes: int
    preferred_chunk_minutes: int
    requires_single_session: bool = False
    source: str = "qwen_semantic"

    def __post_init__(self):
        if not isinstance(self.task_ref, str) or not self.task_ref.strip():
            raise ValueError("profile task_ref 必须非空")
        if not isinstance(self.splittable, bool) or not isinstance(self.requires_single_session, bool):
            raise ValueError("profile flags 必须是 bool")
        if self.requires_single_session and self.splittable:
            raise ValueError("single-session profile 不能同时 splittable")
        if isinstance(self.minimum_chunk_minutes, bool) or self.minimum_chunk_minutes < 1:
            raise ValueError("minimum chunk 必须为正整数")
        if isinstance(self.preferred_chunk_minutes, bool) or self.preferred_chunk_minutes < self.minimum_chunk_minutes:
            raise ValueError("preferred chunk 必须不小于 minimum chunk")
        if self.source not in ("qwen_semantic", "user_explicit"):
            raise ValueError("profile source 无效")


class CurrentLocationSource(str, Enum):
    USER = "user"
    ASSUMED = "assumed"
    UNKNOWN = "unknown"


class ExecutionConfirmationKind(str, Enum):
    MEAL_LOCATION_CONTEXT_REQUIRED = "meal_location_context_required"
    CURRENT_LOCATION_ASSUMED = "current_location_assumed"


@dataclass(frozen=True)
class ExecutionLocation:
    campus_id: str
    node_id: str
    display_name: str
    source: ExecutionLocationSource

    @classmethod
    def from_resolution(cls, resolution, source):
        if not isinstance(resolution, LocationResolution) or not resolution.usable:
            raise ValueError("需要可用的 LocationResolution")
        if not resolution.campus_id or not resolution.node_id or not resolution.display_name:
            raise ValueError("地点解析缺少稳定 campus/node/display identity")
        return cls(resolution.campus_id, resolution.node_id, resolution.display_name, source)


@dataclass(frozen=True)
class ExecutableTaskBinding:
    task_ref: str
    execution_location: Optional[ExecutionLocation] = None
    activity_kind: Optional[str] = None
    effective_duration_minutes: Optional[int] = None
    duration_source: Optional[str] = None
    execution_profile: Optional[TaskExecutionProfile] = None
    not_before_commitment_ref: Optional[str] = None
    # Meal temporal facts are deliberately separate from the generic
    # ``not_before`` relation.  A meal can be after one commitment and before
    # another, while ordinary tasks only need the former today.
    meal_period: Optional[str] = None
    meal_before_commitment_ref: Optional[str] = None
    meal_explicit_time: Optional[str] = None
    meal_temporal_source: Optional[str] = None
    meal_window_start_minutes: Optional[int] = None
    meal_window_end_minutes: Optional[int] = None
    # Optional user-confirmed work boundary carried by the execution fact.
    # It is not a second task ledger and does not affect completed progress.
    scope_summary: Optional[str] = None
    completion_criteria: Optional[str] = None
    latest_end_time: Optional[str] = None
    # Material-confirmed absolute deadline. Do not turn a future deadline into
    # a same-day clock boundary on restore/replanning.
    deadline_at: Optional[object] = None

    def __post_init__(self):
        if self.deadline_at is not None:
            from datetime import datetime
            if not isinstance(self.deadline_at, datetime) or self.deadline_at.tzinfo is not None:
                raise ValueError("deadline_at 必须为本地绝对日期时间")
        if not isinstance(self.task_ref, str) or not self.task_ref.strip(): raise ValueError("task_ref 必须非空")
        if self.activity_kind not in (None, "generic", "meal"): raise ValueError("activity_kind 无效")
        if self.effective_duration_minutes is not None and (isinstance(self.effective_duration_minutes, bool) or self.effective_duration_minutes < 1): raise ValueError("effective duration 必须为正整数")
        if (self.effective_duration_minutes is None) != (self.duration_source is None): raise ValueError("duration 与 source 必须同时存在")
        if self.execution_profile is not None:
            if not isinstance(self.execution_profile, TaskExecutionProfile):
                raise ValueError("execution_profile 无效")
            if self.execution_profile.task_ref != self.task_ref:
                raise ValueError("execution_profile task_ref 不一致")
        if self.not_before_commitment_ref is not None and (not isinstance(self.not_before_commitment_ref, str) or not self.not_before_commitment_ref.strip()): raise ValueError("not_before_commitment_ref 必须非空或 None")
        if self.meal_period not in (None, "lunch", "dinner", "unspecified"): raise ValueError("meal_period 无效")
        if self.meal_before_commitment_ref is not None and (not isinstance(self.meal_before_commitment_ref, str) or not self.meal_before_commitment_ref.strip()): raise ValueError("meal_before_commitment_ref 必须非空或 None")
        if self.meal_explicit_time is not None:
            import re
            if not isinstance(self.meal_explicit_time, str) or re.match(r"^([01]?\d|2[0-3]):[0-5]\d$", self.meal_explicit_time) is None: raise ValueError("meal_explicit_time 必须为 HH:MM 或 None")
        if self.meal_temporal_source not in (None, "explicit_user", "narrative_order", "inferred_meal_window", "recovery"): raise ValueError("meal_temporal_source 无效")
        if self.activity_kind != "meal" and any(value is not None for value in (self.meal_period, self.meal_before_commitment_ref, self.meal_explicit_time, self.meal_temporal_source)): raise ValueError("meal temporal fields 只能用于 meal")
        if self.meal_before_commitment_ref is not None and self.meal_before_commitment_ref == self.not_before_commitment_ref: raise ValueError("meal 不能同时位于同一固定安排之前和之后")
        if (self.meal_window_start_minutes is None) != (self.meal_window_end_minutes is None): raise ValueError("meal window 必须同时存在或为空")
        if self.meal_window_start_minutes is not None and not (0 <= self.meal_window_start_minutes < self.meal_window_end_minutes <= 24 * 60): raise ValueError("meal window 无效")
        for name in ("scope_summary", "completion_criteria"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError("{} 必须非空或 None".format(name))
        if self.latest_end_time is not None:
            import re
            if not isinstance(self.latest_end_time, str) or re.match(
                r"^([01]?\d|2[0-3]):[0-5]\d$", self.latest_end_time
            ) is None:
                raise ValueError("latest_end_time 必须为 HH:MM 或 None")


@dataclass(frozen=True)
class CurrentLocationContext:
    location: Optional[ExecutionLocation] = None
    source: CurrentLocationSource = CurrentLocationSource.UNKNOWN

    def __post_init__(self):
        if (self.source is CurrentLocationSource.UNKNOWN) != (self.location is None): raise ValueError("unknown 必须无地点；user/assumed 必须有地点")


@dataclass(frozen=True)
class ExecutionConfirmation:
    task_ref: str
    kind: ExecutionConfirmationKind

    def __post_init__(self):
        if not isinstance(self.task_ref, str) or not self.task_ref.strip(): raise ValueError("task_ref 必须非空")
        if not isinstance(self.kind, ExecutionConfirmationKind): raise ValueError("confirmation kind 无效")


@dataclass(frozen=True)
class ExecutionPlanContext:
    bindings: Tuple[ExecutableTaskBinding, ...] = ()
    current_location: CurrentLocationContext = CurrentLocationContext()
    confirmations: Tuple[ExecutionConfirmation, ...] = ()
    # A day-intake transport preference belongs to this round's executable
    # context.  It is optional for legacy contexts; callers default to walk.
    transport_mode: Optional[str] = None
    # Explicit user authorization for a task to run during one fixed
    # commitment.  This is separate from task location/duration facts because
    # it is a relation between two stable planning refs.
    concurrency_authorizations: Tuple[ConcurrentExecutionAuthorization, ...] = ()

    def __post_init__(self):
        refs = [item.task_ref for item in self.bindings]
        if len(refs) != len(set(refs)): raise ValueError("task_ref binding 不能重复")
        confirmation_refs = [(item.task_ref, item.kind) for item in self.confirmations]
        if len(confirmation_refs) != len(set(confirmation_refs)): raise ValueError("confirmation 不能重复")
        campuses = {item.execution_location.campus_id for item in self.bindings if item.execution_location}
        if self.current_location.location: campuses.add(self.current_location.location.campus_id)
        if len(campuses) > 1: raise ValueError("ExecutionPlanContext 不允许混合校区")
        if self.transport_mode not in (None, "walk", "bike"):
            raise ValueError("transport_mode 必须是 walk、bike 或 None")
        pairs = []
        for item in self.concurrency_authorizations:
            if not isinstance(item, ConcurrentExecutionAuthorization):
                raise ValueError("concurrency authorization 无效")
            pairs.append((item.task_ref, item.commitment_ref))
        if len(pairs) != len(set(pairs)):
            raise ValueError("同一 task/commitment concurrency authorization 不能重复")

    def binding_for(self, task_ref):
        return next((item for item in self.bindings if item.task_ref == task_ref), None)

    def upsert(self, binding):
        kept = tuple(item for item in self.bindings if item.task_ref != binding.task_ref)
        return ExecutionPlanContext(kept + (binding,), self.current_location, self.confirmations, self.transport_mode, self.concurrency_authorizations)

    def with_current_location(self, current_location):
        return ExecutionPlanContext(self.bindings, current_location, self.confirmations, self.transport_mode, self.concurrency_authorizations)

    def with_confirmations(self, confirmations):
        return ExecutionPlanContext(self.bindings, self.current_location, tuple(confirmations), self.transport_mode, self.concurrency_authorizations)

    def with_transport_mode(self, transport_mode):
        return ExecutionPlanContext(self.bindings, self.current_location, self.confirmations, transport_mode, self.concurrency_authorizations)

    def with_concurrency_authorizations(self, authorizations):
        return ExecutionPlanContext(
            self.bindings,
            self.current_location,
            self.confirmations,
            self.transport_mode,
            tuple(authorizations),
        )

    def without_concurrency_authorization(self, task_ref, commitment_ref=None):
        """Return a new round context after a user revokes parallel work."""
        if not isinstance(task_ref, str) or not task_ref.strip():
            raise ValueError("task_ref 必须非空")
        if commitment_ref is not None and (not isinstance(commitment_ref, str) or not commitment_ref.strip()):
            raise ValueError("commitment_ref 必须非空或 None")
        kept = tuple(
            item for item in self.concurrency_authorizations
            if not (
                item.task_ref == task_ref
                and (commitment_ref is None or item.commitment_ref == commitment_ref)
            )
        )
        return self.with_concurrency_authorizations(kept)


def load_execution_context(store):
    value = store.get(EXECUTION_PLAN_CONTEXT_KEY)
    return value if isinstance(value, ExecutionPlanContext) else ExecutionPlanContext()


def save_execution_context(store, context):
    if not isinstance(context, ExecutionPlanContext): raise TypeError("context 必须是 ExecutionPlanContext")
    store[EXECUTION_PLAN_CONTEXT_KEY] = context
