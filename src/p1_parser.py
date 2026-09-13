"""严格解析 ``p1.task-understanding.v1`` 的离线模型输出。

本模块不调用大模型、地图或其他外部服务。模型输出始终按不可信输入处理。
"""

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from src.p1_extraction_models import P1TaskUnderstandingParseResult
from src.p1_models import (
    AttentionLevel,
    ClarificationQuestion,
    FieldEvidence,
    LocationRequirement,
    MAX_DURATION_MINUTES,
    MAX_EXPLANATION_LENGTH,
    MAX_LOCATION_TEXT_LENGTH,
    MAX_MODEL_OUTPUT_LENGTH,
    MAX_ORIGINAL_FRAGMENT_LENGTH,
    MAX_QUESTIONS,
    MAX_QUESTION_LENGTH,
    MAX_QUICK_OPTION_LENGTH,
    MAX_QUICK_OPTIONS,
    MAX_REQUIREMENT_TEXT_LENGTH,
    MAX_REQUIREMENTS_PER_FIELD,
    MAX_TASK_REF_LENGTH,
    MAX_TASKS,
    MAX_TITLE_LENGTH,
    MAX_USER_TEXT_LENGTH,
    SCHEMA_VERSION,
    SourceKind,
    TaskUnderstanding,
    TaskUnderstandingDocument,
    TaskUnderstandingFeatures,
)


TOP_LEVEL_FIELDS = {
    "schema_version",
    "task_interpretations",
    "clarification_questions",
}
TASK_FIELDS = {
    "task_ref",
    "title",
    "original_text",
    "understood_features",
    "field_evidence",
    "needs_confirmation",
}
FEATURE_FIELDS = {
    "location_requirement",
    "location_text",
    "environment_requirements",
    "equipment_requirements",
    "estimated_total_minutes",
    "is_splittable",
    "minimum_slice_minutes",
    "attention_required",
    "interruption_allowed",
    "may_have_open_hours",
}
EVIDENCE_FIELDS = {"source", "explanation"}
QUESTION_FIELDS = {
    "question_id",
    "task_ref",
    "field_name",
    "question",
    "blocking_for_task_understanding",
    "quick_options",
}
CONFIRMABLE_FIELDS = frozenset(FEATURE_FIELDS)
AI_EVIDENCE_FIELDS = frozenset(FEATURE_FIELDS)

_REFERENCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_PERCENT_CONFIDENCE_PATTERN = re.compile(
    r"(?:[0-9]+(?:\.[0-9]+)?\s*%|百分之\s*[0-9]+)"
)
_CHINESE_TEXT_PATTERN = re.compile(r"[\u4e00-\u9fff]")
_PERCENT_CONFIDENCE_CHINESE_PATTERN = re.compile(r"百分比\s*\d+")


class _DuplicateJsonKeyError(ValueError):
    pass


class _P1ParseError(ValueError):
    def __init__(self, error_type: str, safe_message: str):
        super().__init__(safe_message)
        self.error_type = error_type
        self.safe_message = safe_message


def _reject_nonstandard_constant(value: str) -> None:
    raise ValueError("non-standard JSON constant")


def _reject_duplicate_json_keys(
    pairs: List[Tuple[str, Any]]
) -> Dict[str, Any]:
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


def _rejected(error_type: str, message: str) -> P1TaskUnderstandingParseResult:
    return P1TaskUnderstandingParseResult(
        status="rejected",
        error_type=error_type,
        message=message,
        document=None,
    )


def _format_error(message: str = "模型返回字段结构无效。") -> None:
    raise _P1ParseError("format_error", message)


def _validation_error(message: str = "模型返回字段值无效。") -> None:
    raise _P1ParseError("validation_error", message)


def _require_exact_fields(value: object, expected: set) -> Dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        _format_error()
    return value


def _bounded_text(
    value: object,
    maximum: int,
    message: str,
    allow_newlines: bool = False,
) -> str:
    if not isinstance(value, str):
        _validation_error(message)
    cleaned = value.strip()
    if not cleaned or len(cleaned) > maximum:
        _validation_error(message)
    if not allow_newlines and ("\n" in cleaned or "\r" in cleaned):
        _validation_error(message)
    return cleaned


def _optional_bounded_text(
    value: object, maximum: int, message: str
) -> Optional[str]:
    if value is None:
        return None
    return _bounded_text(value, maximum, message)


def _positive_minutes(value: object, field_name: str) -> Optional[int]:
    if value is None:
        return None
    if type(value) is not int:
        _validation_error(f"{field_name} 必须是整数分钟或 null。")
    if value <= 0 or value > MAX_DURATION_MINUTES:
        _validation_error(f"{field_name} 超出允许范围。")
    return value


