from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import src.dynamic_replanner as dynamic_replanner
from src.campus_map import load_campus_map
from src.dynamic_replanner import (
    DynamicReplanningError,
    DynamicReplanningResult,
    ReplanningUpdate,
    _parse_time,
    replan_with_update,
)
from src.input_validator import InputValidator
from src.models import PlanningRequest, Task
from src.route_plan import RoutePlanResult
from src.schedule_checker import ScheduleCheckResult
from src.task_selector import TaskSelectionError, TaskSelectionResult


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_campus_map.json"


@pytest.fixture
def campus_map():
    return load_campus_map(FIXTURE_PATH)


def make_task(
    location,
    description=None,
    mandatory=True,
    duration=10,
    deadline=None,
):
    return Task(
        location=location,
        description=description or f"在{location}办事",
        is_mandatory=mandatory,
        estimated_duration_minutes=duration,
        deadline=deadline,
    )


def make_request(tasks=None, current_time="09:00"):
    return PlanningRequest(
        current_location="dormitory",
        destination="gate",
        current_time=current_time,
        tasks=(
            [make_task("library"), make_task("canteen", mandatory=False)]
            if tasks is None
            else tasks
        ),
    )


def make_selection(request, feasible=True, dropped=()):
    kept = tuple(task for task in request.tasks if task not in dropped)
    route_plan = RoutePlanResult(
        visit_order=(request.current_location, request.destination),
        walking_minutes=10,
        stay_minutes=sum(task.estimated_duration_minutes for task in kept),
        total_minutes=10 + sum(
            task.estimated_duration_minutes for task in kept
        ),
        segments=(),
        tasks=kept,
    )
    schedule = ScheduleCheckResult(
        is_feasible=feasible,
        start_time=request.current_time,
        finish_time=request.current_time,
        scheduled_tasks=(),
        missed_deadlines=(),
    )
    return TaskSelectionResult(
        is_feasible=feasible,
        kept_tasks=kept,
        dropped_tasks=tuple(dropped),
        route_plan=route_plan,
        schedule_result=schedule,
        message="mock",
    )


def install_selector(monkeypatch, feasible=True, dropped=()):
    calls = []

    def fake_selector(campus_map, request):
        calls.append((campus_map, request))
        return make_selection(request, feasible=feasible, dropped=dropped)

    monkeypatch.setattr(
        "src.dynamic_replanner.select_feasible_tasks", fake_selector
    )
    return calls


@pytest.mark.parametrize(
    ("location", "expected_id"),
    [
        ("library", "library"),
        ("测试图书馆", "library"),
        ("图书馆", "library"),
    ],
)
def test_updates_current_location_from_id_name_or_alias(
    monkeypatch, campus_map, location, expected_id
):
    install_selector(monkeypatch)

    result = replan_with_update(
        campus_map, make_request(), ReplanningUpdate(new_current_location=location)
    )

    assert result.updated_request.current_location == expected_id


def test_unknown_current_location_is_rejected(monkeypatch, campus_map):
    calls = install_selector(monkeypatch)

    with pytest.raises(DynamicReplanningError, match="无法识别新当前位置"):
        replan_with_update(
            campus_map,
            make_request(),
            ReplanningUpdate(new_current_location="未知地点"),
        )

    assert calls == []


@pytest.mark.parametrize("bad_location", ["", "   ", 123, False])
def test_new_current_location_must_be_nonempty_string(
    campus_map, bad_location
):
    with pytest.raises(DynamicReplanningError, match="新当前位置必须是非空字符串"):
        replan_with_update(
            campus_map,
            make_request(),
            ReplanningUpdate(new_current_location=bad_location),
        )


@pytest.mark.parametrize(
    ("delay", "expected"), [(15, "09:15"), (0, "09:00")]
)
def test_updates_delay_minutes(monkeypatch, campus_map, delay, expected):
    install_selector(monkeypatch)
    result = replan_with_update(
        campus_map, make_request(), ReplanningUpdate(delay_minutes=delay)
    )

    assert result.previous_current_time == "09:00"
    assert result.updated_current_time == expected
    assert result.updated_request.current_time == expected


