"""卫津路宿舍批次：本地静态地图与 coverage 审计回归。"""
import json
from pathlib import Path

from src.p3_map_loader import load_campus_map_data
from src.p3_location_resolver import build_location_resolver_prompt
from src.p3_map_schema import RouteStatus, TransportMode
from src.p3_route_provider import plan_route


ROOT = Path(__file__).parent.parent
MAP_PATH = ROOT / "data" / "weijinlu_map.json"
COVERAGE_PATH = ROOT / "docs" / "weijinlu_map_coverage.md"

TARGET_DORMS = (
    "一斋", "二斋", "三斋", "四斋", "五斋",
    "23斋", "24斋", "25斋", "26斋", "28斋", "29斋", "30斋", "31斋",
    "32斋", "33斋", "34斋", "35斋", "36斋", "37斋", "38斋", "39斋",
    "40斋", "41斋", "43斋", "44斋", "45斋", "46斋", "47斋", "48斋",
    "49斋", "50斋", "51斋", "52斋", "53斋", "57斋", "鹏翔学生公寓", "友园",
)


def test_dorm_coverage_inventory_has_no_silent_omission():
    text = COVERAGE_PATH.read_text(encoding="utf-8")
    final_table = text.split("## 宿舍最终状态（本轮收口）", 1)[1]
    for name in TARGET_DORMS:
        assert "| {} | DONE |".format(name) in final_table


def test_verified_dorm_nodes_are_static_resolvable_and_provenanced():
    payload = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    nodes = {node["id"]: node for node in payload["nodes"]}
    expected = {
        "weijinlu_dorm_3": ("三斋", "3斋"),
        "weijinlu_dorm_43": ("43斋", "四十三斋"),
        "weijinlu_pengxiang_student_apartment": ("鹏翔学生公寓", "鹏翔公寓"),
        "weijinlu_dorm_28": ("28斋", "二十八斋"),
        "weijinlu_dorm_29": ("29斋", "二十九斋"),
        "weijinlu_dorm_30": ("30斋", "三十斋"),
        "weijinlu_dorm_33": ("33斋", "三十三斋"),
        "weijinlu_dorm_34": ("34斋", "三十四斋"),
        "weijinlu_dorm_44": ("44斋", "四十四斋"),
    }
    for node_id, (name, alias) in expected.items():
        node = nodes[node_id]
        assert node["name"] == name
        assert alias in node["aliases"]
        assert node["category"] == "dormitory"
        assert node["source"]
        assert node["latitude"] is not None and node["longitude"] is not None

    approximate_provenance = {
        "weijinlu_dorm_28": "tju_official_map_approximate",
        "weijinlu_dorm_29": "tju_official_map_approximate",
        "weijinlu_dorm_30": "tju_official_map_approximate",
        "weijinlu_dorm_33": "tju_official_map_approximate",
        "weijinlu_dorm_34": "tju_official_map_approximate",
        "weijinlu_dorm_43": "amap_related_poi_anchor",
        "weijinlu_dorm_44": "tju_official_map_approximate",
    }
    for node_id, provenance in approximate_provenance.items():
        assert nodes[node_id]["precision"] == "approximate"
        assert nodes[node_id]["provenance"] == provenance

    campus_map = load_campus_map_data(MAP_PATH)
    assert campus_map.resolve_node_id("3斋") == "weijinlu_dorm_3"
    assert campus_map.resolve_node_id("四十三斋") == "weijinlu_dorm_43"
    assert campus_map.resolve_node_id("鹏翔公寓") == "weijinlu_pengxiang_student_apartment"
    assert campus_map.resolve_node_id("三十四斋") == "weijinlu_dorm_34"
    assert campus_map.resolve_node_id("四十四斋") == "weijinlu_dorm_44"
    node_34 = next(node for node in campus_map.nodes if node.id == "weijinlu_dorm_34")
    assert node_34.precision == "approximate"
    assert node_34.provenance == "tju_official_map_approximate"


