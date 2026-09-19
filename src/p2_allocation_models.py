"""P2b 全天任务分配结果模型（Python 3.8 兼容）。

TaskAllocation / DayAllocationPlan 是“基于当前 DayPlanningState 生成的计划快照”：
- 可以随时重新生成；
- 不增加 completed；
- 不修改任务生命周期；
- 不写 history；
- 不负责 session。

planned_minutes 只表示“计划”，不等于用户已经完成的事实；
completed_minutes 只能由 P2a 的 progress report 更新。
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional, Tuple

ALLOCATION_REF_PREFIX = "allocation_"


@dataclass(frozen=True)
class TaskAllocation:
    """一个窗口内一个任务的一次计划片段。"""

    allocation_ref: str
    task_ref: str
    task_title: str
    window_ref: Optional[str]
    planned_minutes: int
    sequence_index: int
    is_partial: bool
    remaining_before: int
    remaining_after: int
    # None preserves the historical sequential-window representation.
    starts_at: Optional[datetime] = None
    occupies_attention: bool = True

    def __post_init__(self):
        if not isinstance(self.occupies_attention, bool):
            raise ValueError('occupies_attention must be bool')
        if self.starts_at is not None and not isinstance(self.starts_at, datetime):
            raise ValueError('allocation starts_at must be datetime or None')
        _require_non_empty("allocation_ref", self.allocation_ref)
        _require_non_empty("task_ref", self.task_ref)
        _require_non_empty("task_title", self.task_title)
        if self.window_ref is None:
            if self.occupies_attention or self.starts_at is None:
                raise ValueError('only explicit-time background intervals can omit a person window')
        else:
            _require_non_empty("window_ref", self.window_ref)
        _require_int_min("planned_minutes", self.planned_minutes, minimum=1)
        _require_int_min("sequence_index", self.sequence_index, minimum=0)
        if not isinstance(self.is_partial, bool):
            raise ValueError("is_partial must be a bool")
        _require_int_min("remaining_before", self.remaining_before, minimum=0)
        _require_int_min("remaining_after", self.remaining_after, minimum=0)
        if self.planned_minutes > self.remaining_before:
            raise ValueError("planned_minutes must not exceed remaining_before")
        if self.remaining_after != self.remaining_before - self.planned_minutes:
            raise ValueError("remaining_after must equal remaining_before - planned_minutes")
        if self.is_partial != (self.planned_minutes < self.remaining_before):
            raise ValueError("is_partial must be derived: planned_minutes < remaining_before")


@dataclass(frozen=True)
class DayAllocationPlan:
    """基于当前 DayPlanningState 生成的确定性计划快照。

    主要不变量由 allocator 强校验（总量不超 remaining / capacity 等）；
    本模型只做内部一致性保护。
    """

    allocations: Tuple[TaskAllocation, ...]
    unallocated_task_refs: Tuple[str, ...]
    unused_capacity_by_window: Tuple[Tuple[str, int], ...]
    current_window_ref: Optional[str]
    current_allocation_ref: Optional[str]
    later_allocations: Tuple[TaskAllocation, ...]
    warnings: Tuple[str, ...]
    total_planned_minutes: int

    def __post_init__(self):
        for allocation in self.allocations:
            if not isinstance(allocation, TaskAllocation):
                raise TypeError("allocations must contain TaskAllocation instances")
        _require_unique_refs("allocation", [a.allocation_ref for a in self.allocations])
        for ref in self.unallocated_task_refs:
            _require_non_empty("unallocated task ref", ref)
        for window_ref, unused in self.unused_capacity_by_window:
            _require_non_empty("unused capacity window ref", window_ref)
            _require_int_min("unused capacity", unused, minimum=0)
        for warning in self.warnings:
            _require_non_empty("warning", warning)
        if self.current_window_ref is not None:
            _require_non_empty("current_window_ref", self.current_window_ref)
        if self.current_allocation_ref is not None:
            _require_non_empty("current_allocation_ref", self.current_allocation_ref)
            matches = [a for a in self.allocations if a.allocation_ref == self.current_allocation_ref]
            if len(matches) != 1:
                raise ValueError("current_allocation_ref must reference exactly one allocation")
            if self.current_window_ref is None or matches[0].window_ref != self.current_window_ref:
                raise ValueError("current_allocation must belong to the current window")
        for allocation in self.later_allocations:
            if not isinstance(allocation, TaskAllocation):
                raise TypeError("later_allocations must contain TaskAllocation instances")
        allocation_refs = [a.allocation_ref for a in self.allocations]
        for allocation in self.later_allocations:
            if allocation.allocation_ref not in allocation_refs:
                raise ValueError("later_allocations must be a subset of allocations")
            if self.current_window_ref is not None and allocation.window_ref == self.current_window_ref:
                raise ValueError("later_allocations must not belong to the current window")
        if self.total_planned_minutes != sum(a.planned_minutes for a in self.allocations):
            raise ValueError("total_planned_minutes must equal the sum of planned_minutes")

    @property
    def current_allocation(self) -> Optional[TaskAllocation]:
        """active window 中第一个 allocation，无则 None。"""
        if self.current_allocation_ref is None:
            return None
        for allocation in self.allocations:
            if allocation.allocation_ref == self.current_allocation_ref:
                return allocation
        return None

    @property
    def planned_minutes_by_task(self) -> Dict[str, int]:
        result = {}
        for allocation in self.allocations:
            result[allocation.task_ref] = result.get(allocation.task_ref, 0) + allocation.planned_minutes
        return result

    @property
    def planned_minutes_by_window(self) -> Dict[str, int]:
        result = {}
        for allocation in self.allocations:
            if not allocation.occupies_attention:
                continue
            result[allocation.window_ref] = result.get(allocation.window_ref, 0) + allocation.planned_minutes
        return result

    @property
    def remaining_after_plan_by_task(self) -> Dict[str, int]:
        result = {}
        for allocation in self.allocations:
            result[allocation.task_ref] = allocation.remaining_after
        return result


def make_allocation_ref(sequence_index: int) -> str:
    """按分配顺序稳定生成 allocation_ref（allocation_001...），不使用随机 UUID。"""
    if isinstance(sequence_index, bool) or not isinstance(sequence_index, int):
        raise ValueError("sequence_index must be an integer")
    if sequence_index < 0:
        raise ValueError("sequence_index must be >= 0")
    return "{}{:03d}".format(ALLOCATION_REF_PREFIX, sequence_index + 1)


def _require_non_empty(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("{} must be a non-empty string".format(name))


def _require_int_min(name: str, value: int, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("{} must be an integer, got {}".format(name, type(value).__name__))
    if value < minimum:
        raise ValueError("{} must be >= {}".format(name, minimum))


def _require_unique_refs(kind: str, refs) -> None:
    seen = set()
    for ref in refs:
        if not isinstance(ref, str) or not ref:
            raise ValueError("{} refs must be non-empty strings".format(kind))
        if ref in seen:
            raise ValueError("duplicate {} ref: {}".format(kind, ref))
        seen.add(ref)
