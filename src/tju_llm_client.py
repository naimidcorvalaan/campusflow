import json
import logging
import os
from pathlib import Path
from typing import Optional, Tuple

import requests
from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")

logger = logging.getLogger("campusflow.tju_client")

# 开发侧 HTTP 诊断日志：只输出到终端，绝不展示给用户页面。
# 响应体最多打印约 1500 字符；API key / Authorization 一律脱敏。
_MAX_RESPONSE_BODY_LOG_CHARS = 1500
_SENSITIVE_TEXT_PATTERNS = ("authorization", "proxy-authorization", "bearer ")


class TJUClientError(RuntimeError):
    """Raised for configuration and request errors without exposing sensitive values."""


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise TJUClientError(f"缺少配置：{name}。")
    return value


def _require_all_envs() -> Tuple[str, str, str]:
    base_url = _require_env("TJU_LLM_BASE_URL")
    api_key = _require_env("TJU_LLM_API_KEY")
    model_name = _require_env("TJU_LLM_MODEL")
    return base_url, api_key, model_name


def _build_chat_url(base_url: str) -> str:
    clean_base = base_url.rstrip("/")
    return f"{clean_base}/chat/completions"


def _safe_error_message(status_code: Optional[int] = None, detail: Optional[str] = None) -> str:
    if status_code is not None:
        return f"TJU LLM 请求失败，HTTP 状态码为 {status_code}。"
    return detail or "TJU LLM 请求失败。"


def _redact_sensitive(text: str, api_key: Optional[str] = None) -> str:
    """把日志文本中的敏感内容替换为 ***（API key 原文 + 常见敏感标记）。"""
    if not isinstance(text, str):
        return ""
    cleaned = text
    if api_key:
        cleaned = cleaned.replace(api_key, "***")
    lower = cleaned.lower()
    for marker in _SENSITIVE_TEXT_PATTERNS:
        if marker in lower:
            cleaned = cleaned.replace(marker, "***")
    return cleaned


def _log_request_summary(url: str, payload: dict, api_key: Optional[str] = None) -> None:
    """打印请求结构摘要（不含任何敏感值 / API key / 完整 prompt 内容）。"""
    try:
        endpoint = url.split("//", 1)[1] if "//" in url else url
    except Exception:  # noqa: BLE001 - 日志兜底
        endpoint = url
    messages = payload.get("messages") or ()
    system_chars = 0
    user_chars = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        text_length = 0
        if isinstance(content, str):
            text_length = len(content)
        elif isinstance(content, list):
            # Multimodal payloads may contain large base64 image URLs.  Count
            # only text parts and never log or inspect encoded image content.
            text_length = sum(
                len(item.get("text", ""))
                for item in content
                if isinstance(item, dict) and isinstance(item.get("text"), str)
            )
        if message.get("role") == "system":
            system_chars += text_length
        elif message.get("role") == "user":
            user_chars += text_length
    logger.info(
        "[CampusFlow][TJU] request endpoint=%s model=%s messages=%s temperature=%s max_tokens=%s",
        endpoint,
        payload.get("model"),
        len(messages),
        payload.get("temperature"),
        payload.get("max_tokens"),
    )
    logger.info(
        "[CampusFlow][TJU] system_chars=%s user_chars=%s payload_keys=%s",
        system_chars,
        user_chars,
        sorted(str(key) for key in payload.keys()),
    )


def _safe_response_body(response) -> str:
    """安全提取响应体：JSON 优先打印 error/message/detail 等字段，否则纯文本。"""
    text = getattr(response, "text", "")
    if not isinstance(text, str):
        text = ""
    try:
        data = response.json()
    except Exception:  # noqa: BLE001 - 非 JSON 响应直接走文本
        return text
    if isinstance(data, dict):
        safe = {}
        for key in ("error", "message", "detail", "code", "type"):
            if key in data:
                safe[key] = data[key]
        if safe:
            try:
                return json.dumps(safe, ensure_ascii=False)
            except (TypeError, ValueError):
                return text
    return text


def _log_response_error(response, api_key: Optional[str] = None) -> None:
    """HTTP 错误时打印 status / content-type / 截断后的安全响应体。"""
    status = getattr(response, "status_code", None)
    headers = getattr(response, "headers", None) or {}
    content_type = headers.get("Content-Type", "") if isinstance(headers, dict) else ""
    body = _redact_sensitive(_safe_response_body(response), api_key)
    body = body[:_MAX_RESPONSE_BODY_LOG_CHARS]
    logger.error("[CampusFlow][TJU] status=%s", status)
    logger.error("[CampusFlow][TJU] response_content_type=%s", content_type)
    logger.error("[CampusFlow][TJU] response_body=%s", body)