@pytest.mark.parametrize("bad_delay", [-1, True, False, 1.5, "5", None])
def test_invalid_delay_is_rejected(campus_map, bad_delay):
    with pytest.raises(DynamicReplanningError, match="延误分钟"):
        replan_with_update(
            campus_map, make_request(), ReplanningUpdate(delay_minutes=bad_delay)
        )


def test_delay_to_2359_is_allowed(monkeypatch, campus_map):
    install_selector(monkeypatch)
    result = replan_with_update(
        campus_map,
        make_request(current_time="23:50"),
        ReplanningUpdate(delay_minutes=9),
    )

    assert result.updated_current_time == "23:59"


def test_delay_across_midnight_is_rejected_without_wrapping(campus_map):
    request = make_request(current_time="23:50")

    with pytest.raises(DynamicReplanningError, match="不支持跨天"):
        replan_with_update(
            campus_map, request, ReplanningUpdate(delay_minutes=10)
        )

    assert request.current_time == "23:50"


@pytest.mark.parametrize(
    ("cancel_location", "cancelled_description"),
    [("食堂", "在canteen办事"), ("图书馆", "在library办事")],
)
def test_cancels_optional_or_mandatory_task(
    monkeypatch, campus_map, cancel_location, cancelled_description
):
    install_selector(monkeypatch)
    request = make_request()
    result = replan_with_update(
        campus_map,
        request,
        ReplanningUpdate(cancelled_task_locations=(cancel_location,)),
    )

    assert result.cancelled_tasks[0].description == cancelled_description
    assert result.cancelled_tasks[0] not in result.updated_request.tasks


def test_cancels_multiple_tasks_in_original_order(monkeypatch, campus_map):
    tasks = [
        make_task("library", "A"),
        make_task("canteen", "B", False),
        make_task("teaching_hall", "C"),
    ]
    install_selector(monkeypatch)

    result = replan_with_update(
        campus_map,
        make_request(tasks),
        ReplanningUpdate(cancelled_task_locations=("教学楼", "图书馆")),
    )

    assert result.cancelled_tasks == (tasks[0], tasks[2])
    assert result.updated_request.tasks == [tasks[1]]


def test_cancelling_missing_task_is_rejected(campus_map):
    with pytest.raises(DynamicReplanningError, match="没有对应的现有任务"):
        replan_with_update(
            campus_map,
            make_request(),
            ReplanningUpdate(cancelled_task_locations=("教学楼",)),
        )


def test_duplicate_cancelled_location_is_rejected(campus_map):
    with pytest.raises(DynamicReplanningError, match="重复取消"):
        replan_with_update(
            campus_map,
            make_request(),
            ReplanningUpdate(cancelled_task_locations=("library", "图书馆")),
        )


def test_unknown_cancelled_location_is_rejected(campus_map):
    with pytest.raises(DynamicReplanningError, match="取消指令中存在未知任务地点"):
        replan_with_update(
            campus_map,
            make_request(),
            ReplanningUpdate(cancelled_task_locations=("未知地点",)),
        )


def test_adds_one_task(monkeypatch, campus_map):
    added = make_task("teaching_hall", "新任务", False)
    install_selector(monkeypatch)

    result = replan_with_update(
        campus_map, make_request(), ReplanningUpdate(added_tasks=(added,))
    )

    assert result.updated_request.tasks[-1] is added
    assert result.added_tasks == (added,)


def test_adds_multiple_tasks_in_given_order(monkeypatch, campus_map):
    added = (make_task("teaching_hall", "C"), make_task("gate", "D"))
    install_selector(monkeypatch)

    result = replan_with_update(
        campus_map, make_request(), ReplanningUpdate(added_tasks=added)
    )

    assert result.updated_request.tasks[-2:] == list(added)
    assert result.added_tasks == added


