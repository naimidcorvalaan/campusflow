import json
from typing import Any, Dict, List, Optional

from src.extraction_models import ExtractedPlanningRequest, ExtractedTask, ParseResult
from src.input_validator import InputValidator
from src.models import PlanningRequest, Task


class StructuredParser:
    """解析模拟大模型返回的 JSON，并在信息完整时构造正式对象。"""

    ALLOWED_TOP_LEVEL_FIELDS = {"current_location", "destination", "current_time", "tasks"}
    ALLOWED_TASK_FIELDS = {
        "location",
        "description",
        "is_mandatory",
        "estimated_duration_minutes",
        "deadline",
    }

    @staticmethod
    def parse(raw_text: str) -> ParseResult:
        """解析模型返回字符串，返回统一的 ParseResult。"""
        stripped = (raw_text or "").strip()
        if not stripped:
            return ParseResult(
                status="rejected",
                error_type="format_error",
                message="错误：模型返回为空。",
                missing_fields=[],
                questions=[],
                planning_request=None,
            )

        if stripped.startswith("```") or "```" in stripped or not stripped.startswith("{"):
            return ParseResult(
                status="rejected",
                error_type="format_error",
                message="错误：模型返回不是合法的JSON对象。",
                missing_fields=[],
                questions=[],
                planning_request=None,
            )

        try:
            payload = json.loads(stripped)
        except (TypeError, ValueError):
            return ParseResult(
                status="rejected",
                error_type="format_error",
                message="错误：模型返回不是合法的JSON。",
                missing_fields=[],
                questions=[],
                planning_request=None,
            )

        if not isinstance(payload, dict):
            return ParseResult(
                status="rejected",
                error_type="format_error",
                message="错误：模型返回必须是JSON对象。",
                missing_fields=[],
                questions=[],
                planning_request=None,
            )

        unknown_fields = set(payload.keys()) - StructuredParser.ALLOWED_TOP_LEVEL_FIELDS
        if unknown_fields:
            return ParseResult(
                status="rejected",
                error_type="format_error",
                message="错误：顶层存在未知字段。",
                missing_fields=[],
                questions=[],
                planning_request=None,
            )

        missing_fields: List[str] = []

        current_location = payload.get("current_location")
        if current_location is None or (isinstance(current_location, str) and current_location.strip() == ""):
            missing_fields.append("current_location")
        elif not isinstance(current_location, str):
            return ParseResult(
                status="rejected",
                error_type="format_error",
                message="错误：current_location必须是字符串。",
                missing_fields=[],
                questions=[],
                planning_request=None,
            )

        destination = payload.get("destination")
        if destination is None or (isinstance(destination, str) and destination.strip() == ""):
            missing_fields.append("destination")
        elif not isinstance(destination, str):
            return ParseResult(
                status="rejected",
                error_type="format_error",
                message="错误：destination必须是字符串。",
                missing_fields=[],
                questions=[],
                planning_request=None,
            )

        current_time = payload.get("current_time")
        if current_time is None or (isinstance(current_time, str) and current_time.strip() == ""):
            missing_fields.append("current_time")
        elif not isinstance(current_time, str):
            return ParseResult(
                status="rejected",
                error_type="format_error",
                message="错误：current_time必须是字符串。",
                missing_fields=[],
                questions=[],
                planning_request=None,
            )

        tasks_value = payload.get("tasks")
        if tasks_value is None or tasks_value == []:
            missing_fields.append("tasks")
        elif not isinstance(tasks_value, list):
            return ParseResult(
                status="rejected",
                error_type="format_error",
                message="错误：tasks必须是列表。",
                missing_fields=[],
                questions=[],
                planning_request=None,
            )

        extracted_tasks: List[ExtractedTask] = []
        if isinstance(tasks_value, list):
            for index, item in enumerate(tasks_value):
                if not isinstance(item, dict):
                    return ParseResult(
                        status="rejected",
                        error_type="format_error",
                        message="错误：tasks中的元素必须是对象。",
                        missing_fields=[],
                        questions=[],
                        planning_request=None,
                    )

                unknown_task_fields = set(item.keys()) - StructuredParser.ALLOWED_TASK_FIELDS
                if unknown_task_fields:
                    return ParseResult(
                        status="rejected",
                        error_type="format_error",
                        message="错误：任务对象存在未知字段。",
                        missing_fields=[],
                        questions=[],
                        planning_request=None,
                    )

                task = ExtractedTask(
                    location=item.get("location"),
                    description=item.get("description"),
                    is_mandatory=item.get("is_mandatory"),
                    estimated_duration_minutes=item.get("estimated_duration_minutes"),
                    deadline=item.get("deadline"),
                )

                if task.location is None or (isinstance(task.location, str) and task.location.strip() == ""):
                    missing_fields.append(f"tasks[{index}].location")
                elif not isinstance(task.location, str):
                    return ParseResult(
                        status="rejected",
                        error_type="format_error",
                        message=f"错误：第{index + 1}个任务的location必须是字符串。",
                        missing_fields=[],
                        questions=[],
                        planning_request=None,
                    )

                if task.description is None or (isinstance(task.description, str) and task.description.strip() == ""):
                    missing_fields.append(f"tasks[{index}].description")
                elif not isinstance(task.description, str):
                    return ParseResult(
                        status="rejected",
                        error_type="format_error",
                        message=f"错误：第{index + 1}个任务的description必须是字符串。",
                        missing_fields=[],
                        questions=[],
                        planning_request=None,
                    )

                if task.is_mandatory is None:
                    missing_fields.append(f"tasks[{index}].is_mandatory")
                elif type(task.is_mandatory) is not bool:
                    return ParseResult(
                        status="rejected",
                        error_type="format_error",
                        message=f"错误：第{index + 1}个任务的is_mandatory必须是布尔值。",
                        missing_fields=[],
                        questions=[],
                        planning_request=None,
                    )

                if task.estimated_duration_minutes is None:
                    missing_fields.append(f"tasks[{index}].estimated_duration_minutes")
                elif type(task.estimated_duration_minutes) is not int:
                    return ParseResult(
                        status="rejected",
                        error_type="format_error",
                        message=f"错误：第{index + 1}个任务的estimated_duration_minutes必须是整数。",
                        missing_fields=[],
                        questions=[],
                        planning_request=None,
                    )

                if "deadline" in item and item["deadline"] is not None and not isinstance(item["deadline"], str):
                    return ParseResult(
                        status="rejected",
                        error_type="format_error",
                        message=f"错误：第{index + 1}个任务的deadline必须是字符串。",
                        missing_fields=[],
                        questions=[],
                        planning_request=None,
                    )

                extracted_tasks.append(task)

        if missing_fields:
            unique_missing_fields = []
            for field in missing_fields:
                if field not in unique_missing_fields:
                    unique_missing_fields.append(field)
            return ParseResult(
                status="needs_clarification",
                error_type="missing_information",
                message="错误：信息不完整，需要补充缺失字段。",
                missing_fields=unique_missing_fields,
                questions=StructuredParser._build_questions(unique_missing_fields),
                planning_request=None,
            )

        extracted = ExtractedPlanningRequest(
            current_location=current_location,
            destination=destination,
            current_time=current_time,
            tasks=extracted_tasks,
        )

        formal_request = StructuredParser._convert_to_formal_request(extracted)
        is_valid, message = InputValidator.validate(formal_request)
        if not is_valid:
            return ParseResult(
                status="rejected",
                error_type="validation_error",
                message=message,
                missing_fields=[],
                questions=[],
                planning_request=None,
            )

        return ParseResult(
            status="ok",
            error_type=None,
            message="校验通过",
            missing_fields=[],
            questions=[],
            planning_request=formal_request,
        )

    @staticmethod
    def _build_questions(missing_fields: List[str]) -> List[str]:
        questions: List[str] = []
        for field in missing_fields:
            if field == "current_location":
                questions.append("请说明当前所在地点。")
            elif field == "destination":
                questions.append("请说明最终目的地。")
            elif field == "current_time":
                questions.append("请说明当前时间，格式为HH:MM。")
            elif field == "tasks":
                questions.append("请提供任务列表。")
            elif field.endswith(".location"):
                questions.append("请说明该任务发生的位置。")
            elif field.endswith(".description"):
                questions.append("请说明该任务内容。")
            elif field.endswith(".is_mandatory"):
                questions.append("请说明该任务是否必须完成。")
            elif field.endswith(".estimated_duration_minutes"):
                questions.append("请说明该任务的预计停留时间（分钟）。")
        return questions

    @staticmethod
    def _convert_to_formal_request(extracted: ExtractedPlanningRequest) -> PlanningRequest:
        tasks = []
        for task in extracted.tasks:
            tasks.append(
                Task(
                    location=task.location,
                    description=task.description,
                    is_mandatory=task.is_mandatory,
                    estimated_duration_minutes=task.estimated_duration_minutes,
                    deadline=task.deadline,
                )
            )
        return PlanningRequest(
            current_location=extracted.current_location,
            destination=extracted.destination,
            current_time=extracted.current_time,
            tasks=tasks,
        )
