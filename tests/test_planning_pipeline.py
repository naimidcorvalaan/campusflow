from dataclasses import FrozenInstanceError
from unittest.mock import Mock, patch

import pytest

import src.planning_pipeline as planning_pipeline
from src.extraction_models import ParseResult
from src.models import PlanningRequest, Task
from src.planning_pipeline import PlanningPipelineResult, plan_from_text
from src.route_plan import RoutePlanResult
from src.schedule_checker import ScheduleCheckResult
from src.task_selector import TaskSelectionError, TaskSelectionResult


def make_request(tasks=None):
    return PlanningRequest(
        current_location="dormitory",
        destination="gate",
        current_time="09:00",
        tasks=[] if tasks is None else tasks,
    )


def make_parse_result(status="ok", planning_request=None, message="解析成功"):
    return ParseResult(
        status=status,
        error_type=None,
        message=message,
        planning_request=planning_request,
    )


def make_route_plan(tasks=()):
    return RoutePlanResult(
        visit_order=("dormitory", "gate"),
        walking_minutes=20,
        stay_minutes=0,
        total_minutes=20,
        segments=(),
        tasks=tuple(tasks),
    )


def make_schedule_result(is_feasible=True):
    return ScheduleCheckResult(
        is_feasible=is_feasible,
        start_time="09:00",
        finish_time="09:20",
        scheduled_tasks=(),
        missed_deadlines=(),
    )


def make_selection_result(
    *, is_feasible=True, kept_tasks=(), dropped_tasks=()
):
    route_plan = make_route_plan(kept_tasks)
    schedule_result = make_schedule_result(is_feasible)
    return TaskSelectionResult(
        is_feasible=is_feasible,
        kept_tasks=tuple(kept_tasks),
        dropped_tasks=tuple(dropped_tasks),
        route_plan=route_plan,
        schedule_result=schedule_result,
        message="任务取舍完成",
    )


@pytest.mark.parametrize(
    ("parse_status", "expected_status"),
    [("needs_clarification", "needs_clarification"), ("rejected", "error")],
)
def test_non_ok_parse_stops_before_task_selection(
    parse_status, expected_status
):
    parse_result = make_parse_result(
        status=parse_status, message="输入信息不完整"
    )
    with patch(
        "src.planning_pipeline.parse_natural_language",
        return_value=parse_result,
    ), patch(
        "src.planning_pipeline.select_feasible_tasks"
    ) as selector_mock:
        result = plan_from_text(Mock(), "输入", "09:00")

    assert result.status == expected_status
    assert result.route_plan is None
    assert result.schedule_result is None
    assert result.task_selection_result is None
    selector_mock.assert_not_called()


def test_ok_parse_without_planning_request_stops_before_selection():
    with patch(
        "src.planning_pipeline.parse_natural_language",
        return_value=make_parse_result(planning_request=None),
    ), patch(
        "src.planning_pipeline.select_feasible_tasks"
    ) as selector_mock:
        result = plan_from_text(Mock(), "输入", "09:00")

    assert result.status == "error"
    assert "缺少规划请求" in result.message
    assert result.task_selection_result is None
    selector_mock.assert_not_called()


def test_all_tasks_feasible_returns_complete_selection_result():
    campus_map = Mock()
    request = make_request()
    parse_result = make_parse_result(planning_request=request)
    selection = make_selection_result()
    with patch(
        "src.planning_pipeline.parse_natural_language",
        return_value=parse_result,
    ) as parse_mock, patch(
        "src.planning_pipeline.select_feasible_tasks", return_value=selection
    ) as selector_mock:
        result = plan_from_text(campus_map, "帮我规划", "09:00")

    parse_mock.assert_called_once_with("帮我规划", "09:00")
    selector_mock.assert_called_once_with(campus_map, request)
    assert result.status == "ok"
    assert "规划成功" in result.message
    assert result.route_plan is selection.route_plan
    assert result.schedule_result is selection.schedule_result
    assert result.task_selection_result is selection


def test_dropped_optional_tasks_still_return_ok():
    optional = Task("library", "可选任务", False, 10, None)
    request = make_request([optional])
    selection = make_selection_result(dropped_tasks=(optional,))
    with patch(
        "src.planning_pipeline.parse_natural_language",
        return_value=make_parse_result(planning_request=request),
    ), patch(
        "src.planning_pipeline.select_feasible_tasks", return_value=selection
    ):
        result = plan_from_text(Mock(), "输入", "09:00")

    assert result.status == "ok"
    assert result.task_selection_result.dropped_tasks == (optional,)
    assert "保留全部必须任务" in result.message
    assert "放弃部分可选任务" in result.message
    assert result.route_plan is selection.route_plan
    assert result.schedule_result is selection.schedule_result


