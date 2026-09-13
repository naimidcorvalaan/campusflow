"""P3a 地图 loader / validator / provenance 测试。"""
import json
from pathlib import Path

import pytest

from src.p3_map_loader import load_campus_map_data, parse_campus_map_data
from src.p3_map_schema import MapDataProvenance

REAL_DATA_PATH = Path(__file__).parent.parent / "data" / "beiyangyuan_locations.json"
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "p3_synthetic_map.json"


def base_payload():
    return {
        "metadata": {"campus": "测试", "provenance": "synthetic_test"},
        "nodes": [
            {"id": "a", "name": "地点A", "aliases": [], "category": "teaching"},
            {"id": "b", "name": "地点B", "aliases": [], "category": "library"},
        ],
    }


def test_real_locations_file_loads_as_unavailable():
    """现有北洋园 locations 文件可加载；无道路边时判定不可用，绝不伪造路线。"""
    campus_map = load_campus_map_data(REAL_DATA_PATH)
    assert campus_map.campus == "天津大学北洋园校区"
    assert len(campus_map.nodes) == 10
    assert campus_map.edges == ()
    assert campus_map.provenance is MapDataProvenance.UNAVAILABLE
    assert any("没有道路边" in note for note in campus_map.notes)


def test_synthetic_fixture_loads():
    campus_map = load_campus_map_data(FIXTURE_PATH)
    assert campus_map.provenance is MapDataProvenance.SYNTHETIC_TEST
    assert len(campus_map.nodes) == 6
    assert len(campus_map.edges) == 7
    assert campus_map.resolve_node_id("东门") == "gate"
    assert campus_map.resolve_node_id("gate") == "gate"


def test_legacy_locations_key_supported():
    payload = {
        "metadata": {"campus": "测试", "data_status": "locations_only"},
        "locations": [
            {"id": "a", "name": "地点A", "aliases": ["A"], "category": "teaching"},
        ],
    }
    campus_map = parse_campus_map_data(payload)
    assert len(campus_map.nodes) == 1
    assert campus_map.provenance is MapDataProvenance.UNAVAILABLE


def test_missing_nodes_rejected():
    with pytest.raises(ValueError, match="nodes/locations"):
        parse_campus_map_data({"metadata": {"campus": "测试"}})


def test_duplicate_node_id_rejected():
    payload = {
        "metadata": {"campus": "测试"},
        "nodes": [
            {"id": "a", "name": "地点A", "aliases": [], "category": "teaching"},
            {"id": "a", "name": "地点B", "aliases": [], "category": "library"},
        ],
    }
    with pytest.raises(ValueError, match="重复"):
        parse_campus_map_data(payload)


def test_alias_conflict_across_nodes_rejected():
    payload = {
        "metadata": {"campus": "测试"},
        "nodes": [
            {"id": "a", "name": "地点A", "aliases": ["图书馆"], "category": "teaching"},
            {"id": "b", "name": "图书馆", "aliases": [], "category": "library"},
        ],
    }
    with pytest.raises(ValueError, match="冲突"):
        parse_campus_map_data(payload)


def test_edge_referencing_unknown_node_rejected():
    payload = base_payload()
    payload["edges"] = [{"from": "a", "to": "missing", "distance_m": 100,
                         "modes": ["walk"], "bidirectional": True}]
    with pytest.raises(ValueError, match="未知地点"):
        parse_campus_map_data(payload)


def test_duplicate_directed_edge_rejected():
    payload = base_payload()
    payload["edges"] = [
        {"from": "a", "to": "b", "distance_m": 100, "modes": ["walk"], "bidirectional": True},
        {"from": "a", "to": "b", "distance_m": 200, "modes": ["walk"], "bidirectional": True},
    ]
    with pytest.raises(ValueError, match="重复的有向边"):
        parse_campus_map_data(payload)


