import json
from datetime import datetime

from src.extraction_models import ParseResult
from src.prompt_builder import build_system_prompt, build_user_prompt
from src.structured_parser import StructuredParser
from src.llm_errors import LLMClientError as TJUClientError
from src.llm_provider import call_llm as call_tju_llm


def _validate_user_text(user_text: str) -> None:
    if not isinstance(user_text, str):
        raise TypeError("user_text 必须是字符串。")
    if not user_text.strip():
        raise ValueError("user_text 不能为空。")


def _validate_current_time(current_time: str) -> None:
    if not isinstance(current_time, str):
        raise TypeError("current_time 必须是字符串。")
    if not current_time.strip():
        raise ValueError("current_time 不能为空。")

    accepted_formats = [
        "%H:%M",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%dT%H:%M:%S",
    ]

    for fmt in accepted_formats:
        try:
            datetime.strptime(current_time, fmt)
            return
        except ValueError:
            continue

    raise ValueError("current_time 格式不正确。")


def _normalize_current_time_value(current_time: str) -> str:
    """Normalize the trusted Python time to the validator-compatible HH:MM format."""
    accepted_formats = [
        "%H:%M",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%dT%H:%M:%S",
    ]

    for fmt in accepted_formats:
        try:
            return datetime.strptime(current_time, fmt).strftime("%H:%M")
        except ValueError:
            continue

    raise ValueError("current_time 格式不正确。")


def _inject_current_time_in_json(raw_reply: str, current_time: str) -> str:
    """Only inject the trusted Python current_time into valid JSON objects."""
    try:
        payload = json.loads(raw_reply)
    except (TypeError, ValueError):
        return raw_reply

    if not isinstance(payload, dict):
        return raw_reply

    if not isinstance(current_time, str):
        return raw_reply

    try:
        trusted_time = _normalize_current_time_value(current_time)
    except ValueError:
        return raw_reply

    payload["current_time"] = trusted_time
    return json.dumps(payload, ensure_ascii=False)


def parse_natural_language(user_text: str, current_time: str) -> ParseResult:
    """Parse natural-language route requests into a structured planning request."""
    try:
        _validate_user_text(user_text)
        _validate_current_time(current_time)
    except (TypeError, ValueError) as exc:
        return ParseResult(
            status="rejected",
            error_type="validation_error",
            message=str(exc),
            missing_fields=[],
            questions=[],
            planning_request=None,
        )

    system_prompt = build_system_prompt(current_time)
    user_prompt = build_user_prompt(user_text, current_time)

    try:
        raw_reply = call_tju_llm(
            user_prompt,
            temperature=0,
            system_prompt=system_prompt,
        )
    except TJUClientError:
        return ParseResult(
            status="rejected",
            error_type="api_error",
            message="模型服务暂时不可用，当前计划未改变。",
            missing_fields=[],
            questions=[],
            planning_request=None,
        )

    processed_reply = _inject_current_time_in_json(raw_reply, current_time)
    return StructuredParser.parse(processed_reply)
