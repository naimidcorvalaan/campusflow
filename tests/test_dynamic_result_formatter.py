from copy import deepcopy
from dataclasses import replace

import pytest

from src.dynamic_planning_pipeline import DynamicPlanningPipelineResult
from src.dynamic_replanner import DynamicReplanningResult
from src.models import PlanningRequest, Task
from src.replanning_parser import ReplanningParseResult
from src.result_formatter import format_dynamic_planning_result
from src.route_plan import RoutePlanResult
from src.schedule_checker import ScheduleCheckResult, ScheduledTask
from src.task_selector import TaskSelectionResult


_DEFAULT = object()
INCOMPLETE_MESSAGE = "动态规划结果不完整，无法展示。"


def task(location, description, mandatory=True, deadline=None):
    return Task(location, description, mandatory, 5, deadline)


def make_dynamic_result(
    status="ok",
    *,
    cancelled=_DEFAULT,
    added=_DEFAULT,
    kept=_DEFAULT,
    dropped=_DEFAULT,
    visit_order=_DEFAULT,
    scheduled=_DEFAULT,
    missed=_DEFAULT,
    previous_time="09:00",
    updated_time="09:10",
):
    cancelled = (
        (task("cafe", "cancel coffee", False),)
        if cancelled is _DEFAULT
        else cancelled
    )
    added = (
        (task("lab", "new experiment", True),)
        if added is _DEFAULT
        else added
    )
    kept = (
        (task("library", "borrow", True, "10:00"),)
        if kept is _DEFAULT
        else kept
    )
    dropped = (
        (task("gym", "exercise", False),)
        if dropped is _DEFAULT
        else dropped
    )
    visit_order = (
        ("dormitory", "library", "gate")
        if visit_order is _DEFAULT
        else visit_order
    )
    route = RoutePlanResult(
        tuple(visit_order),
        12,
        5,
        17,
        (),
        tuple(kept),
    )
    if scheduled is _DEFAULT:
        scheduled = tuple(
            ScheduledTask(item, item.location, "09:05", "09:10", item.deadline, True)
            for item in kept
        )
    if missed is _DEFAULT:
        missed = ()
    schedule = ScheduleCheckResult(
        status != "infeasible",
        previous_time,
        updated_time,
        tuple(scheduled),
        tuple(missed),
    )
    selection = TaskSelectionResult(
        status != "infeasible",
        tuple(kept),
        tuple(dropped),
        route,
        schedule,
        "selected",
    )
    updated_request = PlanningRequest(
        "dormitory", "gate", updated_time, list(kept) + list(dropped)
    )
    replanning = DynamicReplanningResult(
        updated_request,
        selection,
        tuple(cancelled),
        tuple(added),
        previous_time,
        updated_time,
        "updated",
    )
    parsed = ReplanningParseResult("ok", None, "parsed")
    return DynamicPlanningPipelineResult(parsed, replanning, status, "result")


def with_selection(result, selection):
    return replace(
        result,
        replanning_result=replace(
            result.replanning_result, task_selection_result=selection
        ),
    )


def test_ok_displays_time_and_all_task_groups():
    text = format_dynamic_planning_result(make_dynamic_result())

    assert "时间变化：09:00 → 09:10" in text
    assert "已取消任务：\n- cafe：cancel coffee（可选）" in text
    assert "新增任务：\n- lab：new experiment（必须）" in text
    assert "保留任务：\n- library：borrow（必须）" in text
    assert "暂时放弃的可选任务：\n- gym：exercise（可选）" in text


def test_ok_displays_route_durations_and_schedule():
    text = format_dynamic_planning_result(make_dynamic_result())

    assert "推荐路线：dormitory → library → gate" in text
    assert "预计步行：12 分钟" in text
    assert "任务停留：5 分钟" in text
    assert "预计总耗时：17 分钟" in text
    assert "任务安排：" in text
    assert "1. 到达 library：09:05，完成：09:10" in text
    assert "截止：10:00，状态：可按时完成" in text


def test_infeasible_displays_missed_deadline():
    late_task = task("library", "borrow", True, "09:10")
    late = ScheduledTask(late_task, "library", "09:05", "09:20", "09:10", False)
    result = make_dynamic_result(
        "infeasible", kept=(late_task,), scheduled=(late,), missed=(late,)
    )

    text = format_dynamic_planning_result(result)

    assert "已放弃全部可选任务，但必须任务仍无法满足截止时间。" in text
    assert "超时任务：" in text
    assert "- library：完成：09:20，截止：09:10，状态：超时 10 分钟" in text


