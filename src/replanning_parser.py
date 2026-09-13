"""将动态变化模型输出解析为 ``ReplanningUpdate``。

本模块只解析结构化 JSON，不执行重规划，不调用地图或路线算法。
"""

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from src.dynamic_replanner import ReplanningUpdate
from src.models import PlanningRequest, Task
from src.replanning_prompt_builder import (
    build_replanning_system_prompt,
    build_replanning_user_prompt,
)
from src.tju_llm_client import TJUClientError, call_tju_llm


@dataclass(frozen=True)
class ReplanningParseResult:
    status: str
    error_type: Optional[str]
    message: str
    update: Optional[ReplanningUpdate] = None
    missing_fields: Tuple[str, ...] = ()
    questions: Tuple[str, ...] = ()


TOP_LEVEL_FIELDS = {
    "new_current_location",
    "delay_minutes",
    "cancelled_task_locations",
    "added_tasks",
}
TASK_FIELDS = {
    "location",
    "description",
    "is_mandatory",
    "estimated_duration_minutes",
    "deadline",
}
TIME_PATTERN = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")


class _DuplicateJsonKeyError(ValueError):
    pass


def _reject_nonstandard_constant(value: str) -> None:
    raise ValueError("non-standard JSON constant")


def _reject_duplicate_json_keys(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKeyError("duplicate JSON key")
        result[key] = value
    return result


def _strict_json_loads(raw_text: str) -> object:
    return json.loads(
        raw_text,
        parse_constant=_reject_nonstandard_constant,
        object_pairs_hook=_reject_duplicate_json_keys,
    )


def _rejected(error_type: str, message: str) -> ReplanningParseResult:
    return ReplanningParseResult(
        status="rejected", error_type=error_type, message=message
    )


def _questions_for(fields: List[str]) -> Tuple[str, ...]:
    questions = []
    for field in fields:
        if field == "change":
            questions.append("请说明当前位置、延误、取消任务或新增任务中的至少一项变化。")
        elif field == "delay_minutes":
            questions.append("请说明具体延误了多少分钟。")
        elif field.endswith(".location"):
            questions.append("请说明新增任务的地点。")
        elif field.endswith(".description"):
            questions.append("请说明新增任务的内容。")
        elif field.endswith(".is_mandatory"):
            questions.append("请说明新增任务是否必须完成。")
        elif field.endswith(".estimated_duration_minutes"):
            questions.append("请说明新增任务预计需要多少分钟。")
    return tuple(questions)


def _needs(fields: List[str]) -> ReplanningParseResult:
    unique = tuple(dict.fromkeys(fields))
    return ReplanningParseResult(
        status="needs_clarification",
        error_type="missing_information",
        message="需要补充变化信息。",
        missing_fields=unique,
        questions=_questions_for(list(unique)),
    )


def _is_nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def parse_replanning_model_output(raw_reply: str) -> ReplanningParseResult:
    if not isinstance(raw_reply, str):
        return _rejected("format_error", "模型返回格式无效。")
    if not raw_reply.strip() or "```" in raw_reply:
        return _rejected("format_error", "模型返回必须是纯 JSON。")
    try:
        payload = _strict_json_loads(raw_reply)
    except (TypeError, ValueError):
        return _rejected("format_error", "模型返回不是合法 JSON。")
    if not isinstance(payload, dict):
        return _rejected("format_error", "模型返回必须是 JSON 对象。")
    if set(payload) != TOP_LEVEL_FIELDS:
        return _rejected("format_error", "模型返回字段结构无效。")

    location = payload["new_current_location"]
    if location is not None and not _is_nonempty_string(location):
        return _rejected("format_error", "当前位置字段格式无效。")

    delay = payload["delay_minutes"]
    missing: List[str] = []
    if delay is None:
        missing.append("delay_minutes")
    elif type(delay) is not int or delay < 0:
        return _rejected("validation_error", "延误分钟字段无效。")

    cancelled = payload["cancelled_task_locations"]
    if not isinstance(cancelled, list):
        return _rejected("format_error", "取消任务地点必须是列表。")
    seen_cancelled = set()
    for value in cancelled:
        if not _is_nonempty_string(value):
            return _rejected("validation_error", "取消任务地点字段无效。")
        if value in seen_cancelled:
            return _rejected("validation_error", "取消任务地点不能重复。")
        seen_cancelled.add(value)

    added = payload["added_tasks"]
    if not isinstance(added, list):
        return _rejected("format_error", "新增任务必须是列表。")
    parsed_tasks: List[Task] = []
    for index, item in enumerate(added):
        prefix = f"added_tasks[{index}]"
        if not isinstance(item, dict):
            return _rejected("format_error", "新增任务必须是对象。")
        if set(item) - TASK_FIELDS:
            return _rejected("format_error", "新增任务存在未知字段。")
        required = (
            "location",
            "description",
            "is_mandatory",
            "estimated_duration_minutes",
        )
        missing_required = [
            f"{prefix}.{field}" for field in required if field not in item
        ]
        if missing_required:
            missing.extend(missing_required)
        if "deadline" not in item:
            return _rejected("format_error", "新增任务必须明确提供 deadline 字段。")

        task_location = item.get("location")
        task_description = item.get("description")
        mandatory = item.get("is_mandatory")
        duration = item.get("estimated_duration_minutes")
        deadline = item.get("deadline")
        if "location" in item and (
            task_location is None or not _is_nonempty_string(task_location)
        ):
            return _rejected("validation_error", "新增任务地点无效。")
        if "description" in item and (
            task_description is None or not _is_nonempty_string(task_description)
        ):
            return _rejected("validation_error", "新增任务描述无效。")
        if mandatory is None and "is_mandatory" in item:
            missing.append(f"{prefix}.is_mandatory")
        elif mandatory is not None and type(mandatory) is not bool:
            return _rejected("validation_error", "新增任务是否必须字段无效。")
        if duration is None and "estimated_duration_minutes" in item:
            missing.append(f"{prefix}.estimated_duration_minutes")
        elif duration is not None and (type(duration) is not int or duration < 0):
            return _rejected("validation_error", "新增任务停留时间字段无效。")
        if deadline is not None and (
            not isinstance(deadline, str) or TIME_PATTERN.fullmatch(deadline) is None
        ):
            return _rejected("validation_error", "新增任务 deadline 字段无效。")
        if missing_required:
            continue
        parsed_tasks.append(
            Task(task_location, task_description, mandatory, duration, deadline)
        )

    if missing:
        return _needs(missing)
    if delay is None:
        return _needs(["delay_minutes"])
    if (
        location is None
        and delay == 0
        and not cancelled
        and not added
    ):
        return _needs(["change"])
    return ReplanningParseResult(
        status="ok",
        error_type=None,
        message="变化信息解析成功。",
        update=ReplanningUpdate(
            new_current_location=location,
            delay_minutes=delay,
            cancelled_task_locations=tuple(cancelled),
            added_tasks=tuple(parsed_tasks),
        ),
    )


def parse_replanning_text(
    change_text: str, planning_request: PlanningRequest
) -> ReplanningParseResult:
    if not isinstance(change_text, str) or not change_text.strip():
        return _rejected("validation_error", "变化文本不能为空。")
    if not isinstance(planning_request, PlanningRequest):
        return _rejected("validation_error", "规划请求格式无效。")
    try:
        system_prompt = build_replanning_system_prompt()
        user_prompt = build_replanning_user_prompt(
            change_text, planning_request
        )
    except (TypeError, ValueError):
        return _rejected("validation_error", "变化信息格式无效。")
    try:
        raw_reply = call_tju_llm(
            user_prompt,
            temperature=0,
            system_prompt=system_prompt,
            max_tokens=1024,
        )
    except TJUClientError:
        return _rejected("api_error", "变化信息解析服务暂时不可用。")
    return parse_replanning_model_output(raw_reply)
