"""P2c Agent 输出解析与校验（Python 3.8 兼容）。

只做结构化校验与安全转换，不重新解释用户意图；解析失败抛出
AgenticParseError（消息只含安全类别，不含异常原文 / 模型原文）。
"""

import json
import re
from typing import Any, Optional, Tuple

from src.p2_agentic_models import (
    DAY_PLAN_INTENT_SCHEMA_VERSION,
    MAX_QUESTION_LENGTH,
    MAX_REASON_LENGTH,
    MAX_TITLE_LENGTH,
    RECONCILIATION_SCHEMA_VERSION,
    REVIEW_SCHEMA_VERSION,
    DayPlanIntent,
    LifecycleAction,
    ReconciliationResult,
    ReconciliationUpdate,
    ReviewResult,
    TaskEstimate,
    TotalSourceChoice,
)


class AgenticParseError(Exception):
    """Agent 输出无法解析或校验时抛出。"""


_ALLOWED_LIFECYCLE_VALUES = {action.value for action in LifecycleAction}
_ALLOWED_SOURCE_VALUES = {choice.value for choice in TotalSourceChoice}
_FENCE = chr(96) * 3
_SENSITIVE_TOKENS = (
    "authorization",
    "bearer ",
    "api key",
    "apikey",
    "traceback",
    "http://",
    "https://",
)


def parse_reconciliation(text: str) -> ReconciliationResult:
    """解析 Reconciliation Agent 输出为 ReconciliationResult。"""
    payload = _load_object(text, RECONCILIATION_SCHEMA_VERSION)
    updates = _require_list(payload, "updates")
    questions = _require_list(payload, "questions")
    parsed_updates = tuple(_parse_update(item) for item in updates)
    _reject_duplicate_targets(parsed_updates)
    return ReconciliationResult(
        schema_version=RECONCILIATION_SCHEMA_VERSION,
        updates=parsed_updates,
        questions=tuple(_parse_question(item) for item in questions),
    )


def _reject_duplicate_targets(updates: Tuple[ReconciliationUpdate, ...]) -> None:
    """同一轮 reconciliation 不允许两个 update 指向同一个非空 target_task_ref。

    模型重复输出会被整体拒绝并交给 repair/fallback，避免用户事实被重复应用。
    新增任务（target_task_ref 为 null）不受此限制。
    """
    seen = set()
    for update in updates:
        if update.target_task_ref is None:
            continue
        if update.target_task_ref in seen:
            raise AgenticParseError(
                "updates 中同一个 target_task_ref 出现多次：{}".format(update.target_task_ref)
            )
        seen.add(update.target_task_ref)


def parse_day_plan_intent(text: str) -> DayPlanIntent:
    """解析 Day Plan Agent 输出为 DayPlanIntent。"""
    payload = _load_object(text, DAY_PLAN_INTENT_SCHEMA_VERSION)
    task_order = _require_list(payload, "task_order")
    include_low_attention = _require_bool(payload, "include_low_attention")
    estimates = _require_list(payload, "task_estimates")
    rationale = _optional_text(payload.get("rationale"), "rationale", MAX_REASON_LENGTH)
    return DayPlanIntent(
        schema_version=DAY_PLAN_INTENT_SCHEMA_VERSION,
        task_order=tuple(_parse_ref(item, "task_order") for item in task_order),
        include_low_attention=include_low_attention,
        task_estimates=tuple(_parse_estimate(item) for item in estimates),
        rationale=rationale,
    )


def parse_review(text: str) -> ReviewResult:
    """解析 Review Agent 输出为 ReviewResult。"""
    payload = _load_object(text, REVIEW_SCHEMA_VERSION)
    decision = _require_str(payload, "decision")
    if decision not in ("accept", "revise"):
        raise AgenticParseError("decision 必须为 accept 或 revise")
    reason = _optional_text(payload.get("reason"), "reason", MAX_REASON_LENGTH)
    suggested = payload.get("suggested_task_order")
    parsed_suggested = None
    if suggested is not None:
        items = _require_list(payload, "suggested_task_order")
        parsed_suggested = tuple(_parse_ref(item, "suggested_task_order") for item in items)
    include_low = _optional_bool(payload.get("include_low_attention"), "include_low_attention")
    return ReviewResult(
        schema_version=REVIEW_SCHEMA_VERSION,
        decision=decision,
        reason=reason,
        suggested_task_order=parsed_suggested,
        include_low_attention=include_low,
    )


def extract_json_object(text: str) -> Any:
    """从模型输出中提取 JSON 对象（容忍 markdown 代码块与尾随逗号）。"""
    if not isinstance(text, str) or not text.strip():
        raise AgenticParseError("模型输出为空")
    candidate = text.strip()
    lines = candidate.splitlines()
    if lines and lines[0].strip().startswith(_FENCE):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith(_FENCE):
        lines = lines[:-1]
    candidate = "\n".join(lines)
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise AgenticParseError("输出中未找到 JSON 对象")
    payload = candidate[start:end + 1]
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        cleaned = _clean_json(payload)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            raise AgenticParseError("JSON 解析失败") from None


