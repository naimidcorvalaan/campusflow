"""将任务停留时间加入多任务路线结果。"""

from dataclasses import dataclass
from typing import Dict, Tuple

from src.campus_map import CampusMap
from src.models import PlanningRequest, Task
from src.route_ordering import (
    RouteOrderingError,
    find_optimal_route,
)
from src.shortest_path import ShortestPathResult


class RoutePlanError(Exception):
    """路线计划构建中的可预期输入、地图或任务错误。"""


@dataclass(frozen=True)
class RoutePlanResult:
    visit_order: Tuple[str, ...]
    walking_minutes: int
    stay_minutes: int
    total_minutes: int
    segments: Tuple[ShortestPathResult, ...]
    tasks: Tuple[Task, ...]


def build_route_plan(
    campus_map: CampusMap,
    planning_request: PlanningRequest,
) -> RoutePlanResult:
    """按最短访问顺序组织任务，并汇总步行时间与任务停留时间。

    本阶段不使用任务截止时间和必选属性，也不进行任务取舍或动态重规划。
    同一解析地点存在多个任务时会被拒绝。
    """
    if not isinstance(campus_map, CampusMap):
        raise RoutePlanError("campus_map 必须是有效的 CampusMap。")
    if not isinstance(planning_request, PlanningRequest):
        raise RoutePlanError("planning_request 必须是有效的 PlanningRequest。")
    if not isinstance(planning_request.tasks, list):
        raise RoutePlanError("planning_request.tasks 必须是列表。")

    for index, task in enumerate(planning_request.tasks):
        if not isinstance(task, Task):
            raise RoutePlanError(f"第{index + 1}个任务必须是有效的 Task。")
        if (
            type(task.estimated_duration_minutes) is not int
            or task.estimated_duration_minutes < 0
        ):
            raise RoutePlanError(
                f"第{index + 1}个任务的预计停留时间必须是非负整数。"
            )

    task_locations = [task.location for task in planning_request.tasks]
    try:
        ordered_route = find_optimal_route(
            campus_map=campus_map,
            current_location=planning_request.current_location,
            destination=planning_request.destination,
            task_locations=task_locations,
        )
    except RouteOrderingError as exc:
        raise RoutePlanError(f"无法构建路线计划：{exc}") from exc

    task_by_location_id: Dict[str, Task] = {}
    for task in planning_request.tasks:
        location_id = campus_map.resolve_location_id(task.location)
        if location_id is None:
            raise RoutePlanError(f"无法识别任务地点：{task.location}")
        if location_id in task_by_location_id:
            raise RoutePlanError(f"同一地点不能安排多个任务：{task.location}")
        task_by_location_id[location_id] = task

    ordered_task_ids = ordered_route.visit_order[1:-1]
    try:
        ordered_tasks = tuple(
            task_by_location_id[location_id] for location_id in ordered_task_ids
        )
    except KeyError as exc:
        raise RoutePlanError("任务顺序与路线访问顺序不一致。") from exc

    walking_minutes = ordered_route.total_minutes
    stay_minutes = sum(
        task.estimated_duration_minutes for task in ordered_tasks
    )

    return RoutePlanResult(
        visit_order=ordered_route.visit_order,
        walking_minutes=walking_minutes,
        stay_minutes=stay_minutes,
        total_minutes=walking_minutes + stay_minutes,
        segments=ordered_route.segments,
        tasks=ordered_tasks,
    )
