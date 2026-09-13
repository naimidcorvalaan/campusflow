import logging
import os
from pathlib import Path
from typing import Optional, Tuple

import requests
from dotenv import load_dotenv
from src.llm_errors import LLMClientError
from src.llm_messages import validate_messages
from src.llm_diagnostics import _log_failure, _log_transport_failure


ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")

logger = logging.getLogger("campusflow.tju_client")


class TJUClientError(LLMClientError):
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
    return validate_messages(messages, error_type=TJUClientError, prefix="TJU LLM")


if __name__ == "__main__":
    try:
        print(call_tju_llm("只回复：连接成功"))
    except TJUClientError as exc:
        print(f"安全错误: {exc}")
        raise SystemExit(1)
