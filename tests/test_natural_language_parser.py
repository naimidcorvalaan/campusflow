from src.natural_language_parser import parse_natural_language
from src.tju_llm_client import TJUClientError


def test_parse_successful_json_returns_planning_request(monkeypatch):
    monkeypatch.setattr(
        "src.natural_language_parser.call_tju_llm",
        lambda *args, **kwargs: '{"current_location": "宿舍", "destination": "教学楼", "current_time": "2026-08-15 09:35", "tasks": [{"location": "图书馆", "description": "还书", "is_mandatory": true, "estimated_duration_minutes": 5, "deadline": null}]}',
    )

    result = parse_natural_language("我在宿舍，去图书馆还书。", "2026-08-15 09:00")

    assert result.status == "ok"
    assert result.planning_request is not None
    assert result.planning_request.destination == "教学楼"
    assert result.planning_request.current_time == "09:00"


def test_injected_current_time_overrides_model_time(monkeypatch):
    monkeypatch.setattr(
        "src.natural_language_parser.call_tju_llm",
        lambda *args, **kwargs: '{"current_location": "宿舍", "destination": "教学楼", "current_time": "2026-08-15 09:35", "tasks": [{"location": "图书馆", "description": "还书", "is_mandatory": true, "estimated_duration_minutes": 5, "deadline": null}]}',
    )

    result = parse_natural_language("我在宿舍，去图书馆还书。", "2026-08-15 09:00")

    assert result.status == "ok"
    assert result.planning_request.current_time == "09:00"


def test_missing_current_time_is_injected_from_python(monkeypatch):
    monkeypatch.setattr(
        "src.natural_language_parser.call_tju_llm",
        lambda *args, **kwargs: '{"current_location": "宿舍", "destination": "教学楼", "tasks": [{"location": "图书馆", "description": "还书", "is_mandatory": true, "estimated_duration_minutes": 5, "deadline": null}]}',
    )

    result = parse_natural_language("我在宿舍，去图书馆还书。", "2026-08-15 09:00")

    assert result.status == "ok"
    assert result.planning_request.current_time == "09:00"


def test_invalid_json_returns_rejected(monkeypatch):
    monkeypatch.setattr("src.natural_language_parser.call_tju_llm", lambda *args, **kwargs: '{not valid json}')

    result = parse_natural_language("我在宿舍。", "2026-08-15 09:00")

    assert result.status == "rejected"
    assert result.error_type == "format_error"


def test_markdown_code_block_returns_rejected(monkeypatch):
    monkeypatch.setattr("src.natural_language_parser.call_tju_llm", lambda *args, **kwargs: '```json\n{"current_location": "宿舍"}\n```')

    result = parse_natural_language("我在宿舍。", "2026-08-15 09:00")

    assert result.status == "rejected"
    assert result.error_type == "format_error"


def test_top_level_array_returns_rejected(monkeypatch):
    monkeypatch.setattr("src.natural_language_parser.call_tju_llm", lambda *args, **kwargs: '[{"current_location": "宿舍"}]')

    result = parse_natural_language("我在宿舍。", "2026-08-15 09:00")

    assert result.status == "rejected"
    assert result.error_type == "format_error"


def test_unknown_fields_are_preserved_and_rejected_by_structured_parser(monkeypatch):
    monkeypatch.setattr(
        "src.natural_language_parser.call_tju_llm",
        lambda *args, **kwargs: '{"current_location": "宿舍", "destination": "教学楼", "current_time": "2026-08-15 09:00", "extra": "value", "tasks": [{"location": "图书馆", "description": "还书", "is_mandatory": true, "estimated_duration_minutes": 5, "deadline": null}]}',
    )

    result = parse_natural_language("我在宿舍，去图书馆还书。", "2026-08-15 09:00")

    assert result.status == "rejected"
    assert result.error_type == "format_error"


def test_unknown_task_fields_are_preserved_and_rejected_by_structured_parser(monkeypatch):
    monkeypatch.setattr(
        "src.natural_language_parser.call_tju_llm",
        lambda *args, **kwargs: '{"current_location": "宿舍", "destination": "教学楼", "current_time": "2026-08-15 09:00", "tasks": [{"location": "图书馆", "description": "还书", "is_mandatory": true, "estimated_duration_minutes": 5, "deadline": null, "extra_task_field": "x"}]}',
    )

    result = parse_natural_language("我在宿舍，去图书馆还书。", "2026-08-15 09:00")

    assert result.status == "rejected"
    assert result.error_type == "format_error"


def test_tasks_are_not_modified(monkeypatch):
    monkeypatch.setattr(
        "src.natural_language_parser.call_tju_llm",
        lambda *args, **kwargs: '{"current_location": "宿舍", "destination": "教学楼", "current_time": "2026-08-15 09:00", "tasks": [{"location": "图书馆", "description": "还书", "is_mandatory": true, "estimated_duration_minutes": 5, "deadline": null}]}',
    )

    result = parse_natural_language("我在宿舍，去图书馆还书。", "2026-08-15 09:00")

    assert result.status == "ok"
    assert len(result.planning_request.tasks) == 1
    assert result.planning_request.tasks[0].location == "图书馆"
    assert result.planning_request.tasks[0].description == "还书"


def test_api_client_error_is_converted_to_api_error(monkeypatch):
    def raise_error(*args, **kwargs):
        raise TJUClientError("认证失败：API Key 无效或缺失。")

    monkeypatch.setattr("src.natural_language_parser.call_tju_llm", raise_error)

    result = parse_natural_language("我在宿舍。", "2026-08-15 09:00")

    assert result.status == "rejected"
    assert result.error_type == "api_error"
    assert "API Key" not in str(result.message)


def test_error_message_does_not_contain_test_api_key(monkeypatch):
    key = "test-api-key-xyz"

    def raise_error(*args, **kwargs):
        raise TJUClientError(key)

    monkeypatch.setattr("src.natural_language_parser.call_tju_llm", raise_error)

    result = parse_natural_language("我在宿舍。", "2026-08-15 09:00")

    assert key not in str(result.message)


def test_user_text_empty_is_rejected_without_api_call(monkeypatch):
    called = {"value": False}

    def fake_call(*args, **kwargs):
        called["value"] = True
        return "{}"

    monkeypatch.setattr("src.natural_language_parser.call_tju_llm", fake_call)

    result = parse_natural_language("   ", "2026-08-15 09:00")

    assert result.status == "rejected"
    assert called["value"] is False


def test_user_text_type_error_is_rejected_without_api_call(monkeypatch):
    called = {"value": False}

    def fake_call(*args, **kwargs):
        called["value"] = True
        return "{}"

    monkeypatch.setattr("src.natural_language_parser.call_tju_llm", fake_call)

    result = parse_natural_language(None, "2026-08-15 09:00")

    assert result.status == "rejected"
    assert called["value"] is False


def test_current_time_bad_format_is_rejected_without_api_call(monkeypatch):
    called = {"value": False}

    def fake_call(*args, **kwargs):
        called["value"] = True
        return "{}"

    monkeypatch.setattr("src.natural_language_parser.call_tju_llm", fake_call)

    result = parse_natural_language("我在宿舍。", "invalid-time")

    assert result.status == "rejected"
    assert called["value"] is False
