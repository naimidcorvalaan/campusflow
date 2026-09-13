import ast
import importlib
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

import src.main as main_module
from src.main import (
    CURRENT_REQUEST_KEY,
    DYNAMIC_DISPLAY_KEY,
    DYNAMIC_HISTORY_KEY,
    INITIAL_TIME_INPUT_KEY,
    PLANNING_DISPLAY_KEY,
    RESET_INITIAL_TIME_KEY,
    build_effective_request_from_dynamic_result,
    build_effective_request_from_initial_result,
    build_dynamic_planning_display,
    clear_planning_state,
    format_current_plan_summary,
    initialize_initial_time_state,
    save_initial_plan_state,
    save_dynamic_plan_state,
)
from src.dynamic_planning_pipeline import DynamicPlanningPipelineResult
from src.dynamic_replanner import DynamicReplanningResult
from src.extraction_models import ParseResult
from src.models import PlanningRequest, Task
from src.planning_pipeline import PlanningPipelineResult
from src.replanning_parser import ReplanningParseResult
from src.route_plan import RoutePlanResult
from src.schedule_checker import ScheduleCheckResult
from src.task_selector import TaskSelectionResult


def req():
    return PlanningRequest("a", "b", "09:00", [Task("x", "do", True, 1, None)])


def make_parse_result(planning_request=None, status="ok"):
    return ParseResult(status, None, "parsed", [], [], planning_request)


def make_route(tasks=()):
    return RoutePlanResult(("a", "b"), 1, 1, 2, (), tuple(tasks))


def make_schedule(feasible=True):
    return ScheduleCheckResult(feasible, "09:00", "09:02", (), ())


def make_selection(kept=(), dropped=(), feasible=True):
    route = make_route(kept)
    return TaskSelectionResult(
        feasible,
        tuple(kept),
        tuple(dropped),
        route,
        make_schedule(feasible),
        "selected",
    )


def make_initial_result(status="ok", kept=(), dropped=(), selection=True):
    source = PlanningRequest("a", "b", "09:00", list(kept) + list(dropped))
    route = make_route(source.tasks)
    selected = (
        make_selection(kept, dropped, status == "ok")
        if selection
        else None
    )
    return PlanningPipelineResult(
        make_parse_result(source),
        route,
        make_schedule(status == "ok"),
        status,
        "result",
        selected,
    )


def make_dynamic_result(
    status="ok", kept=(), dropped=(), previous_time="09:00", updated_time="09:10"
):
    source = PlanningRequest("a", "b", updated_time, list(kept) + list(dropped))
    selection = make_selection(kept, dropped, status == "ok")
    replanning = DynamicReplanningResult(
        source,
        selection,
        (),
        (),
        previous_time,
        updated_time,
        "updated",
    )
    parsed = ReplanningParseResult("ok", None, "parsed")
    return DynamicPlanningPipelineResult(parsed, replanning, status, "result")


@pytest.mark.parametrize("value", ["", "   ", None, 123])
def test_empty_change_does_not_call_dependencies(value):
    planner = Mock(); formatter = Mock()
    text, updated = build_dynamic_planning_display("map", req(), value, planner, formatter)
    assert updated is None and text
    planner.assert_not_called(); formatter.assert_not_called()


def test_valid_change_calls_dependencies_once_and_preserves_identity():
    result = Mock(status="needs_clarification")
    planner = Mock(return_value=result); formatter = Mock(return_value="中文结果")
    current = req(); text, updated = build_dynamic_planning_display("map", current, "变化", planner, formatter)
    assert text == "中文结果" and updated is None
    planner.assert_called_once_with("map", current, "变化")
    formatter.assert_called_once_with(result)


def test_dynamic_state_history_is_bounded_and_cleaned():
    state = {DYNAMIC_HISTORY_KEY: []}
    current = req()
    for i in range(12):
        save_dynamic_plan_state(state, current, "display", "  change\n" + str(i) + "  ")
    assert len(state[DYNAMIC_HISTORY_KEY]) == 10
    assert state[DYNAMIC_HISTORY_KEY][0]["change_text"] == "change 2"
    assert state[CURRENT_REQUEST_KEY] is current


