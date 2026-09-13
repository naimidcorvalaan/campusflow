"""P1d：依赖注入的双路离线理解管线。"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Tuple

from src.p1_context_bundle import P1ContextBundle, build_p1_context_bundle
from src.p1_extraction_normalizer import (
    ExtractionNormalizationError,
    normalize_task_extraction,
    normalize_window_extraction,
)
from src.p1_extraction_models import P1TaskUnderstandingParseResult
from src.p1_parser import parse_task_understanding
from src.p1_prompt_builder import build_p1_prompt
from src.p1_window_extraction_models import P1WindowParseResult
from src.p1_window_parser import parse_window_context
from src.p1_window_prompt_builder import build_p1_window_prompt


ModelCaller = Callable[[str, str], str]
RETRY_SUFFIX = "\n\n上次输出未通过严格解析，请只返回符合 schema 的纯 JSON。"
_RETRY_CATEGORIES = {
    "json_format_error": "JSON 格式错误",
    "format_error": "JSON 格式或字段结构错误",
    "missing_required_field": "缺少必要字段或存在未知字段",
    "original_fragment_mismatch": "原始片段不匹配",
    "time_format_error": "时间格式或时间先后错误",
    "source_contract_error": "字段来源数组不一致",
    "field_type_error": "字段类型错误",
    "schema_version_error": "schema 版本错误",
    "validation_error": "字段值未通过校验",
    "call_error": "调用暂时失败",
}


@dataclass(frozen=True)
class P1IntakeResult:
    bundle: P1ContextBundle
    task_attempts: int
    window_attempts: int


def _safe_task_call(caller, system, user, original_user):
    try:
        canonical = normalize_task_extraction(caller(system, user), original_user)
        return parse_task_understanding(canonical, original_user)
    except ExtractionNormalizationError as exc:
        return P1TaskUnderstandingParseResult("rejected", exc.error_type, "任务模型输出未通过严格校验。", None)
    except Exception:
        return P1TaskUnderstandingParseResult("rejected", "call_error", "任务理解暂未完成。", None)


def _safe_window_call(caller, system, user, original_user, reference):
    try:
        canonical = normalize_window_extraction(caller(system, user), original_user, reference)
        return parse_window_context(canonical, original_user, reference)
    except ExtractionNormalizationError as exc:
        return P1WindowParseResult("rejected", exc.error_type, "窗口模型输出未通过严格校验。", None)
    except Exception:
        return P1WindowParseResult("rejected", "call_error", "时间窗口理解暂未完成。", None)


def _retry_prompt(user_prompt, result):
    category = _RETRY_CATEGORIES.get(result.error_type, "字段值未通过校验")
    return user_prompt + RETRY_SUFFIX + "\n失败类别：" + category + "。"


def run_p1_intake(user_text, reference_datetime, task_caller, window_caller):
    """并行首次调用；只有失败分支以相同原始输入重试一次。"""
    if not isinstance(reference_datetime, datetime):
        raise TypeError("reference_datetime 必须是 datetime。")
    task_system, task_user = build_p1_prompt(user_text)
    window_system, window_user = build_p1_window_prompt(user_text, reference_datetime)
    with ThreadPoolExecutor(max_workers=2) as executor:
        task_future = executor.submit(_safe_task_call, task_caller, task_system, task_user, user_text)
        window_future = executor.submit(_safe_window_call, window_caller, window_system, window_user, user_text, reference_datetime)
        task_result, window_result = task_future.result(), window_future.result()
    task_attempts = window_attempts = 1
    if task_result.document is None:
        task_attempts = 2
        task_result = _safe_task_call(task_caller, task_system, _retry_prompt(task_user, task_result), user_text)
    if window_result.document is None:
        window_attempts = 2
        window_result = _safe_window_call(window_caller, window_system, _retry_prompt(window_user, window_result), user_text, reference_datetime)
    return P1IntakeResult(build_p1_context_bundle(task_result, window_result), task_attempts, window_attempts)
