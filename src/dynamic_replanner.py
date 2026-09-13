"""结构化变化指令的本地动态重规划核心。

本模块只接受已结构化的 ``ReplanningUpdate``，不理解“排队了”
等自然语言，不自动识别已完成任务，不支持跨天时间，不接入网页
或实时定位，不修改现有路线算法，并仍受最多 8 个任务的限制。
"""

import re
from dataclasses import dataclass
from typing import List, Optional, Set, Tuple

from src.campus_map import CampusMap
from src.input_validator import InputValidator
from src.models import PlanningRequest, Task
from src.route_plan import RoutePlanResult
from src.schedule_checker import ScheduleCheckResult
from src.task_selector import (
    MAX_TASKS,
    TaskSelectionError,
    TaskSelectionResult,
    select_feasible_tasks,
)


class DynamicReplanningError(Exception):
    """动态重规划的安全中文业务错误。"""


@dataclass(frozen=True)
class ReplanningUpdate:
    new_current_location: Optional[str] = None
    delay_minutes: int = 0
    cancelled_task_locations: Tuple[str, ...] = ()
    added_tasks: Tuple[Task, ...] = ()


@dataclass(frozen=True)
class DynamicReplanningResult:
    updated_request: PlanningRequest
    task_selection_result: TaskSelectionResult
    cancelled_tasks: Tuple[Task, ...]
    added_tasks: Tuple[Task, ...]
    previous_current_time: str
    updated_current_time: str
    message: str


_TIME_PATTERN = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")


def _parse_time(value: object) -> int:
    if not isinstance(value, str) or _TIME_PATTERN.fullmatch(value) is None:
        raise DynamicReplanningError("原规划的当前时间必须是合法的 HH:MM。")
    hours, minutes = (int(part) for part in value.split(":"))
    return hours * 60 + minutes


def _format_time(total_minutes: int) -> str:
    return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def _resolve_location(
    campus_map: CampusMap, value: str, error_message: str
) -> str:
    location_id = campus_map.resolve_location_id(value)
    if location_id is None:
        raise DynamicReplanningError(error_message)
    return location_id


def _validate_request_allow_empty(
    request: PlanningRequest, context_message: str
) -> None:
    if not isinstance(request, PlanningRequest):
        raise DynamicReplanningError(context_message)
    if not isinstance(request.current_location, str) or not request.current_location.strip():
        raise DynamicReplanningError(context_message)
    if not isinstance(request.destination, str) or not request.destination.strip():
        raise DynamicReplanningError(context_message)
    try:
        _parse_time(request.current_time)
    except DynamicReplanningError as exc:
        raise DynamicReplanningError(context_message) from exc
    if not isinstance(request.tasks, list):
        raise DynamicReplanningError(context_message)
    if not request.tasks:
        return
    is_valid, _ = InputValidator.validate(request)
    if not is_valid:
        raise DynamicReplanningError(context_message)


def _resolved_original_tasks(
    campus_map: CampusMap, tasks: List[Task]
) -> Tuple[Tuple[Task, str], ...]:
    resolved = []
    seen: Set[str] = set()
    for task in tasks:
        location_id = _resolve_location(
            campus_map,
            task.location,
            "原规划中存在未知任务地点。",
        )
        if location_id in seen:
            raise DynamicReplanningError("原规划中存在重复任务地点。")
        seen.add(location_id)
        resolved.append((task, location_id))
    return tuple(resolved)


