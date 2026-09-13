import pytest
import requests

from src.tju_llm_client import TJUClientError, call_tju_llm, call_tju_llm_messages


@pytest.fixture
def valid_env(monkeypatch):
    monkeypatch.setenv("TJU_LLM_BASE_URL", "https://ai.tju.edu.cn/api/v3")
    monkeypatch.setenv("TJU_LLM_API_KEY", "test-api-key-123")
    monkeypatch.setenv("TJU_LLM_MODEL", "tju-llm")


def test_success_response(valid_env, monkeypatch):
    class DummyResponse:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "连接成功"}}]}

    def fake_post(url, headers, json, timeout):
        assert url == "https://ai.tju.edu.cn/api/v3/chat/completions"
        assert headers["Authorization"] == "Bearer test-api-key-123"
        assert headers["Content-Type"] == "application/json"
        assert json["model"] == "tju-llm"
        assert json["max_tokens"] == 256
        assert timeout == 30
        return DummyResponse()

    monkeypatch.setattr("requests.post", fake_post)

    response = call_tju_llm("只回复：连接成功")

    assert response == "连接成功"


def test_multimodal_message_boundary_preserves_payload_without_logging_image(valid_env, monkeypatch, caplog):
    class DummyResponse:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "结构化结果"}}]}

    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["payload"] = json
        return DummyResponse()

    monkeypatch.setattr("requests.post", fake_post)
    marker = "private-base64-image-marker"
    result = call_tju_llm_messages([
        {"role": "system", "content": "只分析任务"},
        {"role": "user", "content": [
            {"type": "text", "text": "估算这份任务"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + marker}},
        ]},
    ])
    assert result == "结构化结果"
    assert captured["payload"]["messages"][1]["content"][1]["image_url"]["url"].endswith(marker)
    assert marker not in caplog.text


def test_missing_configuration(monkeypatch):
    monkeypatch.delenv("TJU_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("TJU_LLM_API_KEY", raising=False)
    monkeypatch.delenv("TJU_LLM_MODEL", raising=False)

    with pytest.raises(TJUClientError, match="缺少配置：TJU_LLM_BASE_URL") as exc:
        call_tju_llm("只回复：连接成功")

    assert "test-api-key-123" not in str(exc.value)


def test_missing_all_three_configs(monkeypatch):
    monkeypatch.setenv("TJU_LLM_BASE_URL", "")
    monkeypatch.setenv("TJU_LLM_API_KEY", "")
    monkeypatch.setenv("TJU_LLM_MODEL", "")

    with pytest.raises(TJUClientError, match="缺少配置") as exc:
        call_tju_llm("只回复：连接成功")

    error_text = str(exc.value)
    assert any(var in error_text for var in ["TJU_LLM_BASE_URL", "TJU_LLM_API_KEY", "TJU_LLM_MODEL"])
    assert "test-api-key-123" not in error_text


def test_unauthorized_status(valid_env, monkeypatch):
    class DummyResponse:
        status_code = 401

    monkeypatch.setattr("requests.post", lambda *args, **kwargs: DummyResponse())

    with pytest.raises(TJUClientError, match="认证失败") as exc:
        call_tju_llm("只回复：连接成功")

    assert "test-api-key-123" not in str(exc.value)


def test_rate_limit_status(valid_env, monkeypatch):
    class DummyResponse:
        status_code = 429

    monkeypatch.setattr("requests.post", lambda *args, **kwargs: DummyResponse())

    with pytest.raises(TJUClientError, match="速率限制") as exc:
        call_tju_llm("只回复：连接成功")

    assert "test-api-key-123" not in str(exc.value)


def test_server_error_status(valid_env, monkeypatch):
    class DummyResponse:
        status_code = 500

    monkeypatch.setattr("requests.post", lambda *args, **kwargs: DummyResponse())

    with pytest.raises(TJUClientError, match="500") as exc:
        call_tju_llm("只回复：连接成功")

    assert "test-api-key-123" not in str(exc.value)


def test_timeout(valid_env, monkeypatch):
    def fake_post(*args, **kwargs):
        raise requests.exceptions.Timeout("timed out")

    monkeypatch.setattr("requests.post", fake_post)

    with pytest.raises(TJUClientError, match="超时") as exc:
        call_tju_llm("只回复：连接成功")

    assert "test-api-key-123" not in str(exc.value)


def test_connection_error(valid_env, monkeypatch):
    def fake_post(*args, **kwargs):
        raise requests.exceptions.ConnectionError("connection failed")

    monkeypatch.setattr("requests.post", fake_post)

    with pytest.raises(TJUClientError, match="连接失败") as exc:
        call_tju_llm("只回复：连接成功")

    assert "test-api-key-123" not in str(exc.value)


def test_non_json_response(valid_env, monkeypatch):
    class DummyResponse:
        status_code = 200

        def json(self):
            raise ValueError("not json")

    monkeypatch.setattr("requests.post", lambda *args, **kwargs: DummyResponse())

    with pytest.raises(TJUClientError, match="非 JSON") as exc:
        call_tju_llm("只回复：连接成功")

    assert "test-api-key-123" not in str(exc.value)


def test_invalid_response_structure(valid_env, monkeypatch):
    class DummyResponse:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {}}]}

    monkeypatch.setattr("requests.post", lambda *args, **kwargs: DummyResponse())

    with pytest.raises(TJUClientError, match="响应结构错误") as exc:
        call_tju_llm("只回复：连接成功")

    assert "test-api-key-123" not in str(exc.value)