def test_dynamic_none_request_keeps_old_request():
    current = req(); state = {CURRENT_REQUEST_KEY: current}
    save_dynamic_plan_state(state, None, "追问", "变化")
    assert state[CURRENT_REQUEST_KEY] is current
    assert state[DYNAMIC_DISPLAY_KEY] == "追问"


def test_clear_only_removes_planning_keys():
    state = {
        CURRENT_REQUEST_KEY: req(),
        PLANNING_DISPLAY_KEY: "x",
        DYNAMIC_DISPLAY_KEY: "y",
        DYNAMIC_HISTORY_KEY: [],
        INITIAL_TIME_INPUT_KEY: "09:00",
        "other": 1,
    }
    clear_planning_state(state)
    assert state == {INITIAL_TIME_INPUT_KEY: "09:00", "other": 1}


def test_initialize_initial_time_state_sets_time_once():
    state = {}
    provider = Mock(return_value="14:30")

    initialize_initial_time_state(state, provider)

    assert state[INITIAL_TIME_INPUT_KEY] == "14:30"
    provider.assert_called_once_with()


def test_initialize_initial_time_state_preserves_user_time_on_rerun():
    state = {INITIAL_TIME_INPUT_KEY: "09:00"}
    provider = Mock(return_value="14:31")

    initialize_initial_time_state(state, provider)

    assert state[INITIAL_TIME_INPUT_KEY] == "09:00"
    provider.assert_not_called()


def test_initialize_initial_time_state_resets_only_when_requested():
    state = {
        INITIAL_TIME_INPUT_KEY: "09:00",
        RESET_INITIAL_TIME_KEY: True,
    }
    provider = Mock(return_value="14:32")

    initialize_initial_time_state(state, provider)

    assert state[INITIAL_TIME_INPUT_KEY] == "14:32"
    assert RESET_INITIAL_TIME_KEY not in state
    provider.assert_called_once_with()


def test_initialize_initial_time_state_keeps_manual_time_across_reruns():
    state = {INITIAL_TIME_INPUT_KEY: "09:00"}
    provider = Mock(return_value="14:33")

    initialize_initial_time_state(state, provider)
    initialize_initial_time_state(state, provider)

    assert state[INITIAL_TIME_INPUT_KEY] == "09:00"
    provider.assert_not_called()


def test_initialize_initial_time_state_changes_only_time_keys():
    state = {"other": object(), "count": 1}
    original_other = state["other"]

    initialize_initial_time_state(state, Mock(return_value="14:34"))

    assert state["other"] is original_other
    assert state["count"] == 1
    assert set(state) == {"other", "count", INITIAL_TIME_INPUT_KEY}


def test_initialize_initial_time_state_rejects_non_string_provider_value():
    state = {}

    with pytest.raises(TypeError, match="必须返回字符串"):
        initialize_initial_time_state(state, Mock(return_value=object()))

    assert INITIAL_TIME_INPUT_KEY not in state


def test_current_plan_summary_displays_fields_and_tasks_in_order():
    first = Task("library", "borrow", True, 5, None)
    second = Task("canteen", "lunch", False, 20, None)
    request = PlanningRequest("dormitory", "gate", "09:00", [first, second])

    text = format_current_plan_summary(request)

    assert "当前地点：dormitory" in text
    assert "当前时间：09:00" in text
    assert "最终目的地：gate" in text
    assert text.index("地点：library") < text.index("地点：canteen")
    assert "描述：borrow；属性：必须" in text
    assert "描述：lunch；属性：可选" in text


def test_current_plan_summary_displays_empty_tasks():
    request = PlanningRequest("a", "b", "09:00", [])

    assert "当前保留任务：无" in format_current_plan_summary(request)


def test_current_plan_summary_normalizes_task_whitespace():
    task = Task("  图书\n\t馆  ", "  归还\n  图书\t ", True, 5, None)

    text = format_current_plan_summary(
        PlanningRequest(" 宿舍\n A ", " 校门\t B ", " 09:00 ", [task])
    )

    assert "当前地点：宿舍 A" in text
    assert "最终目的地：校门 B" in text
    assert "地点：图书 馆；描述：归还 图书" in text