def test_cancel_then_add_same_location_is_allowed(monkeypatch, campus_map):
    replacement = make_task("library", "替换任务", False)
    install_selector(monkeypatch)

    result = replan_with_update(
        campus_map,
        make_request(),
        ReplanningUpdate(
            cancelled_task_locations=("图书馆",),
            added_tasks=(replacement,),
        ),
    )

    assert result.cancelled_tasks[0].location == "library"
    assert result.updated_request.tasks[-1] is replacement


def test_added_task_conflicting_with_remaining_task_is_rejected(campus_map):
    with pytest.raises(DynamicReplanningError, match="重复任务地点"):
        replan_with_update(
            campus_map,
            make_request(),
            ReplanningUpdate(added_tasks=(make_task("图书馆"),)),
        )


def test_original_tasks_with_duplicate_canonical_locations_are_rejected(
    campus_map,
):
    request = make_request(
        [make_task("library", "A"), make_task("图书馆", "B", False)]
    )

    with pytest.raises(DynamicReplanningError, match="重复任务地点"):
        replan_with_update(campus_map, request, ReplanningUpdate())


def test_added_tasks_cannot_share_same_canonical_location(campus_map):
    added = (
        make_task("teaching_hall", "A"),
        make_task("教学楼", "B"),
    )

    with pytest.raises(DynamicReplanningError, match="重复任务地点"):
        replan_with_update(
            campus_map, make_request(), ReplanningUpdate(added_tasks=added)
        )


def test_added_task_with_unknown_location_is_rejected(campus_map):
    with pytest.raises(DynamicReplanningError, match="新增任务中存在未知地点"):
        replan_with_update(
            campus_map,
            make_request(),
            ReplanningUpdate(added_tasks=(make_task("未知"),)),
        )


@pytest.mark.parametrize(
    "bad_task",
    [
        Task("teaching_hall", "", True, 10, None),
        Task("teaching_hall", "x", 1, 10, None),
        Task("teaching_hall", "x", True, True, None),
        Task("teaching_hall", "x", True, -1, None),
        Task("teaching_hall", "x", True, 10, "24:00"),
    ],
)
def test_invalid_added_task_fields_are_rejected(campus_map, bad_task):
    with pytest.raises(DynamicReplanningError, match="更新后的规划请求无效"):
        replan_with_update(
            campus_map,
            make_request(),
            ReplanningUpdate(added_tasks=(bad_task,)),
        )


def test_more_than_eight_updated_tasks_is_rejected(campus_map):
    added = tuple(make_task("teaching_hall", str(index)) for index in range(8))

    with pytest.raises(DynamicReplanningError, match="最多支持8个任务"):
        replan_with_update(
            campus_map, make_request(), ReplanningUpdate(added_tasks=added)
        )


def make_eight_task_request():
    tasks = [make_task(f"place_{index}", str(index)) for index in range(8)]
    return PlanningRequest("dormitory", "gate", "09:00", tasks)


def install_identity_location_resolver(monkeypatch, campus_map):
    monkeypatch.setattr(
        campus_map,
        "resolve_location_id",
        lambda value: value.strip().casefold()
        if isinstance(value, str) and value.strip()
        else None,
    )


def test_exactly_eight_updated_tasks_are_allowed(monkeypatch, campus_map):
    install_identity_location_resolver(monkeypatch, campus_map)
    calls = install_selector(monkeypatch)

    result = replan_with_update(
        campus_map, make_eight_task_request(), ReplanningUpdate()
    )

    assert len(result.updated_request.tasks) == 8
    assert len(calls) == 1


def test_nine_updated_tasks_are_rejected_before_selector(monkeypatch, campus_map):
    install_identity_location_resolver(monkeypatch, campus_map)
    calls = install_selector(monkeypatch)
    request = make_eight_task_request()
    added = make_task("place_8", "8")

    with pytest.raises(DynamicReplanningError, match="最多支持8个任务"):
        replan_with_update(
            campus_map, request, ReplanningUpdate(added_tasks=(added,))
        )

    assert calls == []