def _optional_bool(value: object, field_name: str) -> Optional[bool]:
    if value is None:
        return None
    if type(value) is not bool:
        _validation_error(f"{field_name} 必须是布尔值或 null。")
    return value


def _text_tuple(
    value: object,
    maximum_items: int,
    maximum_length: int,
    message: str,
) -> Tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum_items:
        _validation_error(message)
    result: List[str] = []
    seen = set()
    for item in value:
        cleaned = _bounded_text(item, maximum_length, message)
        if cleaned in seen:
            _validation_error(message)
        seen.add(cleaned)
        result.append(cleaned)
    return tuple(result)


def _parse_location_requirement(value: object) -> LocationRequirement:
    if not isinstance(value, str):
        _validation_error("location_requirement 枚举值无效。")
    try:
        return LocationRequirement(value)
    except ValueError:
        _validation_error("location_requirement 枚举值无效。")
    raise AssertionError("unreachable")


def _parse_attention(value: object) -> Optional[AttentionLevel]:
    if value is None:
        return None
    if not isinstance(value, str):
        _validation_error("attention_required 枚举值无效。")
    try:
        return AttentionLevel(value)
    except ValueError:
        _validation_error("attention_required 枚举值无效。")
    raise AssertionError("unreachable")


def _parse_features(value: object) -> TaskUnderstandingFeatures:
    item = _require_exact_fields(value, FEATURE_FIELDS)
    location_requirement = _parse_location_requirement(
        item["location_requirement"]
    )
    location_text = _optional_bounded_text(
        item["location_text"],
        MAX_LOCATION_TEXT_LENGTH,
        "location_text 字段无效。",
    )

    if location_requirement in (
        LocationRequirement.NO_SPECIFIC_LOCATION,
        LocationRequirement.LOCATION_REQUIREMENT_UNKNOWN,
    ) and location_text is not None:
        _validation_error("地点要求与地点文本不一致。")

    total_minutes = _positive_minutes(
        item["estimated_total_minutes"], "estimated_total_minutes"
    )
    minimum_slice = _positive_minutes(
        item["minimum_slice_minutes"], "minimum_slice_minutes"
    )
    is_splittable = _optional_bool(item["is_splittable"], "is_splittable")
    if (
        total_minutes is not None
        and minimum_slice is not None
        and minimum_slice > total_minutes
    ):
        _validation_error("minimum_slice_minutes 不能超过预计总时长。")
    if is_splittable is False:
        if total_minutes is None and minimum_slice is not None:
            _validation_error("不可拆分任务未知总时长时不能提供最小片段。")
        if total_minutes is not None and minimum_slice != total_minutes:
            _validation_error("不可拆分任务的最小片段必须等于预计总时长。")

    return TaskUnderstandingFeatures(
        location_requirement=location_requirement,
        location_text=location_text,
        environment_requirements=_text_tuple(
            item["environment_requirements"],
            MAX_REQUIREMENTS_PER_FIELD,
            MAX_REQUIREMENT_TEXT_LENGTH,
            "environment_requirements 字段无效。",
        ),
        equipment_requirements=_text_tuple(
            item["equipment_requirements"],
            MAX_REQUIREMENTS_PER_FIELD,
            MAX_REQUIREMENT_TEXT_LENGTH,
            "equipment_requirements 字段无效。",
        ),
        estimated_total_minutes=total_minutes,
        is_splittable=is_splittable,
        minimum_slice_minutes=minimum_slice,
        attention_required=_parse_attention(item["attention_required"]),
        interruption_allowed=_optional_bool(
            item["interruption_allowed"], "interruption_allowed"
        ),
        may_have_open_hours=_optional_bool(
            item["may_have_open_hours"], "may_have_open_hours"
        ),
    )


def _feature_has_value(
    features: TaskUnderstandingFeatures, field_name: str
) -> bool:
    value = getattr(features, field_name)
    if isinstance(value, tuple):
        return bool(value)
    return value is not None