def test_current_plan_summary_truncates_long_fields():
    request = PlanningRequest(
        "地" * 250,
        "终点",
        "09:00",
        [Task("任务点", "描" * 250, True, 5, None)],
    )

    text = format_current_plan_summary(request)

    assert f"当前地点：{'地' * 199}…" in text
    assert f"描述：{'描' * 199}…" in text
    assert "地" * 200 not in text
    assert "描" * 200 not in text


def test_current_plan_summary_does_not_stringify_non_strings():
    marker = object()
    request = PlanningRequest(marker, "b", marker, [Task(marker, marker, True, 5, None)])

    text = format_current_plan_summary(request)

    assert text.count("未说明") == 4
    assert repr(marker) not in text
    assert "object at" not in text


def test_current_plan_summary_marks_invalid_task_attribute():
    request = PlanningRequest("a", "b", "09:00", [Task("x", "do", 1, 5, None)])

    text = format_current_plan_summary(request)

    assert "属性：任务属性异常" in text
    assert "属性：必须" not in text
    assert "属性：可选" not in text


@pytest.mark.parametrize(
    "value",
    [
        object(),
        PlanningRequest("a", "b", "09:00", ()),
        PlanningRequest("a", "b", "09:00", [object()]),
    ],
    ids=["not-request", "tasks-not-list", "non-task-item"],
)
def test_current_plan_summary_rejects_invalid_structure(value):
    assert format_current_plan_summary(value) == "当前计划信息异常，无法展示。"


def test_current_plan_summary_does_not_modify_input():
    task = Task(" x\n ", " do\t now ", True, 5, None)
    tasks = [task]
    request = PlanningRequest(" a\n ", " b ", " 09:00 ", tasks)
    request_before = vars(request).copy()
    task_before = vars(task).copy()

    format_current_plan_summary(request)

    assert vars(request) == request_before
    assert vars(task) == task_before
    assert request.tasks is tasks
    assert request.tasks[0] is task


@pytest.mark.parametrize("status", ["ok", "infeasible"])
def test_initial_effective_request_uses_only_kept_tasks(status):
    kept = Task("library", "borrow", True, 5, None)
    dropped = Task("gym", "exercise", False, 20, None)
    result = make_initial_result(status, (kept,), (dropped,))

    effective = build_effective_request_from_initial_result(result)

    assert effective.tasks == [kept]
    assert effective.tasks[0] is kept
    assert dropped not in effective.tasks


def test_initial_effective_request_supports_legacy_result_without_selection():
    first = Task("library", "borrow", True, 5, None)
    second = Task("canteen", "lunch", False, 20, None)
    result = make_initial_result("ok", (first, second), selection=False)

    effective = build_effective_request_from_initial_result(result)

    assert effective.tasks == [first, second]
    assert effective.tasks is not result.parse_result.planning_request.tasks


def test_initial_effective_request_allows_empty_kept_tasks():
    result = make_initial_result("infeasible", (), (Task("gym", "exercise", False, 5, None),))

    effective = build_effective_request_from_initial_result(result)

    assert effective is not None
    assert effective.tasks == []


@pytest.mark.parametrize("status", ["needs_clarification", "error"])
def test_initial_effective_request_rejects_non_final_status(status):
    result = make_initial_result("ok", (Task("x", "do", True, 1, None),))
    result = PlanningPipelineResult(
        result.parse_result,
        result.route_plan,
        result.schedule_result,
        status,
        result.message,
        result.task_selection_result,
    )

    assert build_effective_request_from_initial_result(result) is None


def _invalid_initial_results():
    request = req()
    route = make_route(request.tasks)
    schedule = make_schedule()
    return [
        PlanningPipelineResult(object(), route, schedule, "ok", "x", None),
        PlanningPipelineResult(make_parse_result(object()), route, schedule, "ok", "x", None),
        PlanningPipelineResult(make_parse_result(request), object(), schedule, "ok", "x", None),
        PlanningPipelineResult(make_parse_result(request), route, schedule, "ok", "x", object()),
        PlanningPipelineResult(
            make_parse_result(request),
            route,
            schedule,
            "ok",
            "x",
            TaskSelectionResult(True, list(request.tasks), (), route, schedule, "x"),
        ),
        PlanningPipelineResult(
            make_parse_result(request),
            route,
            schedule,
            "ok",
            "x",
            TaskSelectionResult(True, (object(),), (), route, schedule, "x"),
        ),
    ]