def _load_object(text: str, expected_version: str) -> dict:
    payload = extract_json_object(text)
    if not isinstance(payload, dict):
        raise AgenticParseError("输出必须是 JSON 对象")
    if payload.get("schema_version") != expected_version:
        raise AgenticParseError("schema_version 不匹配")
    return payload


def _parse_update(item: Any) -> ReconciliationUpdate:
    if not isinstance(item, dict):
        raise AgenticParseError("updates 中的每一项必须是对象")
    target = _optional_text(item.get("target_task_ref"), "target_task_ref")
    new_title = _optional_text(item.get("new_task_title"), "new_task_title", MAX_TITLE_LENGTH)
    if target is not None and new_title is not None:
        raise AgenticParseError("target_task_ref 与 new_task_title 不能同时设置")
    if target is None and new_title is None:
        raise AgenticParseError("update 必须引用已有任务或定义新任务")
    progress = _optional_int(item.get("progress_delta_minutes"), "progress_delta_minutes", minimum=1)
    set_total = _optional_int(item.get("set_total_minutes"), "set_total_minutes", minimum=1)
    source_value = item.get("set_total_source")
    source = None
    if source_value is not None:
        if not isinstance(source_value, str) or source_value not in _ALLOWED_SOURCE_VALUES:
            raise AgenticParseError("set_total_source 必须为 user_text 或 ai_estimate")
        source = TotalSourceChoice(source_value)
    if (set_total is None) != (source is None):
        raise AgenticParseError("set_total_minutes 与 set_total_source 必须同时提供")
    lifecycle_value = item.get("lifecycle_action")
    if not isinstance(lifecycle_value, str) or lifecycle_value not in _ALLOWED_LIFECYCLE_VALUES:
        raise AgenticParseError("lifecycle_action 非法")
    lifecycle = LifecycleAction(lifecycle_value)
    splittable = _optional_bool(item.get("is_splittable"), "is_splittable")
    min_slice = _optional_int(item.get("minimum_slice_minutes"), "minimum_slice_minutes", minimum=1)
    return ReconciliationUpdate(
        target_task_ref=target,
        new_task_title=new_title,
        progress_delta_minutes=progress,
        set_total_minutes=set_total,
        set_total_source=source,
        lifecycle_action=lifecycle,
        is_splittable=splittable,
        minimum_slice_minutes=min_slice,
    )


def _parse_estimate(item: Any) -> TaskEstimate:
    if not isinstance(item, dict):
        raise AgenticParseError("task_estimates 中的每一项必须是对象")
    ref = _require_str(item, "task_ref")
    total = _optional_int(item.get("estimated_total_minutes"), "estimated_total_minutes", minimum=1)
    if total is None:
        raise AgenticParseError("estimated_total_minutes 必须为正整数")
    splittable = _optional_bool(item.get("is_splittable"), "is_splittable")
    min_slice = _optional_int(item.get("minimum_slice_minutes"), "minimum_slice_minutes", minimum=1)
    return TaskEstimate(
        task_ref=ref,
        estimated_total_minutes=total,
        is_splittable=splittable,
        minimum_slice_minutes=min_slice,
    )


def _parse_question(item: Any) -> str:
    return _optional_text(item, "question", MAX_QUESTION_LENGTH)


def _parse_ref(item: Any, name: str) -> str:
    if not isinstance(item, str) or not item.strip():
        raise AgenticParseError("{} 中的每一项必须是非空字符串".format(name))
    return item


def _optional_int(value: Any, name: str, minimum: int) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise AgenticParseError("{} 必须是整数".format(name))
    if value < minimum:
        raise AgenticParseError("{} 必须 >= {}".format(name, minimum))
    return value


def _optional_bool(value: Any, name: str) -> Optional[bool]:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise AgenticParseError("{} 必须是布尔值".format(name))
    return value


def _optional_text(value: Any, name: str, max_len: Optional[int] = None) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise AgenticParseError("{} 必须是非空字符串".format(name))
    stripped = value.strip()
    if not stripped:
        return None
    if _has_sensitive_content(stripped):
        return "[已过滤敏感内容]"
    if max_len is not None and len(stripped) > max_len:
        stripped = stripped[:max_len]
    return stripped


def _require_str(payload: dict, name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise AgenticParseError("{} 必须是非空字符串".format(name))
    return value


def _require_bool(payload: dict, name: str) -> bool:
    value = payload.get(name)
    if not isinstance(value, bool):
        raise AgenticParseError("{} 必须是布尔值".format(name))
    return value


def _require_list(payload: dict, name: str) -> list:
    value = payload.get(name)
    if value is None:
        return []
    if not isinstance(value, list):
        raise AgenticParseError("{} 必须是数组".format(name))
    return value


def _clean_json(payload: str) -> str:
    return re.sub(r",\s*([}\]])", r"\1", payload)


def _has_sensitive_content(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in _SENSITIVE_TOKENS)