def replan_with_update(
    campus_map: CampusMap,
    planning_request: PlanningRequest,
    update: ReplanningUpdate,
) -> DynamicReplanningResult:
    """应用结构化变化并复用任务取舍器重新规划。"""
    if not isinstance(campus_map, CampusMap):
        raise DynamicReplanningError("校园地图格式无效。")
    if not isinstance(planning_request, PlanningRequest):
        raise DynamicReplanningError("原规划请求格式无效。")
    if not isinstance(update, ReplanningUpdate):
        raise DynamicReplanningError("重规划更新指令格式无效。")
    if type(update.delay_minutes) is not int or update.delay_minutes < 0:
        raise DynamicReplanningError("额外延误分钟必须是非负整数。")
    if not isinstance(update.cancelled_task_locations, tuple):
        raise DynamicReplanningError("取消任务地点必须是元组。")
    if not isinstance(update.added_tasks, tuple):
        raise DynamicReplanningError("新增任务必须是元组。")

    _validate_request_allow_empty(
        planning_request, "原规划请求无效，请检查输入信息。"
    )
    resolved_original = _resolved_original_tasks(
        campus_map, planning_request.tasks
    )

    previous_current_time = planning_request.current_time
    updated_minutes = _parse_time(previous_current_time) + update.delay_minutes
    if updated_minutes >= 24 * 60:
        raise DynamicReplanningError("当前版本不支持跨天动态重规划。")
    updated_current_time = _format_time(updated_minutes)

    if update.new_current_location is None:
        updated_current_location = planning_request.current_location
    else:
        if (
            not isinstance(update.new_current_location, str)
            or not update.new_current_location.strip()
        ):
            raise DynamicReplanningError("新当前位置必须是非空字符串。")
        updated_current_location = _resolve_location(
            campus_map,
            update.new_current_location,
            "无法识别新当前位置。",
        )

    cancelled_ids: List[str] = []
    seen_cancelled: Set[str] = set()
    for value in update.cancelled_task_locations:
        if not isinstance(value, str) or not value.strip():
            raise DynamicReplanningError("取消任务地点必须是非空字符串。")
        location_id = _resolve_location(
            campus_map, value, "取消指令中存在未知任务地点。"
        )
        if location_id in seen_cancelled:
            raise DynamicReplanningError("不能重复取消同一任务地点。")
        seen_cancelled.add(location_id)
        cancelled_ids.append(location_id)

    original_ids = {location_id for _, location_id in resolved_original}
    if any(location_id not in original_ids for location_id in cancelled_ids):
        raise DynamicReplanningError("要取消的地点没有对应的现有任务。")

    cancelled_id_set = set(cancelled_ids)
    cancelled_tasks = tuple(
        task
        for task, location_id in resolved_original
        if location_id in cancelled_id_set
    )
    remaining_pairs = tuple(
        (task, location_id)
        for task, location_id in resolved_original
        if location_id not in cancelled_id_set
    )

    for task in update.added_tasks:
        if not isinstance(task, Task):
            raise DynamicReplanningError("新增任务格式无效。")

    if len(remaining_pairs) + len(update.added_tasks) > MAX_TASKS:
        raise DynamicReplanningError("更新后最多支持8个任务。")

    final_location_ids = {location_id for _, location_id in remaining_pairs}
    for task in update.added_tasks:
        if not isinstance(task.location, str) or not task.location.strip():
            raise DynamicReplanningError("新增任务的地点必须是非空字符串。")
        location_id = _resolve_location(
            campus_map, task.location, "新增任务中存在未知地点。"
        )
        if location_id in final_location_ids:
            raise DynamicReplanningError("更新后不能存在重复任务地点。")
        final_location_ids.add(location_id)

    updated_tasks = [task for task, _ in remaining_pairs] + list(
        update.added_tasks
    )
    updated_request = PlanningRequest(
        current_location=updated_current_location,
        destination=planning_request.destination,
        current_time=updated_current_time,
        tasks=updated_tasks,
    )
    _validate_request_allow_empty(
        updated_request, "更新后的规划请求无效，请检查任务信息。"
    )

    try:
        task_selection_result = select_feasible_tasks(
            campus_map, updated_request
        )
    except TaskSelectionError as exc:
        raise DynamicReplanningError(
            "动态重规划无法生成任务方案。"
        ) from exc

    if (
        not isinstance(task_selection_result, TaskSelectionResult)
        or not isinstance(task_selection_result.route_plan, RoutePlanResult)
        or not isinstance(
            task_selection_result.schedule_result, ScheduleCheckResult
        )
        or type(task_selection_result.is_feasible) is not bool
        or task_selection_result.is_feasible
        is not task_selection_result.schedule_result.is_feasible
    ):
        raise DynamicReplanningError("动态重规划结果异常，请稍后重试。")

    if task_selection_result.is_feasible:
        message = (
            "动态重规划完成，已保留必须任务并放弃部分可选任务。"
            if task_selection_result.dropped_tasks
            else "动态重规划完成，更新后的全部任务均可行。"
        )
    else:
        message = "动态重规划完成，但必须任务仍无法满足截止时间。"

    return DynamicReplanningResult(
        updated_request=updated_request,
        task_selection_result=task_selection_result,
        cancelled_tasks=cancelled_tasks,
        added_tasks=tuple(update.added_tasks),
        previous_current_time=previous_current_time,
        updated_current_time=updated_current_time,
        message=message,
    )
