"""Normalize compact P1 extraction replies into the existing strict v1 schemas.

The model-facing v2 shape intentionally omits references, evidence explanations,
confirmation questions, and absent optional fields.  This module supplies those
deterministically, then the unchanged v1 parsers remain the security boundary.
"""
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Tuple

from src.p1_models import (
    MAX_MODEL_OUTPUT_LENGTH,
    MAX_TASKS,
    SCHEMA_VERSION,
)
from src.p1_window_models import (
    MAX_COMMITMENTS,
    MAX_WINDOW_OUTPUT_LENGTH,
    MAX_WINDOW_QUESTIONS,
    WINDOW_SCHEMA_VERSION,
)


TASK_EXTRACTION_SCHEMA_VERSION = "p1.task-extraction.v2"
WINDOW_EXTRACTION_SCHEMA_VERSION = "p1.window-extraction.v2"

_TASK_TOP = {"schema_version", "tasks"}
_TASK_REQUIRED = {"title", "original_text", "features"}
_TASK_OPTIONAL = {"extracted_fields", "estimated_fields", "confirmation_fields"}
_FEATURES = {
    "location_requirement", "location_text", "environment_requirements",
    "equipment_requirements", "estimated_total_minutes", "is_splittable",
    "minimum_slice_minutes", "attention_required", "interruption_allowed",
    "may_have_open_hours",
}
_FEATURE_DEFAULTS = {
    "location_text": None,
    "environment_requirements": [],
    "equipment_requirements": [],
    "estimated_total_minutes": None,
    "is_splittable": None,
    "minimum_slice_minutes": None,
    "attention_required": None,
    "interruption_allowed": None,
    "may_have_open_hours": None,
}
_IMPORTANT_CONFIRMATION_FIELDS = {"estimated_total_minutes"}
_FIELD_LABELS = {
    "location_requirement": "地点要求",
    "location_text": "具体地点",
    "environment_requirements": "环境要求",
    "equipment_requirements": "设备要求",
    "estimated_total_minutes": "预计总时长",
    "is_splittable": "可拆分性",
    "minimum_slice_minutes": "最小有效片段",
    "attention_required": "注意力要求",
    "interruption_allowed": "可中断性",
    "may_have_open_hours": "开放时间限制",
}

_WINDOW_REQUIRED = {
    "schema_version", "current_location", "commitments",
    "free_duration_minutes", "ends_at", "user_buffer_minutes",
}
_WINDOW_OPTIONAL = {"assumed_current_datetime"}
_CURRENT_LOCATION = {"text", "fragment"}
_ASSUMED_TIME = {"value", "fragment"}
_COMMITMENT = {
    "title", "original_text", "starts_at", "ends_at", "location_text",
    "availability_during",
}
_ISO_DATETIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?$"
)


class ExtractionNormalizationError(ValueError):
    def __init__(self, error_type, safe_message):
        ValueError.__init__(self, safe_message)
        self.error_type = error_type
        self.safe_message = safe_message


class _DuplicateKeyError(ValueError):
    pass


def _pairs(items: List[Tuple[str, Any]]) -> Dict[str, Any]:
    result = {}
    for key, value in items:
        if key in result:
            raise _DuplicateKeyError("duplicate key")
        result[key] = value
    return result


def _load(raw_reply, maximum):
    if not isinstance(raw_reply, str) or not raw_reply.strip() or len(raw_reply) > maximum:
        raise ExtractionNormalizationError("json_format_error", "模型返回格式无效。")
    text = raw_reply.strip()
    if "```" in text or not text.startswith("{") or not text.endswith("}"):
        raise ExtractionNormalizationError("json_format_error", "模型必须只返回 JSON 对象。")
    try:
        payload = json.loads(
            text,
            object_pairs_hook=_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError()),
        )
    except (TypeError, ValueError, RecursionError):
        raise ExtractionNormalizationError("json_format_error", "模型返回不是合法 JSON。")
    if not isinstance(payload, dict):
        raise ExtractionNormalizationError("json_format_error", "模型返回必须是 JSON 对象。")
    return text, payload


def _exact(value, fields, name):
    if not isinstance(value, dict) or set(value) != fields:
        raise ExtractionNormalizationError("missing_required_field", name + "字段不完整或包含未知字段。")
    return value


def _allowed(value, required, optional, name):
    if (not isinstance(value, dict) or not required.issubset(set(value))
            or not set(value).issubset(required | optional)):
        raise ExtractionNormalizationError("missing_required_field", name + "字段不完整或包含未知字段。")
    return value


def _string_list(value, allowed, name):
    if (not isinstance(value, list)
            or any(not isinstance(item, str) or item not in allowed for item in value)
            or len(value) != len(set(value))):
        raise ExtractionNormalizationError("source_contract_error", name + "字段来源数组无效。")
    return list(value)


