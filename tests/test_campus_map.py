import json
from pathlib import Path

import pytest

from src.campus_map import CampusMap, load_campus_map


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_campus_map.json"


def test_load_synthetic_map_successfully():
    campus_map = load_campus_map(FIXTURE_PATH)

    assert campus_map.campus == "测试校区"
    assert campus_map.data_status == "synthetic_test_data"
    assert len(campus_map.locations) == 5
    assert len(campus_map.edges) == 5
    assert campus_map.is_routable is True


def test_find_location_by_name_and_alias():
    campus_map = load_campus_map(FIXTURE_PATH)

    assert campus_map.resolve_location_id("测试图书馆") == "library"
    assert campus_map.resolve_location_id("library") == "library"
    assert campus_map.resolve_location_id("食堂") == "canteen"


def test_unknown_location_returns_none():
    campus_map = load_campus_map(FIXTURE_PATH)

    assert campus_map.resolve_location_id("不存在的地点") is None
    assert campus_map.resolve_location_id("   ") is None


def test_duplicate_location_id_is_rejected(tmp_path):
    payload = {
        "metadata": {"campus": "测试", "data_status": "synthetic_test_data"},
        "locations": [
            {"id": "dup", "name": "地点A", "aliases": ["A"], "category": "teaching"},
            {"id": "dup", "name": "地点B", "aliases": ["B"], "category": "library"},
        ],
        "edges": [],
    }
    file_path = tmp_path / "duplicate.json"
    file_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="重复"):
        load_campus_map(file_path)


def test_empty_name_is_rejected(tmp_path):
    payload = {
        "metadata": {"campus": "测试", "data_status": "synthetic_test_data"},
        "locations": [
            {"id": "loc1", "name": "", "aliases": [], "category": "gate"},
        ],
        "edges": [],
    }
    file_path = tmp_path / "empty_name.json"
    file_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="name"):
        load_campus_map(file_path)


def test_aliases_type_error_is_rejected(tmp_path):
    payload = {
        "metadata": {"campus": "测试", "data_status": "synthetic_test_data"},
        "locations": [
            {"id": "loc1", "name": "地点A", "aliases": "bad", "category": "gate"},
        ],
        "edges": [],
    }
    file_path = tmp_path / "alias_error.json"
    file_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="aliases"):
        load_campus_map(file_path)


def test_edge_reference_unknown_location_is_rejected(tmp_path):
    payload = {
        "metadata": {"campus": "测试", "data_status": "synthetic_test_data"},
        "locations": [
            {"id": "loc1", "name": "地点A", "aliases": [], "category": "gate"},
            {"id": "loc2", "name": "地点B", "aliases": [], "category": "library"},
        ],
        "edges": [{"source": "loc1", "target": "ghost", "walk_minutes": 5}],
    }
    file_path = tmp_path / "unknown_edge.json"
    file_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="未知地点"):
        load_campus_map(file_path)


def test_self_loop_is_rejected(tmp_path):
    payload = {
        "metadata": {"campus": "测试", "data_status": "synthetic_test_data"},
        "locations": [
            {"id": "loc1", "name": "地点A", "aliases": [], "category": "gate"},
        ],
        "edges": [{"source": "loc1", "target": "loc1", "walk_minutes": 5}],
    }
    file_path = tmp_path / "self_loop.json"
    file_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="自环"):
        load_campus_map(file_path)


@pytest.mark.parametrize("bad_minutes", [0, -1, 1.5, "5", True])
def test_walk_minutes_invalid_values_are_rejected(tmp_path, bad_minutes):
    payload = {
        "metadata": {"campus": "测试", "data_status": "synthetic_test_data"},
        "locations": [
            {"id": "loc1", "name": "地点A", "aliases": [], "category": "gate"},
            {"id": "loc2", "name": "地点B", "aliases": [], "category": "library"},
        ],
        "edges": [{"source": "loc1", "target": "loc2", "walk_minutes": bad_minutes}],
    }
    file_path = tmp_path / "bad_minutes.json"
    file_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="walk_minutes"):
        load_campus_map(file_path)


def test_duplicate_undirected_edge_is_rejected(tmp_path):
    payload = {
        "metadata": {"campus": "测试", "data_status": "synthetic_test_data"},
        "locations": [
            {"id": "loc1", "name": "地点A", "aliases": [], "category": "gate"},
            {"id": "loc2", "name": "地点B", "aliases": [], "category": "library"},
        ],
        "edges": [
            {"source": "loc1", "target": "loc2", "walk_minutes": 5},
            {"source": "loc2", "target": "loc1", "walk_minutes": 5},
        ],
    }
    file_path = tmp_path / "duplicate_edge.json"
    file_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="重复"):
        load_campus_map(file_path)


