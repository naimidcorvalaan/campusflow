"""串联结构化变化解析与动态重规划。

本模块只负责调用现有解析器和动态重规划核心，不自行解析 JSON、更新任务、
计算路线或接入网页。异常边界仅处理明确的业务异常。
"""

from dataclasses import dataclass
from typing import Optional

from src.campus_map import CampusMap
from src.dynamic_replanner import (
    DynamicReplanningError,
    DynamicReplanningResult,
    ReplanningUpdate,
    replan_with_update,
)
from src.models import PlanningRequest
from src.replanning_parser import ReplanningParseResult, parse_replanning_text
from src.route_plan import RoutePlanResult
from src.schedule_checker import ScheduleCheckResult
from src.task_selector import TaskSelectionResult


@dataclass(frozen=True)
class DynamicPlanningPipelineResult:
    parse_result: ReplanningParseResult
    replanning_result: Optional[DynamicReplanningResult]
    status: str
    message: str


def _result_error(parse_result: ReplanningParseResult, message: str) -> DynamicPlanningPipelineResult:
    return DynamicPlanningPipelineResult(parse_result, None, "error", message)


def _internal_parse_error() -> ReplanningParseResult:
    return ReplanningParseResult(
        status="rejected",
        error_type="validation_error",
        message="计划变化解析结果异常，请稍后重试。",
    )


def _validate_replanning_result(result: object) -> bool:
    if not isinstance(result, DynamicReplanningResult):
        return False
    selection = result.task_selection_result
    if not isinstance(selection, TaskSelectionResult):
        return False
    if not isinstance(selection.route_plan, RoutePlanResult):
        return False
    if not isinstance(selection.schedule_result, ScheduleCheckResult):
        return False
    if type(selection.is_feasible) is not bool:
        return False
    if type(selection.schedule_result.is_feasible) is not bool:
        return False
    if selection.is_feasible != selection.schedule_result.is_feasible:
        return False
    if not isinstance(result.updated_request, PlanningRequest):
        return False
    if result.updated_current_time != result.updated_request.current_time:
        return False
    return True


def replan_from_text(
    campus_map: CampusMap,
    planning_request: PlanningRequest,
    change_text: str,
) -> DynamicPlanningPipelineResult:
    """解析变化文本并执行一次动态重规划。"""
    parse_result = parse_replanning_text(change_text, planning_request)
    if not isinstance(parse_result, ReplanningParseResult):
        return _result_error(
            _internal_parse_error(), "计划变化解析结果异常，请稍后重试。"
        )

    if parse_result.status == "needs_clarification":
        return DynamicPlanningPipelineResult(
            parse_result, None, "needs_clarification", "需要补充计划变化信息。"
        )
    if parse_result.status == "rejected":
        return _result_error(parse_result, "计划变化解析失败，请检查输入后重试。")
    if parse_result.status != "ok":
        return _result_error(parse_result, "计划变化解析结果异常，请稍后重试。")
    if not isinstance(parse_result.update, ReplanningUpdate):
        return _result_error(parse_result, "计划变化结果不完整，请稍后重试。")

    try:
        replanning_result = replan_with_update(
            campus_map, planning_request, parse_result.update
        )
    except DynamicReplanningError as exc:
        return _result_error(
            parse_result, "动态重规划失败，请检查位置、时间和任务变化。"
        )

    if not _validate_replanning_result(replanning_result):
        return _result_error(parse_result, "动态重规划结果异常，请稍后重试。")
    if replanning_result.task_selection_result.is_feasible:
        return DynamicPlanningPipelineResult(
            parse_result, replanning_result, "ok", "动态重规划完成。"
        )
    return DynamicPlanningPipelineResult(
        parse_result,
        replanning_result,
        "infeasible",
        "动态重规划完成，但必须任务仍无法满足截止时间。",
    )
