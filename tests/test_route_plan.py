from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from src.campus_map import load_campus_map
from src.models import PlanningRequest, Task
from src.route_plan import RoutePlanError, RoutePlanResult, build_route_plan


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_campus_map.json"
BEIYANGYUAN_PATH = Path(__file__).resolve().parents[1] / "data" / "beiyangyuan_locations.json"


@pytest.fixture
def campus_map():
    return load_campus_map(FIXTURE_PATH)


def make_task(location, description, duration):
    return Task(
        location=location,
        description=description,
        is_mandatory=True,
        estimated_duration_minutes=duration,
    )


def make_request(tasks):
    return PlanningRequest(
        current_location="dormitory",
        destination="gate",
        current_time="09:00",
        tasks=tasks,
    )


def test_single_task_total_is_walking_plus_stay(campus_map):
    task = make_task("canteen", "吃饭", 15)
    request = PlanningRequest(
        current_location="dormitory",
        destination="teaching_hall",
        current_time="09:00",
        tasks=[task],
    )

    result = build_route_plan(campus_map, request)

    assert result.walking_minutes == 13
    assert result.stay_minutes == 15
    assert result.total_minutes == 28
    assert result.total_minutes == result.walking_minutes + result.stay_minutes


def test_multiple_tasks_follow_optimal_visit_order(campus_map):
    library_task = make_task("library", "借书", 20)
    canteen_task = make_task("食堂", "吃饭", 30)
    request = make_request([library_task, canteen_task])

    result = build_route_plan(campus_map, request)

    assert result.visit_order == ("dormitory", "canteen", "library", "gate")
    assert result.tasks == (canteen_task, library_task)
    assert result.walking_minutes == 32
    assert result.stay_minutes == 50
    assert result.total_minutes == 82


def test_empty_tasks_degrades_to_direct_route(campus_map):
    result = build_route_plan(campus_map, make_request([]))

    assert result.visit_order == ("dormitory", "gate")
    assert result.tasks == ()
    assert result.stay_minutes == 0
    assert result.walking_minutes == 20
    assert result.total_minutes == 20


def test_multiple_tasks_at_same_resolved_location_are_rejected(campus_map):
    request = make_request(
        [
            make_task("library", "借书", 10),
            make_task("图书馆", "还书", 5),
        ]
    )

    with pytest.raises(RoutePlanError, match="任务地点不能重复"):
        build_route_plan(campus_map, request)


def test_unknown_task_location_returns_chinese_error(campus_map):
    request = make_request([make_task("未知任务地点", "办事", 10)])

    with pytest.raises(RoutePlanError, match="无法识别第1个任务地点"):
        build_route_plan(campus_map, request)


def test_locations_only_map_is_rejected():
    campus_map = load_campus_map(BEIYANGYUAN_PATH)
    request = PlanningRequest(
        current_location="东门",
        destination="图书馆",
        current_time="09:00",
        tasks=[],
    )

    with pytest.raises(RoutePlanError, match="缺少可用步行边"):
        build_route_plan(campus_map, request)


def test_result_and_sequences_are_immutable(campus_map):
    result = build_route_plan(
        campus_map, make_request([make_task("canteen", "吃饭", 15)])
    )

    assert isinstance(result, RoutePlanResult)
    assert isinstance(result.visit_order, tuple)
    assert isinstance(result.segments, tuple)
    assert isinstance(result.tasks, tuple)
    with pytest.raises(FrozenInstanceError):
        result.total_minutes = 999
    with pytest.raises(AttributeError):
        result.tasks.append(make_task("library", "借书", 10))


def test_does_not_modify_request_tasks_or_map(campus_map):
    tasks = [
        make_task("library", "借书", 20),
        make_task("canteen", "吃饭", 30),
    ]
    request = make_request(tasks)
    original_request_values = (
        request.current_location,
        request.destination,
        request.current_time,
        tuple(request.tasks),
    )
    original_task_values = tuple(
        (
            task.location,
            task.description,
            task.is_mandatory,
            task.estimated_duration_minutes,
            task.deadline,
        )
        for task in tasks
    )
    original_locations = tuple(campus_map.locations)
    original_edges = tuple(campus_map.edges)

    build_route_plan(campus_map, request)

    assert (
        request.current_location,
        request.destination,
        request.current_time,
        tuple(request.tasks),
    ) == original_request_values
    assert tuple(
        (
            task.location,
            task.description,
            task.is_mandatory,
            task.estimated_duration_minutes,
            task.deadline,
        )
        for task in tasks
    ) == original_task_values
    assert tuple(campus_map.locations) == original_locations
    assert tuple(campus_map.edges) == original_edges