def test_needs_clarification_displays_questions_in_original_order():
    parsed = ReplanningParseResult(
        "needs_clarification",
        "missing_information",
        "message",
        None,
        (),
        ("  first\n question ", "second\tquestion"),
    )
    result = DynamicPlanningPipelineResult(
        parsed, None, "needs_clarification", "message"
    )

    text = format_dynamic_planning_result(result)

    assert "1. first question" in text
    assert "2. second question" in text
    assert text.index("1. first question") < text.index("2. second question")


@pytest.mark.parametrize(
    "message",
    [
        "Traceback: secret",
        "Bearer secret-token",
        "Authorization: token",
        "API Key: secret",
        "api_key=secret",
        '{"token": "secret"}',
        "DynamicReplanningResult(updated_request=secret)",
    ],
    ids=[
        "traceback",
        "bearer",
        "authorization",
        "api-key",
        "api_key",
        "json",
        "object-repr",
    ],
)
def test_error_hides_sensitive_message(message):
    result = DynamicPlanningPipelineResult(
        ReplanningParseResult("rejected", "error", "parsed"),
        None,
        "error",
        message,
    )

    assert format_dynamic_planning_result(result) == (
        "动态规划失败：请检查输入信息后重试。"
    )


@pytest.mark.parametrize("status", ["unknown", "rejected", ""])
def test_unknown_status_returns_fixed_message(status):
    result = DynamicPlanningPipelineResult(
        ReplanningParseResult("ok", None, "parsed"), None, status, "message"
    )

    assert format_dynamic_planning_result(result) == "动态规划结果状态未知，无法展示。"


def test_missing_replanning_result_returns_incomplete_message():
    result = replace(make_dynamic_result(), replanning_result=None)

    assert format_dynamic_planning_result(result) == INCOMPLETE_MESSAGE


def _malformed_results():
    base = make_dynamic_result()
    dynamic = base.replanning_result
    selection = dynamic.task_selection_result
    schedule = selection.schedule_result
    cases = [
        ("bad-selection", replace(base, replanning_result=replace(dynamic, task_selection_result=object()))),
        ("bad-route", with_selection(base, replace(selection, route_plan=object()))),
        ("bad-schedule", with_selection(base, replace(selection, schedule_result=object()))),
        ("cancelled-not-tuple", replace(base, replanning_result=replace(dynamic, cancelled_tasks=[]))),
        ("added-not-tuple", replace(base, replanning_result=replace(dynamic, added_tasks=[]))),
        ("kept-not-tuple", with_selection(base, replace(selection, kept_tasks=[]))),
        ("dropped-not-tuple", with_selection(base, replace(selection, dropped_tasks=[]))),
        ("cancelled-bad-item", replace(base, replanning_result=replace(dynamic, cancelled_tasks=(object(),)))),
        ("added-bad-item", replace(base, replanning_result=replace(dynamic, added_tasks=(object(),)))),
        ("kept-bad-item", with_selection(base, replace(selection, kept_tasks=(object(),)))),
        ("dropped-bad-item", with_selection(base, replace(selection, dropped_tasks=(object(),)))),
        ("scheduled-not-tuple", with_selection(base, replace(selection, schedule_result=replace(schedule, scheduled_tasks=[])))),
        ("missed-not-tuple", with_selection(base, replace(selection, schedule_result=replace(schedule, missed_deadlines=[])))),
        ("scheduled-bad-item", with_selection(base, replace(selection, schedule_result=replace(schedule, scheduled_tasks=(object(),))))),
        ("missed-bad-item", with_selection(base, replace(selection, schedule_result=replace(schedule, missed_deadlines=(object(),))))),
    ]
    return [pytest.param(result, id=case_id) for case_id, result in cases]


@pytest.mark.parametrize("result", _malformed_results())
def test_invalid_nested_structure_returns_incomplete_message(result):
    assert format_dynamic_planning_result(result) == INCOMPLETE_MESSAGE


def test_empty_task_groups_and_schedule_display_normally():
    result = make_dynamic_result(
        cancelled=(), added=(), kept=(), dropped=(), scheduled=(), missed=()
    )

    text = format_dynamic_planning_result(result)

    assert "已取消任务：无" in text
    assert "新增任务：无" in text
    assert "保留任务：无" in text
    assert "暂时放弃的可选任务" not in text
    assert "任务安排：无" in text


