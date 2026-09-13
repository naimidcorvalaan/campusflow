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


def test_server_error_json_body_omitted(valid_env, monkeypatch, caplog):
    import logging

    monkeypatch.setattr("requests.post", lambda *a, **k: _Json500Response())
    caplog.set_level(logging.INFO, logger="campusflow.tju_client")

    with pytest.raises(TJUClientError, match="500"):
        call_tju_llm("只回复：连接成功")

    records = caplog.text
    assert "stage=http_response category=http_5xx" in records
    assert "status=500 timeout=False response_received=True" in records
    assert "endpoint_host=ai.tju.edu.cn endpoint_path=/api/v3/chat/completions" in records
    for private in ("response_body=", "upstream model timeout", "abc-123",
                    "model=tju-llm", "test-api-key-123", "Authorization", "Bearer"):
        assert private not in records


def test_server_error_text_body_omitted(valid_env, monkeypatch, caplog):
    import logging

    monkeypatch.setattr("requests.post", lambda *a, **k: _Text500Response())
    caplog.set_level(logging.INFO, logger="campusflow.tju_client")

    with pytest.raises(TJUClientError, match="500"):
        call_tju_llm("只回复：连接成功")

    assert "status=500" in caplog.text
    assert "response_body=" not in caplog.text
    assert "server exploded" not in caplog.text
    assert "test-api-key-123" not in caplog.text


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
    assert "boom" not in caplog.text  # Error bodies stay private on the server too.


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


@pytest.mark.parametrize("error, category, timed_out", [
    (requests.exceptions.ConnectTimeout("private exception text"), "connect_timeout", True),
    (requests.exceptions.ReadTimeout("private exception text"), "read_timeout", True),
    (requests.exceptions.Timeout("private exception text"), "timeout", True),
    (requests.exceptions.SSLError("private exception text"), "tls_error", False),
    (requests.exceptions.ConnectionError("private exception text"), "connection_error", False),
    (requests.exceptions.ChunkedEncodingError("private exception text"), "requests_exception", False),
    (requests.exceptions.InvalidURL("private exception text"), "requests_exception", False),
])
def test_transport_diagnostics_preserve_cause(valid_env, monkeypatch, caplog,
                                             error, category, timed_out):
    def post(*args, **kwargs):
        raise error
    monkeypatch.setattr(requests, "post", post)
    with pytest.raises(TJUClientError) as caught:
        call_tju_llm("private user material")
    assert caught.value.__cause__ is error
    assert "stage=request category=" + category in caplog.text
    assert "exception_type=" + type(error).__name__ in caplog.text
    assert "timeout={} response_received=False".format(timed_out) in caplog.text
    assert "private exception text" not in caplog.text
    assert "private user material" not in caplog.text
    assert all(r.exc_info is None and r.stack_info is None for r in caplog.records)


def test_nested_dns_failure_without_logging_exception_text(valid_env, monkeypatch, caplog):
    import socket
    from urllib3.exceptions import MaxRetryError
    reason = socket.gaierror(-2, "private hostname and key")
    wrapped = MaxRetryError(None, "https://private.invalid/person", reason)
    error = requests.exceptions.ConnectionError(wrapped)
    def post(*args, **kwargs):
        raise error
    monkeypatch.setattr(requests, "post", post)
    with pytest.raises(TJUClientError):
        call_tju_llm("private material")
    assert "category=dns_error" in caplog.text
    assert "cause_type=gaierror" in caplog.text
    assert "timeout=False response_received=False" in caplog.text
    assert "private" not in caplog.text


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500, 502, 503, 302])
def test_http_metadata_never_reads_headers_or_body(valid_env, monkeypatch, caplog, status):
    class Response:
        status_code = status
        @property
        def text(self):
            raise AssertionError("must not read body")
        @property
        def headers(self):
            raise AssertionError("must not read headers")
        def json(self):
            raise AssertionError("must not parse error body")
    monkeypatch.setattr(requests, "post", lambda *a, **k: Response())
    with pytest.raises(TJUClientError):
        call_tju_llm("private material")
    category = "http_5xx" if status >= 500 else "http_{}".format(status)
    assert "stage=http_response category=" + category in caplog.text
    assert "status={} timeout=False response_received=True".format(status) in caplog.text
    assert len([r for r in caplog.records if r.name == "campusflow.tju_client"]) == 1


