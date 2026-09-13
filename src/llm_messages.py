"""Existing inline PNG/JPEG message contract shared by both providers."""
from src.llm_errors import LLMClientError


def validate_messages(messages, error_type=LLMClientError, prefix="模型"):
    if not isinstance(messages, (list, tuple)) or not messages:
        raise error_type(prefix + " messages 必须是非空列表。")
    result = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in (
            "system", "user", "assistant"
        ):
            raise error_type(prefix + " message 结构错误。")
        content = message.get("content")
        if not isinstance(content, (str, list)):
            raise error_type(prefix + " message content 结构错误。")
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict) or part.get("type") not in (
                    "text", "image_url"
                ):
                    raise error_type(prefix + " 多模态消息结构错误。")
                if part.get("type") == "text" and not isinstance(part.get("text"), str):
                    raise error_type(prefix + " 多模态文本结构错误。")
                if part.get("type") == "image_url":
                    image = part.get("image_url")
                    url = image.get("url") if isinstance(image, dict) else None
                    if not isinstance(url, str) or not url.startswith(
                        ("data:image/png;base64,", "data:image/jpeg;base64,")
                    ):
                        raise error_type("图片必须以内嵌 JPG 或 PNG 数据发送。")
        result.append({"role": message["role"], "content": content})
    return tuple(result)