def test_infeasible_mandatory_tasks_preserve_all_results():
    mandatory = Task("library", "必须任务", True, 10, "09:01")
    request = make_request([mandatory])
    selection = make_selection_result(is_feasible=False, kept_tasks=(mandatory,))
    with patch(
        "src.planning_pipeline.parse_natural_language",
        return_value=make_parse_result(planning_request=request),
    ), patch(
        "src.planning_pipeline.select_feasible_tasks", return_value=selection
    ):
        result = plan_from_text(Mock(), "输入", "09:00")

    assert result.status == "infeasible"
    assert result.route_plan is selection.route_plan
    assert result.schedule_result is selection.schedule_result
    assert result.task_selection_result is selection
    assert "必须任务" in result.message
    assert "仍无法满足截止时间" in result.message


def test_task_selection_error_returns_fixed_safe_message():
    sensitive = (
        'Authorization: Bearer test-secret {"api_key":"secret"} '
        "TaskSelectionResult(secret) RuntimeError"
    )
    with patch(
        "src.planning_pipeline.parse_natural_language",
        return_value=make_parse_result(planning_request=make_request()),
    ), patch(
        "src.planning_pipeline.select_feasible_tasks",
        side_effect=TaskSelectionError(sensitive),
    ):
        result = plan_from_text(Mock(), "输入", "09:00")

    assert result.status == "error"
    assert result.route_plan is None
    assert result.schedule_result is None
    assert result.task_selection_result is None
    assert result.message == "任务方案无法生成，请检查地点和时间信息。"
    for leaked_text in (
        "Authorization", "Bearer", "test-secret", "api_key",
        "TaskSelectionResult", "RuntimeError", "{",
    ):
        assert leaked_text not in result.message


def test_pipeline_no_longer_imports_direct_route_or_schedule_steps():
    assert not hasattr(planning_pipeline, "build_route_plan")
    assert not hasattr(planning_pipeline, "check_schedule_feasibility")


def test_selector_receives_original_objects_once():
    campus_map = Mock()
    request = make_request()
    selection = make_selection_result()
    with patch(
        "src.planning_pipeline.parse_natural_language",
        return_value=make_parse_result(planning_request=request),
    ), patch(
        "src.planning_pipeline.select_feasible_tasks", return_value=selection
    ) as selector_mock:
        plan_from_text(campus_map, "输入", "09:00")

    selector_mock.assert_called_once_with(campus_map, request)


def test_pipeline_result_is_frozen_and_new_field_defaults_to_none():
    result = PlanningPipelineResult(
        parse_result=make_parse_result(status="rejected"),
        route_plan=None,
        schedule_result=None,
        status="error",
        message="规划失败",
    )
    assert result.task_selection_result is None
    with pytest.raises(FrozenInstanceError):
        result.status = "ok"


def test_pipeline_does_not_modify_input_objects():
    campus_map = Mock()
    task = Task("library", "借书", True, 10, None)
    request = make_request([task])
    original_request = (
        request.current_location, request.destination,
        request.current_time, tuple(request.tasks),
    )
    original_task = vars(task).copy()
    selection = make_selection_result(kept_tasks=(task,))
    with patch(
        "src.planning_pipeline.parse_natural_language",
        return_value=make_parse_result(planning_request=request),
    ), patch(
        "src.planning_pipeline.select_feasible_tasks", return_value=selection
    ):
        plan_from_text(campus_map, "输入", "09:00")

    assert (
        request.current_location, request.destination,
        request.current_time, tuple(request.tasks),
    ) == original_request
    assert vars(task) == original_task


def test_repeated_calls_with_same_mock_result_are_stable():
    request = make_request()
    parse_result = make_parse_result(planning_request=request)
    selection = make_selection_result()
    with patch(
        "src.planning_pipeline.parse_natural_language", return_value=parse_result
    ), patch(
        "src.planning_pipeline.select_feasible_tasks", return_value=selection
    ):
        first = plan_from_text(Mock(), "输入", "09:00")
        second = plan_from_text(Mock(), "输入", "09:00")

    assert first.status == second.status == "ok"
    assert first.route_plan is second.route_plan is selection.route_plan
    assert first.schedule_result is second.schedule_result
    assert first.task_selection_result is second.task_selection_result


