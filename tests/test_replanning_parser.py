import json
from unittest.mock import patch

import pytest

import src.replanning_parser as replanning_parser
from src.dynamic_replanner import ReplanningUpdate
from src.models import PlanningRequest, Task
from src.replanning_parser import (
    ReplanningParseResult,
    parse_replanning_model_output,
    parse_replanning_text,
)
from src.route_plan import RoutePlanResult
from src.schedule_checker import ScheduleCheckResult
from src.task_selector import TaskSelectionResult
from src.tju_llm_client import TJUClientError


TOP_FIELDS = {
    "new_current_location": None,
    "delay_minutes": 0,
    "cancelled_task_locations": [],
    "added_tasks": [],
}


def make_request():
    return PlanningRequest("dormitory", "gate", "09:00", [])


def make_payload(**updates):
    payload = dict(TOP_FIELDS)
    payload.update(updates)
    return payload


def make_task_payload(**updates):
    task = {
        "location": "express",
        "description": "取快递",
        "is_mandatory": True,
        "estimated_duration_minutes": 10,
        "deadline": "18:00",
    }
    task.update(updates)
    return task


def parse_payload(payload):
    return parse_replanning_model_output(
        json.dumps(payload, ensure_ascii=False)
    )


def test_complete_update_builds_frozen_update():
    result = parse_payload(
        make_payload(
            new_current_location="图书馆",
            delay_minutes=20,
            cancelled_task_locations=["食堂"],
            added_tasks=[make_task_payload()],
        )
    )

    assert result.status == "ok"
    assert result.error_type is None
    assert isinstance(result.update, ReplanningUpdate)
    assert result.update.new_current_location == "图书馆"
    assert result.update.delay_minutes == 20
    assert result.update.cancelled_task_locations == ("食堂",)
    assert isinstance(result.update.added_tasks, tuple)
    assert result.update.added_tasks[0].location == "express"
    assert result.missing_fields == ()
    assert result.questions == ()


@pytest.mark.parametrize(
    "payload",
    [
        make_payload(new_current_location="图书馆"),
        make_payload(delay_minutes=20),
        make_payload(cancelled_task_locations=["食堂"]),
        make_payload(added_tasks=[make_task_payload()]),
        make_payload(
            new_current_location="图书馆",
            delay_minutes=20,
            cancelled_task_locations=["食堂"],
            added_tasks=[make_task_payload()],
        ),
    ],
)
def test_single_or_combined_changes_are_accepted(payload):
    result = parse_payload(payload)

    assert result.status == "ok"
    assert result.update is not None


def test_all_empty_changes_need_clarification():
    result = parse_payload(make_payload())

    assert result.status == "needs_clarification"
    assert result.error_type == "missing_information"
    assert result.update is None
    assert result.missing_fields == ("change",)
    assert result.questions == (
        "请说明当前位置、延误、取消任务或新增任务中的至少一项变化。",
    )


@pytest.mark.parametrize(
    "raw",
    [
        "not-json",
        "说明：{}",
        "{}说明",
        "```json\n{}\n```",
        "[1, 2]",
        '"text"',
        "123",
        "null",
    ],
)
def test_non_pure_json_outputs_are_rejected(raw):
    result = parse_replanning_model_output(raw)

    assert result.status == "rejected"
    assert result.error_type == "format_error"
    assert result.update is None


@pytest.mark.parametrize(
    "raw",
    [
        '{"new_current_location":null,"delay_minutes":NaN,"cancelled_task_locations":[],"added_tasks":[]}',
        '{"new_current_location":null,"delay_minutes":Infinity,"cancelled_task_locations":[],"added_tasks":[]}',
        '{"new_current_location":null,"delay_minutes":-Infinity,"cancelled_task_locations":[],"added_tasks":[]}',
        '{"new_current_location":null,"delay_minutes":0,"cancelled_task_locations":[],"added_tasks":[{"location":"x","description":"y","is_mandatory":true,"estimated_duration_minutes":NaN,"deadline":null}]}',
        '{"new_current_location":null,"delay_minutes":0,"cancelled_task_locations":[],"added_tasks":[{"location":"x","description":{"nested":Infinity},"is_mandatory":true,"estimated_duration_minutes":1,"deadline":null}]}',
    ],
)
def test_nonstandard_json_constants_are_rejected_at_load(raw):
    result = parse_replanning_model_output(raw)

    assert result.status == "rejected"
    assert result.error_type == "format_error"
    assert result.update is None
    assert "NaN" not in result.message
    assert "Infinity" not in result.message


