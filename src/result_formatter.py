"""将规划管线结果格式化为确定性的中文展示文本。"""

import re
from typing import Optional

from src.models import Task
from src.dynamic_planning_pipeline import DynamicPlanningPipelineResult
from src.planning_pipeline import PlanningPipelineResult
from src.schedule_checker import ScheduledTask


MAX_DISPLAY_TEXT_LENGTH = 200
HIDDEN_TEXT_MARKERS = (
    "task(",
    "taskselectionresult(",
    "routeplanresult(",
    "schedulecheckresult(",
    "planningpipelineresult(",
    "is_mandatory=",
)


def _time_to_minutes(value: Optional[str]) -> Optional[int]:
    if not isinstance(value, str):
        return None
    parts = value.split(":")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        return None
    hours, minutes = (int(part) for part in parts)
    if minutes > 59:
        return None
    return hours * 60 + minutes


def _deadline_status(scheduled_task: ScheduledTask) -> str:
    if scheduled_task.deadline is None:
        return "截止：无截止时间，状态：无需检查"
    deadline_text = _single_line_text(scheduled_task.deadline)
    if scheduled_task.is_deadline_met is True:
        return f"截止：{deadline_text}，状态：可按时完成"
    if scheduled_task.is_deadline_met is False:
        finish = _time_to_minutes(scheduled_task.finish_time)
        deadline = _time_to_minutes(scheduled_task.deadline)
        if finish is not None and deadline is not None and finish > deadline:
            return (
                f"截止：{deadline_text}，"
                f"状态：超时 {finish - deadline} 分钟"
            )
        return f"截止：{deadline_text}，状态：已超时"
    return f"截止：{deadline_text}，状态：未检查"


def _safe_error_message(message: object) -> str:
    if not isinstance(message, str) or not message.strip():
        return "请检查输入信息后重试。"

    first_line = message.strip().splitlines()[0]
    lowered = first_line.casefold()
    unsafe_markers = (
        "traceback",
        "api key",
        "apikey",
        "api_key",
        "api-key",
        "authorization",
        "bearer",
        "planningpipelineresult(",
        "dynamicplanningpipelineresult(",
        "dynamicreplanningresult(",
        "replanningparseresult(",
        "taskselectionresult(",
        "routeplanresult(",
        "schedulecheckresult(",
        "task(",
    )
    if (
        any(marker in lowered for marker in unsafe_markers)
        or any(character in first_line for character in "{}[]")
        or len(first_line) > 200
    ):
        return "请检查输入信息后重试。"
    return _single_line_text(first_line)


def _single_line_text(value: object) -> str:
    """将用户提供的字段整理为不修改原对象的单行文本。"""
    if not isinstance(value, str):
        return "未说明"
    cleaned = re.sub(r"\s+", " ", value).strip()
    if not cleaned:
        return "未说明"
    lowered = cleaned.casefold()
    if any(marker in lowered for marker in HIDDEN_TEXT_MARKERS):
        return "内容已隐藏"
    if len(cleaned) > MAX_DISPLAY_TEXT_LENGTH:
        return cleaned[: MAX_DISPLAY_TEXT_LENGTH - 2] + "……"
    return cleaned


def _task_type_label(value: object) -> str:
    if value is True:
        return "必须"
    if value is False:
        return "可选"
    return "任务属性异常"


def _format_task_line(task: Task) -> str:
    label = _task_type_label(task.is_mandatory)
    return (
        f"- {_single_line_text(task.location)}："
        f"{_single_line_text(task.description)}（{label}）"
    )