def test_cancelling_last_task_allows_empty_request(monkeypatch, campus_map):
    task = make_task("library", "最后任务")
    request = make_request([task])
    calls = install_selector(monkeypatch)

    result = replan_with_update(
        campus_map,
        request,
        ReplanningUpdate(cancelled_task_locations=("library",)),
    )

    assert result.updated_request.tasks == []
    assert result.cancelled_tasks == (task,)
    assert result.added_tasks == ()
    assert len(calls) == 1
    assert calls[0][1].tasks == []
    assert result.task_selection_result.route_plan.tasks == ()
    assert request.tasks == [task]
    assert task.location == "library"


def test_original_empty_request_is_allowed(monkeypatch, campus_map):
    calls = install_selector(monkeypatch)
    request = make_request([])

    result = replan_with_update(
        campus_map,
        request,
        ReplanningUpdate(new_current_location="图书馆", delay_minutes=5),
    )

    assert result.updated_request.tasks == []
    assert result.updated_request.current_location == "library"
    assert result.updated_request.current_time == "09:05"
    assert len(calls) == 1


def test_two_consecutive_replans_from_empty_request(monkeypatch, campus_map):
    install_selector(monkeypatch)
    first = replan_with_update(
        campus_map,
        make_request([make_task("library")]),
        ReplanningUpdate(cancelled_task_locations=("library",)),
    )
    second = replan_with_update(
        campus_map,
        first.updated_request,
        ReplanningUpdate(new_current_location="食堂", delay_minutes=10),
    )

    assert first.updated_request.tasks == []
    assert second.updated_request.tasks == []
    assert second.updated_request.current_location == "canteen"
    assert second.updated_request.current_time == "09:10"


def test_invalid_original_request_is_rejected(campus_map):
    request = make_request(current_time="9:00")

    with pytest.raises(DynamicReplanningError, match="原规划请求无效"):
        replan_with_update(campus_map, request, ReplanningUpdate())


def test_input_validator_checks_original_and_updated_requests(
    monkeypatch, campus_map
):
    original_validate = InputValidator.validate
    validated_requests = []

    def recording_validate(request):
        validated_requests.append(request)
        return original_validate(request)

    monkeypatch.setattr(InputValidator, "validate", recording_validate)
    install_selector(monkeypatch)
    request = make_request()

    result = replan_with_update(campus_map, request, ReplanningUpdate())

    assert validated_requests == [request, result.updated_request]


@pytest.mark.parametrize("value", ["00:00", "09:00", "23:59"])
def test_ascii_time_values_are_accepted(value):
    assert isinstance(_parse_time(value), int)


@pytest.mark.parametrize(
    "value",
    [
        "０９:００",
        "٠٩:٠٠",
        "0９:00",
        "09:０٠",
        "9:00",
        "24:00",
        "09:60",
        None,
        900,
    ],
)
def test_non_ascii_or_invalid_time_values_are_rejected(value):
    with pytest.raises(DynamicReplanningError):
        _parse_time(value)


def test_combined_update_builds_expected_request(monkeypatch, campus_map):
    request = make_request()
    added = make_task("teaching_hall", "取材料", False)
    install_selector(monkeypatch)

    result = replan_with_update(
        campus_map,
        request,
        ReplanningUpdate(
            new_current_location="图书馆",
            delay_minutes=20,
            cancelled_task_locations=("食堂",),
            added_tasks=(added,),
        ),
    )

    assert result.updated_request.current_location == "library"
    assert result.updated_request.destination == request.destination
    assert result.updated_request.current_time == "09:20"
    assert result.updated_request.tasks == [request.tasks[0], added]


