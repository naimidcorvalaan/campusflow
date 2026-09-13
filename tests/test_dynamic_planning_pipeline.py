from dataclasses import FrozenInstanceError
from unittest.mock import Mock, patch

import pytest

from src.dynamic_planning_pipeline import (
    DynamicPlanningPipelineResult,
    replan_from_text,
)
from src.dynamic_replanner import DynamicReplanningError, DynamicReplanningResult, ReplanningUpdate
from src.models import PlanningRequest, Task
from src.replanning_parser import ReplanningParseResult
from src.route_plan import RoutePlanResult
from src.schedule_checker import ScheduleCheckResult
from src.task_selector import TaskSelectionResult


def request():
    return PlanningRequest("dormitory", "gate", "09:00", [Task("library", "borrow", True, 5, None)])


def update():
    return ReplanningUpdate(delay_minutes=0)


def parse(status="ok", value=update()):
    return ReplanningParseResult(status, None if status == "ok" else "x", "s", value if status == "ok" else None)


def replanning(feasible=True):
    schedule = ScheduleCheckResult(feasible, "09:00", "09:00", (), ())
    route = RoutePlanResult(("dormitory", "gate"), 0, 0, 0, (), ())
    selection = TaskSelectionResult(feasible, (), (), route, schedule, "ok")
    return DynamicReplanningResult(
        request(), selection, (), (), "09:00", "09:00", "ok"
    )


def test_needs_clarification_keeps_parse_and_skips_replanner():
    with patch("src.dynamic_planning_pipeline.parse_replanning_text", return_value=parse("needs_clarification")) as p, patch("src.dynamic_planning_pipeline.replan_with_update") as r:
        result = replan_from_text(Mock(), request(), "x")
    assert result.status == "needs_clarification"
    assert result.replanning_result is None
    p.assert_called_once(); r.assert_not_called()


@pytest.mark.parametrize("status", ["rejected", "mystery"])
def test_non_ok_statuses_are_safe(status):
    with patch("src.dynamic_planning_pipeline.parse_replanning_text", return_value=parse(status)), patch("src.dynamic_planning_pipeline.replan_with_update") as r:
        result = replan_from_text(Mock(), request(), "x")
    assert result.status == "error"; result.replanning_result is None; r.assert_not_called()


def test_ok_calls_replanner_once_and_returns_ok():
    expected = replanning(True)
    with patch("src.dynamic_planning_pipeline.parse_replanning_text", return_value=parse()) as p, patch("src.dynamic_planning_pipeline.replan_with_update", return_value=expected) as r:
        result = replan_from_text("map", "req", "change")
    assert result.status == "ok" and result.replanning_result is expected
    p.assert_called_once_with("change", "req"); r.assert_called_once_with("map", "req", update())


def test_infeasible_status_preserves_result():
    expected = replanning(False)
    with patch("src.dynamic_planning_pipeline.parse_replanning_text", return_value=parse()), patch("src.dynamic_planning_pipeline.replan_with_update", return_value=expected):
        result = replan_from_text(Mock(), request(), "x")
    assert result.status == "infeasible" and result.replanning_result is expected


def test_dynamic_error_is_fixed_safe_error():
    with patch("src.dynamic_planning_pipeline.parse_replanning_text", return_value=parse()), patch("src.dynamic_planning_pipeline.replan_with_update", side_effect=DynamicReplanningError("Bearer api_key {\"x\":1}")):
        result = replan_from_text(Mock(), request(), "x")
    assert result.status == "error"
    assert result.message == "动态重规划失败，请检查位置、时间和任务变化。"


def test_invalid_internal_result_is_error():
    with patch("src.dynamic_planning_pipeline.parse_replanning_text", return_value=parse()), patch("src.dynamic_planning_pipeline.replan_with_update", return_value=None):
        result = replan_from_text(Mock(), request(), "x")
    assert result.status == "error" and result.replanning_result is None


def test_result_is_frozen():
    result = DynamicPlanningPipelineResult(parse(), None, "error", "x")
    with pytest.raises(FrozenInstanceError):
        result.status = "ok"


@pytest.mark.parametrize("bad", [None, "bad", object()])
def test_parser_bad_return_is_safe_and_skips_replanner(bad):
    with patch("src.dynamic_planning_pipeline.parse_replanning_text", return_value=bad) as parser, patch("src.dynamic_planning_pipeline.replan_with_update") as replanner:
        result = replan_from_text("map", request(), "change")
    assert result.status == "error"
    assert result.replanning_result is None
    assert result.message == "计划变化解析结果异常，请稍后重试。"
    parser.assert_called_once(); replanner.assert_not_called()


@pytest.mark.parametrize("bad", [None, "bad", object()])
def test_ok_invalid_update_is_safe_and_skips_replanner(bad):
    parsed = ReplanningParseResult("ok", None, "sensitive", bad)
    with patch("src.dynamic_planning_pipeline.parse_replanning_text", return_value=parsed), patch("src.dynamic_planning_pipeline.replan_with_update") as replanner:
        result = replan_from_text("map", request(), "change")
    assert result.status == "error" and result.replanning_result is None
    assert result.message == "计划变化结果不完整，请稍后重试。"
    replanner.assert_not_called()


def test_sensitive_parse_messages_are_not_exposed():
    parsed = ReplanningParseResult("rejected", "x", "Bearer secret api_key Authorization JSON ReplanningUpdate(...) ")
    with patch("src.dynamic_planning_pipeline.parse_replanning_text", return_value=parsed):
        result = replan_from_text("map", request(), "change")
    assert result.message == "计划变化解析失败，请检查输入后重试。"
    assert all(token not in result.message for token in ("Bearer", "api_key", "Authorization", "JSON", "ReplanningUpdate"))