def test_distance_must_be_positive_int_and_not_bool():
    for bad in (0, -1, 1.5, True):
        payload = base_payload()
        payload["edges"] = [{"from": "a", "to": "b", "distance_m": bad,
                             "modes": ["walk"], "bidirectional": True}]
        with pytest.raises(ValueError, match="distance_m"):
            parse_campus_map_data(payload)


def test_modes_empty_rejected():
    payload = base_payload()
    payload["edges"] = [{"from": "a", "to": "b", "distance_m": 100,
                         "modes": [], "bidirectional": True}]
    with pytest.raises(ValueError, match="modes"):
        parse_campus_map_data(payload)


def test_unknown_mode_rejected():
    payload = base_payload()
    payload["edges"] = [{"from": "a", "to": "b", "distance_m": 100,
                         "modes": ["car"], "bidirectional": True}]
    with pytest.raises(ValueError, match="walk 或 bike"):
        parse_campus_map_data(payload)


def test_bidirectional_must_be_bool():
    payload = base_payload()
    payload["edges"] = [{"from": "a", "to": "b", "distance_m": 100,
                         "modes": ["walk"], "bidirectional": 1}]
    with pytest.raises(ValueError, match="bidirectional"):
        parse_campus_map_data(payload)


def test_claimed_real_without_edges_rejected():
    payload = base_payload()
    payload["metadata"]["provenance"] = "real_map"
    with pytest.raises(ValueError, match="真实地图但没有道路边"):
        parse_campus_map_data(payload)


def test_claimed_real_requires_edge_source():
    payload = base_payload()
    payload["metadata"]["provenance"] = "real_map"
    payload["edges"] = [{"from": "a", "to": "b", "distance_m": 100,
                         "modes": ["walk"], "bidirectional": True}]
    with pytest.raises(ValueError, match="source"):
        parse_campus_map_data(payload)


def test_claimed_real_requires_node_source():
    payload = base_payload()
    payload["metadata"]["provenance"] = "real_map"
    payload["edges"] = [{"from": "a", "to": "b", "distance_m": 100,
                         "modes": ["walk"], "bidirectional": True, "source": "实测"}]
    with pytest.raises(ValueError, match="source"):
        parse_campus_map_data(payload)


def test_claimed_real_complete_is_real_map():
    payload = {
        "metadata": {"campus": "真实测试", "provenance": "real_map",
                     "data_status": "routable"},
        "nodes": [
            {"id": "a", "name": "地点A", "aliases": [], "category": "teaching",
             "latitude": 39.1, "longitude": 117.1, "source": "实测"},
            {"id": "b", "name": "地点B", "aliases": [], "category": "library",
             "latitude": 39.2, "longitude": 117.2, "source": "实测"},
        ],
        "edges": [{"from": "a", "to": "b", "distance_m": 300,
                   "modes": ["walk", "bike"], "bidirectional": True, "source": "实测"}],
    }
    campus_map = parse_campus_map_data(payload)
    assert campus_map.provenance is MapDataProvenance.REAL_MAP


def test_missing_provenance_with_edges_is_synthetic_not_real():
    payload = base_payload()
    del payload["metadata"]["provenance"]
    payload["edges"] = [{"from": "a", "to": "b", "distance_m": 100,
                         "modes": ["walk"], "bidirectional": True}]
    campus_map = parse_campus_map_data(payload)
    assert campus_map.provenance is MapDataProvenance.SYNTHETIC_TEST
    assert any("不冒充真实地图" in note for note in campus_map.notes)


def test_invalid_latitude_rejected():
    payload = base_payload()
    payload["nodes"][0]["latitude"] = 91.0
    with pytest.raises(ValueError, match="latitude"):
        parse_campus_map_data(payload)


def test_bool_not_accepted_as_coordinate():
    payload = base_payload()
    payload["nodes"][0]["latitude"] = True
    with pytest.raises(ValueError, match="latitude"):
        parse_campus_map_data(payload)
