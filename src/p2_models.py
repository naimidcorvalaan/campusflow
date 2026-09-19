"""P2a 全天规划领域底座：任务进度、日窗口与日规划状态。

P2a 是纯离线、无 LLM、无 UI 的领域层，只负责表达：
- 一个任务还剩多少工作量；
- 一天可以形成哪些可安排窗口；
- 当前时刻的 active window 是谁。

P1 已封存：本模块只读复用 P1 的基础枚举与值对象
（SourceKind / AvailabilityLevel / FixedCommitment），不向任何 P1 类型追加字段。
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, Sequence, Tuple

from src.p1_models import SourceKind
from src.p1_window_models import AvailabilityLevel, FixedCommitment


MAX_HISTORY_LEN = 10
DAY_WINDOW_REF_PREFIX = "day_window_"


class TaskState(str, Enum):
    """P2 任务生命周期最小状态集合。

    ACTIVE：今天仍可安排。
    COMPLETED：已完成，不再安排。
    SKIPPED_TODAY：今天剩余时间不再安排，但不是永久删除。
    ABANDONED：用户明确永久放弃，不得复活。

    不引入 IN_PROGRESS / PAUSED / QUEUED 等额外状态：
    completed_minutes > 0 且 remaining > 0 时仍然可以是 ACTIVE。
    """

    ACTIVE = "active"
    COMPLETED = "completed"
    SKIPPED_TODAY = "skipped_today"
    ABANDONED = "abandoned"


@dataclass(frozen=True)
class TaskProgress:
    """一天内某个任务的稳定进度记录。

    task_ref 是当天状态中的稳定引用；P2a 不做自然语言任务身份匹配。
    completed_minutes 表示用户真实已经投入的工作，不因 total 重估被抹掉。
    """

    task_ref: str
    title: str
    total_minutes: Optional[int]
    completed_minutes: int
    total_source: Optional[SourceKind]
    state: TaskState
    is_splittable: Optional[bool]
    minimum_slice_minutes: Optional[int]
    # Causal/user-required completion edges, never inferred from list order.
    predecessor_task_refs: Tuple[str, ...] = ()
    attention_mode: str = 'active'
    launch_task_ref: Optional[str] = None
    background_reason: Optional[str] = None
    user_reported_running: bool = False
    departure_after_task_refs: Tuple[str, ...] = ()
    overlap_task_ref: Optional[str] = None

    def __post_init__(self):
        _require_non_empty_text("task_ref", self.task_ref)
        from src.task_attention import validate_attention
        validate_attention(self)
        if (not isinstance(self.departure_after_task_refs, tuple)
                or any(ref not in self.predecessor_task_refs for ref in self.departure_after_task_refs)
                or len(set(self.departure_after_task_refs)) != len(self.departure_after_task_refs)):
            raise ValueError('departure dependencies must be a unique subset of task predecessors')
        if (not isinstance(self.predecessor_task_refs, tuple)
                or any(not isinstance(ref, str) or not ref.strip() for ref in self.predecessor_task_refs)
                or self.task_ref in self.predecessor_task_refs
                or len(set(self.predecessor_task_refs)) != len(self.predecessor_task_refs)):
            raise ValueError("invalid predecessor task refs")
        _require_non_empty_text("title", self.title)
        _require_minutes("completed_minutes", self.completed_minutes, minimum=0)
        if self.total_minutes is not None:
            _require_minutes("total_minutes", self.total_minutes, minimum=1)
        if self.minimum_slice_minutes is not None:
            _require_minutes("minimum_slice_minutes", self.minimum_slice_minutes, minimum=1)
        if self.total_source is not None and not isinstance(self.total_source, SourceKind):
            raise ValueError("total_source must be a SourceKind or None")
        if not isinstance(self.state, TaskState):
            raise ValueError("state must be a TaskState")
        if self.is_splittable is not None and not isinstance(self.is_splittable, bool):
            raise ValueError("is_splittable must be a bool or None")
        if self.total_minutes is not None and self.completed_minutes >= self.total_minutes:
            object.__setattr__(self, "state", TaskState.COMPLETED)

    @property
    def remaining_minutes(self) -> Optional[int]:
        """已知总量时返回剩余工作量，未知时返回 None，绝不返回负数。"""
        if self.total_minutes is None:
            return None
        return max(self.total_minutes - self.completed_minutes, 0)


@dataclass(frozen=True)
class DayWindow:
    """一天中一个真实空档边界对应的可安排窗口。

    starts_at / ends_at 表示真实空档边界；capacity_minutes 表示其中真正
    可用于任务的分钟。不使用把 ends_at 前移的方式来隐藏 buffer / travel。
    """

    window_ref: str
    starts_at: datetime
    ends_at: datetime
    availability: AvailabilityLevel
    safety_buffer_minutes: int
    travel_minutes: int
    next_commitment_ref: Optional[str]
    capacity_minutes: int

    def __post_init__(self):
        _require_non_empty_text("window_ref", self.window_ref)
        if not isinstance(self.starts_at, datetime) or not isinstance(self.ends_at, datetime):
            raise ValueError("starts_at and ends_at must be datetimes")
        if not self.starts_at < self.ends_at:
            raise ValueError("starts_at must be before ends_at")
        if not isinstance(self.availability, AvailabilityLevel):
            raise ValueError("availability must be an AvailabilityLevel")
        if self.availability == AvailabilityLevel.UNAVAILABLE:
            raise ValueError("DayWindow must not be UNAVAILABLE")
        _require_minutes("safety_buffer_minutes", self.safety_buffer_minutes, minimum=0)
        _require_minutes("travel_minutes", self.travel_minutes, minimum=0)
        _require_minutes("capacity_minutes", self.capacity_minutes, minimum=0)
        if self.next_commitment_ref is not None:
            _require_non_empty_text("next_commitment_ref", self.next_commitment_ref)


@dataclass(frozen=True)
class WindowDerivationResult:
    """窗口派生函数的纯输出。"""

    windows: Tuple[DayWindow, ...]
    unresolved_commitment_refs: Tuple[str, ...]


@dataclass(frozen=True)
class DayPlanningState:
    """一天规划状态的只读快照。

    不变量：
    1. windows 按 starts_at 排序且互不重叠；
    2. window.starts_at < window.ends_at 且 capacity_minutes >= 0；
    3. active_window_ref 非 null 时必须指向真实存在且包含 now 的窗口；
    4. task_ref / window_ref / commitment_ref 均唯一；
    5. history 最多 MAX_HISTORY_LEN 条。
    """

    reference_datetime: datetime
    now: datetime
    day_end: datetime
    commitments: Tuple[FixedCommitment, ...]
    tasks: Tuple[TaskProgress, ...]
    windows: Tuple[DayWindow, ...]
    active_window_ref: Optional[str]
    unresolved_commitment_refs: Tuple[str, ...]
    history: Tuple[str, ...]

    def __post_init__(self):
        if not isinstance(self.now, datetime) or not isinstance(self.day_end, datetime):
            raise ValueError("now and day_end must be datetimes")
        if not self.now < self.day_end:
            raise ValueError("now must be before day_end")
        _require_unique_refs("commitment", [c.commitment_ref for c in self.commitments])
        _require_unique_refs("task", [t.task_ref for t in self.tasks])
        _require_unique_refs("window", [w.window_ref for w in self.windows])
        for left, right in zip(self.windows, self.windows[1:]):
            if left.starts_at > right.starts_at:
                raise ValueError("windows must be sorted by starts_at")
            if right.starts_at < left.ends_at:
                raise ValueError("windows must not overlap")
        if self.active_window_ref is not None:
            matches = [w for w in self.windows if w.window_ref == self.active_window_ref]
            if len(matches) != 1:
                raise ValueError("active_window_ref must reference exactly one window")
            if not (matches[0].starts_at <= self.now < matches[0].ends_at):
                raise ValueError("active_window_ref must reference a window containing now")
        elif any(w.starts_at <= self.now < w.ends_at for w in self.windows):
            raise ValueError("active_window_ref must be set when a window contains now")
        if len(self.history) > MAX_HISTORY_LEN:
            raise ValueError("history must not exceed {} entries".format(MAX_HISTORY_LEN))
        for entry in self.history:
            _require_non_empty_text("history entry", entry)
        for ref in self.unresolved_commitment_refs:
            _require_non_empty_text("unresolved_commitment_refs entry", ref)


def _require_minutes(field_name: str, value: int, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("{} must be an integer, got {}".format(field_name, type(value).__name__))
    if value < minimum:
        raise ValueError("{} must be >= {}".format(field_name, minimum))


def _require_non_empty_text(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("{} must be a non-empty string".format(name))


def _require_unique_refs(kind: str, refs: Sequence[str]) -> None:
    seen = set()
    for ref in refs:
        _require_non_empty_text("{} ref".format(kind), ref)
        if ref in seen:
            raise ValueError("duplicate {} ref: {}".format(kind, ref))
        seen.add(ref)


def append_history(history: Tuple[str, ...], entry: str) -> Tuple[str, ...]:
    """向历史追加一条；超过 MAX_HISTORY_LEN 时自动丢弃最旧一条。"""
    if not isinstance(history, tuple) or any(not isinstance(e, str) or not e.strip() for e in history):
        raise ValueError("history must be a tuple of non-empty strings")
    if not isinstance(entry, str) or not entry.strip():
        raise ValueError("history entry must be a non-empty string")
    updated = history + (entry,)
    if len(updated) > MAX_HISTORY_LEN:
        updated = updated[len(updated) - MAX_HISTORY_LEN:]
    return updated


def make_window_ref(sequence_index: int) -> str:
    """按当天窗口顺序稳定生成 window_ref（day_window_001...），不使用随机 UUID。"""
    if isinstance(sequence_index, bool) or not isinstance(sequence_index, int):
        raise ValueError("sequence_index must be an integer")
    if sequence_index < 0:
        raise ValueError("sequence_index must be >= 0")
    return "{}{:03d}".format(DAY_WINDOW_REF_PREFIX, sequence_index + 1)