def test_clarification_questions_and_message_are_preserved_safely():
    parsed = ReplanningParseResult("needs_clarification", "missing_information", "Bearer secret", None, ("delay_minutes", "added_tasks[0].location"), ("Q1", "Q2"))
    with patch("src.dynamic_planning_pipeline.parse_replanning_text", return_value=parsed):
        result = replan_from_text("map", request(), "change")
    assert result.parse_result.questions == ("Q1", "Q2")
    assert result.message == "需要补充计划变化信息。"


@pytest.mark.parametrize("bad", [None, "bad", object()])
def test_replanner_bad_return_is_safe(bad):
    with patch("src.dynamic_planning_pipeline.parse_replanning_text", return_value=parse()), patch("src.dynamic_planning_pipeline.replan_with_update", return_value=bad):
        result = replan_from_text("map", request(), "change")
    assert result.status == "error" and result.replanning_result is None


@pytest.mark.parametrize("field,bad", [
    ("task_selection_result", object()),
    ("route_plan", object()),
    ("schedule_result", object()),
    ("updated_request", object()),
])
def test_replanner_internal_types_are_checked(field, bad):
    valid = replanning(True)
    values = {
        "task_selection_result": valid.task_selection_result,
        "route_plan": valid.task_selection_result.route_plan,
        "schedule_result": valid.task_selection_result.schedule_result,
        "updated_request": valid.updated_request,
    }
    if field == "task_selection_result":
        result_obj = DynamicReplanningResult(valid.updated_request, bad, (), (), "09:00", "09:00", "ok")
    elif field == "route_plan":
        selection = TaskSelectionResult(True, (), (), bad, valid.task_selection_result.schedule_result, "ok")
        result_obj = DynamicReplanningResult(valid.updated_request, selection, (), (), "09:00", "09:00", "ok")
    elif field == "schedule_result":
        selection = TaskSelectionResult(True, (), (), valid.task_selection_result.route_plan, bad, "ok")
        result_obj = DynamicReplanningResult(valid.updated_request, selection, (), (), "09:00", "09:00", "ok")
    else:
        result_obj = DynamicReplanningResult(bad, valid.task_selection_result, (), (), "09:00", "09:00", "ok")
    with patch("src.dynamic_planning_pipeline.parse_replanning_text", return_value=parse()), patch("src.dynamic_planning_pipeline.replan_with_update", return_value=result_obj):
        result = replan_from_text("map", request(), "change")
    assert result.status == "error" and result.replanning_result is None


@pytest.mark.parametrize("outer,schedule", [(0, True), (1, True), ("true", True), (None, True), (True, 0), (True, 1), (True, "false"), (True, None), (True, False), (False, True)])
def test_feasibility_types_and_consistency_are_checked(outer, schedule):
    route = RoutePlanResult(("dormitory", "gate"), 0, 0, 0, (), ())
    selection = TaskSelectionResult(outer, (), (), route, ScheduleCheckResult(schedule, "09:00", "09:00", (), ()), "x")
    obj = DynamicReplanningResult(request(), selection, (), (), "09:00", "09:00", "x")
    with patch("src.dynamic_planning_pipeline.parse_replanning_text", return_value=parse()), patch("src.dynamic_planning_pipeline.replan_with_update", return_value=obj):
        result = replan_from_text("map", request(), "change")
    assert result.status == "error" and result.replanning_result is None


def test_identity_and_single_calls():
    campus = object(); req = request(); text = "same"; upd = update(); parsed = ReplanningParseResult("ok", None, "x", upd); expected = replanning(True)
    with patch("src.dynamic_planning_pipeline.parse_replanning_text", return_value=parsed) as parser, patch("src.dynamic_planning_pipeline.replan_with_update", return_value=expected) as replanner:
        result = replan_from_text(campus, req, text)
    assert result.status == "ok"
    parser.assert_called_once()
    args, kwargs = parser.call_args; assert args[0] is text and args[1] is req
    args, kwargs = replanner.call_args; assert args[0] is campus and args[1] is req and args[2] is upd


def test_runtime_errors_propagate():
    with patch("src.dynamic_planning_pipeline.parse_replanning_text", side_effect=RuntimeError("parser")), patch("src.dynamic_planning_pipeline.replan_with_update") as replanner:
        with pytest.raises(RuntimeError): replan_from_text("map", request(), "x")
    replanner.assert_not_called()
    with patch("src.dynamic_planning_pipeline.parse_replanning_text", return_value=parse()), patch("src.dynamic_planning_pipeline.replan_with_update", side_effect=RuntimeError("replanner")):
        with pytest.raises(RuntimeError): replan_from_text("map", request(), "x")


def test_empty_request_and_repeated_call():
    empty = PlanningRequest("dormitory", "gate", "09:00", [])
    first = replanning(True); second = replanning(True)
    first = DynamicReplanningResult(empty, first.task_selection_result, (), (), "09:00", "09:00", "ok")
    with patch("src.dynamic_planning_pipeline.parse_replanning_text", return_value=parse()) as parser, patch("src.dynamic_planning_pipeline.replan_with_update", side_effect=[first, second]) as replanner:
        one = replan_from_text("map", empty, "one")
        two = replan_from_text("map", one.replanning_result.updated_request, "two")
    assert one.status == two.status == "ok"
    assert parser.call_count == replanner.call_count == 2
    assert parser.call_args_list[1].args[1] is one.replanning_result.updated_request
