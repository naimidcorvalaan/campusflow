from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from src.campus_map import CampusMap, load_campus_map
from src.shortest_path import (
    ShortestPathError,
    ShortestPathResult,
    find_shortest_path,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_campus_map.json"
BEIYANGYUAN_PATH = Path(__file__).resolve().parents[1] / "data" / "beiyangyuan_locations.json"


@pytest.fixture
def campus_map():
    return load_campus_map(FIXTURE_PATH)


def test_finds_shortest_path_with_correct_endpoints_and_minutes(campus_map):
    result = find_shortest_path(campus_map, "dormitory", "teaching_hall")

    assert result == ShortestPathResult(
        start_id="dormitory",
        destination_id="teaching_hall",
        path=("dormitory", "canteen", "teaching_hall"),
        total_minutes=13,
    )


def test_selects_multi_edge_route_when_it_is_shorter_than_direct_edge():
    campus_map = CampusMap.from_dict(
        {
            "metadata": {"campus": "算法测试地图", "data_status": "synthetic_test_data"},
            "locations": [
                {"id": "a", "name": "地点A", "aliases": [], "category": "test"},
                {"id": "b", "name": "地点B", "aliases": [], "category": "test"},
                {"id": "c", "name": "地点C", "aliases": [], "category": "test"},
            ],
            "edges": [
                {"source": "a", "target": "c", "walk_minutes": 10},
                {"source": "a", "target": "b", "walk_minutes": 3},
                {"source": "b", "target": "c", "walk_minutes": 4},
            ],
        }
    )

    result = find_shortest_path(campus_map, "a", "c")

    assert result.path == ("a", "b", "c")
    assert result.total_minutes == 7
    assert result.total_minutes < 10


def test_undirected_edges_support_reverse_query(campus_map):
    result = find_shortest_path(campus_map, "teaching_hall", "dormitory")

    assert result.path == ("teaching_hall", "canteen", "dormitory")
    assert result.total_minutes == 13


@pytest.mark.parametrize(
    ("start", "destination", "expected_start", "expected_destination"),
    [
        ("dormitory", "gate", "dormitory", "gate"),
        ("测试宿舍", "测试校门", "dormitory", "gate"),
        ("宿舍", "校门", "dormitory", "gate"),
        ("  dormitory  ", "  gate  ", "dormitory", "gate"),
        ("DORMITORY", "GATE", "dormitory", "gate"),
    ],
)
def test_resolves_ids_names_aliases_whitespace_and_case(
    campus_map, start, destination, expected_start, expected_destination
):
    result = find_shortest_path(campus_map, start, destination)

    assert result.start_id == expected_start
    assert result.destination_id == expected_destination
    assert result.path[0] == expected_start
    assert result.path[-1] == expected_destination


def test_same_start_and_destination_returns_zero_length_route(campus_map):
    result = find_shortest_path(campus_map, "library", "图书馆")

    assert result.path == ("library",)
    assert result.total_minutes == 0


@pytest.mark.parametrize(
    ("start", "destination", "message"),
    [
        ("未知起点", "gate", "无法识别起点"),
        ("dormitory", "未知终点", "无法识别终点"),
        (None, "gate", "起点必须是字符串"),
        ("dormitory", 123, "终点必须是字符串"),
    ],
)
def test_invalid_query_input_raises_shortest_path_error(
    campus_map, start, destination, message
):
    with pytest.raises(ShortestPathError, match=message):
        find_shortest_path(campus_map, start, destination)


def test_locations_only_map_cannot_be_used_for_routing():
    campus_map = load_campus_map(BEIYANGYUAN_PATH)

    with pytest.raises(
        ShortestPathError, match="当前地图缺少可用步行边，无法规划路线"
    ):
        find_shortest_path(campus_map, "东门", "图书馆")


def test_result_is_frozen_and_path_is_tuple(campus_map):
    result = find_shortest_path(campus_map, "dormitory", "gate")

    assert isinstance(result.path, tuple)
    with pytest.raises(FrozenInstanceError):
        result.total_minutes = 999
    with pytest.raises(AttributeError):
        result.path.append("other")


def test_algorithm_does_not_modify_map(campus_map):
    original_locations = tuple(campus_map.locations)
    original_edges = tuple(campus_map.edges)

    find_shortest_path(campus_map, "dormitory", "gate")

    assert tuple(campus_map.locations) == original_locations
    assert tuple(campus_map.edges) == original_edges


def test_repeated_calls_return_same_result(campus_map):
    first = find_shortest_path(campus_map, "dormitory", "gate")
    second = find_shortest_path(campus_map, "dormitory", "gate")

    assert first == second
