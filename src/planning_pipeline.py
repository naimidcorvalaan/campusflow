"""从自然语言输入到路线与日程检查的端到端规划入口。"""

from dataclasses import dataclass
from typing import Optional

from src.campus_map import CampusMap
from src.extraction_models import ParseResult
from src.natural_language_parser import parse_natural_language
from src.route_plan import RoutePlanResult
from src.schedule_checker import ScheduleCheckResult
from src.task_selector import (
    TaskSelectionError,
    TaskSelectionResult,
    select_feasible_tasks,
)


@dataclass(frozen=True)
class PlanningPipelineResult:
    parse_result: ParseResult
    route_plan: Optional[RoutePlanResult]
    schedule_result: Optional[ScheduleCheckResult]
    status: str
    message: str
    task_selection_result: Optional[TaskSelectionResult] = None


def plan_from_text(
    campus_map: CampusMap,
    user_text: str,
    current_time: str,
) -> PlanningPipelineResult:
    """依次执行自然语言解析、路线计划和截止时间可行性检查。"""
    parse_result = parse_natural_language(user_text, current_time)

    if parse_result.status == "needs_clarification":
        return PlanningPipelineResult(
            parse_result=parse_result,
            route_plan=None,
            schedule_result=None,
            status="needs_clarification",
            message="需要补充任务信息。",
            task_selection_result=None,
        )

    if parse_result.status == "rejected":
        return PlanningPipelineResult(
            parse_result=parse_result,
            route_plan=None,
            schedule_result=None,
            status="error",
            message="任务信息解析失败，请检查输入后重试。",
            task_selection_result=None,
        )

    if parse_result.status != "ok":
        return PlanningPipelineResult(
            parse_result=parse_result,
            route_plan=None,
            schedule_result=None,
            status="error",
            message="任务信息解析结果异常，请稍后重试。",
            task_selection_result=None,
        )

    planning_request = parse_result.planning_request
    if planning_request is None:
        return PlanningPipelineResult(
            parse_result=parse_result,
            route_plan=None,
            schedule_result=None,
            status="error",
            message="解析失败：解析结果缺少规划请求。",
            task_selection_result=None,
        )

    try:
        task_selection_result = select_feasible_tasks(
            campus_map, planning_request
        )
    except TaskSelectionError:
        return PlanningPipelineResult(
            parse_result=parse_result,
            route_plan=None,
            schedule_result=None,
            status="error",
            message="任务方案无法生成，请检查地点和时间信息。",
            task_selection_result=None,
        )

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
        return PlanningPipelineResult(
            parse_result=parse_result,
            route_plan=None,
            schedule_result=None,
            status="error",
            message="任务选择结果异常，请稍后重试。",
            task_selection_result=None,
        )

    route_plan = task_selection_result.route_plan
    schedule_result = task_selection_result.schedule_result
    if task_selection_result.is_feasible:
        status = "ok"
        message = (
            "已保留全部必须任务，并放弃部分可选任务。"
            if task_selection_result.dropped_tasks
            else "规划成功，全部任务均可按时完成。"
        )
    else:
        status = "infeasible"
        message = "只保留全部必须任务后仍无法满足截止时间。"

    return PlanningPipelineResult(
        parse_result=parse_result,
        route_plan=route_plan,
        schedule_result=schedule_result,
        status=status,
        message=message,
        task_selection_result=task_selection_result,
    )
