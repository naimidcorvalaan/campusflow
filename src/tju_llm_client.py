import ipaddress
import re
import socket
import logging
import os
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlsplit

import requests
from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")

logger = logging.getLogger("campusflow.tju_client")


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


def _exception_chain(exc):
    """Inspect exception objects only, never their messages or request data."""
    found, pending, seen = [], [exc], set()
    while pending and len(found) < 12:
        current = pending.pop(0)
        if not isinstance(current, BaseException) or id(current) in seen:
            continue
        seen.add(id(current))
        found.append(current)
        pending.extend((current.__cause__, current.__context__,
                        getattr(current, "reason", None)))
        pending.extend(arg for arg in current.args if isinstance(arg, BaseException))
    return found


def _failure_kind(exc):
    chain = _exception_chain(exc)
    names = {type(item).__name__ for item in chain}
    if any(isinstance(item, socket.gaierror) for item in chain) or "NameResolutionError" in names:
        return "dns_error", False
    if names & {"SSLError", "SSLCertVerificationError", "CertificateError"}:
        return "tls_error", False
    if names & {"ConnectTimeout", "ConnectTimeoutError"}:
        return "connect_timeout", True
    if names & {"ReadTimeout", "ReadTimeoutError"}:
        return "read_timeout", True
    if any(isinstance(item, requests.exceptions.Timeout) for item in chain) or names & {
        "TimeoutException", "TimeoutError", "PoolTimeout", "WriteTimeout"
    }:
        return "timeout", True
    if any(isinstance(item, requests.exceptions.ConnectionError) for item in chain):
        return "connection_error", False
    if any(type(item).__module__.split(".")[0] == "httpx" for item in chain):
        return "httpx_exception", False
    if isinstance(exc, requests.exceptions.RequestException):
        return "requests_exception", False
    return "unexpected_exception", False


def _safe_endpoint(url, api_key):
    """Only known public hosts/IPs and generic API path segments may be logged.

    Unknown service subdomains and tenant paths can identify a user. Omit them,
    as well as userinfo, port, query and fragment. Never log the original URL.
    """
    host, path = "redacted", "redacted"
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname or ""
        try:
            ipaddress.ip_address(hostname)
            host = hostname
        except ValueError:
            if hostname in {"ai.tju.edu.cn", "api.tju.edu.cn", "llm.tju.edu.cn",
                            "model.invalid"}:
                host = hostname
        segments = parsed.path.split("/")
        if all(part in {"", "api", "openai", "compatible-mode", "chat", "completions"}
               or re.fullmatch(r"v[0-9]{1,2}", part) for part in segments):
            path = parsed.path
        else:
            path = "/redacted/chat/completions"
    except (ValueError, TypeError):
        pass
    return tuple(_safe_diagnostic_token(value, api_key) for value in (host, path))


def _safe_diagnostic_token(value, api_key):
    if api_key and api_key.casefold() in value.casefold():
        return "redacted"
    return value if re.fullmatch(r"[A-Za-z0-9_./:\-]{1,180}", value) else "redacted"


def _log_failure(url, api_key, *, stage, category, exc=None, response=None,
                 timeout=False):
    """Fixed metadata only: no str(exc), traceback, headers, body or payload."""
    if response is None and exc is not None:
        response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    status = status if type(status) is int and 100 <= status <= 599 else None
    chain = _exception_chain(exc)
    exception_type = _safe_diagnostic_token(type(exc).__name__, api_key) if exc else "none"
    cause_type = _safe_diagnostic_token(type(chain[-1]).__name__, api_key) if chain else "none"
    host, path = _safe_endpoint(url, api_key)
    logger.error(
        "[CampusFlow][TJU] stage=%s category=%s exception_type=%s cause_type=%s "
        "status=%s timeout=%s response_received=%s endpoint_host=%s endpoint_path=%s",
        stage, category, exception_type, cause_type, status, timeout,
        response is not None, host, path,
    )


def _log_transport_failure(url, api_key, exc):
    category, timeout = _failure_kind(exc)
    _log_failure(url, api_key, stage="request", category=category, exc=exc,
                 timeout=timeout)


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
        _log_transport_failure(url, api_key, exc)
        raise TJUClientError("TJU LLM 请求超时。") from exc
    except requests.exceptions.ConnectionError as exc:
        _log_transport_failure(url, api_key, exc)
        raise TJUClientError("TJU LLM 连接失败。") from exc
    except requests.exceptions.RequestException as exc:
        _log_transport_failure(url, api_key, exc)
        raise TJUClientError(f"TJU LLM 请求失败：{exc.__class__.__name__}。") from exc
    except Exception as exc:
        # Preserve unexpected/httpx exception semantics; add metadata only.
        _log_transport_failure(url, api_key, exc)
        raise

    status_code = response.status_code

    if status_code != 200:
        category = "http_5xx" if 500 <= status_code <= 599 else "http_{}".format(status_code)
        _log_failure(url, api_key, stage="http_response", category=category,
                     response=response)

    if status_code == 200:
        try:
            data = response.json()
        except ValueError as exc:
            _log_failure(url, api_key, stage="response_json", category="response_parse_error",
                         exc=exc, response=response)
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
            _log_failure(url, api_key, stage="response_structure", category="response_parse_error",
                         exc=exc, response=response)
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
