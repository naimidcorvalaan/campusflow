import os
import subprocess
import sys

from src import runtime_environment


def test_unconfigured_local_clock_is_untouched(monkeypatch):
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setattr(runtime_environment.time, "tzset",
                        lambda: (_ for _ in ()).throw(AssertionError("clock changed")),
                        raising=False)
    runtime_environment.apply_configured_timezone()


def test_secrets_timezone_is_applied_on_posix(monkeypatch):
    applied = []
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    monkeypatch.setattr(runtime_environment.time, "tzset",
                        lambda: applied.append(os.environ["TZ"]), raising=False)
    runtime_environment.apply_configured_timezone()
    assert applied == ["Asia/Shanghai"]


def test_windows_without_tzset_keeps_existing_clock(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    monkeypatch.delattr(runtime_environment.time, "tzset", raising=False)
    runtime_environment.apply_configured_timezone()


def test_direct_entry_import_without_repo_on_python_path(tmp_path):
    # Reproduce the hosted console-script import path; do not run main or read
    # credentials. Importing the official entry must not need a model call.
    from pathlib import Path
    entry = Path(__file__).resolve().parents[1] / "src" / "p2_live_main.py"
    code = """
import runpy, sys
import dotenv, requests
dotenv.load_dotenv = lambda *a, **k: (_ for _ in ()).throw(AssertionError('dotenv read'))
requests.sessions.Session.request = lambda *a, **k: (_ for _ in ()).throw(AssertionError('network'))
sys.path = [p for p in sys.path if p and p != sys.argv[2]]
runpy.run_path(sys.argv[1], run_name='import_check')
"""
    result = subprocess.run([sys.executable, "-c", code, str(entry), str(entry.parent.parent)],
                            cwd=str(tmp_path), capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_cloud_root_secrets_reach_existing_configuration_and_caller(tmp_path, monkeypatch):
    from streamlit.runtime.secrets import Secrets
    from src.p1_tju_llm_adapter import load_project_configuration
    from src.tju_llm_client import _require_all_envs

    values = {"TJU_LLM_BASE_URL": "https://model.invalid/v1",
              "TJU_LLM_API_KEY": "fake-not-a-secret", "TJU_LLM_MODEL": "fake-model"}
    for name in values:
        monkeypatch.setenv(name, "")  # restore all process changes after the test
    path = tmp_path / "secrets.toml"
    path.write_text("\n".join('{} = "{}"'.format(k, v) for k, v in values.items()),
                    encoding="utf8")
    secrets = Secrets([str(path)])
    monkeypatch.setattr(secrets, "_maybe_install_file_watchers", lambda: None)
    assert secrets.load_if_toml_exists()
    def no_dotenv(*args, **kwargs):
        raise AssertionError("Cloud configuration must not need .env")
    assert load_project_configuration(loader=no_dotenv) == ()
    assert _require_all_envs() == (values["TJU_LLM_BASE_URL"], values["TJU_LLM_API_KEY"],
                                   values["TJU_LLM_MODEL"])
