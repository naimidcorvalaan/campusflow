"""检查既定路线中各任务的截止时间可行性。"""

import re
from dataclasses import dataclass
from typing import Optional, Tuple

from src.models import PlanningRequest, Task
from src.route_plan import RoutePlanResult


class ScheduleCheckError(Exception):
    """日程可行性检查中的可预期输入或结构错误。"""


@dataclass(frozen=True)
class ScheduledTask:
    task: Task
    location_id: str
    arrival_time: str
    finish_time: str
    deadline: Optional[str]
    is_deadline_met: Optional[bool]


@dataclass(frozen=True)
class ScheduleCheckResult:
    is_feasible: bool
    start_time: str
    finish_time: str
    scheduled_tasks: Tuple[ScheduledTask, ...]
    missed_deadlines: Tuple[ScheduledTask, ...]


def _parse_time(value: object, field_name: str) -> int:
    if not isinstance(value, str) or re.fullmatch(r"\d{2}:\d{2}", value) is None:
        raise ScheduleCheckError(f"{field_name} 必须使用 HH:MM 格式。")

    hours, minutes = (int(part) for part in value.split(":"))
    if not 0 <= hours <= 23 or not 0 <= minutes <= 59:
        raise ScheduleCheckError(f"{field_name} 必须是当天有效的 HH:MM 时间。")
    return hours * 60 + minutes


def _format_time(total_minutes: int) -> str:
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours:02d}:{minutes:02d}"


def check_schedule_feasibility(
    route_plan: RoutePlanResult,
    planning_request: PlanningRequest,
) -> ScheduleCheckResult:
    """沿既定路线推进时间，并检查任务完成时刻是否超过截止时间。

    该函数不重新排序、不取舍任务，也不执行动态重规划。超过午夜后继续
    使用累计分钟数，并显示为 24:10、25:05 等扩展小时格式。
    """
    if not isinstance(route_plan, RoutePlanResult):
        raise ScheduleCheckError("route_plan 必须是有效的 RoutePlanResult。")
    if not isinstance(planning_request, PlanningRequest):
        raise ScheduleCheckError("planning_request 必须是有效的 PlanningRequest。")

    start_minutes = _parse_time(
        planning_request.current_time, "current_time"
    )
    tasks = route_plan.tasks
    visit_order = route_plan.visit_order
    segments = route_plan.segments

    if len(visit_order) != len(tasks) + 2:
        raise ScheduleCheckError("route_plan.tasks 与 visit_order 对不上。")
    if len(segments) != len(visit_order) - 1:
        raise ScheduleCheckError("route_plan.segments 与 visit_order 对不上。")
    if len(planning_request.tasks) != len(tasks) or {
        id(task) for task in planning_request.tasks
    } != {id(task) for task in tasks}:
        raise ScheduleCheckError("route_plan.tasks 与 planning_request.tasks 对不上。")

    for index, segment in enumerate(segments):
        if (
            segment.start_id != visit_order[index]
            or segment.destination_id != visit_order[index + 1]
        ):
            raise ScheduleCheckError("route_plan.segments 与 visit_order 对不上。")

    task_location_ids = tuple(visit_order[1:-1])
    normalized_task_location_ids = {
        location_id.strip().casefold() for location_id in task_location_ids
    }
    for index, task in enumerate(tasks):
        if not isinstance(task, Task):
            raise ScheduleCheckError(f"第{index + 1}个任务不是有效的 Task。")
        if isinstance(task.location, str):
            normalized_location = task.location.strip().casefold()
            expected_location = task_location_ids[index].strip().casefold()
            if (
                normalized_location in normalized_task_location_ids
                and normalized_location != expected_location
            ):
                raise ScheduleCheckError("路线计划中的任务地点与访问顺序不一致。")

    current_minutes = start_minutes
    scheduled_tasks = []
    missed_deadlines = []

    for index, task in enumerate(tasks):
        if (
            type(task.estimated_duration_minutes) is not int
            or task.estimated_duration_minutes < 0
        ):
            raise ScheduleCheckError(
                f"第{index + 1}个任务的预计停留时间必须是非负整数。"
            )

        current_minutes += segments[index].total_minutes
        arrival_time = _format_time(current_minutes)
        current_minutes += task.estimated_duration_minutes
        finish_time = _format_time(current_minutes)

        deadline_minutes = None
        is_deadline_met = None
        if task.deadline is not None:
            deadline_minutes = _parse_time(
                task.deadline, f"第{index + 1}个任务的 deadline"
            )
            is_deadline_met = current_minutes <= deadline_minutes

        scheduled_task = ScheduledTask(
            task=task,
            location_id=visit_order[index + 1],
            arrival_time=arrival_time,
            finish_time=finish_time,
            deadline=task.deadline,
            is_deadline_met=is_deadline_met,
        )
        scheduled_tasks.append(scheduled_task)
        if is_deadline_met is False:
            missed_deadlines.append(scheduled_task)

    current_minutes += segments[-1].total_minutes

    return ScheduleCheckResult(
        is_feasible=not missed_deadlines,
        start_time=_format_time(start_minutes),
        finish_time=_format_time(current_minutes),
        scheduled_tasks=tuple(scheduled_tasks),
        missed_deadlines=tuple(missed_deadlines),
    )
