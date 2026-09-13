import json

import pytest

from src.extraction_models import ParseResult
from src.input_validator import InputValidator
from src.models import PlanningRequest, Task
from src.structured_parser import StructuredParser


VALID_PAYLOAD = {
    "current_location": "宿舍",
    "destination": "图书馆",
    "current_time": "09:00",
    "tasks": [
        {
            "location": "食堂",
            "description": "吃早饭",
            "is_mandatory": True,
            "estimated_duration_minutes": 20,
            "deadline": "09:30",
        }
    ],
}


class TestStructuredParser:
    def test_parse_complete_json_returns_ok_result(self):
        result = StructuredParser.parse(json.dumps(VALID_PAYLOAD))

        assert isinstance(result, ParseResult)
        assert result.status == "ok"
        assert result.error_type is None
        assert result.planning_request is not None
        assert isinstance(result.planning_request, PlanningRequest)
        assert isinstance(result.planning_request.tasks[0], Task)
        assert result.planning_request.destination == "图书馆"

    @pytest.mark.parametrize(
        "raw_text",
        [
            "{not valid json}",
            "下面是结果：{\"a\":1}",
            "```json\n{\"a\":1}\n```",
            "[1, 2, 3]",
        ],
    )
    def test_parse_invalid_input_or_wrong_top_level_type(self, raw_text):
        result = StructuredParser.parse(raw_text)

        assert result.status == "rejected"
        assert result.error_type == "format_error"
        assert result.planning_request is None

    @pytest.mark.parametrize(
        "payload, expected_missing",
        [
            ({"current_location": "宿舍", "current_time": "09:00", "tasks": [VALID_PAYLOAD["tasks"][0]]}, ["destination"]),
            ({"destination": "图书馆", "current_time": "09:00", "tasks": [VALID_PAYLOAD["tasks"][0]]}, ["current_location"]),
            ({"current_location": "宿舍", "destination": "图书馆", "tasks": [VALID_PAYLOAD["tasks"][0]]}, ["current_time"]),
            ({"current_location": None, "destination": "图书馆", "current_time": "09:00", "tasks": [VALID_PAYLOAD["tasks"][0]]}, ["current_location"]),
            ({"current_location": "   ", "destination": "图书馆", "current_time": "09:00", "tasks": [VALID_PAYLOAD["tasks"][0]]}, ["current_location"]),
            ({"current_location": "宿舍", "destination": "图书馆", "current_time": "09:00", "tasks": []}, ["tasks"]),
            ({"current_location": "宿舍", "destination": "图书馆", "current_time": "09:00"}, ["tasks"]),
            ({"current_location": "宿舍", "destination": "图书馆", "current_time": "09:00", "tasks": None}, ["tasks"]),
        ],
    )
    def test_missing_required_fields_are_reported(self, payload, expected_missing):
        result = StructuredParser.parse(json.dumps(payload))

        assert result.status == "needs_clarification"
        assert result.error_type == "missing_information"
        assert result.planning_request is None
        for field in expected_missing:
            assert field in result.missing_fields
        assert result.questions

    @pytest.mark.parametrize(
        "payload, expected_missing",
        [
            ({"current_location": "宿舍", "destination": "图书馆", "current_time": "09:00", "tasks": [{"location": "食堂", "description": "吃饭", "is_mandatory": True, "estimated_duration_minutes": None, "deadline": "09:30"}]}, ["tasks[0].estimated_duration_minutes"]),
            ({"current_location": "宿舍", "destination": "图书馆", "current_time": "09:00", "tasks": [{"location": "食堂", "description": "吃饭", "estimated_duration_minutes": 20, "deadline": "09:30"}]}, ["tasks[0].is_mandatory"]),
            ({"current_location": "宿舍", "destination": "图书馆", "current_time": "09:00", "tasks": [{"description": "吃饭", "is_mandatory": True, "estimated_duration_minutes": 20, "deadline": "09:30"}]}, ["tasks[0].location"]),
            ({"current_location": "宿舍", "destination": "图书馆", "current_time": "09:00", "tasks": [{"location": "食堂", "is_mandatory": True, "estimated_duration_minutes": 20, "deadline": "09:30"}]}, ["tasks[0].description"]),
        ],
    )
    def test_missing_task_fields_are_reported(self, payload, expected_missing):
        result = StructuredParser.parse(json.dumps(payload))

        assert result.status == "needs_clarification"
        assert result.error_type == "missing_information"
        for field in expected_missing:
            assert field in result.missing_fields

    @pytest.mark.parametrize(
        "payload, message_fragment",
        [
            ({"current_location": 123, "destination": "图书馆", "current_time": "09:00", "tasks": [VALID_PAYLOAD["tasks"][0]]}, "current_location"),
            ({"current_location": "宿舍", "destination": 123, "current_time": "09:00", "tasks": [VALID_PAYLOAD["tasks"][0]]}, "destination"),
            ({"current_location": "宿舍", "destination": "图书馆", "current_time": 900, "tasks": [VALID_PAYLOAD["tasks"][0]]}, "current_time"),
        ],
    )
    def test_top_level_type_errors_are_format_errors(self, payload, message_fragment):
        result = StructuredParser.parse(json.dumps(payload))

        assert result.status == "rejected"
        assert result.error_type == "format_error"
        assert result.planning_request is None
        assert message_fragment in result.message

    @pytest.mark.parametrize(
        "payload, message_fragment",
        [
            ({"current_location": "宿舍", "destination": "图书馆", "current_time": "09:00", "tasks": "not a list"}, "tasks"),
            ({"current_location": "宿舍", "destination": "图书馆", "current_time": "09:00", "tasks": [None]}, "tasks中的元素"),
            ({"current_location": "宿舍", "destination": "图书馆", "current_time": "09:00", "tasks": ["task"]}, "tasks中的元素"),
        ],
    )
    def test_task_collection_type_errors_are_format_errors(self, payload, message_fragment):
        result = StructuredParser.parse(json.dumps(payload))

        assert result.status == "rejected"
        assert result.error_type == "format_error"
        assert message_fragment in result.message

    @pytest.mark.parametrize(
        "payload, message_fragment",
        [
            ({"current_location": "宿舍", "destination": "图书馆", "current_time": "09:00", "tasks": [{"location": 123, "description": "吃饭", "is_mandatory": True, "estimated_duration_minutes": 20, "deadline": "09:30"}]}, "location"),
            ({"current_location": "宿舍", "destination": "图书馆", "current_time": "09:00", "tasks": [{"location": "食堂", "description": 123, "is_mandatory": True, "estimated_duration_minutes": 20, "deadline": "09:30"}]}, "description"),
            ({"current_location": "宿舍", "destination": "图书馆", "current_time": "09:00", "tasks": [{"location": "食堂", "description": "吃饭", "is_mandatory": "是", "estimated_duration_minutes": 20, "deadline": "09:30"}]}, "is_mandatory"),
            ({"current_location": "宿舍", "destination": "图书馆", "current_time": "09:00", "tasks": [{"location": "食堂", "description": "吃饭", "is_mandatory": True, "estimated_duration_minutes": True, "deadline": "09:30"}]}, "estimated_duration_minutes"),
            ({"current_location": "宿舍", "destination": "图书馆", "current_time": "09:00", "tasks": [{"location": "食堂", "description": "吃饭", "is_mandatory": True, "estimated_duration_minutes": 20, "deadline": 123}]}, "deadline"),
        ],
    )
    def test_task_field_type_errors_are_format_errors(self, payload, message_fragment):
        result = StructuredParser.parse(json.dumps(payload))

        assert result.status == "rejected"
        assert result.error_type == "format_error"
        assert message_fragment in result.message

    @pytest.mark.parametrize(
        "payload",
        [
            {"current_location": "宿舍", "destination": "图书馆", "current_time": "25:00", "tasks": [VALID_PAYLOAD["tasks"][0]]},
            {"current_location": "宿舍", "destination": "图书馆", "current_time": "09:00", "tasks": [{"location": "食堂", "description": "吃饭", "is_mandatory": True, "estimated_duration_minutes": -5, "deadline": "09:30"}]},
            {"current_location": "宿舍", "destination": "图书馆", "current_time": "09:00", "tasks": [{"location": "食堂", "description": "吃饭", "is_mandatory": True, "estimated_duration_minutes": 20, "deadline": ""}]},
            {"current_location": "宿舍", "destination": "图书馆", "current_time": "09:00", "tasks": [{"location": "食堂", "description": "吃饭", "is_mandatory": True, "estimated_duration_minutes": 20, "deadline": "   "}]},
            {"current_location": "宿舍", "destination": "图书馆", "current_time": "09:00", "tasks": [{"location": "食堂", "description": "吃饭", "is_mandatory": True, "estimated_duration_minutes": 20, "deadline": "25:00"}]},
        ],
    )
    def test_validation_errors_are_rejected(self, payload):
        result = StructuredParser.parse(json.dumps(payload))

        assert result.status == "rejected"
        assert result.error_type == "validation_error"
        assert result.planning_request is None

    @pytest.mark.parametrize(
        "payload",
        [
            {**VALID_PAYLOAD, "unknown_top": 1},
            {"current_location": "宿舍", "destination": "图书馆", "current_time": "09:00", "tasks": [{**VALID_PAYLOAD["tasks"][0], "unknown_field": 1}]},
        ],
    )
    def test_unknown_fields_are_rejected(self, payload):
        result = StructuredParser.parse(json.dumps(payload))

        assert result.status == "rejected"
        assert result.error_type == "format_error"

    def test_full_success_path_calls_input_validator(self, monkeypatch):
        calls = []
        original = InputValidator.validate

        def fake_validate(request):
            calls.append(request)
            return True, "校验通过"

        monkeypatch.setattr(InputValidator, "validate", staticmethod(fake_validate))
        result = StructuredParser.parse(json.dumps(VALID_PAYLOAD))

        assert result.status == "ok"
        assert len(calls) == 1
        assert result.planning_request is not None
        assert result.planning_request.current_location == "宿舍"

    @pytest.mark.parametrize(
        "payload, expected_missing",
        [
            ({"current_location": "宿舍", "destination": "图书馆", "current_time": "09:00", "tasks": [{"location": "食堂", "description": "吃饭", "is_mandatory": True, "estimated_duration_minutes": 10, "deadline": None}]}, []),
            ({"current_location": "宿舍", "destination": "图书馆", "current_time": "09:00", "tasks": [{"location": "食堂", "description": "吃饭", "is_mandatory": True, "estimated_duration_minutes": 10}]}, []),
        ],
    )
    def test_deadline_missing_or_none_is_allowed(self, payload, expected_missing):
        result = StructuredParser.parse(json.dumps(payload))

        assert result.status == "ok"
        assert result.error_type is None
        assert result.planning_request is not None
        assert result.missing_fields == expected_missing