def _parse_evidence(
    value: object, features: TaskUnderstandingFeatures
) -> Dict[str, FieldEvidence]:
    if not isinstance(value, dict) or len(value) > len(AI_EVIDENCE_FIELDS):
        _format_error("field_evidence 字段结构无效。")

    result: Dict[str, FieldEvidence] = {}
    for field_name, raw_evidence in value.items():
        if field_name not in AI_EVIDENCE_FIELDS:
            _format_error("field_evidence 引用了未知字段。")
        evidence = _require_exact_fields(raw_evidence, EVIDENCE_FIELDS)
        try:
            source = SourceKind(evidence["source"])
        except (TypeError, ValueError):
            _validation_error("模型不得声明用户确认或系统事实来源。")
        if source not in (
            SourceKind.AI_EXTRACTED_FROM_USER_TEXT,
            SourceKind.AI_ESTIMATED,
        ):
            _validation_error("模型不得声明用户确认或系统事实来源。")
        explanation = _bounded_text(
            evidence["explanation"],
            MAX_EXPLANATION_LENGTH,
            "AI 暂估必须提供简短解释。",
            allow_newlines=False,
        )
        if (
            _PERCENT_CONFIDENCE_PATTERN.search(explanation)
            or _PERCENT_CONFIDENCE_CHINESE_PATTERN.search(explanation)
        ):
            _validation_error("AI 暂估解释不得使用百分比置信度。")
        if _CHINESE_TEXT_PATTERN.search(explanation) is None:
            _validation_error("AI 暂估必须提供自然的中文解释。")
        if not _feature_has_value(features, field_name):
            _validation_error("字段为空时不得提供来源。")
        result[field_name] = FieldEvidence(
            source=source,
            explanation=explanation,
        )
    return result


def _parse_needs_confirmation(value: object) -> Tuple[str, ...]:
    if not isinstance(value, list) or len(value) > len(CONFIRMABLE_FIELDS):
        _validation_error("needs_confirmation 字段无效。")
    result: List[str] = []
    seen = set()
    for field_name in value:
        if not isinstance(field_name, str) or field_name not in CONFIRMABLE_FIELDS:
            _validation_error("needs_confirmation 引用了未知字段。")
        if field_name in seen:
            _validation_error("needs_confirmation 不能包含重复字段。")
        seen.add(field_name)
        result.append(field_name)
    return tuple(result)


def _validate_confirmation_contract(
    features: TaskUnderstandingFeatures,
    evidence: Dict[str, FieldEvidence],
    needs_confirmation: Tuple[str, ...],
) -> None:
    needs = set(needs_confirmation)
    if (
        features.location_requirement
        is LocationRequirement.LOCATION_REQUIREMENT_UNKNOWN
        and "location_requirement" not in needs
    ):
        _validation_error("地点要求未知时必须进入待确认字段。")
    if (
        features.location_requirement is LocationRequirement.SPECIFIC_LOCATION
        and features.location_text is None
        and "location_text" not in needs
    ):
        _validation_error("具体地点缺失时必须进入待确认字段。")

    for field_name in AI_EVIDENCE_FIELDS:
        if _feature_has_value(features, field_name) and field_name not in evidence:
            _validation_error("重要任务属性有值时必须提供来源。")


def _parse_task(
    value: object,
    user_text: str,
) -> TaskUnderstanding:
    item = _require_exact_fields(value, TASK_FIELDS)
    task_ref = _bounded_text(
        item["task_ref"], MAX_TASK_REF_LENGTH, "task_ref 字段无效。"
    )
    if _REFERENCE_PATTERN.fullmatch(task_ref) is None:
        _validation_error("task_ref 字段无效。")
    title = _bounded_text(
        item["title"], MAX_TITLE_LENGTH, "title 字段无效。"
    )
    original_text = _bounded_text(
        item["original_text"],
        MAX_ORIGINAL_FRAGMENT_LENGTH,
        "original_text 字段无效。",
        allow_newlines=True,
    )
    if original_text not in user_text:
        _validation_error("original_text 必须来自用户原始输入。")

    features = _parse_features(item["understood_features"])
    evidence = _parse_evidence(item["field_evidence"], features)
    needs_confirmation = _parse_needs_confirmation(
        item["needs_confirmation"]
    )
    _validate_confirmation_contract(
        features, evidence, needs_confirmation
    )

    # 用户原文来源由程序在验证片段后建立，模型不能自行声明。
    evidence["original_text"] = FieldEvidence(
        source=SourceKind.USER_STATED,
        explanation="来自用户原始输入",
    )
    return TaskUnderstanding(
        task_ref=task_ref,
        title=title,
        original_text=original_text,
        features=features,
        field_evidence=evidence,
        needs_confirmation=needs_confirmation,
    )