@pytest.mark.parametrize("body,stage,original", [
    (ValueError("private response body"), "response_json", "ValueError"),
    ({"choices": []}, "response_structure", "ValueError"),
    ({"choices": [{"message": {}}]}, "response_structure", "KeyError"),
    (None, "response_structure", "TypeError"),
])
def test_200_response_parse_failure_is_distinct(valid_env, monkeypatch, caplog,
                                               body, stage, original):
    class Response:
        status_code = 200
        def json(self):
            if isinstance(body, Exception):
                raise body
            return body
    calls = []
    def post(*args, **kwargs):
        calls.append(True)
        return Response()
    monkeypatch.setattr(requests, "post", post)
    with pytest.raises(TJUClientError):
        call_tju_llm("private material")
    assert calls == [True]
    assert "stage={} category=response_parse_error".format(stage) in caplog.text
    assert "exception_type=" + original in caplog.text
    assert "status=200 timeout=False response_received=True" in caplog.text
    assert "private" not in caplog.text


def test_response_on_exception_is_not_lost_due_to_false_bool(valid_env, monkeypatch, caplog):
    response = requests.Response()
    response.status_code = 403
    assert not response
    error = requests.exceptions.HTTPError("private details", response=response)
    def post(*args, **kwargs):
        raise error
    monkeypatch.setattr(requests, "post", post)
    with pytest.raises(TJUClientError):
        call_tju_llm("private material")
    assert "category=requests_exception" in caplog.text
    assert "status=403 timeout=False response_received=True" in caplog.text


@pytest.mark.parametrize("base", [
    "https://private-user:private-password@ai.tju.edu.cn/api/v3?secret=private-query#private-fragment",
    "https://ai.tju.edu.cn/tenant/private-person/private-token",
    "https://private-person.example.invalid/api/v3",
    "https://[malformed",
])
def test_endpoint_privacy_and_response_echoes(valid_env, monkeypatch, caplog, base):
    key = "tk-" + "synthetic-private-key" * 2
    monkeypatch.setenv("TJU_LLM_API_KEY", key)
    monkeypatch.setenv("TJU_LLM_BASE_URL", base)
    monkeypatch.setenv("TJU_LLM_MODEL", "private-person-model")
    class Response:
        status_code = 401
        headers = {"Content-Type": "private-person"}
        text = key + " private-user-material Authorization: Bearer another-private-token"
        def json(self):
            return {"error": self.text}
    monkeypatch.setattr(requests, "post", lambda *a, **k: Response())
    with pytest.raises(TJUClientError):
        call_tju_llm("private-user-material", system_prompt="private-system")
    assert key not in caplog.text
    for value in ("private", "Authorization", "Bearer", "response_body", "model="):
        assert value not in caplog.text
    assert "status=401" in caplog.text


def test_key_cannot_escape_via_endpoint_or_exception_class(valid_env, monkeypatch, caplog):
    key = "SensitiveDiagnosticMarker"
    monkeypatch.setenv("TJU_LLM_API_KEY", key)
    monkeypatch.setenv("TJU_LLM_BASE_URL", "https://" + key + ".invalid/" + key)
    error_type = type(key, (requests.exceptions.ConnectionError,), {})
    def post(*args, **kwargs):
        raise error_type(key)
    monkeypatch.setattr(requests, "post", post)
    with pytest.raises(TJUClientError):
        call_tju_llm(key)
    assert key.casefold() not in caplog.text.casefold()
    assert "exception_type=redacted" in caplog.text


@pytest.mark.parametrize("name,category", [("RemoteProtocolError", "httpx_exception"),
                                         ("ReadTimeout", "read_timeout")])
def test_unexpected_httpx_exception_keeps_original_semantics(valid_env, monkeypatch, caplog,
                                                           name, category):
    # The real client uses requests. No httpx dependency or new request flow:
    # an injected third-party failure is logged then re-raised unchanged.
    error_type = type(name, (Exception,), {"__module__": "httpx"})
    error = error_type("private message")
    def post(*args, **kwargs):
        raise error
    monkeypatch.setattr(requests, "post", post)
    with pytest.raises(error_type) as caught:
        call_tju_llm("private material")
    assert caught.value is error
    assert "category=" + category in caplog.text
    assert "private" not in caplog.text


def test_cyclic_exception_chain_is_bounded(valid_env, monkeypatch, caplog):
    error = requests.exceptions.ConnectionError("private message")
    error.__cause__ = error
    def post(*args, **kwargs):
        raise error
    monkeypatch.setattr(requests, "post", post)
    with pytest.raises(TJUClientError):
        call_tju_llm("private material")
    assert "category=connection_error" in caplog.text
