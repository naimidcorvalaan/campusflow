from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from src.campus_map import load_campus_map
from src.models import PlanningRequest, Task
from src.route_plan import build_route_plan
from src.schedule_checker import (
    ScheduleCheckError,
    ScheduleCheckResult,
    ScheduledTask,
    check_schedule_feasibility,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_campus_map.json"


@pytest.fixture
def campus_map():
    return load_campus_map(FIXTURE_PATH)


def make_task(location, duration, deadline=None):
    return Task(
        location=location,
        description=f"在{location}办事",
        is_mandatory=True,
        estimated_duration_minutes=duration,
        deadline=deadline,
    )


def make_request(tasks, current_time="09:00", destination="gate"):
    return PlanningRequest(
        current_location="dormitory",
        destination=destination,
        current_time=current_time,
        tasks=tasks,
    )


def build_and_check(campus_map, request):
    route_plan = build_route_plan(campus_map, request)
    return route_plan, check_schedule_feasibility(route_plan, request)


def test_task_without_deadline_is_feasible(campus_map):
    task = make_task("canteen", 15)
    _, result = build_and_check(campus_map, make_request([task]))

    assert result.is_feasible is True
    assert result.missed_deadlines == ()
    assert result.scheduled_tasks[0].deadline is None
    assert result.scheduled_tasks[0].is_deadline_met is None


def test_deadline_equal_to_finish_time_is_feasible(campus_map):
    task = make_task("canteen", 14, deadline="09:20")
    request = make_request([task], destination="teaching_hall")
    _, result = build_and_check(campus_map, request)

    scheduled = result.scheduled_tasks[0]
    assert scheduled.arrival_time == "09:06"
    assert scheduled.finish_time == "09:20"
    assert scheduled.is_deadline_met is True
    assert result.is_feasible is True


def test_task_finishing_after_deadline_is_infeasible(campus_map):
    task = make_task("canteen", 15, deadline="09:20")
    _, result = build_and_check(campus_map, make_request([task]))

    assert result.is_feasible is False
    assert result.scheduled_tasks[0].finish_time == "09:21"
    assert result.missed_deadlines == (result.scheduled_tasks[0],)


def test_only_late_task_is_in_missed_deadlines(campus_map):
    library_task = make_task("library", 20, deadline="10:10")
    canteen_task = make_task("canteen", 30, deadline="09:35")
    request = make_request([library_task, canteen_task])
    _, result = build_and_check(campus_map, request)

    assert tuple(item.location_id for item in result.scheduled_tasks) == (
        "canteen",
        "library",
    )
    assert result.scheduled_tasks[0].is_deadline_met is False
    assert result.scheduled_tasks[1].is_deadline_met is True
    assert result.missed_deadlines == (result.scheduled_tasks[0],)


def test_none_deadline_is_not_checked_when_another_task_is_late(campus_map):
    library_task = make_task("library", 20, deadline=None)
    canteen_task = make_task("canteen", 30, deadline="09:35")
    _, result = build_and_check(
        campus_map, make_request([library_task, canteen_task])
    )

    assert result.scheduled_tasks[1].deadline is None
    assert result.scheduled_tasks[1].is_deadline_met is None
    assert result.missed_deadlines == (result.scheduled_tasks[0],)


@pytest.mark.parametrize("current_time", ["9:00", "09:60", "24:00", None])
def test_invalid_current_time_returns_chinese_error(campus_map, current_time):
    request = make_request([], current_time=current_time)
    route_plan = build_route_plan(campus_map, request)

    with pytest.raises(ScheduleCheckError, match="current_time.*HH:MM"):
        check_schedule_feasibility(route_plan, request)


@pytest.mark.parametrize("deadline", ["9:30", "12:60", "24:00", 930])
def test_invalid_deadline_returns_chinese_error(campus_map, deadline):
    task = make_task("canteen", 10, deadline=deadline)
    request = make_request([task])
    route_plan = build_route_plan(campus_map, request)

    with pytest.raises(ScheduleCheckError, match="deadline.*HH:MM"):
        check_schedule_feasibility(route_plan, request)


@pytest.mark.parametrize(
    ("current_time", "expected_finish"),
    [("23:50", "24:10"), ("23:45", "24:05")],
)
def test_finish_time_can_extend_past_midnight(
    campus_map, current_time, expected_finish
):
    request = make_request([], current_time=current_time)
    _, result = build_and_check(campus_map, request)

    assert result.finish_time == expected_finish


def test_mismatched_route_plan_structure_returns_chinese_error(campus_map):
    task = make_task("canteen", 10)
    request = make_request([task])
    route_plan = build_route_plan(campus_map, request)
    broken_plan = replace(
        route_plan,
        visit_order=("dormitory", "library", "gate"),
    )

    with pytest.raises(ScheduleCheckError, match="与 visit_order 对不上"):
        check_schedule_feasibility(broken_plan, request)


def test_swapped_route_plan_tasks_are_rejected(campus_map):
    library_task = make_task("library", 20, deadline="10:30")
    canteen_task = make_task("canteen", 30, deadline="10:00")
    request = make_request([library_task, canteen_task])
    route_plan = build_route_plan(campus_map, request)
    assert route_plan.tasks == (canteen_task, library_task)

    broken_plan = replace(
        route_plan,
        tasks=(library_task, canteen_task),
    )

    with pytest.raises(
        ScheduleCheckError, match="任务地点与访问顺序不一致"
    ):
        check_schedule_feasibility(broken_plan, request)


def test_result_objects_and_sequences_are_immutable(campus_map):
    task = make_task("canteen", 10, deadline="10:00")
    _, result = build_and_check(campus_map, make_request([task]))

    assert isinstance(result, ScheduleCheckResult)
    assert isinstance(result.scheduled_tasks[0], ScheduledTask)
    assert isinstance(result.scheduled_tasks, tuple)
    assert isinstance(result.missed_deadlines, tuple)
    with pytest.raises(FrozenInstanceError):
        result.is_feasible = False
    with pytest.raises(FrozenInstanceError):
        result.scheduled_tasks[0].finish_time = "00:00"


def test_does_not_modify_route_plan_request_or_tasks(campus_map):
    tasks = [
        make_task("library", 20, deadline="10:30"),
        make_task("canteen", 30, deadline=None),
    ]
    request = make_request(tasks)
    route_plan = build_route_plan(campus_map, request)
    original_plan = replace(route_plan)
    original_request = (
        request.current_location,
        request.destination,
        request.current_time,
        tuple(request.tasks),
    )
    original_tasks = tuple(
        (
            task.location,
            task.description,
            task.is_mandatory,
            task.estimated_duration_minutes,
            task.deadline,
        )
        for task in tasks
    )

    check_schedule_feasibility(route_plan, request)

    assert route_plan == original_plan
    assert (
        request.current_location,
        request.destination,
        request.current_time,
        tuple(request.tasks),
    ) == original_request
    assert tuple(
        (
            task.location,
            task.description,
            task.is_mandatory,
            task.estimated_duration_minutes,
            task.deadline,
        )
        for task in tasks
    ) == original_tasks