def test_disconnected_map_is_rejected(tmp_path):
    payload = {
        "metadata": {"campus": "测试", "data_status": "synthetic_test_data"},
        "locations": [
            {"id": "loc1", "name": "地点A", "aliases": [], "category": "gate"},
            {"id": "loc2", "name": "地点B", "aliases": [], "category": "library"},
            {"id": "loc3", "name": "地点C", "aliases": [], "category": "teaching"},
        ],
        "edges": [{"source": "loc1", "target": "loc2", "walk_minutes": 5}],
    }
    file_path = tmp_path / "disconnected.json"
    file_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="连通"):
        load_campus_map(file_path)


def test_invalid_json_file_is_rejected(tmp_path):
    file_path = tmp_path / "invalid.json"
    file_path.write_text("{not valid json}", encoding="utf-8")

    with pytest.raises(ValueError, match="JSON格式错误"):
        load_campus_map(file_path)


def test_locations_only_file_loads_without_edges():
    campus_map = load_campus_map(Path(__file__).resolve().parents[1] / "data" / "beiyangyuan_locations.json")

    assert campus_map.campus == "天津大学北洋园校区"
    assert campus_map.data_status == "locations_only"
    assert campus_map.metadata["walking_times_verified"] is False
    assert len(campus_map.locations) == 10
    assert campus_map.edges == []
    assert campus_map.is_routable is False


def make_location(location_id, name, aliases=None, category="teaching"):
    return {
        "id": location_id,
        "name": name,
        "aliases": aliases or [],
        "category": category,
    }


def make_payload(locations, data_status="locations_only", edges=None):
    return {
        "metadata": {"campus": "测试校区", "data_status": data_status},
        "locations": locations,
        "edges": edges or [],
    }


def test_empty_locations_is_rejected():
    with pytest.raises(ValueError, match="locations 不能为空"):
        CampusMap.from_dict(make_payload([]))


@pytest.mark.parametrize("alias", ["", "   "])
def test_empty_or_whitespace_alias_is_rejected(alias):
    payload = make_payload([make_location("loc1", "地点A", [alias])])

    with pytest.raises(ValueError, match="alias 不能为空"):
        CampusMap.from_dict(payload)


@pytest.mark.parametrize(
    "aliases",
    [
        ["Library", " library "],
        ["图书馆", " 图书馆 "],
    ],
)
def test_duplicate_alias_within_location_is_rejected(aliases):
    payload = make_payload([make_location("loc1", "地点A", aliases)])

    with pytest.raises(ValueError, match="alias 重复"):
        CampusMap.from_dict(payload)


@pytest.mark.parametrize("alias", [" LOC1 ", " 地点A "])
def test_alias_matching_own_id_or_name_is_rejected(alias):
    payload = make_payload([make_location("loc1", "地点A", [alias])])

    with pytest.raises(ValueError, match="自身 id 或 name"):
        CampusMap.from_dict(payload)


@pytest.mark.parametrize(
    "locations",
    [
        [make_location("a", "同名地点"), make_location("b", " 同名地点 ")],
        [make_location("a", "地点A", ["shared"]), make_location("b", "地点B", [" SHARED "])],
        [make_location("a", "地点A", ["地点B"]), make_location("b", "地点B")],
        [make_location("a", "地点A", [" B "]), make_location("b", "地点B")],
    ],
)
def test_cross_location_identifier_conflict_is_rejected(locations):
    with pytest.raises(ValueError, match="解析标识冲突"):
        CampusMap.from_dict(make_payload(locations))


def test_location_values_are_stripped_and_aliases_are_immutable_tuple():
    payload = make_payload(
        [make_location(" loc1 ", " 地点A ", [" 别名A ", " EnglishAlias "], " teaching ")]
    )

    location = CampusMap.from_dict(payload).locations[0]

    assert location.id == "loc1"
    assert location.name == "地点A"
    assert location.category == "teaching"
    assert location.aliases == ("别名A", "EnglishAlias")
    assert isinstance(location.aliases, tuple)
    with pytest.raises(AttributeError):
        location.aliases.append("新别名")


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("  LOC1  ", "loc1"),
        ("  ENGLISH NAME  ", "loc1"),
        ("  LIBRARY  ", "loc1"),
    ],
)
def test_resolve_location_normalizes_whitespace_and_case(query, expected):
    payload = make_payload(
        [make_location("loc1", "English Name", ["Library"])]
    )
    campus_map = CampusMap.from_dict(payload)

    assert campus_map.resolve_location_id(query) == expected


def test_locations_only_with_edge_is_rejected():
    payload = make_payload(
        [make_location("a", "地点A"), make_location("b", "地点B")],
        edges=[{"source": "a", "target": "b", "walk_minutes": 5}],
    )

    with pytest.raises(ValueError, match="locations_only.*不能包含边"):
        CampusMap.from_dict(payload)
