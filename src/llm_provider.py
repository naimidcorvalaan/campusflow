"""Thin server-side routing. No cached credentials, retries or business state."""
import os
from src.llm_errors import LLMConfigurationError

PROVIDER_ENV = "CAMPUSFLOW_LLM_PROVIDER"
TJU_REQUIRED_ENV = ("TJU_LLM_BASE_URL", "TJU_LLM_API_KEY", "TJU_LLM_MODEL")
DEEPSEEK_REQUIRED_ENV = ("DEEPSEEK_API_KEY",)


def provider_name(environ=None):
    values = os.environ if environ is None else environ
    name = str(values.get(PROVIDER_ENV, "tju")).strip().lower() or "tju"
    if name not in ("tju", "deepseek"):
        # The configured value itself might be a pasted secret.
        raise LLMConfigurationError("CAMPUSFLOW_LLM_PROVIDER 必须是 tju 或 deepseek。")
    return name


def required_environment(environ=None):
    return TJU_REQUIRED_ENV if provider_name(environ) == "tju" else DEEPSEEK_REQUIRED_ENV


class TJUProvider:
    def completion(self, prompt, **kwargs):
        from src.tju_llm_client import call_tju_llm
        return call_tju_llm(prompt, **kwargs)

    def chat(self, messages, **kwargs):
        from src.tju_llm_client import call_tju_llm_messages
        return call_tju_llm_messages(messages, **kwargs)


def get_provider():
    if provider_name() == "tju":
        return TJUProvider()
    from src.deepseek_llm_client import DeepSeekProvider
    return DeepSeekProvider()


def call_llm(prompt, **kwargs):
    return get_provider().completion(prompt, **kwargs)


def call_llm_messages(messages, **kwargs):
    return get_provider().chat(messages, **kwargs)