def _parse_questions(
    value: object,
    tasks: Tuple[TaskUnderstanding, ...],
) -> Tuple[ClarificationQuestion, ...]:
    if not isinstance(value, list) or len(value) > MAX_QUESTIONS:
        _validation_error("clarification_questions 字段无效。")

    result: List[ClarificationQuestion] = []
    seen_ids = set()
    for raw_question in value:
        item = _require_exact_fields(raw_question, QUESTION_FIELDS)
        question_id = _bounded_text(
            item["question_id"],
            MAX_TASK_REF_LENGTH,
            "question_id 字段无效。",
        )
        if (
            _REFERENCE_PATTERN.fullmatch(question_id) is None
            or question_id in seen_ids
        ):
            _validation_error("question_id 字段无效或重复。")
        seen_ids.add(question_id)

        task_ref = _bounded_text(
            item["task_ref"], MAX_TASK_REF_LENGTH, "task_ref 字段无效。"
        )
        field_name = _bounded_text(
            item["field_name"], MAX_TASK_REF_LENGTH, "field_name 字段无效。"
        )
        task_by_ref = {task.task_ref: task for task in tasks}
        task = task_by_ref.get(task_ref)
        if task is None or field_name not in CONFIRMABLE_FIELDS:
            _validation_error("确认问题引用了不存在的任务或字段。")
        if field_name not in task.needs_confirmation:
            _validation_error("问题必须引用任务的待确认字段。")

        blocking = item["blocking_for_task_understanding"]
        if type(blocking) is not bool:
            _validation_error(
                "blocking_for_task_understanding 必须是布尔值。"
            )
        result.append(
            ClarificationQuestion(
                question_id=question_id,
                task_ref=task_ref,
                field_name=field_name,
                question=_bounded_text(
                    item["question"],
                    MAX_QUESTION_LENGTH,
                    "question 字段无效。",
                ),
                blocking_for_task_understanding=blocking,
                quick_options=_text_tuple(
                    item["quick_options"],
                    MAX_QUICK_OPTIONS,
                    MAX_QUICK_OPTION_LENGTH,
                    "quick_options 字段无效。",
                ),
            )
        )
    return tuple(result)


class P1TaskUnderstandingParser:
    """严格解析 P1 首切片 JSON，并返回安全、稳定的结果对象。"""

    @staticmethod
    def parse(
        raw_reply: object, user_text: object
    ) -> P1TaskUnderstandingParseResult:
        if not isinstance(user_text, str) or not user_text.strip():
            return _rejected("validation_error", "用户原始文本无效。")
        if len(user_text) > MAX_USER_TEXT_LENGTH:
            return _rejected("validation_error", "用户原始文本过长。")
        if not isinstance(raw_reply, str):
            return _rejected("format_error", "模型返回格式无效。")
        if len(raw_reply) > MAX_MODEL_OUTPUT_LENGTH:
            return _rejected("validation_error", "模型返回内容过长。")
        if not raw_reply.strip():
            return _rejected("format_error", "模型返回为空。")

        stripped = raw_reply.strip()
        if (
            "```" in stripped
            or not stripped.startswith("{")
            or not stripped.endswith("}")
        ):
            return _rejected("format_error", "模型返回必须是纯 JSON 对象。")

        try:
            payload = _strict_json_loads(stripped)
        except (TypeError, ValueError, RecursionError):
            return _rejected("format_error", "模型返回不是合法 JSON。")

        try:
            top = _require_exact_fields(payload, TOP_LEVEL_FIELDS)
            if top["schema_version"] != SCHEMA_VERSION:
                _validation_error("schema_version 无效。")
            raw_tasks = top["task_interpretations"]
            if (
                not isinstance(raw_tasks, list)
                or len(raw_tasks) > MAX_TASKS
            ):
                _validation_error("task_interpretations 数量无效。")

            tasks = tuple(_parse_task(item, user_text) for item in raw_tasks)
            task_refs = [task.task_ref for task in tasks]
            if len(set(task_refs)) != len(task_refs):
                _validation_error("task_ref 不能重复。")

            questions = _parse_questions(
                top["clarification_questions"], tasks
            )
        except _P1ParseError as exc:
            return _rejected(exc.error_type, exc.safe_message)

        document = TaskUnderstandingDocument(
            schema_version=SCHEMA_VERSION,
            original_user_input=user_text,
            tasks=tasks,
            clarification_questions=questions,
        )
        return P1TaskUnderstandingParseResult(
            status="ok",
            error_type=None,
            message="任务理解解析成功。",
            document=document,
        )


def parse_task_understanding(
    raw_reply: object, user_text: object
) -> P1TaskUnderstandingParseResult:
    """函数式入口，便于后续管线或测试调用。"""
    return P1TaskUnderstandingParser.parse(raw_reply, user_text)


parse_task_understanding_model_output = parse_task_understanding
