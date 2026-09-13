"""P3a 地图数据模型测试。"""
import pytest

from src.p3_map_schema import (
    MapDataProvenance,
    RouteResult,
    RouteStatus,
    TransportMode,
    parse_transport_mode,
)


def test_transport_mode_values():
    assert TransportMode.WALK.value == "walk"
    assert TransportMode.BIKE.value == "bike"


def test_parse_transport_mode():
    assert parse_transport_mode("walk") is TransportMode.WALK
    assert parse_transport_mode("BIKE") is TransportMode.BIKE
    assert parse_transport_mode("  walk  ") is TransportMode.WALK
    assert parse_transport_mode(TransportMode.BIKE) is TransportMode.BIKE


def test_parse_transport_mode_rejects_unknown():
    for bad in ("drive", "car", 1, None, True):
        with pytest.raises(ValueError):
            parse_transport_mode(bad)


def test_provenance_values():
    assert MapDataProvenance.REAL_MAP.value == "real_map"
    assert MapDataProvenance.SYNTHETIC_TEST.value == "synthetic_test"
    assert MapDataProvenance.UNAVAILABLE.value == "unavailable"


def test_route_result_shape():
    result = RouteResult(
        status=RouteStatus.COMPUTED,
        origin="gate",
        destination="main",
        mode=TransportMode.WALK,
        total_distance_m=800,
        path=("gate", "main"),
        provenance=MapDataProvenance.SYNTHETIC_TEST,
    )
    assert result.total_distance_m == 800
    assert result.path == ("gate", "main")
    assert result.note is None
