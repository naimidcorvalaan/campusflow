"""DeepSeek Chat Completions, single attempt, final content only.

Official model/parameter reference: docs/llm_providers.md. HTTP requests stay
server-side; neither credentials nor provider response bodies are logged.
"""
import os
import requests
from urllib.parse import urlsplit
from src.llm_errors import LLMClientError, LLMConfigurationError, VisionUnavailable
from src.llm_messages import validate_messages
from src.llm_diagnostics import _log_failure, _log_transport_failure

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_TEXT_MODEL = "deepseek-flash"
DEFAULT_VISION_MODEL = "deepseek-flash"


def _configured(name, default=""):
    return os.environ.get(name, "").strip() or default


class DeepSeekProvider:
    def completion(self, prompt, *, system_prompt=None, **kwargs):
        messages = []
        if system_prompt is not None:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return self.chat(messages, **kwargs)

    def chat(self, messages, *, model=None, timeout=30, max_tokens=256,
             temperature=0.2):
        messages = validate_messages(messages)
        visual = False
        for message in messages:
            content = message["content"]
            if isinstance(content, list) and any(p["type"] == "image_url" for p in content):
                if message["role"] != "user":
                    raise LLMClientError("图片必须放在用户消息中。")
                visual = True
        key = _configured("DEEPSEEK_API_KEY")
        if not key:
            raise LLMConfigurationError("缺少配置：DEEPSEEK_API_KEY。")
        base = _configured("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL)
        # Reject credentials/query tokens in configuration before any request.
        try:
            endpoint = urlsplit(base)
            valid = (endpoint.scheme == "https" and endpoint.hostname and
                     not endpoint.username and not endpoint.password and
                     not endpoint.query and not endpoint.fragment)
        except ValueError:
            valid = False
        if not valid:
            raise LLMConfigurationError("DEEPSEEK_BASE_URL 需要不含凭据和查询参数的 HTTPS 地址。")
        selected = _configured("DEEPSEEK_VISION_MODEL", DEFAULT_VISION_MODEL) if visual else (
            model or _configured("DEEPSEEK_MODEL", DEFAULT_TEXT_MODEL))
        url = base.rstrip("/") + "/chat/completions"
        payload = dict(model=selected, messages=list(messages), max_tokens=max_tokens,
                       temperature=0.2 if temperature is None else temperature,
                       thinking={"type": "disabled"}, stream=False)
        error_type = VisionUnavailable if visual else LLMClientError
        safe_message = ("当前图片理解服务暂不可用，请稍后重试。" if visual else
                        "模型服务暂时不可用，当前计划未改变。")
        try:
            response = requests.post(url, json=payload, timeout=timeout,
                headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                allow_redirects=False)
        except Exception as exc:
            _log_transport_failure(url, key, exc, provider="deepseek")
            raise error_type(safe_message) from None
        if response.status_code != 200:
            status = response.status_code
            category = "http_5xx" if 500 <= status <= 599 else "http_{}".format(status)
            _log_failure(url, key, stage="http_response", category=category,
                         response=response, provider="deepseek")
            raise error_type(safe_message)
        try:
            data = response.json()
        except ValueError as exc:
            _log_failure(url, key, stage="response_json", category="response_parse_error",
                         response=response, exc=exc, provider="deepseek")
            raise error_type(safe_message) from None
        try:
            # Never consume reasoning_content, tools or any other response field.
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise ValueError("invalid final content")
            if not content.strip():
                raise ValueError("empty final content")
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            _log_failure(url, key, stage="response_structure", category="response_parse_error",
                         response=response, exc=exc, provider="deepseek")
            raise error_type(safe_message) from None
        # Domain JSON/fences/prose validation and bounded repair remain upstream.
        return content