@pytest.mark.parametrize("result", _invalid_initial_results())
def test_initial_effective_request_rejects_invalid_structure(result):
    assert build_effective_request_from_initial_result(result) is None


def test_initial_effective_request_copies_list_without_mutating_source():
    kept = Task("library", "borrow", True, 5, None)
    dropped = Task("gym", "exercise", False, 10, None)
    result = make_initial_result("ok", (kept,), (dropped,))
    source_tasks = result.parse_result.planning_request.tasks
    source_snapshot = list(source_tasks)

    effective = build_effective_request_from_initial_result(result)

    assert effective.tasks is not source_tasks
    assert effective.tasks[0] is kept
    assert source_tasks == source_snapshot
    assert result.task_selection_result.kept_tasks == (kept,)
    assert result.task_selection_result.dropped_tasks == (dropped,)


@pytest.mark.parametrize("status", ["ok", "infeasible"])
def test_dynamic_effective_request_uses_only_kept_tasks(status):
    kept = Task("library", "borrow", True, 5, None)
    dropped = Task("gym", "exercise", False, 10, None)
    result = make_dynamic_result(status, (kept,), (dropped,))

    effective = build_effective_request_from_dynamic_result(result)

    assert effective.tasks == [kept]
    assert effective.tasks is not result.replanning_result.updated_request.tasks
    assert effective.tasks[0] is kept
    assert dropped not in effective.tasks


def test_dynamic_effective_request_allows_empty_kept_tasks():
    result = make_dynamic_result("ok", (), (Task("gym", "exercise", False, 10, None),))

    effective = build_effective_request_from_dynamic_result(result)

    assert effective is not None
    assert effective.tasks == []


@pytest.mark.parametrize("status", ["needs_clarification", "error"])
def test_dynamic_effective_request_rejects_non_final_status(status):
    result = make_dynamic_result("ok", (Task("x", "do", True, 1, None),))
    result = DynamicPlanningPipelineResult(
        result.parse_result, result.replanning_result, status, result.message
    )

    assert build_effective_request_from_dynamic_result(result) is None


def _invalid_dynamic_results():
    valid = make_dynamic_result("ok", (Task("x", "do", True, 1, None),))
    dynamic = valid.replanning_result
    selection = dynamic.task_selection_result
    bad_kept_type = TaskSelectionResult(
        True,
        list(selection.kept_tasks),
        (),
        selection.route_plan,
        selection.schedule_result,
        "x",
    )
    bad_kept_item = TaskSelectionResult(
        True,
        (object(),),
        (),
        selection.route_plan,
        selection.schedule_result,
        "x",
    )
    return [
        DynamicPlanningPipelineResult(valid.parse_result, None, "ok", "x"),
        DynamicPlanningPipelineResult(valid.parse_result, object(), "ok", "x"),
        DynamicPlanningPipelineResult(
            valid.parse_result,
            DynamicReplanningResult(object(), selection, (), (), "09:00", "09:10", "x"),
            "ok",
            "x",
        ),
        DynamicPlanningPipelineResult(
            valid.parse_result,
            DynamicReplanningResult(dynamic.updated_request, object(), (), (), "09:00", "09:10", "x"),
            "ok",
            "x",
        ),
        DynamicPlanningPipelineResult(
            valid.parse_result,
            DynamicReplanningResult(dynamic.updated_request, bad_kept_type, (), (), "09:00", "09:10", "x"),
            "ok",
            "x",
        ),
        DynamicPlanningPipelineResult(
            valid.parse_result,
            DynamicReplanningResult(dynamic.updated_request, bad_kept_item, (), (), "09:00", "09:10", "x"),
            "ok",
            "x",
        ),
    ]


@pytest.mark.parametrize("result", _invalid_dynamic_results())
def test_dynamic_effective_request_rejects_invalid_nested_structure(result):
    assert build_effective_request_from_dynamic_result(result) is None


