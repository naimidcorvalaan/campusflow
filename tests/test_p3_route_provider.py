"""P3a 寻路测试：别名解析、walk/bike 模式过滤、Dijkstra、不可达、不可用。"""
from pathlib import Path

import pytest

from src.p3_map_loader import load_campus_map_data
from src.p3_map_schema import MapDataProvenance, RouteStatus, TransportMode
from src.p3_route_provider import plan_route

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "p3_synthetic_map.json"
REAL_DATA_PATH = Path(__file__).parent.parent / "data" / "beiyangyuan_locations.json"


@pytest.fixture(scope="module")
def campus_map():
    return load_campus_map_data(FIXTURE_PATH)


def test_walk_shortest_path(campus_map):
    result = plan_route(campus_map, "东门", "图书馆", "walk")
    assert result.status is RouteStatus.COMPUTED
    assert result.origin == "gate"
    assert result.destination == "library"
    assert result.total_distance_m == 1400
    assert result.path == ("gate", "main", "library")
    assert result.mode is TransportMode.WALK
    assert result.provenance is MapDataProvenance.SYNTHETIC_TEST


def test_bike_uses_direct_edge(campus_map):
    result = plan_route(campus_map, "gate", "gym", "bike")
    assert result.status is RouteStatus.COMPUTED
    assert result.total_distance_m == 2200
    assert result.path == ("gate", "gym")


def test_walk_cannot_use_bike_only_edge(campus_map):
    result = plan_route(campus_map, "gate", "gym", "walk")
    assert result.status is RouteStatus.COMPUTED
    assert result.total_distance_m == 2000
    assert result.path == ("gate", "main", "canteen", "gym")


def test_bike_cannot_use_walk_only_edge(campus_map):
    result = plan_route(campus_map, "gate", "canteen", "bike")
    assert result.status is RouteStatus.UNREACHABLE
    assert result.total_distance_m is None
    assert result.path == ()


def test_directed_edge_not_reversed(campus_map):
    # gate->gym 是单向 2200；反向走 gym->library->main->gate = 2300
    result = plan_route(campus_map, "gym", "gate", "bike")
    assert result.status is RouteStatus.COMPUTED
    assert result.total_distance_m == 2300
    assert result.path == ("gym", "library", "main", "gate")


def test_disconnected_node_unreachable(campus_map):
    result = plan_route(campus_map, "gate", "dorm", "walk")
    assert result.status is RouteStatus.UNREACHABLE
    assert result.total_distance_m is None
    assert result.path == ()


def test_unknown_location_unavailable(campus_map):
    result = plan_route(campus_map, "不存在的地方", "gate", "walk")
    assert result.status is RouteStatus.UNAVAILABLE
    assert result.total_distance_m is None
    assert result.provenance is MapDataProvenance.UNAVAILABLE


def test_same_node_zero_distance(campus_map):
    result = plan_route(campus_map, "gate", "校门", "walk")
    assert result.status is RouteStatus.COMPUTED
    assert result.total_distance_m == 0
    assert result.path == ("gate",)


def test_locations_only_real_file_never_fabricates_route():
    campus_map = load_campus_map_data(REAL_DATA_PATH)
    result = plan_route(campus_map, "东门", "主楼", "walk")
    assert result.status is RouteStatus.UNAVAILABLE
    assert result.total_distance_m is None
    assert result.provenance is MapDataProvenance.UNAVAILABLE
    assert result.note is not None


def test_synthetic_provenance_not_masquerading_as_real(campus_map):
    assert campus_map.provenance is MapDataProvenance.SYNTHETIC_TEST
    for mode in ("walk", "bike"):
        result = plan_route(campus_map, "gate", "library", mode)
        assert result.provenance is MapDataProvenance.SYNTHETIC_TEST


def test_route_provider_rejects_empty_query(campus_map):
    with pytest.raises(ValueError, match="origin"):
        plan_route(campus_map, "", "gate", "walk")