def call_tju_llm_messages(
    messages,
    *,
    model: Optional[str] = None,
    timeout: int = 30,
    max_tokens: int = 256,
    temperature: Optional[float] = 0.2,
) -> str:
    """Send validated OpenAI-compatible messages without logging their content."""
    messages = _validated_messages(messages)
    base_url, api_key, model_name = _require_all_envs()
    resolved_model = model or model_name

    url = _build_chat_url(base_url)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": resolved_model,
        "messages": list(messages),
        "temperature": temperature if temperature is not None else 0.2,
        "max_tokens": max_tokens,
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except requests.exceptions.Timeout as exc:
        raise TJUClientError("TJU LLM 请求超时。") from exc
    except requests.exceptions.ConnectionError as exc:
        raise TJUClientError("TJU LLM 连接失败。") from exc
    except requests.exceptions.RequestException as exc:
        raise TJUClientError(f"TJU LLM 请求失败：{exc.__class__.__name__}。") from exc

    status_code = response.status_code

    if status_code >= 400:
        # 开发侧诊断：用户侧仍只收到安全 TJUClientError，详情只进终端。
        _log_request_summary(url, payload, api_key)
        _log_response_error(response, api_key)

    if status_code == 200:
        try:
            data = response.json()
        except ValueError as exc:
            raise TJUClientError("TJU LLM 返回了非 JSON 响应。") from exc

        try:
            choices = data["choices"]
            if not isinstance(choices, list) or not choices:
                raise ValueError("响应缺少 choices。")

            message = choices[0]["message"]
            content = message["content"]
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict) and "text" in item:
                        parts.append(str(item["text"]))
                if parts:
                    return "".join(parts)
                raise ValueError("响应 content 列表为空。")
            if not isinstance(content, str):
                raise ValueError("响应 content 不是字符串。")
            return content
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise TJUClientError("TJU LLM 响应结构错误。") from exc

    if status_code == 401:
        raise TJUClientError("认证失败：API Key 无效或缺失。")
    if status_code == 429:
        raise TJUClientError("TJU LLM 请求过于频繁，触发速率限制。")
    if status_code == 500:
        raise TJUClientError("TJU LLM 服务端返回 500。")

    raise TJUClientError(_safe_error_message(status_code=status_code))


def call_tju_llm(
    prompt: str,
    *,
    model: Optional[str] = None,
    timeout: int = 30,
    max_tokens: int = 256,
    system_prompt: Optional[str] = None,
    temperature: Optional[float] = 0.2,
) -> str:
    """Send a text-only chat-completion request to the TJU endpoint."""
    messages = []
    if system_prompt is not None:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    return call_tju_llm_messages(
        messages,
        model=model,
        timeout=timeout,
        max_tokens=max_tokens,
        temperature=temperature,
    )


def _validated_messages(messages):
    if not isinstance(messages, (list, tuple)) or not messages:
        raise TJUClientError("TJU LLM messages 必须是非空列表。")
    result = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in (
            "system", "user", "assistant"
        ):
            raise TJUClientError("TJU LLM message 结构错误。")
        content = message.get("content")
        if not isinstance(content, (str, list)):
            raise TJUClientError("TJU LLM message content 结构错误。")
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict) or part.get("type") not in (
                    "text", "image_url"
                ):
                    raise TJUClientError("TJU LLM 多模态消息结构错误。")
                if part.get("type") == "text" and not isinstance(part.get("text"), str):
                    raise TJUClientError("TJU LLM 多模态文本结构错误。")
                if part.get("type") == "image_url":
                    image = part.get("image_url")
                    url = image.get("url") if isinstance(image, dict) else None
                    if not isinstance(url, str) or not url.startswith(
                        ("data:image/png;base64,", "data:image/jpeg;base64,")
                    ):
                        raise TJUClientError("图片必须以内嵌 JPG 或 PNG 数据发送。")
        result.append({"role": message["role"], "content": content})
    return tuple(result)


if __name__ == "__main__":
    try:
        print(call_tju_llm("只回复：连接成功"))
    except TJUClientError as exc:
        print(f"安全错误: {exc}")
        raise SystemExit(1)