def test_input_objects_are_not_modified(monkeypatch, campus_map):
    request = make_request()
    added = make_task("teaching_hall", "C")
    original_request = (
        request.current_location,
        request.destination,
        request.current_time,
        tuple(request.tasks),
    )
    original_tasks = tuple(vars(task).copy() for task in request.tasks + [added])
    original_locations = tuple(campus_map.locations)
    original_edges = tuple(campus_map.edges)
    install_selector(monkeypatch)

    replan_with_update(
        campus_map,
        request,
        ReplanningUpdate(delay_minutes=5, added_tasks=(added,)),
    )

    assert (
        request.current_location,
        request.destination,
        request.current_time,
        tuple(request.tasks),
    ) == original_request
    assert tuple(vars(task) for task in request.tasks + [added]) == original_tasks
    assert tuple(campus_map.locations) == original_locations
    assert tuple(campus_map.edges) == original_edges


def test_selector_is_called_once_with_original_map_and_updated_request(
    monkeypatch, campus_map
):
    calls = install_selector(monkeypatch)

    result = replan_with_update(campus_map, make_request(), ReplanningUpdate())

    assert calls == [(campus_map, result.updated_request)]


def test_module_does_not_import_lower_level_route_functions():
    for name in (
        "build_route_plan",
        "check_schedule_feasibility",
        "find_optimal_route",
        "find_shortest_path",
    ):
        assert not hasattr(dynamic_replanner, name)


def test_task_selection_error_is_safely_converted(monkeypatch, campus_map):
    sensitive = (
        'Authorization Bearer secret {"api_key":"x"} '
        "TaskSelectionResult(secret)"
    )

    def failing_selector(campus_map, request):
        raise TaskSelectionError(sensitive)

    monkeypatch.setattr(
        "src.dynamic_replanner.select_feasible_tasks", failing_selector
    )

    with pytest.raises(DynamicReplanningError) as exc_info:
        replan_with_update(campus_map, make_request(), ReplanningUpdate())

    message = str(exc_info.value)
    assert message == "动态重规划无法生成任务方案。"
    for marker in (
        "Authorization", "Bearer", "secret", "api_key", "{",
        "TaskSelectionResult",
    ):
        assert marker not in message


def run_with_mock_selection(monkeypatch, campus_map, selection):
    monkeypatch.setattr(
        "src.dynamic_replanner.select_feasible_tasks",
        lambda campus_map, request: selection,
    )
    return replan_with_update(campus_map, make_request(), ReplanningUpdate())


@pytest.mark.parametrize("bad_selection", [None, "selection", object()])
def test_invalid_selection_result_object_is_rejected(
    monkeypatch, campus_map, bad_selection
):
    with pytest.raises(
        DynamicReplanningError, match="动态重规划结果异常"
    ) as exc_info:
        run_with_mock_selection(monkeypatch, campus_map, bad_selection)

    assert "object at" not in str(exc_info.value)
    assert "{" not in str(exc_info.value)


@pytest.mark.parametrize(
    "bad_selection",
    [
        TaskSelectionResult(
            True, (), (), "route", make_selection(make_request()).schedule_result, "x"
        ),
        TaskSelectionResult(
            True, (), (), make_selection(make_request()).route_plan, "schedule", "x"
        ),
        TaskSelectionResult(
            1, (), (), make_selection(make_request()).route_plan,
            make_selection(make_request()).schedule_result, "x"
        ),
        TaskSelectionResult(
            "true", (), (), make_selection(make_request()).route_plan,
            make_selection(make_request()).schedule_result, "x"
        ),
        TaskSelectionResult(
            True, (), (), make_selection(make_request()).route_plan,
            make_selection(make_request(), feasible=False).schedule_result, "x"
        ),
        TaskSelectionResult(
            False, (), (), make_selection(make_request()).route_plan,
            make_selection(make_request()).schedule_result, "x"
        ),
    ],
)
def test_inconsistent_selection_result_is_rejected(
    monkeypatch, campus_map, bad_selection
):
    with pytest.raises(
        DynamicReplanningError, match="动态重规划结果异常"
    ):
        run_with_mock_selection(monkeypatch, campus_map, bad_selection)


