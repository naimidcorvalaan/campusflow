import importlib
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock

import pytest

import src.p1_tju_llm_adapter as adapter_module
from src.p1_tju_llm_adapter import (
    REQUIRED_ENV,
    TJUP1CallAdapter,
    configuration_available,
    load_project_configuration,
    missing_configuration,
)


@pytest.mark.parametrize("method_name,user_prompt", [
    ("task_caller", "task-user"),
    ("window_caller", "window-user"),
    ("candidate_caller", "candidate-user"),
    ("agent_caller", "agent-user"),
])
def test_each_adapter_callable_keeps_messages_separate_and_temperature_zero(
        method_name, user_prompt):
    calls = []

    def fake(prompt, **kwargs):
        calls.append((prompt, kwargs))
        return "model text"

    result = getattr(TJUP1CallAdapter(fake), method_name)("system", user_prompt)
    assert result == "model text"
    assert calls == [(user_prompt, {"system_prompt": "system", "temperature": 0})]


def test_adapter_does_not_retry_and_propagates_client_exception():
    calls = []

    def failing(prompt, **kwargs):
        calls.append(prompt)
        raise RuntimeError("Bearer secret https://example.invalid")

    with pytest.raises(RuntimeError):
        TJUP1CallAdapter(failing).task_caller("system", "user")
    assert calls == ["user"]


def test_parallel_callers_do_not_share_mutable_request_state():
    barrier = Barrier(2)
    lock = Lock()
    calls = []

    def fake(prompt, **kwargs):
        barrier.wait(timeout=2)
        with lock:
            calls.append((prompt, kwargs["system_prompt"]))
        return prompt

    adapter = TJUP1CallAdapter(fake)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda item: item[0](item[1], item[2]),
            ((adapter.task_caller, "task-system", "task-user"),
             (adapter.window_caller, "window-system", "window-user")),
        ))
    assert set(results) == {"task-user", "window-user"}
    assert set(calls) == {("task-user", "task-system"),
                          ("window-user", "window-system")}


def test_complete_process_environment_skips_dotenv_loader():
    environ = {name: "configured" for name in REQUIRED_ENV}
    calls = []
    assert load_project_configuration(environ, lambda *a, **k: calls.append((a, k))) == ()
    assert calls == []


def test_missing_environment_uses_injected_loader_with_override_false(tmp_path):
    environ = {}
    calls = []
    env_path = tmp_path / "not-read.env"

    def loader(path, override):
        calls.append((path, override))
        environ.update({name: "fake-value" for name in REQUIRED_ENV})

    assert load_project_configuration(environ, loader, env_path) == ()
    assert calls == [(env_path, False)]


def test_configuration_reports_names_only_and_never_values():
    environ = {"TJU_LLM_API_KEY": "UNIQUE_FAKE_API_KEY"}
    missing = missing_configuration(environ)
    assert missing == ("TJU_LLM_BASE_URL", "TJU_LLM_MODEL")
    assert "UNIQUE_FAKE_API_KEY" not in repr(missing)
    assert not configuration_available(environ)


def test_dotenv_loader_failure_is_reduced_to_missing_names():
    def failing_loader(path, override):
        raise RuntimeError("Authorization Bearer UNIQUE_FAKE_API_KEY https://invalid")

    result = load_project_configuration({}, failing_loader, "fake.env")
    assert result == REQUIRED_ENV
    joined = " ".join(result)
    assert "Bearer" not in joined and "UNIQUE_FAKE_API_KEY" not in joined


def test_adapter_module_import_does_not_import_tju_client_or_touch_network():
    previous = sys.modules.pop("src.tju_llm_client", None)
    try:
        importlib.reload(adapter_module)
        assert "src.tju_llm_client" not in sys.modules
    finally:
        if previous is not None:
            sys.modules["src.tju_llm_client"] = previous
