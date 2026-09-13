from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from src.campus_map import load_campus_map
from src.models import PlanningRequest, Task
from src.route_plan import RoutePlanError, RoutePlanResult
from src.schedule_checker import ScheduleCheckError, ScheduleCheckResult
from src.task_selector import (
    TaskSelectionError,
    TaskSelectionResult,
    select_feasible_tasks,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_campus_map.json"


@pytest.fixture
def campus_map():
    return load_campus_map(FIXTURE_PATH)


def make_task(location, description, mandatory, duration=10, deadline=None):
    return Task(
        location=location,
        description=description,
        is_mandatory=mandatory,
        estimated_duration_minutes=duration,
        deadline=deadline,
    )


def make_request(tasks, current_time="09:00"):
    return PlanningRequest(
        current_location="dormitory",
        destination="gate",
        current_time=current_time,
        tasks=tasks,
    )


def install_candidate_mocks(
    monkeypatch, feasible_sets, totals=None, reverse_route_tasks=False
):
    totals = totals or {}
    calls = {"build": [], "schedule": []}

    def fake_build(campus_map, request):
        calls["build"].append(request)
        key = frozenset(task.description for task in request.tasks)
        route_tasks = tuple(request.tasks)
        if reverse_route_tasks:
            route_tasks = tuple(reversed(route_tasks))
        return RoutePlanResult(
            visit_order=(request.current_location, request.destination),
            walking_minutes=totals.get(key, 10),
            stay_minutes=0,
            total_minutes=totals.get(key, 10),
            segments=(),
            tasks=route_tasks,
        )

    def fake_schedule(route_plan, request):
        calls["schedule"].append((route_plan, request))
        key = frozenset(task.description for task in request.tasks)
        return ScheduleCheckResult(
            is_feasible=key in feasible_sets,
            start_time=request.current_time,
            finish_time=request.current_time,
            scheduled_tasks=(),
            missed_deadlines=(),
        )

    monkeypatch.setattr("src.task_selector.build_route_plan", fake_build)
    monkeypatch.setattr(
        "src.task_selector.check_schedule_feasibility", fake_schedule
    )
    return calls


def test_all_tasks_feasible_drops_nothing(campus_map):
    tasks = [
        make_task("library", "借书", True, deadline="10:30"),
        make_task("canteen", "吃饭", False, deadline="10:30"),
    ]

    result = select_feasible_tasks(campus_map, make_request(tasks))

    assert result.is_feasible is True
    assert set(id(task) for task in result.kept_tasks) == set(id(task) for task in tasks)
    assert result.dropped_tasks == ()


def test_optional_task_causing_lateness_is_dropped(campus_map):
    mandatory = make_task("library", "借书", True, deadline="10:30")
    optional = make_task("canteen", "吃饭", False, duration=15, deadline="09:20")

    result = select_feasible_tasks(
        campus_map, make_request([mandatory, optional])
    )

    assert result.is_feasible is True
    assert result.dropped_tasks == (optional,)
    assert result.kept_tasks == (mandatory,)


def test_multiple_optional_tasks_only_drops_needed_one(campus_map):
    mandatory = make_task("library", "借书", True, deadline="11:00")
    late_optional = make_task(
        "canteen", "早餐", False, duration=15, deadline="09:20"
    )
    other_optional = make_task("teaching_hall", "取材料", False)

    result = select_feasible_tasks(
        campus_map,
        make_request([mandatory, late_optional, other_optional]),
    )

    assert result.is_feasible is True
    assert result.dropped_tasks == (late_optional,)
    assert other_optional in result.kept_tasks


def test_prefers_more_optional_tasks(monkeypatch, campus_map):
    mandatory = make_task("m", "M", True)
    option_a = make_task("a", "A", False)
    option_b = make_task("b", "B", False)
    option_c = make_task("c", "C", False)
    feasible = {frozenset(("M", "A", "B")), frozenset(("M", "C"))}
    totals = {
        frozenset(("M", "A", "B")): 100,
        frozenset(("M", "C")): 1,
    }
    install_candidate_mocks(monkeypatch, feasible, totals)

    result = select_feasible_tasks(
        campus_map, make_request([mandatory, option_a, option_b, option_c])
    )

    assert result.kept_tasks == (mandatory, option_a, option_b)
    assert result.dropped_tasks == (option_c,)


def test_same_count_prefers_lower_total_minutes(monkeypatch, campus_map):
    mandatory = make_task("m", "M", True)
    option_a = make_task("a", "A", False)
    option_b = make_task("b", "B", False)
    feasible = {frozenset(("M", "A")), frozenset(("M", "B"))}
    totals = {
        frozenset(("M", "A")): 20,
        frozenset(("M", "B")): 10,
    }
    install_candidate_mocks(monkeypatch, feasible, totals)

    result = select_feasible_tasks(
        campus_map, make_request([mandatory, option_a, option_b])
    )

    assert result.kept_tasks == (mandatory, option_b)
    assert result.dropped_tasks == (option_a,)


def test_mandatory_task_is_never_dropped(campus_map):
    mandatory = make_task(
        "canteen", "必须吃饭", True, duration=15, deadline="09:20"
    )
    optional = make_task("library", "可选借书", False)

    result = select_feasible_tasks(
        campus_map, make_request([mandatory, optional])
    )

    assert mandatory in result.kept_tasks
    assert mandatory not in result.dropped_tasks


def test_mandatory_only_solution_can_remain_infeasible(campus_map):
    mandatory = make_task(
        "canteen", "必须吃饭", True, duration=15, deadline="09:20"
    )

    result = select_feasible_tasks(campus_map, make_request([mandatory]))

    assert result.is_feasible is False
    assert result.kept_tasks == (mandatory,)
    assert result.dropped_tasks == ()
    assert result.schedule_result.is_feasible is False
    assert "必须任务" in result.message


def test_all_mandatory_tasks_feasible(campus_map):
    tasks = [
        make_task("canteen", "吃饭", True, deadline="10:30"),
        make_task("library", "借书", True, deadline="11:00"),
    ]

    result = select_feasible_tasks(campus_map, make_request(tasks))

    assert result.is_feasible is True
    assert result.dropped_tasks == ()


def test_all_mandatory_tasks_infeasible(campus_map):
    tasks = [
        make_task("canteen", "吃饭", True, duration=15, deadline="09:20"),
        make_task("library", "借书", True),
    ]

    result = select_feasible_tasks(campus_map, make_request(tasks))

    assert result.is_feasible is False
    assert set(id(task) for task in result.kept_tasks) == set(id(task) for task in tasks)
    assert result.dropped_tasks == ()


def test_no_tasks_is_feasible(campus_map):
    result = select_feasible_tasks(campus_map, make_request([]))

    assert result.is_feasible is True
    assert result.kept_tasks == ()
    assert result.dropped_tasks == ()


@pytest.mark.parametrize("bad_value", [None, 0, 1, "true", "是", "", []])
def test_invalid_is_mandatory_type_returns_chinese_error(
    campus_map, bad_value
):
    task = make_task("library", "借书", bad_value)

    with pytest.raises(TaskSelectionError, match="is_mandatory.*布尔值"):
        select_feasible_tasks(campus_map, make_request([task]))


def test_more_than_eight_tasks_is_rejected(campus_map):
    tasks = [make_task("library", str(index), False) for index in range(9)]

    with pytest.raises(TaskSelectionError, match="最多支持8个任务"):
        select_feasible_tasks(campus_map, make_request(tasks))


def test_unknown_location_is_wrapped_in_chinese_error(campus_map):
    task = make_task("未知地点", "未知任务", False)

    with pytest.raises(
        TaskSelectionError, match="任务方案的路线无法生成"
    ):
        select_feasible_tasks(campus_map, make_request([task]))


def test_dropped_tasks_keep_original_relative_order(monkeypatch, campus_map):
    mandatory = make_task("m", "M", True)
    option_a = make_task("a", "A", False)
    option_b = make_task("b", "B", False)
    option_c = make_task("c", "C", False)
    install_candidate_mocks(monkeypatch, {frozenset(("M", "B"))})

    result = select_feasible_tasks(
        campus_map, make_request([option_a, mandatory, option_b, option_c])
    )

    assert result.dropped_tasks == (option_a, option_c)


def test_kept_tasks_match_route_plan_visit_tasks(monkeypatch, campus_map):
    mandatory = make_task("m", "M", True)
    optional = make_task("a", "A", False)
    install_candidate_mocks(
        monkeypatch,
        {frozenset(("M", "A"))},
        reverse_route_tasks=True,
    )

    result = select_feasible_tasks(
        campus_map, make_request([mandatory, optional])
    )

    assert result.kept_tasks == result.route_plan.tasks
    assert result.kept_tasks == (optional, mandatory)


def test_result_is_frozen_and_task_sequences_are_tuples(campus_map):
    result = select_feasible_tasks(campus_map, make_request([]))

    assert isinstance(result, TaskSelectionResult)
    assert isinstance(result.kept_tasks, tuple)
    assert isinstance(result.dropped_tasks, tuple)
    with pytest.raises(FrozenInstanceError):
        result.is_feasible = False


def test_does_not_modify_request_tasks_or_map(campus_map):
    tasks = [
        make_task("library", "借书", True, deadline="10:30"),
        make_task("canteen", "吃饭", False, deadline="10:30"),
    ]
    request = make_request(tasks)
    original_request = (
        request.current_location,
        request.destination,
        request.current_time,
        tuple(request.tasks),
    )
    original_task_values = tuple(vars(task).copy() for task in tasks)
    original_locations = tuple(campus_map.locations)
    original_edges = tuple(campus_map.edges)

    select_feasible_tasks(campus_map, request)

    assert (
        request.current_location,
        request.destination,
        request.current_time,
        tuple(request.tasks),
    ) == original_request
    assert tuple(vars(task) for task in tasks) == original_task_values
    assert tuple(campus_map.locations) == original_locations
    assert tuple(campus_map.edges) == original_edges


def test_repeated_runs_choose_same_tied_combination(monkeypatch, campus_map):
    mandatory = make_task("m", "M", True)
    option_a = make_task("a", "A", False)
    option_b = make_task("b", "B", False)
    feasible = {frozenset(("M", "A")), frozenset(("M", "B"))}
    totals = {
        frozenset(("M", "A")): 10,
        frozenset(("M", "B")): 10,
    }
    install_candidate_mocks(monkeypatch, feasible, totals)
    request = make_request([mandatory, option_a, option_b])

    first = select_feasible_tasks(campus_map, request)
    second = select_feasible_tasks(campus_map, request)

    assert first.kept_tasks == second.kept_tasks == (mandatory, option_a)


def test_candidates_reuse_route_plan_and_schedule_checker(
    monkeypatch, campus_map
):
    mandatory = make_task("m", "M", True)
    optional = make_task("a", "A", False)
    calls = install_candidate_mocks(
        monkeypatch, {frozenset(("M",))}
    )

    select_feasible_tasks(campus_map, make_request([mandatory, optional]))

    assert len(calls["build"]) == 2
    assert len(calls["schedule"]) == 2
    assert [
        tuple(task.description for task in request.tasks)
        for request in calls["build"]
    ] == [("M", "A"), ("M",)]


def test_only_optional_tasks_can_fall_back_to_empty_candidate(
    monkeypatch, campus_map
):
    option_a = make_task("a", "A", False)
    option_b = make_task("b", "B", False)
    calls = install_candidate_mocks(monkeypatch, {frozenset()})

    result = select_feasible_tasks(
        campus_map, make_request([option_a, option_b])
    )

    assert result.is_feasible is True
    assert result.kept_tasks == ()
    assert result.dropped_tasks == (option_a, option_b)
    assert tuple(calls["build"][-1].tasks) == ()


def test_route_plan_error_is_safely_wrapped(monkeypatch, campus_map):
    sensitive = (
        'Bearer test-secret api_key=secret {"token":"secret"} '
        "RoutePlanResult(secret)"
    )
    schedule_calls = []

    def failing_build(campus_map, request):
        raise RoutePlanError(sensitive)

    def fake_schedule(route_plan, request):
        schedule_calls.append((route_plan, request))

    monkeypatch.setattr("src.task_selector.build_route_plan", failing_build)
    monkeypatch.setattr(
        "src.task_selector.check_schedule_feasibility", fake_schedule
    )

    with pytest.raises(TaskSelectionError) as exc_info:
        select_feasible_tasks(campus_map, make_request([]))

    message = str(exc_info.value)
    assert message == "任务方案的路线无法生成，请检查地图和任务地点。"
    assert schedule_calls == []
    for leaked_text in (
        "Bearer",
        "test-secret",
        "api_key",
        "token",
        "RoutePlanResult",
        "{",
    ):
        assert leaked_text not in message


def test_schedule_error_is_safely_wrapped(monkeypatch, campus_map):
    sensitive = (
        'Authorization: Bearer test-secret {"api_key":"secret"} '
        "ScheduleCheckResult(secret)"
    )

    def fake_build(campus_map, request):
        return RoutePlanResult(
            visit_order=(request.current_location, request.destination),
            walking_minutes=10,
            stay_minutes=0,
            total_minutes=10,
            segments=(),
            tasks=tuple(request.tasks),
        )

    def failing_schedule(route_plan, request):
        raise ScheduleCheckError(sensitive)

    monkeypatch.setattr("src.task_selector.build_route_plan", fake_build)
    monkeypatch.setattr(
        "src.task_selector.check_schedule_feasibility", failing_schedule
    )

    with pytest.raises(TaskSelectionError) as exc_info:
        select_feasible_tasks(campus_map, make_request([]))

    message = str(exc_info.value)
    assert message == "任务方案的时间安排无法检查，请检查任务时间信息。"
    for leaked_text in (
        "Authorization",
        "Bearer",
        "test-secret",
        "api_key",
        "ScheduleCheckResult",
        "{",
    ):
        assert leaked_text not in message


def test_exactly_eight_tasks_are_evaluated(monkeypatch, campus_map):
    tasks = [make_task(str(index), str(index), False) for index in range(8)]
    feasible = {frozenset(str(index) for index in range(8))}
    calls = install_candidate_mocks(monkeypatch, feasible)

    result = select_feasible_tasks(campus_map, make_request(tasks))

    assert result.is_feasible is True
    assert len(calls["build"]) == 1
    assert len(calls["schedule"]) == 1


@pytest.mark.parametrize(
    "campus_map_value,request_factory,error_text",
    [
        (None, lambda: None, "campus_map"),
        ("not-a-map", lambda: None, "campus_map"),
    ],
)
def test_invalid_campus_map_type_is_rejected(
    campus_map_value, request_factory, error_text
):
    with pytest.raises(TaskSelectionError, match=error_text):
        select_feasible_tasks(campus_map_value, request_factory())


@pytest.mark.parametrize("bad_request", [None, "not-a-request"])
def test_invalid_planning_request_type_is_rejected(campus_map, bad_request):
    with pytest.raises(TaskSelectionError, match="planning_request"):
        select_feasible_tasks(campus_map, bad_request)


def test_tasks_must_be_a_list(campus_map):
    request = make_request([])
    request.tasks = ()

    with pytest.raises(TaskSelectionError, match="tasks.*列表"):
        select_feasible_tasks(campus_map, request)


def test_each_task_must_be_a_task_instance(campus_map):
    request = make_request([])
    request.tasks = ["not-a-task"]

    with pytest.raises(TaskSelectionError, match="第1个任务"):
        select_feasible_tasks(campus_map, request)


def test_fully_feasible_candidate_does_not_enumerate_smaller_subsets(
    monkeypatch, campus_map
):
    mandatory = make_task("m", "M", True)
    option_a = make_task("a", "A", False)
    option_b = make_task("b", "B", False)
    all_tasks = frozenset(("M", "A", "B"))
    calls = install_candidate_mocks(monkeypatch, {all_tasks})

    result = select_feasible_tasks(
        campus_map, make_request([mandatory, option_a, option_b])
    )

    assert result.dropped_tasks == ()
    assert len(calls["build"]) == 1
    assert len(calls["schedule"]) == 1
    assert frozenset(
        task.description for task in calls["build"][0].tasks
    ) == all_tasks


def test_infeasible_mandatory_fallback_uses_pure_mandatory_candidate(
    monkeypatch, campus_map
):
    mandatory = make_task("m", "M", True)
    option_a = make_task("a", "A", False)
    option_b = make_task("b", "B", False)
    pure_mandatory = {}

    def fake_build(campus_map, request):
        result = RoutePlanResult(
            visit_order=(request.current_location, request.destination),
            walking_minutes=len(request.tasks),
            stay_minutes=0,
            total_minutes=len(request.tasks),
            segments=(),
            tasks=tuple(request.tasks),
        )
        if request.tasks == [mandatory]:
            pure_mandatory["route_plan"] = result
        return result

    def fake_schedule(route_plan, request):
        result = ScheduleCheckResult(
            is_feasible=False,
            start_time=request.current_time,
            finish_time=request.current_time,
            scheduled_tasks=(),
            missed_deadlines=(),
        )
        if request.tasks == [mandatory]:
            pure_mandatory["schedule_result"] = result
        return result

    monkeypatch.setattr("src.task_selector.build_route_plan", fake_build)
    monkeypatch.setattr(
        "src.task_selector.check_schedule_feasibility", fake_schedule
    )

    result = select_feasible_tasks(
        campus_map, make_request([option_a, mandatory, option_b])
    )

    assert result.is_feasible is False
    assert result.kept_tasks == (mandatory,)
    assert result.dropped_tasks == (option_a, option_b)
    assert result.route_plan is pure_mandatory["route_plan"]
    assert result.schedule_result is pure_mandatory["schedule_result"]
    assert "必须任务不会被删除" in result.message