def test_all_sequences_preserve_original_order():
    cancelled = (task("cancel-1", "one"), task("cancel-2", "two"))
    added = (task("add-1", "one"), task("add-2", "two"))
    kept = (task("keep-1", "one"), task("keep-2", "two"))
    dropped = (task("drop-1", "one", False), task("drop-2", "two", False))
    scheduled = tuple(
        ScheduledTask(item, item.location, "09:00", "09:05", None, True)
        for item in kept
    )
    result = make_dynamic_result(
        cancelled=cancelled,
        added=added,
        kept=kept,
        dropped=dropped,
        visit_order=("route-1", "route-2", "route-3"),
        scheduled=scheduled,
    )

    text = format_dynamic_planning_result(result)

    for first, second in (
        ("cancel-1", "cancel-2"),
        ("add-1", "add-2"),
        ("keep-1", "keep-2"),
        ("drop-1", "drop-2"),
        ("route-1", "route-2"),
        ("到达 keep-1", "到达 keep-2"),
    ):
        assert text.index(first) < text.index(second)


def test_route_locations_are_normalized_individually():
    result = make_dynamic_result(
        visit_order=("  dorm\n A ", " library\t west ", " gate  ")
    )

    text = format_dynamic_planning_result(result)

    assert "推荐路线：dorm A → library west → gate" in text


def test_non_string_route_location_is_not_stringified():
    marker = object()
    result = make_dynamic_result(visit_order=("dorm", marker, "gate"))

    text = format_dynamic_planning_result(result)

    assert "推荐路线：dorm → 未说明 → gate" in text
    assert repr(marker) not in text


def test_all_time_fields_are_normalized():
    kept = task("library", "borrow", True, " 10:00\n ")
    scheduled = ScheduledTask(
        kept, "library", " 09:05\n ", " 09:10\t ", " 10:00\n ", True
    )
    result = make_dynamic_result(
        kept=(kept,),
        previous_time=" 09:00\n ",
        updated_time=" 09:10\t ",
        scheduled=(scheduled,),
    )

    text = format_dynamic_planning_result(result)

    assert "时间变化：09:00 → 09:10" in text
    assert "到达 library：09:05，完成：09:10，截止：10:00" in text
    assert "\t" not in text


def test_non_string_times_are_not_stringified():
    marker = object()
    kept = task("library", "borrow", True, marker)
    scheduled = ScheduledTask(kept, "library", marker, marker, marker, True)
    result = make_dynamic_result(
        kept=(kept,),
        previous_time=marker,
        updated_time=marker,
        scheduled=(scheduled,),
    )

    text = format_dynamic_planning_result(result)

    assert "时间变化：未说明 → 未说明" in text
    assert "到达 library：未说明，完成：未说明，截止：未说明" in text
    assert repr(marker) not in text


def test_none_deadline_displays_no_deadline():
    kept = task("library", "borrow", True, None)
    scheduled = ScheduledTask(kept, "library", "09:05", "09:10", None, True)

    text = format_dynamic_planning_result(
        make_dynamic_result(kept=(kept,), scheduled=(scheduled,))
    )

    assert "截止：无截止时间，状态：无需检查" in text


def test_dangerous_task_text_is_hidden():
    dangerous = task(
        "TaskSelectionResult(secret)", "is_mandatory=True", True
    )
    result = make_dynamic_result(
        cancelled=(dangerous,), added=(), kept=(), dropped=(), scheduled=()
    )

    text = format_dynamic_planning_result(result)

    assert "- 内容已隐藏：内容已隐藏（必须）" in text
    assert "TaskSelectionResult(" not in text
    assert "is_mandatory=" not in text


def test_formatter_does_not_modify_input_objects():
    result = make_dynamic_result()
    snapshot = deepcopy(result)
    updated_tasks = result.replanning_result.updated_request.tasks
    kept_task = result.replanning_result.task_selection_result.kept_tasks[0]

    format_dynamic_planning_result(result)

    assert result == snapshot
    assert result.replanning_result.updated_request.tasks is updated_tasks
    assert result.replanning_result.task_selection_result.kept_tasks[0] is kept_task


def test_output_contains_no_internal_or_collection_repr():
    list_marker = ["secret-list"]
    tuple_marker = ("secret-tuple",)
    result = make_dynamic_result(
        visit_order=(list_marker, tuple_marker, object()),
        previous_time=list_marker,
        updated_time=tuple_marker,
    )

    text = format_dynamic_planning_result(result)

    for marker in (
        "DynamicPlanningPipelineResult(",
        "DynamicReplanningResult(",
        "TaskSelectionResult(",
        "RoutePlanResult(",
        "ScheduleCheckResult(",
        "Task(",
        repr(list_marker),
        repr(tuple_marker),
    ):
        assert marker not in text
