"""First-run setup regression; fake credentials and no real network."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from src import model_service_config as config
from src.model_service_ui import editor_allowed
from src.p1_tju_llm_adapter import load_project_configuration
from src.llm_provider import provider_name

FAKE = {"TJU_LLM_BASE_URL": "https://service.example.invalid/v1",
        "TJU_LLM_MODEL": "tju-llm", "TJU_LLM_API_KEY": "test-key-not-real"}


def test_new_user_and_single_missing_field():
    assert load_project_configuration() == config.FIELDS
    for missing in config.FIELDS:
        values = dict(FAKE)
        values.pop(missing)
        assert load_project_configuration(values) == (missing,)
        with pytest.raises(config.ModelConfigError, match=config.LABELS[missing]):
            config.validate_configuration(values)


def test_save_reload_update_clear_and_no_env_mutation(monkeypatch, tmp_path):
    config.save_local_config(FAKE)
    assert not any(os.environ.get(name) for name in config.FIELDS)
    assert config.config_path().parent == tmp_path / "profile"
    assert load_project_configuration() == ()
    assert config.effective_configuration()["TJU_LLM_API_KEY"] == FAKE["TJU_LLM_API_KEY"]
    updated = dict(FAKE, TJU_LLM_API_KEY="test-key-updated-not-real")
    config.save_local_config(updated)
    # Client and a fresh resolver both read the current file, not old env/cache.
    from src.tju_llm_client import _require_all_envs
    assert _require_all_envs() == (FAKE["TJU_LLM_BASE_URL"], updated["TJU_LLM_API_KEY"], "tju-llm")
    assert FAKE["TJU_LLM_API_KEY"] not in config.config_path().read_text()
    monkeypatch.chdir(tmp_path)
    assert load_project_configuration() == ()  # New release/cwd doesn't matter.
    config.clear_local_config()
    assert load_project_configuration() == config.FIELDS
    assert not config.config_path().exists()


def test_process_env_wins_and_is_not_copied_to_local_file(monkeypatch):
    for name, value in FAKE.items():
        monkeypatch.setenv(name, value)
    config.save_local_config(dict(FAKE, TJU_LLM_API_KEY="test-key-other-not-real"))
    assert config.read_local_config() == {}
    assert load_project_configuration(loader=lambda *a, **k: pytest.fail("unneeded load")) == ()
    config.clear_local_config()
    assert config.effective_configuration()["TJU_LLM_API_KEY"] == FAKE["TJU_LLM_API_KEY"]


def test_existing_dotenv_is_used_and_never_overwritten(monkeypatch, tmp_path):
    from dotenv import dotenv_values
    path = tmp_path / ".env"
    content = "\n".join(name + "=" + value for name, value in FAKE.items())
    path.write_text(content, encoding="utf-8")
    def load(path, override):
        assert override is False
        for name, value in dotenv_values(path).items():
            monkeypatch.setenv(name, value)
    assert load_project_configuration(loader=load, env_path=path) == ()
    config.clear_local_config()
    assert path.read_text(encoding="utf-8") == content
    assert not config.config_path().exists()


def test_partial_env_and_saved_fields(monkeypatch):
    monkeypatch.setenv("TJU_LLM_MODEL", "external-model")
    config.save_local_config(FAKE)
    assert "TJU_LLM_MODEL" not in config.read_local_config()
    assert config.effective_configuration()["TJU_LLM_MODEL"] == "external-model"
    assert load_project_configuration() == ()


def test_atomic_write_failure_keeps_old_config_and_safe_error(monkeypatch, caplog):
    config.save_local_config(FAKE)
    before = config.config_path().read_bytes()
    def fail(*a):
        raise OSError("Authorization test-key-updated-not-real raw private path")
    monkeypatch.setattr(config.os, "replace", fail)
    with pytest.raises(config.ModelConfigError) as error:
        config.save_local_config(dict(FAKE, TJU_LLM_API_KEY="test-key-updated-not-real"))
    assert "test-key" not in str(error.value)
    assert error.value.__suppress_context__
    assert "test-key" not in caplog.text
    assert config.config_path().read_bytes() == before
    assert not list(config.config_path().parent.glob(".model-service-*.tmp"))


@pytest.mark.parametrize("address", ["http://school.example/v1", "https://user:secret@school.example",
    "https://school.example?key=test-key-not-real", "https://school.example/#secret", "file:///secret",
    "https://school.example:bad", "https://school.example/\nkey"])
def test_invalid_address_errors_never_echo_input(address):
    with pytest.raises(config.ModelConfigError) as error:
        config.save_local_config(dict(FAKE, TJU_LLM_BASE_URL=address))
    assert address not in str(error.value)
    assert "test-key-not-real" not in str(error.value)
    assert not config.config_path().exists()


def test_broken_local_file_does_not_break_complete_external_config(monkeypatch):
    config.config_path().parent.mkdir(parents=True)
    config.config_path().write_text("test-key-not-real invalid JSON")
    with pytest.raises(config.ModelConfigError) as error:
        config.read_local_config()
    assert "test-key" not in str(error.value)
    assert load_project_configuration() == config.FIELDS
    for name, value in FAKE.items():
        monkeypatch.setenv(name, value)
    assert load_project_configuration() == ()


@pytest.mark.parametrize("exc", [requests.exceptions.ConnectTimeout,
    requests.exceptions.ReadTimeout, requests.exceptions.SSLError, RuntimeError])
def test_failed_connection_is_bounded_safe_and_non_destructive(exc, caplog):
    config.save_local_config(FAKE)
    calls = []
    def fail(prompt, **kwargs):
        calls.append((prompt, kwargs))
        raise exc("Authorization test-key-not-real private request/response content")
    okay, message = config.test_saved_connection(fail)
    assert not okay and "配置未改变" in message
    assert len(calls) == 1
    assert calls[0][1]["timeout"] == 10
    assert "test-key" not in message + caplog.text
    assert config.read_local_config() == FAKE


def test_successful_test_does_not_echo_response():
    config.save_local_config(FAKE)
    okay, message = config.test_saved_connection(lambda *a, **k: "test-key-not-real echoed")
    assert okay and "test-key" not in message


def test_default_tju_explicit_deepseek_and_local_editor_boundary(monkeypatch):
    local = SimpleNamespace(get_option=lambda name: "127.0.0.1")
    assert provider_name() == "tju" and editor_allowed(local)
    for address in (None, "0.0.0.0", "10.0.0.1"):
        assert not editor_allowed(SimpleNamespace(get_option=lambda name: address))
    config.save_local_config(FAKE)
    monkeypatch.setenv("CAMPUSFLOW_LLM_PROVIDER", "deepseek")
    assert not editor_allowed(local)
    assert load_project_configuration() == ("DEEPSEEK_API_KEY",)
    assert not config.effective_configuration().get("TJU_LLM_API_KEY")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key-deepseek-not-real")
    assert load_project_configuration() == ()


def test_saved_credentials_reach_existing_text_and_vision_protocol(monkeypatch, caplog):
    config.save_local_config(FAKE)
    from src.p2_tju_live_adapter import TJUP2CallAdapter
    calls = []
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(status_code=200, json=lambda: {"choices": [{"message": {"content": "{}"}}]})
    monkeypatch.setattr(requests, "post", post)
    adapter = TJUP2CallAdapter()
    assert adapter.agent_caller("system", "user") == "{}"
    assert adapter.task_estimation_caller("system", "user", "image/png", b"\x89PNG\r\n\x1a\n" + b"fake-image") == "{}"
    assert len(calls) == 2
    assert all(call[1]["headers"]["Authorization"] == "Bearer test-key-not-real" for call in calls)
    assert calls[1][1]["json"]["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "test-key" not in caplog.text


def test_secret_file_is_never_a_release_deliverable():
    from scripts.build_local_release import deliverable
    for path in ("model-service.json", "data/model-service.json", "src/model-service.json",
                 "docs/.model-service-random.tmp"):
        assert not deliverable(Path(path))


@pytest.fixture
def app(monkeypatch):
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    original = st.get_option
    monkeypatch.setattr(st, "get_option", lambda name: "127.0.0.1" if name == "server.address" else original(name))
    app = AppTest.from_file("src/p2_live_main.py", default_timeout=20)
    # Streamlit 1.31 AppTest serializes integer selectboxes through labels.
    app.session_state["p2_live_reference_hour"] = 12
    app.session_state["p2_live_reference_minute"] = 30
    return app


def button(app, label):
    # Streamlit 1.31 testing cannot serialize format_func-backed values.
    # No selectbox is edited in these setup tests: send its current default
    # index, exactly as the browser does. Production controls stay unchanged.
    for select in app.selectbox:
        if select.value is not None and str(select.value) not in select.options:
            select.select_index(select.proto.default)
    return next(item for item in app.button if item.label == label)


def fill(app, address=FAKE["TJU_LLM_BASE_URL"], key=FAKE["TJU_LLM_API_KEY"]):
    app.text_input(key="cf_model_address").set_value(address)
    app.text_input(key="cf_model_key").set_value(key)
    return button(app, "保存配置").click().run()


def test_first_run_form_validation_save_and_fresh_session(app):
    app.run()
    assert not app.exception
    assert len(app.text_input) == 3
    assert app.text_input(key="cf_model_key").proto.type == 1
    assert app.text_input(key="cf_model_model").value == "tju-llm"
    fill(app, address="")
    assert not app.exception
    assert any("API 地址" in x.value for x in app.warning)
    assert app.text_input(key="cf_model_key").value == "test-key-not-real"
    assert len([x for x in app.button if x.label == "保存配置"]) == 1
    fill(app)
    assert not app.exception
    assert not any(x.label == "保存配置" for x in app.button)
    assert any(x.label == "帮我安排" for x in app.button)
    assert not app.session_state.filtered_state.get("cf_model_key")
    fresh = type(app).from_file("src/p2_live_main.py").run(timeout=20)
    assert not fresh.exception
    assert not any(x.label == "保存配置" for x in fresh.button)


def test_existing_environment_skips_first_run(app, monkeypatch):
    for name, value in FAKE.items():
        monkeypatch.setenv(name, value)
    app.run()
    assert not app.exception
    assert not any(x.label == "保存配置" for x in app.button)


def test_skip_then_direct_configuration_link(app):
    app.run()
    button(app, "暂时跳过，先看看").click().run()
    assert not app.exception
    assert any(x.label == "帮我安排" for x in app.button)
    button(app, "连接模型服务").click().run()
    assert not app.exception
    assert len([x for x in app.button if x.label == "保存配置"]) == 1


def test_settings_update_test_failure_clear_preserve_business_state(app, monkeypatch):
    config.save_local_config(FAKE)
    app.run()
    button(app, "个人设置").click().run()
    assert not app.exception
    assert app.text_input(key="cf_model_key").value == ""
    assert len([x for x in app.button if x.label == "保存配置"]) == 1
    app.text_input(key="cf_model_key").set_value("test-key-updated-not-real")
    button(app, "保存配置").click().run()
    assert not app.exception
    assert config.read_local_config()["TJU_LLM_API_KEY"] == "test-key-updated-not-real"
    assert app.text_input(key="cf_model_key").value == ""
    monkeypatch.setattr("src.model_service_ui.test_saved_connection", lambda: (False, "当前无法连接模型服务；已保存的配置未改变。"))
    button(app, "测试已保存的连接").click().run()
    assert not app.exception
    assert any("无法连接" in x.value for x in app.warning)
    assert config.read_local_config()["TJU_LLM_API_KEY"] == "test-key-updated-not-real"
    app.checkbox(key="cf_model_clear_confirm").check()
    button(app, "测试已保存的连接")  # Serialize existing selectboxes only.
    app.run()
    button(app, "清除配置").click().run()
    assert not app.exception
    assert not config.config_path().exists()
    assert len([x for x in app.button if x.label == "保存配置"]) == 1


@pytest.mark.parametrize("field", ["TJU_LLM_BASE_URL", "TJU_LLM_MODEL"])
def test_key_cannot_be_saved_in_visible_fields(field):
    values = dict(FAKE)
    values[field] = values[field] + "/" + FAKE["TJU_LLM_API_KEY"]
    with pytest.raises(config.ModelConfigError) as caught:
        config.save_local_config(values)
    assert FAKE["TJU_LLM_API_KEY"] not in str(caught.value)
    assert not config.config_path().exists()


def test_config_size_limit_roundtrips_or_rejects_without_overwriting():
    config.save_local_config(FAKE)
    before = config.config_path().read_bytes()
    values = dict(FAKE, TJU_LLM_MODEL="模" * 4096, TJU_LLM_API_KEY="密" * 4096)
    with pytest.raises(config.ModelConfigError):
        config.save_local_config(values)
    assert config.config_path().read_bytes() == before


def test_restart_in_new_process_and_directory(tmp_path):
    import subprocess
    import sys
    config.save_local_config(FAKE)
    other = tmp_path / "new-release"
    other.mkdir()
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]),
                       PYTHONDONTWRITEBYTECODE="1")
    code = (
        "import dotenv; dotenv.load_dotenv=lambda *a, **k: False; "
        "from src.p1_tju_llm_adapter import load_project_configuration; "
        "from src.llm_provider import provider_name; "
        "from src.tju_llm_client import _require_all_envs; "
        "assert load_project_configuration()==(); "
        "assert provider_name()=='tju'; "
        "assert _require_all_envs()[1]=='test-key-not-real'; print('restart ok')"
    )
    result = subprocess.run([sys.executable, "-B", "-c", code], cwd=str(other),
                            env=environment, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0 and result.stdout.strip() == "restart ok"


def test_default_windows_config_path_survives_release_directory(monkeypatch, tmp_path):
    monkeypatch.delenv("CAMPUSFLOW_DATA_DIR")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.setattr("src.local_persistence.sys.platform", "win32")
    assert config.config_path() == tmp_path / "Local" / "CampusFlow" / "model-service.json"


@pytest.mark.parametrize("failure", ["transport", "http", "json"])
def test_connection_failure_through_actual_client_has_safe_logs(monkeypatch, caplog, failure):
    config.save_local_config(FAKE)
    before = config.config_path().read_bytes()
    secret = FAKE["TJU_LLM_API_KEY"]
    calls = []
    def post(url, **kwargs):
        calls.append(url)
        assert secret not in url
        if failure == "transport":
            raise requests.exceptions.ConnectionError(secret + " private response")
        def body():
            raise ValueError(secret + " private response")
        return SimpleNamespace(status_code=401 if failure == "http" else 200,
                               json=body, text=secret)
    monkeypatch.setattr(requests, "post", post)
    okay, message = config.test_saved_connection()
    assert not okay and len(calls) == 1
    assert secret not in message + caplog.text
    assert "private response" not in caplog.text
    assert config.config_path().read_bytes() == before


def test_corrupt_file_can_be_cleared_in_first_run(app):
    config.config_path().parent.mkdir(parents=True)
    config.config_path().write_bytes(b"invalid test-key-not-real")
    app.run()
    assert not app.exception
    assert all("test-key-not-real" not in warning.value for warning in app.warning)
    app.checkbox(key="cf_model_clear_confirm").check().run()
    button(app, "清除配置").click().run()
    assert not app.exception and not config.local_config_exists()
    assert any(x.label == "帮我安排" for x in app.button)


def test_external_fields_readonly_and_key_never_returned_to_widgets(app, monkeypatch):
    for name, value in FAKE.items():
        monkeypatch.setenv(name, value)
    app.run()
    button(app, "个人设置").click().run()
    assert not app.exception
    for suffix in ("address", "model", "key"):
        assert app.text_input(key="cf_model_" + suffix).disabled
    assert app.text_input(key="cf_model_key").value == ""
    assert button(app, "保存配置").disabled


def test_settings_keep_saved_key_when_input_blank(app):
    config.save_local_config(FAKE)
    app.run()
    button(app, "个人设置").click().run()
    app.text_input(key="cf_model_model").set_value("updated-model")
    button(app, "保存配置").click().run()
    assert not app.exception
    assert config.read_local_config()["TJU_LLM_MODEL"] == "updated-model"
    assert config.read_local_config()["TJU_LLM_API_KEY"] == FAKE["TJU_LLM_API_KEY"]


def test_local_configuration_never_modifies_business_database():
    from src.local_persistence import LocalProfileStore
    store = LocalProfileStore()
    store.save(expected_revision=0)
    before = store.path.read_bytes()
    config.save_local_config(FAKE)
    config.save_local_config(dict(FAKE, TJU_LLM_API_KEY="test-key-updated-not-real"))
    config.clear_local_config()
    assert store.path.read_bytes() == before
    assert b"test-key" not in before


@pytest.mark.parametrize("kind", ["text", "docx", "pdf_text", "image", "pdf_vision"])
def test_saved_tju_config_material_original_entry(monkeypatch, kind):
    from tests.test_llm_providers import fake_http, response
    from tests.test_material_estimate_recovery import draft_for, action_payload
    from src.material_inbox import extract_material
    from src.p2_tju_live_adapter import TJUP2CallAdapter
    config.save_local_config(FAKE)
    draft, kwargs = draft_for(kind)
    raw = json.dumps(action_payload())
    if kind == "pdf_text":
        from tests.test_file_material import response as material_response
        raw = material_response()
    calls = fake_http(monkeypatch, response(raw))
    adapter = TJUP2CallAdapter()
    result = extract_material(draft, adapter.agent_caller,
        image_caller=adapter.task_estimation_caller, images_caller=adapter.material_images_caller,
        workload_caller=adapter.workload_estimation_caller, **kwargs)
    assert result.diagnostics["estimate_available"]
    assert result.model_calls == len(calls) <= 3
    assert all(call[1]["json"]["model"] == "tju-llm" for call in calls)
    assert all(call[1]["headers"]["Authorization"] == "Bearer test-key-not-real" for call in calls)


def test_saved_tju_config_timetable_original_entry(monkeypatch):
    from tests.test_llm_providers import fake_http, response
    from tests.test_timetable_text_import import settings, payload, course
    from src.timetable_text_import import run_timetable_image_import
    from src.p2_tju_live_adapter import TJUP2CallAdapter
    config.save_local_config(FAKE)
    saved = settings()
    adapter = TJUP2CallAdapter()
    calls = fake_http(monkeypatch, response(payload([course()])))
    draft = run_timetable_image_import("timetable.png", "image/png", b"\x89PNG\r\n\x1a\nfixture",
        saved, adapter.task_estimation_caller, adapter.agent_caller)
    assert draft.rows and saved.courses == ()
    assert calls[0][1]["json"]["model"] == "tju-llm"
    assert calls[0][1]["headers"]["Authorization"] == "Bearer test-key-not-real"


def test_saved_tju_config_planner_original_entry(monkeypatch):
    from tests.test_llm_providers import response
    from tests.test_p2_live_main import CountingCaller, _StubSt, dt, load_live_final_turn
    from src.p2_live_main import main
    config.save_local_config(FAKE)
    model, calls = CountingCaller(), []
    def post(url, **kwargs):
        calls.append(kwargs)
        messages = kwargs["json"]["messages"]
        return response(model(messages[0]["content"], messages[1]["content"]))
    monkeypatch.setattr(requests, "post", post)
    stub = _StubSt().set_inputs(reference_hour=9, reference_minute=0,
        intake="我现在在宿舍，10点到11点半上课。今天要做计组实验。", intake_submitted=True)
    main(st=stub, now_provider=lambda: dt(9))
    assert load_live_final_turn(stub.session_state) is not None and not stub.errors
    assert calls and all(call["json"]["model"] == "tju-llm" for call in calls)
    assert all(call["headers"]["Authorization"] == "Bearer test-key-not-real" for call in calls)



def test_skipped_setup_model_action_points_to_connection_and_retains_input(app):
    from src.p2_live_main import LIVE_INTAKE_KEY
    app.run()
    button(app, "暂时跳过，先看看").click().run()
    app.text_area(key=LIVE_INTAKE_KEY).set_value("今天做实验。")
    button(app, "帮我安排").click().run()
    assert not app.exception
    assert any("请先连接模型服务" in error.value for error in app.error)
    assert app.text_area(key=LIVE_INTAKE_KEY).value == "今天做实验。"
    assert any(item.label == "连接模型服务" for item in app.button)
    assert not config.local_config_exists()
