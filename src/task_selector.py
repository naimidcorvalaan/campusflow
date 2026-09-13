"""在保留全部必须任务的前提下选择可行的可选任务子集。

本模块复用现有 ``build_route_plan`` 和 ``check_schedule_feasibility``。
路线访问顺序仍由“总步行时间最短”的既有策略决定，不会为了满足 deadline
改用更长路线或重新排序；本阶段也不进行动态重规划。
"""

from dataclasses import dataclass
from itertools import combinations
from typing import List, Optional, Tuple

from src.campus_map import CampusMap
from src.models import PlanningRequest, Task
from src.route_plan import RoutePlanError, RoutePlanResult, build_route_plan
from src.schedule_checker import (
    ScheduleCheckError,
    ScheduleCheckResult,
    check_schedule_feasibility,
)


MAX_TASKS = 8


class TaskSelectionError(Exception):
    """任务取舍过程中可预期的中文错误。"""


@dataclass(frozen=True)
class TaskSelectionResult:
    is_feasible: bool
    kept_tasks: Tuple[Task, ...]
    dropped_tasks: Tuple[Task, ...]
    route_plan: RoutePlanResult
    schedule_result: ScheduleCheckResult
    message: str


def _evaluate_candidate(
    campus_map: CampusMap,
    planning_request: PlanningRequest,
    candidate_tasks: List[Task],
) -> Tuple[RoutePlanResult, ScheduleCheckResult]:
    candidate_request = PlanningRequest(
        current_location=planning_request.current_location,
        destination=planning_request.destination,
        current_time=planning_request.current_time,
        tasks=list(candidate_tasks),
    )
    try:
        route_plan = build_route_plan(campus_map, candidate_request)
        schedule_result = check_schedule_feasibility(
            route_plan, candidate_request
        )
    except RoutePlanError as exc:
        raise TaskSelectionError(
            "任务方案的路线无法生成，请检查地图和任务地点。"
        ) from exc
    except ScheduleCheckError as exc:
        raise TaskSelectionError(
            "任务方案的时间安排无法检查，请检查任务时间信息。"
        ) from exc
    return route_plan, schedule_result


def select_feasible_tasks(
    campus_map: CampusMap,
    planning_request: PlanningRequest,
) -> TaskSelectionResult:
    """枚举可选任务子集并按保留数量、总耗时和组合顺序选出方案。"""
    if not isinstance(campus_map, CampusMap):
        raise TaskSelectionError("campus_map 必须是有效的 CampusMap。")
    if not isinstance(planning_request, PlanningRequest):
        raise TaskSelectionError("planning_request 必须是有效的 PlanningRequest。")
    if not isinstance(planning_request.tasks, list):
        raise TaskSelectionError("planning_request.tasks 必须是列表。")
    if len(planning_request.tasks) > MAX_TASKS:
        raise TaskSelectionError("当前版本最多支持8个任务。")

    mandatory_tasks = []
    optional_tasks = []
    for index, task in enumerate(planning_request.tasks):
        if not isinstance(task, Task):
            raise TaskSelectionError(f"第{index + 1}个任务必须是有效的 Task。")
        if type(task.is_mandatory) is not bool:
            raise TaskSelectionError(
                f"第{index + 1}个任务的 is_mandatory 必须严格为布尔值。"
            )
        if task.is_mandatory is True:
            mandatory_tasks.append(task)
        else:
            optional_tasks.append(task)

    mandatory_ids = {id(task) for task in mandatory_tasks}
    optional_position = {
        id(task): index for index, task in enumerate(optional_tasks)
    }
    mandatory_fallback: Optional[
        Tuple[RoutePlanResult, ScheduleCheckResult]
    ] = None

    for optional_count in range(len(optional_tasks), -1, -1):
        best_for_count: Optional[
            Tuple[RoutePlanResult, ScheduleCheckResult, Tuple[Task, ...]]
        ] = None

        for optional_indexes in combinations(
            range(len(optional_tasks)), optional_count
        ):
            selected_optional_ids = {
                id(optional_tasks[index]) for index in optional_indexes
            }
            candidate_tasks = [
                task
                for task in planning_request.tasks
                if id(task) in mandatory_ids
                or id(task) in selected_optional_ids
            ]
            route_plan, schedule_result = _evaluate_candidate(
                campus_map, planning_request, candidate_tasks
            )

            if optional_count == 0:
                mandatory_fallback = (route_plan, schedule_result)

            if not schedule_result.is_feasible:
                continue

            selected_optional = tuple(
                optional_tasks[index] for index in optional_indexes
            )
            if (
                best_for_count is None
                or route_plan.total_minutes
                < best_for_count[0].total_minutes
            ):
                best_for_count = (
                    route_plan,
                    schedule_result,
                    selected_optional,
                )

        if best_for_count is not None:
            route_plan, schedule_result, selected_optional = best_for_count
            selected_optional_ids = {id(task) for task in selected_optional}
            dropped_tasks = tuple(
                task
                for task in planning_request.tasks
                if id(task) in optional_position
                and id(task) not in selected_optional_ids
            )
            return TaskSelectionResult(
                is_feasible=True,
                kept_tasks=tuple(route_plan.tasks),
                dropped_tasks=dropped_tasks,
                route_plan=route_plan,
                schedule_result=schedule_result,
                message=(
                    "全部任务均可按时完成。"
                    if not dropped_tasks
                    else f"已放弃{len(dropped_tasks)}个可选任务，保留可行方案。"
                ),
            )

    if mandatory_fallback is None:
        raise TaskSelectionError("无法生成必须任务方案。")

    route_plan, schedule_result = mandatory_fallback
    return TaskSelectionResult(
        is_feasible=False,
        kept_tasks=tuple(route_plan.tasks),
        dropped_tasks=tuple(
            task
            for task in planning_request.tasks
            if task.is_mandatory is False
        ),
        route_plan=route_plan,
        schedule_result=schedule_result,
        message="只保留全部必须任务后仍不可行，必须任务不会被删除。",
    )