@pytest.mark.parametrize(
    "raw",
    [
        '{"new_current_location":null,"new_current_location":"图书馆","delay_minutes":0,"cancelled_task_locations":[],"added_tasks":[]}',
        '{"new_current_location":null,"delay_minutes":0,"cancelled_task_locations":[],"added_tasks":[{"location":"x","location":"y","description":"z","is_mandatory":true,"estimated_duration_minutes":1,"deadline":null}]}',
        '{"new_current_location":null,"delay_minutes":0,"cancelled_task_locations":[],"added_tasks":[{"location":"x","description":"z","is_mandatory":true,"estimated_duration_minutes":1,"deadline":null,"meta":{"a":1,"a":2}}]}',
    ],
)
def test_duplicate_json_keys_at_any_object_level_are_rejected(raw):
    result = parse_replanning_model_output(raw)

    assert result.status == "rejected"
    assert result.error_type == "format_error"
    assert result.update is None
    for marker in ("new_current_location", "location", "a", "Bearer", "api_key", "{"):
        assert marker not in result.message


def test_json_whitespace_around_valid_payload_is_allowed():
    raw = "\n  " + json.dumps(make_payload(new_current_location="图书馆")) + " \n"

    result = parse_replanning_model_output(raw)

    assert result.status == "ok"
    assert result.update.new_current_location == "图书馆"


def test_top_level_unknown_or_missing_fields_are_rejected():
    unknown = make_payload(extra=True)
    missing = dict(TOP_FIELDS)
    del missing["added_tasks"]

    assert parse_payload(unknown).error_type == "format_error"
    assert parse_payload(missing).error_type == "format_error"


@pytest.mark.parametrize(
    "cancelled",
    [None, "食堂", [""], ["   "], [123], ["食堂", "食堂"]],
)
def test_cancelled_locations_are_strictly_validated(cancelled):
    result = parse_payload(make_payload(cancelled_task_locations=cancelled))

    assert result.status == "rejected"
    assert result.error_type in ("format_error", "validation_error")


@pytest.mark.parametrize("added", [None, "task", ["task"]])
def test_added_tasks_must_be_list_of_objects(added):
    result = parse_payload(make_payload(added_tasks=added))

    assert result.status == "rejected"
    assert result.error_type == "format_error"


def test_added_task_unknown_field_is_rejected():
    task = make_task_payload(extra="bad")

    result = parse_payload(make_payload(added_tasks=[task]))

    assert result.status == "rejected"
    assert result.error_type == "format_error"


@pytest.mark.parametrize(
    "missing_field",
    ["location", "description", "is_mandatory", "estimated_duration_minutes"],
)
def test_missing_required_added_task_field_needs_clarification(missing_field):
    task = make_task_payload()
    del task[missing_field]

    result = parse_payload(make_payload(added_tasks=[task]))

    assert result.status == "needs_clarification"
    assert result.error_type == "missing_information"
    assert result.missing_fields == (f"added_tasks[0].{missing_field}",)
    assert result.update is None


def test_missing_deadline_field_is_rejected():
    task = make_task_payload()
    del task["deadline"]

    result = parse_payload(make_payload(added_tasks=[task]))

    assert result.status == "rejected"
    assert result.error_type == "format_error"


@pytest.mark.parametrize(
    "field,value",
    [
        ("location", None),
        ("location", "   "),
        ("location", 1),
        ("description", None),
        ("description", ""),
        ("description", []),
    ],
)
def test_added_task_text_fields_are_rejected(field, value):
    result = parse_payload(
        make_payload(added_tasks=[make_task_payload(**{field: value})])
    )

    assert result.status == "rejected"
    assert result.error_type == "validation_error"