def test_result_is_frozen_and_sequences_are_tuples(monkeypatch, campus_map):
    install_selector(monkeypatch)
    result = replan_with_update(campus_map, make_request(), ReplanningUpdate())

    assert isinstance(result, DynamicReplanningResult)
    assert isinstance(result.cancelled_tasks, tuple)
    assert isinstance(result.added_tasks, tuple)
    with pytest.raises(FrozenInstanceError):
        result.updated_current_time = "10:00"


def test_replanning_update_is_frozen_and_uses_tuple_defaults():
    update = ReplanningUpdate()

    assert update.cancelled_task_locations == ()
    assert update.added_tasks == ()
    with pytest.raises(FrozenInstanceError):
        update.delay_minutes = 5


def test_repeated_updates_are_stable(monkeypatch, campus_map):
    install_selector(monkeypatch)
    request = make_request()
    update = ReplanningUpdate(delay_minutes=5)

    first = replan_with_update(campus_map, request, update)
    second = replan_with_update(campus_map, request, update)

    assert first.updated_request == second.updated_request
    assert first.cancelled_tasks == second.cancelled_tasks
    assert first.added_tasks == second.added_tasks
    assert first.message == second.message


@pytest.mark.parametrize(
    ("feasible", "has_dropped", "expected"),
    [
        (True, False, "全部任务均可行"),
        (True, True, "放弃部分可选任务"),
        (False, False, "必须任务仍无法满足截止时间"),
    ],
)
def test_result_message_reflects_selection_outcome(
    monkeypatch, campus_map, feasible, has_dropped, expected
):
    dropped = ()

    def fake_selector(campus_map, request):
        nonlocal dropped
        dropped = (request.tasks[-1],) if has_dropped else ()
        return make_selection(request, feasible=feasible, dropped=dropped)

    monkeypatch.setattr(
        "src.dynamic_replanner.select_feasible_tasks", fake_selector
    )

    result = replan_with_update(campus_map, make_request(), ReplanningUpdate())

    assert expected in result.message


@pytest.mark.parametrize(
    ("campus_map_value", "request_value", "update_value"),
    [
        (None, make_request(), ReplanningUpdate()),
        ("map", make_request(), ReplanningUpdate()),
        (None, None, ReplanningUpdate()),
    ],
)
def test_invalid_campus_map_type_is_rejected(
    campus_map_value, request_value, update_value
):
    with pytest.raises(DynamicReplanningError):
        replan_with_update(campus_map_value, request_value, update_value)


@pytest.mark.parametrize("bad_request", [None, "request"])
def test_invalid_planning_request_type_is_rejected(campus_map, bad_request):
    with pytest.raises(DynamicReplanningError, match="原规划请求格式无效"):
        replan_with_update(campus_map, bad_request, ReplanningUpdate())


@pytest.mark.parametrize("bad_update", [None, "update"])
def test_invalid_update_type_is_rejected(campus_map, bad_update):
    with pytest.raises(DynamicReplanningError, match="更新指令格式无效"):
        replan_with_update(campus_map, make_request(), bad_update)


@pytest.mark.parametrize(
    "update",
    [
        ReplanningUpdate(cancelled_task_locations=[]),
        ReplanningUpdate(added_tasks=[]),
    ],
)
def test_update_task_collections_must_be_tuples(campus_map, update):
    with pytest.raises(DynamicReplanningError, match="必须是元组"):
        replan_with_update(campus_map, make_request(), update)


@pytest.mark.parametrize("bad_value", [None, "", "   ", 123])
def test_cancelled_location_items_must_be_nonempty_strings(
    campus_map, bad_value
):
    with pytest.raises(DynamicReplanningError, match="非空字符串"):
        replan_with_update(
            campus_map,
            make_request(),
            ReplanningUpdate(cancelled_task_locations=(bad_value,)),
        )


def test_added_items_must_be_tasks(campus_map):
    with pytest.raises(DynamicReplanningError, match="新增任务格式无效"):
        replan_with_update(
            campus_map,
            make_request(),
            ReplanningUpdate(added_tasks=("not-a-task",)),
        )