def test_dynamic_effective_request_does_not_modify_source():
    kept = Task("library", "borrow", True, 5, None)
    dropped = Task("gym", "exercise", False, 10, None)
    result = make_dynamic_result("ok", (kept,), (dropped,))
    original_tasks = result.replanning_result.updated_request.tasks
    snapshot = list(original_tasks)

    effective = build_effective_request_from_dynamic_result(result)

    assert effective.tasks is not original_tasks
    assert effective.tasks[0] is kept
    assert original_tasks == snapshot
    assert result.replanning_result.task_selection_result.kept_tasks == (kept,)


def test_dynamic_display_strips_change_and_preserves_argument_identity():
    campus_map = object()
    current = req()
    result = make_dynamic_result("ok", tuple(current.tasks))
    replanner = Mock(return_value=result)
    formatter = Mock(return_value="展示")

    display, updated = build_dynamic_planning_display(
        campus_map, current, "  延误十分钟\n", replanner, formatter
    )

    assert display == "展示"
    assert updated.tasks == current.tasks
    replanner.assert_called_once_with(campus_map, current, "延误十分钟")
    formatter.assert_called_once_with(result)
    assert replanner.call_args.args[0] is campus_map
    assert replanner.call_args.args[1] is current


@pytest.mark.parametrize("status", ["ok", "infeasible"])
def test_dynamic_display_returns_effective_request_for_final_status(status):
    kept = Task("library", "borrow", True, 5, None)
    result = make_dynamic_result(status, (kept,))

    display, updated = build_dynamic_planning_display(
        "map", req(), "change", Mock(return_value=result), Mock(return_value="展示")
    )

    assert display == "展示"
    assert updated.tasks == [kept]


@pytest.mark.parametrize("status", ["needs_clarification", "error"])
def test_dynamic_display_returns_none_request_for_non_final_status(status):
    result = make_dynamic_result("ok", ())
    result = DynamicPlanningPipelineResult(
        result.parse_result, result.replanning_result, status, "message"
    )

    assert build_dynamic_planning_display(
        "map", req(), "change", Mock(return_value=result), Mock(return_value="展示")
    ) == ("展示", None)


@pytest.mark.parametrize("error_type", [RuntimeError, TypeError, AttributeError])
def test_dynamic_display_propagates_replanner_errors(error_type):
    formatter = Mock()
    replanner = Mock(side_effect=error_type("failure"))

    with pytest.raises(error_type):
        build_dynamic_planning_display("map", req(), "change", replanner, formatter)

    formatter.assert_not_called()


@pytest.mark.parametrize("error_type", [RuntimeError, TypeError, AttributeError])
def test_dynamic_display_propagates_formatter_errors(error_type):
    result = make_dynamic_result("ok", ())
    formatter = Mock(side_effect=error_type("failure"))

    with pytest.raises(error_type):
        build_dynamic_planning_display(
            "map", req(), "change", Mock(return_value=result), formatter
        )

    formatter.assert_called_once_with(result)


def test_save_initial_state_replaces_dynamic_state():
    kept = Task("library", "borrow", True, 5, None)
    state = {
        CURRENT_REQUEST_KEY: req(),
        DYNAMIC_DISPLAY_KEY: "旧展示",
        DYNAMIC_HISTORY_KEY: [{"old": True}],
    }

    saved = save_initial_plan_state(
        state, make_initial_result("ok", (kept,)), "新规划"
    )

    assert state[CURRENT_REQUEST_KEY] is saved
    assert state[PLANNING_DISPLAY_KEY] == "新规划"
    assert state[DYNAMIC_DISPLAY_KEY] is None
    assert state[DYNAMIC_HISTORY_KEY] == []


def test_save_initial_state_does_not_overwrite_on_invalid_request():
    current = req()
    state = {CURRENT_REQUEST_KEY: current, PLANNING_DISPLAY_KEY: "旧规划"}
    invalid = make_initial_result("needs_clarification", ())

    assert save_initial_plan_state(state, invalid, "新规划") is None
    assert state == {CURRENT_REQUEST_KEY: current, PLANNING_DISPLAY_KEY: "旧规划"}