def test_error_message_does_not_expose_key(valid_env, monkeypatch):
    monkeypatch.setenv("TJU_LLM_API_KEY", "super-secret-key-xyz")

    class DummyResponse:
        status_code = 401

    monkeypatch.setattr("requests.post", lambda *args, **kwargs: DummyResponse())

    with pytest.raises(TJUClientError) as exc:
        call_tju_llm("只回复：连接成功")

    assert "super-secret-key-xyz" not in str(exc.value)


# ---------------------------------------------------------------------------
# P3e 开发诊断：HTTP 500 日志（只进终端，用户侧仍只收到安全错误）
# ---------------------------------------------------------------------------


class _Json500Response:
    status_code = 500
    headers = {"Content-Type": "application/json"}
    text = '{"error": {"message": "upstream model timeout"}, "request_id": "abc-123"}'

    def json(self):
        return {"error": {"message": "upstream model timeout"}, "request_id": "abc-123"}


class _Text500Response:
    status_code = 500
    headers = {"Content-Type": "text/plain; charset=utf-8"}
    text = "server exploded " * 200  # ~3000 chars

    def json(self):
        raise ValueError("not json")


def test_server_error_json_body_logged_safely(valid_env, monkeypatch, caplog):
    import logging

    monkeypatch.setattr("requests.post", lambda *a, **k: _Json500Response())
    caplog.set_level(logging.INFO, logger="campusflow.tju_client")

    with pytest.raises(TJUClientError, match="500"):
        call_tju_llm("只回复：连接成功")

    records = caplog.text
    assert "[CampusFlow][TJU] status=500" in records
    assert "[CampusFlow][TJU] response_content_type=application/json" in records
    assert "response_body=" in records
    assert "upstream model timeout" in records
    # 请求结构摘要
    assert "request endpoint=ai.tju.edu.cn/api/v3/chat/completions" in records
    assert "model=tju-llm" in records
    assert "messages=1" in records
    assert "system_chars=0" in records and "user_chars=8" in records
    assert "payload_keys=" in records
    # 敏感信息绝不出现
    assert "test-api-key-123" not in records
    assert "Authorization" not in records
    assert "Bearer" not in records


def test_server_error_text_body_truncated(valid_env, monkeypatch, caplog):
    import logging

    monkeypatch.setattr("requests.post", lambda *a, **k: _Text500Response())
    caplog.set_level(logging.INFO, logger="campusflow.tju_client")

    with pytest.raises(TJUClientError, match="500"):
        call_tju_llm("只回复：连接成功")

    records = caplog.text
    body_start = records.find("response_body=") + len("response_body=")
    body = records[body_start:]
    assert len(body) <= 1500 + 4  # 截断长度上限（预留日志行尾换行）
    assert "server exploded" in records
    assert "test-api-key-123" not in records


def test_server_error_user_side_stays_safe(valid_env, monkeypatch, caplog):
    import logging

    caplog.set_level(logging.INFO, logger="campusflow.tju_client")

    class _Resp:
        status_code = 500
        headers = {"Content-Type": "application/json"}
        text = '{"error": {"message": "boom"}}'

        def json(self):
            return {"error": {"message": "boom"}}

    monkeypatch.setattr("requests.post", lambda *a, **k: _Resp())

    with pytest.raises(TJUClientError) as exc:
        call_tju_llm("只回复：连接成功")

    user_message = str(exc.value)
    assert "TJU LLM 服务端返回 500。" == user_message
    assert "boom" not in user_message
    assert "test-api-key-123" not in user_message
    assert "boom" in caplog.text  # 详情只在开发日志


def test_success_200_behavior_unchanged_no_error_logs(valid_env, monkeypatch, caplog):
    import logging

    class DummyResponse:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "连接成功"}}]}

    monkeypatch.setattr("requests.post", lambda *a, **k: DummyResponse())
    caplog.set_level(logging.INFO, logger="campusflow.tju_client")

    response = call_tju_llm("只回复：连接成功")
    assert response == "连接成功"
    assert "[CampusFlow][TJU] status=" not in caplog.text
    assert "response_body=" not in caplog.text