def test_missing_mandatory_and_duration_fields_keep_stable_question_order():
    task = make_task_payload()
    task["is_mandatory"] = None
    task["estimated_duration_minutes"] = None

    result = parse_payload(make_payload(added_tasks=[task]))

    assert result.status == "needs_clarification"
    assert result.missing_fields == (
        "added_tasks[0].is_mandatory",
        "added_tasks[0].estimated_duration_minutes",
    )
    assert result.questions == (
        "请说明新增任务是否必须完成。",
        "请说明新增任务预计需要多少分钟。",
    )


@pytest.mark.parametrize("mandatory", ["是", 0, 1])
def test_invalid_mandatory_values_are_rejected(mandatory):
    result = parse_payload(
        make_payload(added_tasks=[make_task_payload(is_mandatory=mandatory)])
    )

    assert result.status == "rejected"
    assert result.error_type == "validation_error"


def test_missing_duration_needs_clarification():
    task = make_task_payload(estimated_duration_minutes=None)
    result = parse_payload(make_payload(added_tasks=[task]))

    assert result.status == "needs_clarification"
    assert "added_tasks[0].estimated_duration_minutes" in result.missing_fields


@pytest.mark.parametrize("delay", [None])
def test_missing_delay_needs_clarification(delay):
    result = parse_payload(make_payload(delay_minutes=delay, new_current_location="图书馆"))

    assert result.status == "needs_clarification"
    assert result.missing_fields == ("delay_minutes",)
    assert result.questions == ("请说明具体延误了多少分钟。",)


@pytest.mark.parametrize("delay", [True, False, 1.2, "10", -1])
def test_invalid_delay_values_are_rejected(delay):
    result = parse_payload(make_payload(delay_minutes=delay))

    assert result.status == "rejected"
    assert result.error_type == "validation_error"


@pytest.mark.parametrize("number", [1.0, 1e2])
def test_float_and_scientific_delay_are_validation_errors(number):
    result = parse_payload(make_payload(delay_minutes=number))

    assert result.status == "rejected"
    assert result.error_type == "validation_error"


@pytest.mark.parametrize("number", [1.5, 1e2])
def test_float_and_scientific_duration_are_validation_errors(number):
    result = parse_payload(
        make_payload(
            added_tasks=[make_task_payload(estimated_duration_minutes=number)]
        )
    )

    assert result.status == "rejected"
    assert result.error_type == "validation_error"


def test_deadline_null_and_ascii_time_are_allowed():
    result = parse_payload(
        make_payload(
            added_tasks=[
                make_task_payload(deadline=None),
                make_task_payload(location="other", deadline="23:59"),
            ]
        )
    )

    assert result.status == "ok"
    assert result.update.added_tasks[0].deadline is None
    assert result.update.added_tasks[1].deadline == "23:59"


@pytest.mark.parametrize(
    "deadline",
    ["", "   ", "９:００", "٠٩:٠٠", "09:６０", "24:00", 900],
)
def test_invalid_deadlines_are_rejected(deadline):
    result = parse_payload(
        make_payload(added_tasks=[make_task_payload(deadline=deadline)])
    )

    assert result.status == "rejected"
    assert result.error_type == "validation_error"


def test_added_task_and_result_are_not_repr_or_raw_output():
    raw = json.dumps(make_payload(added_tasks=[make_task_payload()]))
    result = parse_replanning_model_output(raw)

    assert not hasattr(result, "raw_model_output")
    assert "Task(" not in result.message
    assert "{" not in result.message
    assert isinstance(result.added_tasks if hasattr(result, "added_tasks") else (), tuple)


def test_parse_result_is_frozen_and_sequences_are_tuples():
    result = parse_payload(make_payload(new_current_location="图书馆"))

    assert isinstance(result, ReplanningParseResult)
    assert isinstance(result.missing_fields, tuple)
    assert isinstance(result.questions, tuple)
    with pytest.raises(AttributeError):
        result.status = "error"


def test_api_entry_uses_system_user_roles_and_zero_temperature():
    raw = json.dumps(make_payload(new_current_location="图书馆"))
    with patch(
        "src.replanning_parser.call_tju_llm", return_value=raw
    ) as llm_mock:
        result = parse_replanning_text("我到了图书馆", make_request())

    assert result.status == "ok"
    llm_mock.assert_called_once()
    args, kwargs = llm_mock.call_args
    assert args and "我到了图书馆" in args[0]
    assert kwargs["temperature"] == 0
    assert kwargs["max_tokens"] == 1024
    assert "system_prompt" in kwargs
    assert "我到了图书馆" not in kwargs["system_prompt"]