def test_save_dynamic_state_updates_current_request():
    updated = PlanningRequest("new", "b", "09:10", [])
    state = {CURRENT_REQUEST_KEY: req()}

    save_dynamic_plan_state(state, updated, "展示", "变化")

    assert state[CURRENT_REQUEST_KEY] is updated
    assert state[DYNAMIC_DISPLAY_KEY] == "展示"
    assert state[DYNAMIC_HISTORY_KEY] == [{"change_text": "变化", "display": "展示"}]


def test_save_dynamic_state_ignores_non_string_display():
    current = req()
    history = [{"change_text": "旧", "display": "旧展示"}]
    state = {
        CURRENT_REQUEST_KEY: current,
        DYNAMIC_DISPLAY_KEY: "旧展示",
        DYNAMIC_HISTORY_KEY: history,
    }

    save_dynamic_plan_state(state, None, object(), "变化")

    assert state[CURRENT_REQUEST_KEY] is current
    assert state[DYNAMIC_DISPLAY_KEY] == "旧展示"
    assert state[DYNAMIC_HISTORY_KEY] is history
    assert len(history) == 1


@pytest.mark.parametrize("bad_history", [None, "bad", (), {}])
def test_save_dynamic_state_rebuilds_non_list_history(bad_history):
    state = {DYNAMIC_HISTORY_KEY: bad_history}

    save_dynamic_plan_state(state, None, "展示", "变化")

    assert state[DYNAMIC_HISTORY_KEY] == [{"change_text": "变化", "display": "展示"}]


def test_save_dynamic_state_keeps_latest_ten_records():
    state = {DYNAMIC_HISTORY_KEY: []}

    for index in range(11):
        save_dynamic_plan_state(state, None, f"展示{index}", f"变化{index}")

    assert len(state[DYNAMIC_HISTORY_KEY]) == 10
    assert state[DYNAMIC_HISTORY_KEY][0]["change_text"] == "变化1"
    assert state[DYNAMIC_HISTORY_KEY][-1]["change_text"] == "变化10"


def test_save_dynamic_state_normalizes_and_limits_change_text():
    state = {}

    save_dynamic_plan_state(state, None, "展示", "  A\n\t" + "长" * 250 + "  ")

    change_text = state[DYNAMIC_HISTORY_KEY][0]["change_text"]
    assert change_text.startswith("A ")
    assert "\n" not in change_text and "\t" not in change_text
    assert len(change_text) == 200


def test_empty_task_request_can_be_saved():
    empty = PlanningRequest("a", "b", "09:00", [])
    state = {}

    save_dynamic_plan_state(state, empty, "展示", "变化")

    assert state[CURRENT_REQUEST_KEY] is empty
    assert state[CURRENT_REQUEST_KEY].tasks == []


def test_consecutive_dynamic_updates_use_latest_request_without_reviving_dropped():
    kept = Task("library", "borrow", True, 5, None)
    dropped = Task("gym", "exercise", False, 10, None)
    original = PlanningRequest("a", "b", "09:00", [kept, dropped])
    first_result = make_dynamic_result(
        "ok", (kept,), (dropped,), "09:00", "09:10"
    )
    second_result = make_dynamic_result(
        "ok", (kept,), (), "09:10", "09:10"
    )
    replanner = Mock(side_effect=[first_result, second_result])
    formatter = Mock(side_effect=["第一次", "第二次"])
    time_provider = Mock(return_value="14:35")
    state = {CURRENT_REQUEST_KEY: original, INITIAL_TIME_INPUT_KEY: "09:00"}

    first_display, first_request = build_dynamic_planning_display(
        "map", state[CURRENT_REQUEST_KEY], "first", replanner, formatter
    )
    save_dynamic_plan_state(state, first_request, first_display, "first")
    second_display, second_request = build_dynamic_planning_display(
        "map", state[CURRENT_REQUEST_KEY], "second", replanner, formatter
    )
    save_dynamic_plan_state(state, second_request, second_display, "second")

    assert replanner.call_args_list[1].args[1] is first_request
    assert state[CURRENT_REQUEST_KEY] is second_request
    assert original.current_time == "09:00"
    assert first_request.current_time == "09:10"
    assert second_request.current_time == "09:10"
    assert second_request.tasks == [kept]
    assert dropped not in first_request.tasks
    assert dropped not in second_request.tasks
    initialize_initial_time_state(state, time_provider)
    time_provider.assert_not_called()