def _format_task_selection(result: PlanningPipelineResult) -> list:
    selection = result.task_selection_result
    if selection is None:
        return []

    lines = []
    if result.status == "ok" and not selection.dropped_tasks:
        return ["全部任务均已保留。"]

    if result.status == "ok":
        lines.append("为满足截止时间，已调整任务计划。")
    elif selection.dropped_tasks:
        lines.append("已放弃全部可选任务，但必须任务仍无法满足截止时间。")
    else:
        lines.append("必须任务仍无法满足截止时间。")

    if selection.kept_tasks:
        lines.append("保留任务：")
        lines.extend(_format_task_line(task) for task in selection.kept_tasks)
    else:
        lines.append("保留任务：无")

    if selection.dropped_tasks:
        lines.append("暂时放弃的可选任务：")
        lines.extend(
            _format_task_line(task) for task in selection.dropped_tasks
        )
    return lines


def _format_route_details(result: PlanningPipelineResult) -> str:
    route_plan = result.route_plan
    schedule_result = result.schedule_result
    if route_plan is None or schedule_result is None:
        return "规划结果不完整，无法展示路线详情。"

    lines = [
        f"推荐路线：{' → '.join(route_plan.visit_order)}",
        f"预计步行：{route_plan.walking_minutes} 分钟",
        f"任务停留：{route_plan.stay_minutes} 分钟",
        f"预计总耗时：{route_plan.total_minutes} 分钟",
    ]

    if schedule_result.scheduled_tasks:
        lines.append("任务安排：")
        for index, scheduled_task in enumerate(
            schedule_result.scheduled_tasks, start=1
        ):
            description = _single_line_text(
                scheduled_task.task.description
            )
            task_label = (
                f"，任务：{description}"
                if description != "未说明"
                else ""
            )
            lines.append(
                f"{index}. 到达 {_single_line_text(scheduled_task.location_id)}："
                f"{scheduled_task.arrival_time}，"
                f"完成：{scheduled_task.finish_time}，"
                f"{_deadline_status(scheduled_task)}{task_label}"
            )
    else:
        lines.append("任务安排：无")

    if result.status == "infeasible":
        lines.insert(0, "路线仍可生成，但存在超时任务。")
        lines.append("超时任务：")
        for scheduled_task in schedule_result.missed_deadlines:
            lines.append(
                f"- {_single_line_text(scheduled_task.location_id)}："
                f"{_deadline_status(scheduled_task).split('状态：', 1)[-1]}"
            )

    selection_lines = _format_task_selection(result)
    return "\n".join(selection_lines + lines)


def format_planning_result(result: PlanningPipelineResult) -> str:
    """按照管线状态生成不依赖大模型的中文展示文本。"""
    if not isinstance(result, PlanningPipelineResult):
        return "规划结果格式错误，无法展示。"

    if result.status in ("ok", "infeasible"):
        return _format_route_details(result)

    if result.status == "needs_clarification":
        lines = [
            result.message.strip()
            if isinstance(result.message, str) and result.message.strip()
            else "需要补充信息。"
        ]
        questions = [
            question.strip()
            for question in result.parse_result.questions
            if isinstance(question, str) and question.strip()
        ]
        if questions:
            lines.append("请补充：")
            lines.extend(
                f"{index}. {question}"
                for index, question in enumerate(questions, start=1)
            )
        return "\n".join(lines)

    if result.status == "error":
        return f"规划失败：{_safe_error_message(result.message)}"

    return "规划结果状态未知，无法展示。"
