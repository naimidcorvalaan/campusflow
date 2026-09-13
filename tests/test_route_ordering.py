from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from src.campus_map import load_campus_map
from src.route_ordering import (
    RouteOrderingError,
    RouteOrderingResult,
    find_optimal_route,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_campus_map.json"
BEIYANGYUAN_PATH = Path(__file__).resolve().parents[1] / "data" / "beiyangyuan_locations.json"


@pytest.fixture
def campus_map():
    return load_campus_map(FIXTURE_PATH)


def test_single_task_route(campus_map):
    result = find_optimal_route(
        campus_map, "dormitory", "teaching_hall", ["canteen"]
    )

    assert result.visit_order == ("dormitory", "canteen", "teaching_hall")
    assert tuple(segment.path for segment in result.segments) == (
        ("dormitory", "canteen"),
        ("canteen", "teaching_hall"),
    )
    assert result.total_minutes == 13


def test_multiple_tasks_choose_shortest_order(campus_map):
    result = find_optimal_route(
        campus_map, "dormitory", "gate", ["library", "canteen"]
    )

    assert result.visit_order == ("dormitory", "canteen", "library", "gate")
    assert result.total_minutes == 32


def test_ids_names_and_aliases_all_work(campus_map):
    result = find_optimal_route(
        campus_map,
        "测试宿舍",
        "gate",
        ["食堂", "library"],
    )

    assert result.visit_order == ("dormitory", "canteen", "library", "gate")


@pytest.mark.parametrize(
    ("current_location", "destination", "message"),
    [
        ("未知起点", "gate", "无法识别起点"),
        ("dormitory", "未知终点", "无法识别终点"),
    ],
)
def test_unknown_start_or_destination_raises_error(
    campus_map, current_location, destination, message
):
    with pytest.raises(RouteOrderingError, match=message):
        find_optimal_route(campus_map, current_location, destination, [])


def test_unknown_task_location_raises_error(campus_map):
    with pytest.raises(RouteOrderingError, match="无法识别第2个任务地点"):
        find_optimal_route(
            campus_map, "dormitory", "gate", ["library", "未知任务地点"]
        )


def test_locations_only_map_rejects_ordering():
    campus_map = load_campus_map(BEIYANGYUAN_PATH)

    with pytest.raises(RouteOrderingError, match="缺少可用步行边"):
        find_optimal_route(campus_map, "东门", "图书馆", [])


def test_empty_tasks_degrades_to_direct_shortest_path(campus_map):
    result = find_optimal_route(campus_map, "dormitory", "gate", [])

    assert result.visit_order == ("dormitory", "gate")
    assert len(result.segments) == 1
    assert result.segments[0].path == ("dormitory", "library", "gate")
    assert result.total_minutes == 20


def test_duplicate_tasks_are_rejected_after_location_resolution(campus_map):
    with pytest.raises(RouteOrderingError, match="任务地点不能重复"):
        find_optimal_route(campus_map, "dormitory", "gate", ["library", "图书馆"])


def test_result_and_nested_sequences_are_immutable(campus_map):
    result = find_optimal_route(campus_map, "dormitory", "gate", ["canteen"])

    assert isinstance(result, RouteOrderingResult)
    assert isinstance(result.visit_order, tuple)
    assert isinstance(result.segments, tuple)
    assert isinstance(result.segments[0].path, tuple)
    with pytest.raises(FrozenInstanceError):
        result.total_minutes = 999
    with pytest.raises(AttributeError):
        result.visit_order.append("other")


def test_does_not_modify_map_or_original_task_list(campus_map):
    task_locations = ["library", "canteen"]
    original_tasks = list(task_locations)
    original_locations = tuple(campus_map.locations)
    original_edges = tuple(campus_map.edges)

    find_optimal_route(campus_map, "dormitory", "gate", task_locations)

    assert task_locations == original_tasks
    assert tuple(campus_map.locations) == original_locations
    assert tuple(campus_map.edges) == original_edges


def test_more_than_eight_tasks_are_rejected(campus_map):
    with pytest.raises(RouteOrderingError, match="最多支持8个任务"):
        find_optimal_route(campus_map, "dormitory", "gate", ["library"] * 9)
