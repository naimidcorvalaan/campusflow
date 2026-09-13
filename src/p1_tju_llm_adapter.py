"""P1k: adapt the existing TJU client to P1 callables without eager I/O."""
import os
from pathlib import Path
from src.llm_provider import required_environment


REQUIRED_ENV = ("TJU_LLM_BASE_URL", "TJU_LLM_API_KEY", "TJU_LLM_MODEL")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ENV_PATH = PROJECT_ROOT / ".env"


def missing_configuration(environ=None):
    """Return missing variable names only; never expose configuration values."""
    values = os.environ if environ is None else environ
    return tuple(name for name in required_environment(values) if not str(values.get(name, "")).strip())


def configuration_available(environ=None):
    return not missing_configuration(environ)


def load_project_configuration(environ=None, loader=None, env_path=None):
    """Load the project .env only when necessary, preserving process variables.

    The loader is injectable so tests never need to touch the real project .env.
    Any loader failure is deliberately reduced to the names that remain missing.
    """
    values = os.environ if environ is None else environ
    missing = missing_configuration(values)
    if not missing:
        return ()
    if loader is None:
        from dotenv import load_dotenv
        loader = load_dotenv
    try:
        loader(PROJECT_ENV_PATH if env_path is None else env_path, override=False)
    except Exception:
        pass
    # Local credentials fill only gaps after the original .env loader.
    from src.model_service_config import effective_configuration
    return missing_configuration(effective_configuration(values))


class TJUP1CallAdapter(object):
    """Three stateless callables; parsing and retry remain in P1d/P1g."""

    def __init__(self, call_function=None):
        self._call_function = call_function

    def _call(self, system_prompt, user_prompt):
        if self._call_function is None:
            # Lazy import avoids client/.env side effects during page import and tests.
            from src.llm_provider import call_llm as call_tju_llm
            call_function = call_tju_llm
        else:
            call_function = self._call_function
        return call_function(
            user_prompt,
            system_prompt=system_prompt,
            temperature=0,
        )

    def task_caller(self, system_prompt, user_prompt):
        return self._call(system_prompt, user_prompt)

    def window_caller(self, system_prompt, user_prompt):
        return self._call(system_prompt, user_prompt)

    def candidate_caller(self, system_prompt, user_prompt):
        return self._call(system_prompt, user_prompt)

    def agent_caller(self, system_prompt, user_prompt):
        """Shared stateless caller for generator/reviewer/reviser/presenter stages."""
        return self._call(system_prompt, user_prompt)