def format_dynamic_planning_result(result: DynamicPlanningPipelineResult) -> str:
    """以确定性中文模板展示动态重规划结果。"""
    from src.dynamic_replanner import DynamicReplanningResult
    from src.route_plan import RoutePlanResult
    from src.schedule_checker import ScheduleCheckResult
    from src.task_selector import TaskSelectionResult
    if not isinstance(result, DynamicPlanningPipelineResult):
        return "动态规划结果不完整，无法展示。"
    if result.status == "needs_clarification":
        questions = [
            _single_line_text(question)
            for question in result.parse_result.questions
        ]
        lines = ["需要补充计划变化信息。"]
        lines.extend(
            ["请补充："] + [f"{i}. {question}" for i, question in enumerate(questions, 1)]
            if questions
            else ["请说明当前位置、延误、取消任务或新增任务中的变化。"]
        )
        return "\n".join(lines)
    if result.status == "error":
        return f"动态规划失败：{_safe_error_message(result.message)}"
    if result.status not in ("ok", "infeasible"):
        return "动态规划结果状态未知，无法展示。"
    dynamic = result.replanning_result
    if not isinstance(dynamic, DynamicReplanningResult):
        return "动态规划结果不完整，无法展示。"
    selection = dynamic.task_selection_result
    if not isinstance(selection, TaskSelectionResult):
        return "动态规划结果不完整，无法展示。"
    route_plan = selection.route_plan
    schedule = selection.schedule_result
    if not isinstance(route_plan, RoutePlanResult) or not isinstance(schedule, ScheduleCheckResult):
        return "动态规划结果不完整，无法展示。"
    if not isinstance(dynamic.cancelled_tasks, tuple) or not isinstance(dynamic.added_tasks, tuple):
        return "动态规划结果不完整，无法展示。"
    if not all(isinstance(t, Task) for t in dynamic.cancelled_tasks + dynamic.added_tasks):
        return "动态规划结果不完整，无法展示。"
    if not isinstance(selection.kept_tasks, tuple) or not isinstance(selection.dropped_tasks, tuple):
        return "动态规划结果不完整，无法展示。"
    if not all(isinstance(t, Task) for t in selection.kept_tasks + selection.dropped_tasks):
        return "动态规划结果不完整，无法展示。"
    if not isinstance(schedule.scheduled_tasks, tuple) or not isinstance(schedule.missed_deadlines, tuple):
        return "动态规划结果不完整，无法展示。"
    if not all(isinstance(t, ScheduledTask) for t in schedule.scheduled_tasks + schedule.missed_deadlines):
        return "动态规划结果不完整，无法展示。"
    lines = [
        "动态重规划完成。",
        "时间变化："
        f"{_single_line_text(dynamic.previous_current_time)} → "
        f"{_single_line_text(dynamic.updated_current_time)}",
    ]
    if result.status == "infeasible":
        lines.insert(1, "已放弃全部可选任务，但必须任务仍无法满足截止时间。")
    lines.append("已取消任务：无" if not dynamic.cancelled_tasks else "已取消任务：")
    lines.extend(_format_task_line(t) for t in dynamic.cancelled_tasks)
    lines.append("新增任务：无" if not dynamic.added_tasks else "新增任务：")
    lines.extend(_format_task_line(t) for t in dynamic.added_tasks)
    lines.append("保留任务：" if selection.kept_tasks else "保留任务：无")
    lines.extend(_format_task_line(t) for t in selection.kept_tasks)
    if selection.dropped_tasks:
        lines.append("暂时放弃的可选任务：")
        lines.extend(_format_task_line(t) for t in selection.dropped_tasks)
    lines.extend(
        [
            "推荐路线："
            f"{' → '.join(_single_line_text(location) for location in route_plan.visit_order)}",
            f"预计步行：{route_plan.walking_minutes} 分钟",
            f"任务停留：{route_plan.stay_minutes} 分钟",
            f"预计总耗时：{route_plan.total_minutes} 分钟",
        ]
    )
    lines.append("任务安排：" if schedule.scheduled_tasks else "任务安排：无")
    for i, item in enumerate(schedule.scheduled_tasks, 1):
        lines.append(
            f"{i}. 到达 {_single_line_text(item.location_id)}："
            f"{_single_line_text(item.arrival_time)}，"
            f"完成：{_single_line_text(item.finish_time)}，"
            f"{_deadline_status(item)}"
        )
    if result.status == "infeasible" and schedule.missed_deadlines:
        lines.append("超时任务：")
        lines.extend(
            f"- {_single_line_text(item.location_id)}："
            f"完成：{_single_line_text(item.finish_time)}，"
            f"{_deadline_status(item)}"
            for item in schedule.missed_deadlines
        )
    return "\n".join(lines)