def _main_source_and_tree():
    source = Path(main_module.__file__).read_text(encoding="utf-8")
    return source, ast.parse(source)


def test_main_has_single_entrypoint_and_no_removed_pages():
    source, tree = _main_source_and_tree()
    top_level_main = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    ]
    main_guards = [
        node for node in tree.body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
    ]

    assert len(top_level_main) == 1
    assert len(main_guards) == 1
    for forbidden in (
        "_legacy_main",
        "_legacy_wrapper",
        "_removed_page_code",
        "_removed_wrapper_code",
    ):
        assert forbidden not in source


def test_main_uses_two_independent_submit_forms():
    source, tree = _main_source_and_tree()
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    form_names = {
        call.args[0].value
        for call in calls
        if isinstance(call.func, ast.Attribute)
        and call.func.attr == "form"
        and call.args
        and isinstance(call.args[0], ast.Constant)
    }
    submit_calls = [
        call for call in calls
        if isinstance(call.func, ast.Attribute)
        and call.func.attr == "form_submit_button"
    ]
    bare_button_labels = {
        call.args[0].value
        for call in calls
        if isinstance(call.func, ast.Attribute)
        and call.func.attr == "button"
        and call.args
        and isinstance(call.args[0], ast.Constant)
    }

    assert {"initial_plan_form", "dynamic_plan_form"} <= form_names
    assert len(submit_calls) >= 2
    assert "重新规划" not in bare_button_labels
    assert source.count('with st.form("initial_plan_form")') == 1
    assert source.count('with st.form("dynamic_plan_form")') == 1


def test_main_uses_stable_initial_time_key_without_dynamic_default():
    source, tree = _main_source_and_tree()
    text_input_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "text_input"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "当前时间（HH:MM）"
    ]

    assert len(text_input_calls) == 1
    keywords = {keyword.arg: keyword.value for keyword in text_input_calls[0].keywords}
    assert isinstance(keywords.get("key"), ast.Name)
    assert keywords["key"].id == "INITIAL_TIME_INPUT_KEY"
    assert "value" not in keywords
    assert "value=datetime.now" not in source


def test_clear_button_calls_clear_rerun_fallback_and_returns():
    _, tree = _main_source_and_tree()
    clear_branch = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Call)
        and isinstance(node.test.func, ast.Attribute)
        and node.test.func.attr == "button"
        and node.test.args
        and isinstance(node.test.args[0], ast.Constant)
        and node.test.args[0].value == "开始新计划"
    )
    called_names = {
        node.func.id
        for node in ast.walk(clear_branch)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    called_attributes = {
        node.func.attr
        for node in ast.walk(clear_branch)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert "clear_planning_state" in called_names
    assert {"rerun", "experimental_rerun"} <= called_attributes
    assert any(isinstance(node, ast.Return) for node in ast.walk(clear_branch))
    reset_assignment = next(
        node for node in ast.walk(clear_branch)
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Subscript)
        and isinstance(
            getattr(node.targets[0].slice, "value", node.targets[0].slice),
            ast.Name,
        )
        and getattr(
            node.targets[0].slice, "value", node.targets[0].slice
        ).id == "RESET_INITIAL_TIME_KEY"
    )
    assert isinstance(reset_assignment.value, ast.Constant)
    assert reset_assignment.value.value is True


def test_importing_main_has_no_runtime_side_effects():
    with ExitStack() as stack:
        set_page_config = stack.enter_context(patch("streamlit.set_page_config"))
        title = stack.enter_context(patch("streamlit.title"))
        map_loader = stack.enter_context(patch("src.campus_map.load_campus_map"))
        planner = stack.enter_context(patch("src.planning_pipeline.plan_from_text"))
        replanner = stack.enter_context(
            patch("src.dynamic_planning_pipeline.replan_from_text")
        )
        api_call = stack.enter_context(patch("src.tju_llm_client.call_tju_llm"))
        importlib.reload(main_module)

    try:
        set_page_config.assert_not_called()
        title.assert_not_called()
        map_loader.assert_not_called()
        planner.assert_not_called()
        replanner.assert_not_called()
        api_call.assert_not_called()
    finally:
        importlib.reload(main_module)