def test_api_invalid_json_is_not_cleaned():
    with patch(
        "src.replanning_parser.call_tju_llm",
        return_value="```json\n{}\n```",
    ):
        result = parse_replanning_text("变化", make_request())

    assert result.status == "rejected"
    assert result.error_type == "format_error"


def test_tju_client_error_is_fixed_safe_api_error():
    with patch(
        "src.replanning_parser.call_tju_llm",
        side_effect=TJUClientError(
            'Bearer secret api_key=x Authorization {"x":1}'
        ),
    ):
        result = parse_replanning_text("变化", make_request())

    assert result.status == "rejected"
    assert result.error_type == "api_error"
    assert result.message == "变化信息解析服务暂时不可用。"
    for marker in ("Bearer", "api_key", "Authorization", "{", "secret"):
        assert marker not in result.message


def test_unexpected_runtime_error_propagates():
    with patch(
        "src.replanning_parser.call_tju_llm",
        side_effect=RuntimeError("bug"),
    ):
        with pytest.raises(RuntimeError, match="bug"):
            parse_replanning_text("变化", make_request())


@pytest.mark.parametrize("bad_text", ["", "   ", None, 123])
def test_empty_change_is_rejected_before_api_call(bad_text):
    with patch("src.replanning_parser.call_tju_llm") as llm_mock:
        result = parse_replanning_text(bad_text, make_request())

    assert result.status == "rejected"
    assert result.error_type == "validation_error"
    llm_mock.assert_not_called()


def test_invalid_request_is_rejected_before_api_call():
    with patch("src.replanning_parser.call_tju_llm") as llm_mock:
        result = parse_replanning_text("变化", "not-request")

    assert result.status == "rejected"
    assert result.error_type == "validation_error"
    llm_mock.assert_not_called()


def test_parser_does_not_import_execution_or_route_functions():
    for name in (
        "replan_with_update",
        "select_feasible_tasks",
        "build_route_plan",
        "check_schedule_feasibility",
        "find_optimal_route",
        "find_shortest_path",
    ):
        assert not hasattr(replanning_parser, name)


def test_api_entry_does_not_modify_request_or_tasks():
    request = PlanningRequest(
        "dormitory", "gate", "09:00", [Task("library", "还书", True, 5, None)]
    )
    snapshot = (request.current_location, request.destination, request.current_time, tuple(request.tasks))
    task_values = vars(request.tasks[0]).copy()
    raw = json.dumps(make_payload(new_current_location="图书馆"))
    with patch("src.replanning_parser.call_tju_llm", return_value=raw):
        parse_replanning_text("变化", request)

    assert (request.current_location, request.destination, request.current_time, tuple(request.tasks)) == snapshot
    assert vars(request.tasks[0]) == task_values


def test_multiple_tasks_multiple_missing_fields_have_stable_order():
    first = make_task_payload()
    first["is_mandatory"] = None
    first["estimated_duration_minutes"] = None
    second = make_task_payload(location="other")
    del second["description"]
    second["is_mandatory"] = None

    result = parse_payload(make_payload(added_tasks=[first, second]))

    assert result.status == "needs_clarification"
    assert result.missing_fields == (
        "added_tasks[0].is_mandatory",
        "added_tasks[0].estimated_duration_minutes",
        "added_tasks[1].description",
        "added_tasks[1].is_mandatory",
    )
    assert result.questions == (
        "请说明新增任务是否必须完成。",
        "请说明新增任务预计需要多少分钟。",
        "请说明新增任务的内容。",
        "请说明新增任务是否必须完成。",
    )


def test_long_valid_added_tasks_json_is_parsed_without_truncation():
    tasks = [
        make_task_payload(
            location=f"location-{index}",
            description=f"description-{index}",
        )
        for index in range(8)
    ]
    raw = json.dumps(make_payload(added_tasks=tasks), ensure_ascii=False)

    result = parse_replanning_model_output(raw)

    assert result.status == "ok"
    assert len(result.update.added_tasks) == 8