def test_parse_runtime_error_propagates():
    with patch(
        "src.planning_pipeline.parse_natural_language",
        side_effect=RuntimeError("Bearer secret"),
    ), patch(
        "src.planning_pipeline.select_feasible_tasks"
    ) as selector_mock:
        with pytest.raises(RuntimeError, match="Bearer secret"):
            plan_from_text(Mock(), "输入", "09:00")

    selector_mock.assert_not_called()


def test_rejected_parse_message_is_not_exposed():
    sensitive = (
        'Authorization: Bearer secret api_key=x {"token":"x"} '
        "RoutePlanResult(secret) traceback"
    )
    parse_result = make_parse_result(status="rejected", message=sensitive)

    with patch(
        "src.planning_pipeline.parse_natural_language",
        return_value=parse_result,
    ), patch(
        "src.planning_pipeline.select_feasible_tasks"
    ) as selector_mock:
        result = plan_from_text(Mock(), "输入", "09:00")

    assert result.status == "error"
    assert result.message == "任务信息解析失败，请检查输入后重试。"
    assert sensitive not in result.message
    for marker in (
        "Authorization", "Bearer", "api_key", "token",
        "RoutePlanResult", "traceback", "{",
    ):
        assert marker not in result.message
    selector_mock.assert_not_called()


def test_clarification_uses_safe_message_and_preserves_questions():
    sensitive = 'Bearer secret {"api-key":"x"} PlanningRequest(secret)'
    parse_result = make_parse_result(
        status="needs_clarification", message=sensitive
    )
    parse_result.questions = ["请问您的起点在哪里？"]

    with patch(
        "src.planning_pipeline.parse_natural_language",
        return_value=parse_result,
    ), patch(
        "src.planning_pipeline.select_feasible_tasks"
    ) as selector_mock:
        result = plan_from_text(Mock(), "输入", "09:00")

    assert result.status == "needs_clarification"
    assert result.message == "需要补充任务信息。"
    assert result.parse_result is parse_result
    assert result.parse_result.questions == ["请问您的起点在哪里？"]
    assert sensitive not in result.message
    selector_mock.assert_not_called()


def test_unknown_parse_status_uses_fixed_safe_error():
    unknown_status = "internal_debug_status"
    sensitive = 'Authorization Bearer secret ["api-key"]'
    parse_result = make_parse_result(
        status=unknown_status, message=sensitive
    )

    with patch(
        "src.planning_pipeline.parse_natural_language",
        return_value=parse_result,
    ), patch(
        "src.planning_pipeline.select_feasible_tasks"
    ) as selector_mock:
        result = plan_from_text(Mock(), "输入", "09:00")

    assert result.status == "error"
    assert result.message == "任务信息解析结果异常，请稍后重试。"
    assert unknown_status not in result.message
    assert sensitive not in result.message
    selector_mock.assert_not_called()


def test_inconsistent_selection_feasibility_returns_safe_error():
    selection = TaskSelectionResult(
        is_feasible=True,
        kept_tasks=(),
        dropped_tasks=(),
        route_plan=make_route_plan(),
        schedule_result=make_schedule_result(False),
        message="internal",
    )

    result = run_with_selection(selection)

    assert_invalid_selection_result(result)


@pytest.mark.parametrize(
    "selection",
    [
        "TaskSelectionResult(secret)",
        TaskSelectionResult(
            True, (), (), "RoutePlanResult(secret)",
            make_schedule_result(True), "internal"
        ),
        TaskSelectionResult(
            True, (), (), make_route_plan(),
            "ScheduleCheckResult(secret)", "internal"
        ),
        TaskSelectionResult(
            1, (), (), make_route_plan(),
            make_schedule_result(True), "internal"
        ),
        TaskSelectionResult(
            "true", (), (), make_route_plan(),
            make_schedule_result(True), "internal"
        ),
    ],
)
def test_invalid_selection_result_types_return_safe_error(selection):
    result = run_with_selection(selection)

    assert_invalid_selection_result(result)


def run_with_selection(selection):
    with patch(
        "src.planning_pipeline.parse_natural_language",
        return_value=make_parse_result(planning_request=make_request()),
    ), patch(
        "src.planning_pipeline.select_feasible_tasks",
        return_value=selection,
    ):
        return plan_from_text(Mock(), "输入", "09:00")


def assert_invalid_selection_result(result):
    assert result.status == "error"
    assert result.route_plan is None
    assert result.schedule_result is None
    assert result.task_selection_result is None
    assert result.message == "任务选择结果异常，请稍后重试。"
    for marker in (
        "TaskSelectionResult", "RoutePlanResult", "ScheduleCheckResult",
        "internal", "true",
    ):
        assert marker not in result.message