def test_done_dorms_connect_to_existing_weijinlu_graph_by_verified_walk_routes():
    campus_map = load_campus_map_data(MAP_PATH)
    for node_id, expected_distance in (
        ("weijinlu_dorm_3", 855),
        ("weijinlu_pengxiang_student_apartment", 745),
    ):
        route = plan_route(campus_map, node_id, "weijinlu_chunshui_library", TransportMode.WALK)
        assert route.status is RouteStatus.COMPUTED
        # The final four-metre verified entrance edge is now explicit: the
        # old route ended at 9教's access, while the shared skeleton ends
        # at its roadside junction and then enters the building.
        assert route.total_distance_m == expected_distance + 154


def test_north_dorm_approximate_anchors_have_a_shared_measured_walk_skeleton():
    campus_map = load_campus_map_data(MAP_PATH)
    expected_to_chunshui = {
        "weijinlu_dorm_34": 1181,
        "weijinlu_dorm_43": 1088,
        "weijinlu_dorm_44": 996,
        "weijinlu_dorm_33": 904,
        "weijinlu_dorm_28": 811,
        "weijinlu_dorm_29": 862,
        # Dijkstra chooses the shared 30→29→9 skeleton (801m) over the
        # independently measured 30→9 edge (802m), a one-metre rounding gap.
        "weijinlu_dorm_30": 955,
    }
    for node_id, distance in expected_to_chunshui.items():
        route = plan_route(campus_map, node_id, "weijinlu_chunshui_library", TransportMode.WALK)
        assert route.status is RouteStatus.COMPUTED
        assert route.total_distance_m == distance


def test_all_dorm_destinations_resolve_and_have_walk_and_bike_connectivity():
    campus_map = load_campus_map_data(MAP_PATH)
    for name in TARGET_DORMS:
        node_id = campus_map.resolve_node_id(name)
        assert node_id is not None
        for mode in (TransportMode.WALK, TransportMode.BIKE):
            route = plan_route(campus_map, node_id, "weijinlu_building_9", mode)
            assert route.status is RouteStatus.COMPUTED


def test_dorm_node_precision_and_approximate_provenance_are_explicit():
    campus_map = load_campus_map_data(MAP_PATH)
    dorms = [node for node in campus_map.nodes if node.category == "dormitory"]
    assert len(dorms) == len(TARGET_DORMS)
    for node in dorms:
        assert node.precision in ("precise", "approximate")
        if node.precision == "approximate":
            assert node.provenance


def test_dorm_road_access_nodes_are_not_user_resolver_candidates():
    campus_map = load_campus_map_data(MAP_PATH)
    _, prompt = build_location_resolver_prompt(campus_map, "34斋")
    assert "__road_access" not in prompt
    road_nodes = [node for node in campus_map.nodes if node.node_kind == "road_waypoint"]
    assert len(road_nodes) > len([node for node in campus_map.nodes if node.node_kind == "poi"])
    assert all(not node.aliases for node in road_nodes)


def test_step_derived_shared_backbone_is_used_by_north_west_and_southeast_routes():
    campus_map = load_campus_map_data(MAP_PATH)
    north_34 = plan_route(campus_map, "34斋", "9教", TransportMode.WALK)
    north_43 = plan_route(campus_map, "43斋", "9教", TransportMode.WALK)
    shared_north = "weijinlu_junction_anshanxidao_taile"
    assert shared_north in north_34.path and shared_north in north_43.path
    west = plan_route(campus_map, "鹏翔公寓", "9教", TransportMode.WALK)
    southeast = plan_route(campus_map, "友园", "9教", TransportMode.WALK)
    assert "weijinlu_road_mingde_west" in west.path
    assert "weijinlu_road_taile_southeast" in southeast.path


def test_dorm_pois_do_not_appear_as_transit_nodes_in_dorm_to_teaching_routes():
    campus_map = load_campus_map_data(MAP_PATH)
    poi_ids = {node.id for node in campus_map.nodes if node.node_kind == "poi"}
    for name in TARGET_DORMS:
        route = plan_route(campus_map, name, "weijinlu_building_9", TransportMode.WALK)
        assert route.status is RouteStatus.COMPUTED
        assert not set(route.path[1:-1]).intersection(poi_ids)
