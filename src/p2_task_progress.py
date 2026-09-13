"""P2a 任务进度纯函数操作（Python 3.8 兼容）。

所有函数保持 frozen TaskProgress 的不可变性：每次返回新实例，
由 TaskProgress.__post_init__ 完成分钟校验与自动状态规范化。
"""

from typing import Optional

from src.p1_models import SourceKind
from src.p2_models import TaskProgress, TaskState


def apply_total_update(task: TaskProgress, new_total_minutes: int, new_source: SourceKind) -> TaskProgress:
    """只更新总工作量和来源，completed_minutes 必须保留。

    例如 total 120 -> 150 且 completed=40 时：completed 保持 40，remaining 变为 110。
    如果 new_total < completed：completed 保持原值，remaining=0，state 自动 COMPLETED。
    """
    _require_task(task)
    _require_positive_int_minutes("new_total_minutes", new_total_minutes)
    if not isinstance(new_source, SourceKind):
        raise ValueError("new_source must be a SourceKind")
    return _rebuild(task, total_minutes=new_total_minutes, total_source=new_source)


def apply_progress_report(task: TaskProgress, additional_minutes: int) -> TaskProgress:
    """累加用户报告的额外完成分钟。

    additional_minutes 必须为 > 0 的整数；total 已知且 completed >= total 时自动 COMPLETED；
    不因为 total 未知而拒绝累计。
    """
    _require_task(task)
    _require_positive_int_minutes("additional_minutes", additional_minutes)
    return _rebuild(task, completed_minutes=task.completed_minutes + additional_minutes)


def mark_skipped_today(task: TaskProgress) -> TaskProgress:
    """ACTIVE -> SKIPPED_TODAY。

    COMPLETED / ABANDONED 保持原状态（不得因为 skip 操作重新改变生命周期、不得复活）。
    """
    _require_task(task)
    if task.state != TaskState.ACTIVE:
        return task
    return _rebuild(task, state=TaskState.SKIPPED_TODAY)


def mark_abandoned(task: TaskProgress) -> TaskProgress:
    """除 COMPLETED 外均可进入 ABANDONED；已完成任务保持 COMPLETED。"""
    _require_task(task)
    if task.state == TaskState.COMPLETED:
        return task
    return _rebuild(task, state=TaskState.ABANDONED)


def mark_completed(task: TaskProgress) -> TaskProgress:
    """显式标记完成。

    选择并固定的语义：显式“完成”是用户事实，当 total 已知时把 completed 提升到
    total（不低于已有 completed）；total 未知时只改 state，completed 保留当前值。
    已经 COMPLETED 的任务保持不变（不会把超过 total 的真实投入 clamp 回去）。
    """
    _require_task(task)
    if task.state == TaskState.COMPLETED:
        return task
    if task.total_minutes is not None:
        completed = max(task.completed_minutes, task.total_minutes)
    else:
        completed = task.completed_minutes
    return _rebuild(task, completed_minutes=completed, state=TaskState.COMPLETED)


def resume_today(task: TaskProgress) -> TaskProgress:
    """SKIPPED_TODAY -> ACTIVE；只允许恢复“今天跳过”的任务。

    COMPLETED / ABANDONED 不得复活（保持原状态）；ACTIVE 收到该动作是安全 no-op。
    """
    _require_task(task)
    if task.state != TaskState.SKIPPED_TODAY:
        return task
    return _rebuild(task, state=TaskState.ACTIVE)


def is_task_schedulable_today(task: TaskProgress) -> bool:

    """只有 ACTIVE 任务可以进入当天新的窗口安排。

    这条是防止 COMPLETED / SKIPPED_TODAY / ABANDONED 任务在后续轮次复活的重要底层护栏。
    """
    _require_task(task)
    return task.state == TaskState.ACTIVE


def _require_task(task: TaskProgress) -> None:
    if not isinstance(task, TaskProgress):
        raise TypeError("task must be a TaskProgress")


def _rebuild(task: TaskProgress, **overrides) -> TaskProgress:
    values = {
        "task_ref": task.task_ref,
        "title": task.title,
        "total_minutes": task.total_minutes,
        "completed_minutes": task.completed_minutes,
        "total_source": task.total_source,
        "state": task.state,
        "is_splittable": task.is_splittable,
        "minimum_slice_minutes": task.minimum_slice_minutes,
    }
    values.update(overrides)
    return TaskProgress(**values)


def _require_positive_int_minutes(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("{} must be an integer, got {}".format(name, type(value).__name__))
    if value <= 0:
        raise ValueError("{} must be > 0".format(name))