def _has_value(value):
    if isinstance(value, list):
        return bool(value)
    return value is not None


def _duration_is_explicit(minutes, original_text):
    if not isinstance(minutes, int) or type(minutes) is bool:
        return False
    if re.search(r"(?<!\d)%d\s*分钟" % minutes, original_text):
        return True
    if minutes % 60 == 0 and re.search(
            r"(?<!\d)%d\s*(?:个)?小时" % (minutes // 60), original_text):
        return True
    if minutes == 30 and "半小时" in original_text:
        return True
    if minutes == 90 and "一个半小时" in original_text:
        return True
    return False


def _text_feature_is_explicit(field_name, value, original_text):
    if field_name == "location_text":
        return isinstance(value, str) and value in original_text
    if field_name in ("environment_requirements", "equipment_requirements"):
        return bool(value) and all(isinstance(item, str) and item in original_text
                                   for item in value)
    return False


def _infer_source(field_name, value, original_text):
    if field_name in ("estimated_total_minutes", "minimum_slice_minutes"):
        return "extracted" if _duration_is_explicit(value, original_text) else "estimated"
    if _text_feature_is_explicit(field_name, value, original_text):
        return "extracted"
    return "estimated"


def _fragment(value, user_text, name):
    if not isinstance(value, str) or not value.strip() or value.strip() not in user_text:
        raise ExtractionNormalizationError("original_fragment_mismatch", name + "必须来自用户原文。")
    return value.strip()


def _evidence(field_name, estimated):
    label = _FIELD_LABELS[field_name]
    if estimated:
        explanation = "用户未明确提供，保留模型对%s的暂估" % label
        source = "ai_estimated"
    else:
        explanation = "根据用户原话识别出%s" % label
        source = "ai_extracted_from_user_text"
    return {"source": source, "explanation": explanation}


def _task_confirmation_question(task_ref, title, features, needs, index):
    priority = (
        "estimated_total_minutes", "location_requirement", "location_text",
        "minimum_slice_minutes", "attention_required",
    )
    field_name = next((name for name in priority if name in needs), None)
    if field_name is None:
        return None
    if field_name == "estimated_total_minutes" and isinstance(features[field_name], int):
        text = "我暂时估计“%s”需要%d分钟，这个时间合适吗？" % (
            title, features[field_name])
        options = ["就按这个", "调整时长"]
    elif field_name in ("location_requirement", "location_text"):
        text = "“%s”是否需要特定地点？" % title
        options = ["不需要特定地点", "需要特定地点", "暂不确定"]
    elif field_name == "minimum_slice_minutes" and isinstance(features[field_name], int):
        text = "“%s”每次至少安排%d分钟合适吗？" % (title, features[field_name])
        options = ["就按这个", "调整时长"]
    else:
        text = "我对“%s”的%s作了暂估，这个判断合适吗？" % (
            title, _FIELD_LABELS[field_name])
        options = ["确认", "修改"]
    return {
        "question_id": "task-question-%d" % index,
        "task_ref": task_ref,
        "field_name": field_name,
        "question": text,
        "blocking_for_task_understanding": False,
        "quick_options": options,
    }


def normalize_task_extraction(raw_reply, user_text):
    """Return canonical p1.task-understanding.v1 JSON."""
    text, payload = _load(raw_reply, MAX_MODEL_OUTPUT_LENGTH)
    if payload.get("schema_version") == SCHEMA_VERSION:
        return text
    top = _exact(payload, _TASK_TOP, "任务提取顶层")
    if top["schema_version"] != TASK_EXTRACTION_SCHEMA_VERSION:
        raise ExtractionNormalizationError("schema_version_error", "任务提取 schema_version 无效。")
    if not isinstance(top["tasks"], list) or len(top["tasks"]) > MAX_TASKS:
        raise ExtractionNormalizationError("field_type_error", "tasks 必须是合理长度的数组。")

    canonical_tasks = []
    canonical_questions = []
    for index, raw_task in enumerate(top["tasks"], 1):
        task = _allowed(raw_task, _TASK_REQUIRED, _TASK_OPTIONAL, "任务")
        original_text = _fragment(task["original_text"], user_text, "任务原始片段")
        if not isinstance(task["title"], str) or not task["title"].strip():
            raise ExtractionNormalizationError("field_type_error", "任务标题无效。")
        if not isinstance(task["features"], dict) or not set(task["features"]).issubset(_FEATURES):
            raise ExtractionNormalizationError("missing_required_field", "features 包含未知字段。")
        if "location_requirement" not in task["features"]:
            raise ExtractionNormalizationError("missing_required_field", "features 缺少 location_requirement。")
        features = dict(_FEATURE_DEFAULTS)
        features.update(task["features"])
        extracted = _string_list(task.get("extracted_fields", []), _FEATURES, "extracted_fields")
        estimated = _string_list(task.get("estimated_fields", []), _FEATURES, "estimated_fields")
        confirmations = _string_list(task.get("confirmation_fields", []), _FEATURES, "confirmation_fields")
        if set(extracted) & set(estimated):
            raise ExtractionNormalizationError("source_contract_error", "字段不能同时标记为提取和暂估。")
        valued = {name for name, value in features.items() if _has_value(value)}
        if not (set(extracted) | set(estimated)).issubset(valued):
            raise ExtractionNormalizationError("source_contract_error", "空字段不能声明来源。")
        for name in features:
            if name not in valued or name in extracted or name in estimated:
                continue
            if _infer_source(name, features[name], original_text) == "extracted":
                extracted.append(name)
            else:
                estimated.append(name)

        needs = list(confirmations)
        for name in estimated:
            if name in _IMPORTANT_CONFIRMATION_FIELDS and name not in needs:
                needs.append(name)
        if features["location_requirement"] == "location_requirement_unknown":
            if "location_requirement" not in needs:
                needs.append("location_requirement")
        if features["location_requirement"] == "specific_location" and features["location_text"] is None:
            if "location_text" not in needs:
                needs.append("location_text")
        evidence = {}
        for name in extracted:
            evidence[name] = _evidence(name, False)
        for name in estimated:
            evidence[name] = _evidence(name, True)
        task_ref = "input-task-%d" % index
        canonical_tasks.append({
            "task_ref": task_ref,
            "title": task["title"].strip(),
            "original_text": original_text,
            "understood_features": features,
            "field_evidence": evidence,
            "needs_confirmation": needs,
        })
        question = _task_confirmation_question(
            task_ref, task["title"].strip(), features, needs, index)
        if question is not None:
            canonical_questions.append(question)

    canonical = {
        "schema_version": SCHEMA_VERSION,
        "task_interpretations": canonical_tasks,
        "clarification_questions": canonical_questions,
    }
    return json.dumps(canonical, ensure_ascii=False)


def _window_evidence(label):
    return {
        "source": "ai_extracted_from_user_text",
        "explanation": "根据用户原话识别出%s" % label,
    }


def _iso(value, name):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ExtractionNormalizationError("time_format_error", name + "必须是完整 ISO datetime 或 null。")
    if _ISO_DATETIME.fullmatch(value) is None:
        raise ExtractionNormalizationError("time_format_error", name + "必须是完整 ISO datetime。")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ExtractionNormalizationError("time_format_error", name + "必须是完整 ISO datetime。")
    if parsed.tzinfo is not None:
        raise ExtractionNormalizationError("time_format_error", name + "不能带时区。")
    return parsed


def _question(question_id, target_ref, field_name, text, options=()):
    return {
        "question_id": question_id,
        "target_ref": target_ref,
        "field_name": field_name,
        "question": text,
        "blocking": True,
        "quick_options": list(options),
    }


def normalize_window_extraction(raw_reply, user_text, reference_datetime):
    """Return canonical p1.window-context.v1 JSON."""
    text, payload = _load(raw_reply, MAX_WINDOW_OUTPUT_LENGTH)
    if payload.get("schema_version") == WINDOW_SCHEMA_VERSION:
        return text
    top = _allowed(payload, _WINDOW_REQUIRED, _WINDOW_OPTIONAL, "窗口提取顶层")
    if top["schema_version"] != WINDOW_EXTRACTION_SCHEMA_VERSION:
        raise ExtractionNormalizationError("schema_version_error", "窗口提取 schema_version 无效。")
    if not isinstance(reference_datetime, datetime):
        raise ExtractionNormalizationError("time_format_error", "可信参考时间无效。")

    current_location = top["current_location"]
    current_evidence = {}
    current_needs = []
    if current_location is None:
        location_text = location_fragment = None
        current_needs.append("current_location_text")
    else:
        current_location = _exact(current_location, _CURRENT_LOCATION, "current_location")
        location_text = current_location["text"]
        if not isinstance(location_text, str) or not location_text.strip():
            raise ExtractionNormalizationError("field_type_error", "当前位置文本无效。")
        location_text = location_text.strip()
        location_fragment = _fragment(current_location["fragment"], user_text, "当前位置片段")
        current_evidence["current_location_text"] = _window_evidence("当前位置")

    assumed_raw = top.get("assumed_current_datetime")
    if assumed_raw is None:
        assumed_value = assumed_fragment = None
        current_datetime = reference_datetime
    else:
        assumed_raw = _exact(assumed_raw, _ASSUMED_TIME, "assumed_current_datetime")
        assumed_value = assumed_raw["value"]
        current_datetime = _iso(assumed_value, "假定当前时间")
        assumed_fragment = _fragment(assumed_raw["fragment"], user_text, "假定时间片段")
        current_evidence["assumed_current_datetime"] = _window_evidence("假定当前时间")

    if not isinstance(top["commitments"], list) or len(top["commitments"]) > MAX_COMMITMENTS:
        raise ExtractionNormalizationError("field_type_error", "commitments 必须是合理长度的数组。")
    commitments = []
    questions = []
    question_number = 1
    for index, raw_item in enumerate(top["commitments"], 1):
        item = _exact(raw_item, _COMMITMENT, "固定安排")
        if not isinstance(item["title"], str) or not item["title"].strip():
            raise ExtractionNormalizationError("field_type_error", "固定安排标题无效。")
        original_text = _fragment(item["original_text"], user_text, "固定安排原始片段")
        starts = _iso(item["starts_at"], "固定安排开始时间")
        ends = _iso(item["ends_at"], "固定安排结束时间")
        if starts is not None and starts <= current_datetime:
            raise ExtractionNormalizationError("time_format_error", "固定安排开始时间必须晚于当前时间。")
        if starts is not None and ends is not None and ends < starts:
            raise ExtractionNormalizationError("time_format_error", "固定安排结束时间不能早于开始时间。")
        location = item["location_text"]
        if location is not None and (not isinstance(location, str) or not location.strip()):
            raise ExtractionNormalizationError("field_type_error", "固定安排地点无效。")
        if isinstance(location, str):
            location = location.strip()
        availability = item["availability_during"]
        evidence = {}
        for name, value, label in (
                ("starts_at", item["starts_at"], "固定安排开始时间"),
                ("ends_at", item["ends_at"], "固定安排结束时间"),
                ("location_text", location, "固定安排地点"),
                ("availability_during", availability, "安排期间可用程度")):
            if value is not None:
                evidence[name] = _window_evidence(label)
        needs = []
        ref = "input-commitment-%d" % index
        if starts is None:
            needs.append("starts_at")
            questions.append(_question(
                "window-question-%d" % question_number, ref, "starts_at",
                "“%s”什么时候开始？" % item["title"].strip()))
            question_number += 1
        if location is None:
            needs.append("location_text")
            questions.append(_question(
                "window-question-%d" % question_number, ref, "location_text",
                "“%s”在哪里进行？" % item["title"].strip()))
            question_number += 1
        if availability in ("low_attention", "fully_available") and ends is None:
            needs.append("ends_at")
            questions.append(_question(
                "window-question-%d" % question_number, ref, "ends_at",
                "“%s”什么时候结束？" % item["title"].strip()))
            question_number += 1
        commitments.append({
            "commitment_ref": ref,
            "title": item["title"].strip(),
            "original_text": original_text,
            "starts_at": item["starts_at"],
            "ends_at": item["ends_at"],
            "location_text": location,
            "availability_during": availability,
            "field_evidence": evidence,
            "needs_confirmation": needs,
        })

    free_duration = top["free_duration_minutes"]
    window_end = top["ends_at"]
    user_buffer = top["user_buffer_minutes"]
    parsed_window_end = _iso(window_end, "空闲结束时间")
    if parsed_window_end is not None and parsed_window_end <= current_datetime:
        raise ExtractionNormalizationError("time_format_error", "空闲结束时间必须晚于当前时间。")
    constraint_evidence = {}
    for name, value, label in (
            ("free_duration_minutes", free_duration, "明确空闲时长"),
            ("ends_at", window_end, "明确空闲结束时间"),
            ("user_buffer_minutes", user_buffer, "用户安全缓冲")):
        if value is not None:
            constraint_evidence[name] = _window_evidence(label)
    constraint_needs = []
    if not commitments and free_duration is None and window_end is None:
        constraint_needs.append("free_duration_minutes")
        questions.append(_question(
            "window-question-%d" % question_number,
            "window_constraints", "free_duration_minutes",
            "你接下来有多少可用时间？",
            ("15 分钟", "30 分钟", "1 小时")))

    if location_text is None:
        questions.insert(0, _question(
            "window-question-current-location", "current_context",
            "current_location_text", "你现在在哪里？"))
    canonical = {
        "schema_version": WINDOW_SCHEMA_VERSION,
        "current_context": {
            "current_location_text": location_text,
            "current_location_fragment": location_fragment,
            "assumed_current_datetime": assumed_value,
            "assumed_current_datetime_fragment": assumed_fragment,
            "field_evidence": current_evidence,
            "needs_confirmation": current_needs,
        },
        "commitments": commitments,
        "window_constraints": {
            "free_duration_minutes": free_duration,
            "ends_at": window_end,
            "user_buffer_minutes": user_buffer,
            "field_evidence": constraint_evidence,
            "needs_confirmation": constraint_needs,
        },
        "clarification_questions": questions[:MAX_WINDOW_QUESTIONS],
    }
    return json.dumps(canonical, ensure_ascii=False)
