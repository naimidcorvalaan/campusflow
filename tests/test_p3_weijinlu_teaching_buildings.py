"""Weijinlu numbered teaching-building inventory and shared-skeleton regression."""
import json
from itertools import combinations
from pathlib import Path

from src.p3_map_loader import load_campus_map_data
from src.p3_location_resolver import build_location_resolver_prompt
from src.p3_map_schema import RouteStatus, TransportMode
from src.p3_route_provider import plan_route


ROOT = Path(__file__).parent.parent
MAP_PATH = ROOT / "data" / "weijinlu_map.json"
COVERAGE_PATH = ROOT / "docs" / "weijinlu_map_coverage.md"

TARGETS = (
    "第一教学楼", "第二教学楼", "第三教学楼", "第四教学楼", "第五教学楼",
    "第六教学楼", "第七教学楼", "第八教学楼", "第九教学楼", "第十教学楼",
    "第十一教学楼", "第十二教学楼", "第十四教学楼", "第十五教学楼",
    "第十六教学楼", "第十七教学楼", "第十八教学楼", "第十九教学楼",
    "第二十教学楼", "第二十一教学楼", "第二十三教学楼", "第二十四教学楼",
    "第二十六教学楼", "第二十八教学楼", "第三十五教学楼",
)


TARGETS = (
    "\u7b2c\u4e00\u6559\u5b66\u697c", "\u7b2c\u4e8c\u6559\u5b66\u697c", "\u7b2c\u4e09\u6559\u5b66\u697c", "\u7b2c\u56db\u6559\u5b66\u697c", "\u7b2c\u4e94\u6559\u5b66\u697c",
    "\u7b2c\u516d\u6559\u5b66\u697c", "\u7b2c\u4e03\u6559\u5b66\u697c", "\u7b2c\u516b\u6559\u5b66\u697c", "\u7b2c\u4e5d\u6559\u5b66\u697c", "\u7b2c\u5341\u6559\u5b66\u697c",
    "\u7b2c\u5341\u4e00\u6559\u5b66\u697c", "\u7b2c\u5341\u4e8c\u6559\u5b66\u697c", "\u7b2c\u5341\u4e09\u6559\u5b66\u697c", "\u7b2c\u5341\u56db\u6559\u5b66\u697c", "\u7b2c\u5341\u4e94\u6559\u5b66\u697c",
    "\u7b2c\u5341\u516d\u6559\u5b66\u697c", "\u7b2c\u5341\u4e03\u6559\u5b66\u697c", "\u7b2c\u5341\u516b\u6559\u5b66\u697c", "\u7b2c\u5341\u4e5d\u6559\u5b66\u697c",
    "\u7b2c\u4e8c\u5341\u6559\u5b66\u697c", "\u7b2c\u4e8c\u5341\u4e00\u6559\u5b66\u697c", "\u7b2c\u4e8c\u5341\u4e09\u6559\u5b66\u697c", "\u7b2c\u4e8c\u5341\u56db\u6559\u5b66\u697c",
    "\u7b2c\u4e8c\u5341\u516d\u6559\u5b66\u697c", "\u7b2c\u4e09\u5341\u4e94\u6559\u5b66\u697c",
)


def test_teaching_building_inventory_has_one_explicit_audited_status_per_target():
    text = COVERAGE_PATH.read_text(encoding="utf-8")
    for name in TARGETS:
        assert "| {} | DONE |".format(name) in text or "| {} | PARTIAL |".format(name) in text or "| {} | BLOCKED |".format(name) in text


def test_mapped_teaching_buildings_have_safe_aliases_and_explicit_map_facts():
    raw = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    nodes = {node["name"]: node for node in raw["nodes"]}
    for name in TARGETS:
        if name not in nodes:
            continue
        node = nodes[name]
        assert node["category"] in {"teaching", "teaching_building"}
        assert node["precision"] in {"precise", "approximate"}
        assert node["provenance"]
        assert node["source"]
        number = node["id"].rsplit("_", 1)[1]
        assert "{}教".format(int(number)) in node["aliases"]


def test_building_9_is_not_duplicated_and_core_routes_use_shared_road_nodes():
    campus_map = load_campus_map_data(MAP_PATH)
    assert len([node for node in campus_map.nodes if node.name == "第九教学楼"]) == 1
    for origin in ("第2教学楼", "第6教学楼", "第11教学楼", "第15教学楼"):
        route = plan_route(campus_map, origin, "第九教学楼", TransportMode.WALK)
        assert route.status is RouteStatus.COMPUTED
        assert any("__road_access" not in node_id and "weijinlu_road_" in node_id or "weijinlu_junction_" in node_id for node_id in route.path)
    route_2 = plan_route(campus_map, "第2教学楼", "第九教学楼", TransportMode.WALK)
    route_12 = plan_route(campus_map, "第12教学楼", "第九教学楼", TransportMode.WALK)
    assert set(route_2.path).intersection(route_12.path).intersection({"weijinlu_junction_huadi_yizhi", "weijinlu_road_huadi_core"})


def test_building_aliases_resolve_without_exposing_shared_road_nodes():
    campus_map = load_campus_map_data(MAP_PATH)
    assert campus_map.resolve_node_id("15教") == "weijinlu_building_15"
    assert campus_map.resolve_node_id("十五教") == "weijinlu_building_15"
    _, prompt = build_location_resolver_prompt(campus_map, "15教")
    assert "weijinlu_road_huadi_core" not in prompt


def test_inventory_correction_replaces_28_with_13_and_verifies_local_multimodal_access():
    campus_map = load_campus_map_data(MAP_PATH)
    assert campus_map.resolve_node_id("13教") == "weijinlu_building_13"
    assert campus_map.resolve_node_id("十三教") == "weijinlu_building_13"
    assert campus_map.resolve_node_id("1教") == "weijinlu_building_1"
    assert campus_map.resolve_node_id("一教") == "weijinlu_building_1"
    assert campus_map.resolve_node_id("28教") is None
    for origin, destination in (
        ("weijinlu_building_1", "weijinlu_road_taile_south"),
        ("weijinlu_building_13", "weijinlu_road_huadi_north"),
    ):
        for mode in (TransportMode.WALK, TransportMode.BIKE):
            route = plan_route(campus_map, origin, destination, mode)
            assert route.status is RouteStatus.COMPUTED


def test_done_criterion_is_derived_from_map_facts_not_a_manually_stale_label():
    campus_map = load_campus_map_data(MAP_PATH)
    for node_id in ("weijinlu_building_1", "weijinlu_building_13"):
        node = next(node for node in campus_map.nodes if node.id == node_id)
        assert node.node_kind == "poi"
        assert node.precision in {"precise", "approximate"}
        assert node.provenance and node.source
        assert campus_map.resolve_node_id(node.name) == node_id
        assert any(edge.from_id == "{}__road_access".format(node_id) or edge.to_id == "{}__road_access".format(node_id) for edge in campus_map.edges)
        for mode in (TransportMode.WALK, TransportMode.BIKE):
            route = plan_route(campus_map, node_id, "weijinlu_building_9", mode)
            assert route.status is RouteStatus.COMPUTED


def test_core_teaching_batch_eight_buildings_meet_deterministic_done_criterion():
    campus_map = load_campus_map_data(MAP_PATH)
    for suffix in ("2", "3", "4", "6", "7", "12", "15", "20"):
        node_id = "weijinlu_building_{}".format(suffix)
        node = next(node for node in campus_map.nodes if node.id == node_id)
        assert node.node_kind == "poi"
        assert node.precision in {"precise", "approximate"}
        assert node.provenance and node.aliases
        assert campus_map.resolve_node_id(node.name) == node_id
        assert any(node_id + "__road_access" in (edge.from_id, edge.to_id) for edge in campus_map.edges)
        for mode in (TransportMode.WALK, TransportMode.BIKE):
            assert plan_route(campus_map, node_id, "weijinlu_building_9", mode).status is RouteStatus.COMPUTED


def test_west_teaching_batch_five_buildings_meet_deterministic_done_criterion():
    campus_map = load_campus_map_data(MAP_PATH)
    for suffix in ("11", "16", "18", "21", "23"):
        node_id = "weijinlu_building_{}".format(suffix)
        node = next(node for node in campus_map.nodes if node.id == node_id)
        assert node.node_kind == "poi"
        assert node.precision in {"precise", "approximate"}
        assert node.provenance and node.aliases
        assert campus_map.resolve_node_id(node.name) == node_id
        assert any(node_id + "__road_access" in (edge.from_id, edge.to_id) for edge in campus_map.edges)
        for mode in (TransportMode.WALK, TransportMode.BIKE):
            assert plan_route(campus_map, node_id, "weijinlu_building_9", mode).status is RouteStatus.COMPUTED
    assert plan_route(campus_map, "weijinlu_pengxiang_student_apartment", "weijinlu_building_11", TransportMode.WALK).status is RouteStatus.COMPUTED


def test_east_southeast_teaching_batch_seven_meet_deterministic_done_criterion():
    campus_map = load_campus_map_data(MAP_PATH)
    for suffix in ("5", "8", "10", "14", "17", "24", "35"):
        node_id = "weijinlu_building_{}".format(suffix)
        node = next(node for node in campus_map.nodes if node.id == node_id)
        assert node.node_kind == "poi"
        assert node.precision in {"precise", "approximate"}
        assert node.provenance and node.aliases
        assert campus_map.resolve_node_id(node.name) == node_id
        assert any(node_id + "__road_access" in (edge.from_id, edge.to_id) for edge in campus_map.edges)
        for mode in (TransportMode.WALK, TransportMode.BIKE):
            assert plan_route(campus_map, node_id, "weijinlu_building_9", mode).status is RouteStatus.COMPUTED
    for mode in (TransportMode.WALK, TransportMode.BIKE):
        assert plan_route(campus_map, "weijinlu_dorm_23", "weijinlu_building_35", mode).status is RouteStatus.COMPUTED
        assert plan_route(campus_map, "weijinlu_building_1", "weijinlu_building_14", mode).status is RouteStatus.COMPUTED


def test_final_south_batch_and_all_twenty_five_teaching_buildings_are_done():
    campus_map = load_campus_map_data(MAP_PATH)
    for node_id in ("weijinlu_building_19", "weijinlu_building_26"):
        node = next(node for node in campus_map.nodes if node.id == node_id)
        assert node.precision in {"precise", "approximate"}
        assert node.provenance and node.aliases
        assert any(node_id + "__road_access" in (edge.from_id, edge.to_id) for edge in campus_map.edges)
        for mode in (TransportMode.WALK, TransportMode.BIKE):
            assert plan_route(campus_map, node_id, "weijinlu_building_9", mode).status is RouteStatus.COMPUTED
    assert campus_map.resolve_node_id("19教") == "weijinlu_building_19"
    assert campus_map.resolve_node_id("十九教") == "weijinlu_building_19"
    assert campus_map.resolve_node_id("26教") == "weijinlu_building_26"
    assert campus_map.resolve_node_id("二十六教") == "weijinlu_building_26"
    assert campus_map.resolve_node_id("26教A座") is None
    for origin, destination in (
        ("weijinlu_building_19", "weijinlu_building_26"),
        ("weijinlu_dorm_23", "weijinlu_building_26"),
        ("weijinlu_chunshui_library", "weijinlu_building_19"),
    ):
        for mode in (TransportMode.WALK, TransportMode.BIKE):
            assert plan_route(campus_map, origin, destination, mode).status is RouteStatus.COMPUTED
    teaching_nodes = [
        node for node in campus_map.nodes
        if node.id.startswith("weijinlu_building_") and node.node_kind == "poi"
    ]
    assert len(teaching_nodes) == 25
    for node in teaching_nodes:
        assert node.precision in {"precise", "approximate"}
        assert node.provenance and node.aliases
        for mode in (TransportMode.WALK, TransportMode.BIKE):
            assert plan_route(campus_map, node.id, "weijinlu_building_9", mode).status is RouteStatus.COMPUTED


def test_canteen_library_and_learning_building_batch_ten_meet_done_criterion():
    campus_map = load_campus_map_data(MAP_PATH)
    batch = (
        "weijinlu_canteen_1", "weijinlu_canteen_3", "weijinlu_canteen_4", "weijinlu_canteen_5",
        "weijinlu_library_main", "weijinlu_science_library", "weijinlu_chunshui_library",
        "weijinlu_lecture_hall", "weijinlu_east_lecture_hall", "weijinlu_west_lecture_hall",
    )
    for node_id in batch:
        node = next(node for node in campus_map.nodes if node.id == node_id)
        assert node.node_kind == "poi"
        assert node.precision in {"precise", "approximate"}
        assert node.provenance and node.aliases is not None
        assert campus_map.resolve_node_id(node.name) == node_id
        assert any(node_id + "__road_access" in (edge.from_id, edge.to_id) for edge in campus_map.edges)
        for mode in (TransportMode.WALK, TransportMode.BIKE):
            assert plan_route(campus_map, node_id, "weijinlu_building_9", mode).status is RouteStatus.COMPUTED
    assert campus_map.resolve_node_id("春水馆") == "weijinlu_chunshui_library"
    assert campus_map.resolve_node_id("图书馆") == "weijinlu_library_main"
    assert campus_map.resolve_node_id("图书馆") != "weijinlu_chunshui_library"
    for origin, destination in (
        ("weijinlu_dorm_34", "weijinlu_canteen_1"),
        ("weijinlu_pengxiang_student_apartment", "weijinlu_canteen_5"),
        ("weijinlu_dorm_23", "weijinlu_canteen_3"),
        ("weijinlu_building_6", "weijinlu_library_main"),
        ("weijinlu_building_19", "weijinlu_science_library"),
        ("weijinlu_building_12", "weijinlu_lecture_hall"),
        ("weijinlu_building_6", "weijinlu_west_lecture_hall"),
    ):
        for mode in (TransportMode.WALK, TransportMode.BIKE):
            assert plan_route(campus_map, origin, destination, mode).status is RouteStatus.COMPUTED


def test_pengxiang_to_canteen_five_bike_uses_west_shared_skeleton_without_campus_detour():
    campus_map = load_campus_map_data(MAP_PATH)
    origin = "weijinlu_pengxiang_student_apartment"
    destination = "weijinlu_canteen_5"
    walk = plan_route(campus_map, origin, destination, TransportMode.WALK)
    bike = plan_route(campus_map, origin, destination, TransportMode.BIKE)

    assert walk.status is RouteStatus.COMPUTED
    assert bike.status is RouteStatus.COMPUTED
    assert bike.total_distance_m is not None and bike.total_distance_m <= 400
    assert "weijinlu_road_mingde_west" in bike.path
    assert "weijinlu_building_9__road_access" not in bike.path
    assert bike.path == (
        "weijinlu_pengxiang_student_apartment__road_access",
        "weijinlu_road_mingde_west",
        "weijinlu_canteen_5__road_access",
    )


def test_west_dormitory_to_canteen_five_local_routes_do_not_detour_through_campus_core():
    campus_map = load_campus_map_data(MAP_PATH)
    for origin in ("weijinlu_dorm_1", "weijinlu_dorm_3"):
        for mode in (TransportMode.WALK, TransportMode.BIKE):
            route = plan_route(campus_map, origin, "weijinlu_canteen_5", mode)
            assert route.status is RouteStatus.COMPUTED
            assert route.total_distance_m is not None and route.total_distance_m <= 800
            assert "weijinlu_road_mingde_west" in route.path
            assert "weijinlu_building_9__road_access" not in route.path


def test_academic_research_institution_batch_seven_meets_deterministic_done_criterion():
    campus_map = load_campus_map_data(MAP_PATH)
    batch = (
        "weijinlu_remote_education_college",
        "weijinlu_international_education_college",
        "weijinlu_applied_math_center",
        "weijinlu_information_network_center",
        "weijinlu_feng_jicai_literature_art_institute",
        "weijinlu_wang_xuezhong_art_institute",
        "weijinlu_strategy_institute",
    )
    for node_id in batch:
        node = next(node for node in campus_map.nodes if node.id == node_id)
        assert node.node_kind == "poi"
        assert node.category == "academic_institution"
        assert node.precision in {"precise", "approximate"}
        assert node.provenance and node.source and node.aliases is not None
        assert campus_map.resolve_node_id(node.name) == node_id
        assert any(node_id + "__road_access" in (edge.from_id, edge.to_id) for edge in campus_map.edges)
        for mode in (TransportMode.WALK, TransportMode.BIKE):
            route = plan_route(campus_map, node_id, "weijinlu_building_9", mode)
            assert route.status is RouteStatus.COMPUTED
            assert all(other + "__road_access" not in route.path for other in batch if other != node_id)

    assert campus_map.resolve_node_id("\u8fdc\u7a0b\u4e0e\u7ee7\u7eed\u6559\u80b2\u5b66\u9662") == "weijinlu_remote_education_college"
    assert campus_map.resolve_node_id("\u56fd\u6559\u5b66\u9662") == "weijinlu_international_education_college"
    assert campus_map.resolve_node_id("\u5e94\u7528\u6570\u5b66\u4e2d\u5fc3") == "weijinlu_applied_math_center"
    assert "weijinlu_road_huadi_north" in plan_route(
        campus_map, "weijinlu_international_education_college", "weijinlu_building_9", TransportMode.WALK
    ).path
    assert "weijinlu_road_huadi_north" in plan_route(
        campus_map, "weijinlu_information_network_center", "weijinlu_building_9", TransportMode.BIKE
    ).path
    math_path = plan_route(campus_map, "weijinlu_applied_math_center", "weijinlu_building_20", TransportMode.WALK).path
    assert math_path[:3] == (
        "weijinlu_applied_math_center__road_access",
        "weijinlu_road_youth_lake_west",
        "weijinlu_junction_youth_lake_southwest",
    )
    for mode in (TransportMode.WALK, TransportMode.BIKE):
        remote_to_dorm = plan_route(
            campus_map, "weijinlu_remote_education_college", "weijinlu_dorm_25", mode
        )
        assert remote_to_dorm.total_distance_m is not None and remote_to_dorm.total_distance_m <= 500
        assert remote_to_dorm.path == (
            "weijinlu_remote_education_college__road_access",
            "weijinlu_road_north_math_access",
            "weijinlu_dorm_25__road_access",
        )
    for origin, destination in (
        ("weijinlu_information_network_center", "weijinlu_building_6"),
        ("weijinlu_wang_xuezhong_art_institute", "weijinlu_library_main"),
        ("weijinlu_strategy_institute", "weijinlu_building_3"),
        ("weijinlu_feng_jicai_literature_art_institute", "weijinlu_building_17"),
        ("weijinlu_remote_education_college", "weijinlu_dorm_25"),
    ):
        for mode in (TransportMode.WALK, TransportMode.BIKE):
            assert plan_route(campus_map, origin, destination, mode).status is RouteStatus.COMPUTED


def test_academic_research_engineering_building_batch_six_meets_deterministic_done_criterion():
    campus_map = load_campus_map_data(MAP_PATH)
    batch = (
        "weijinlu_beiyang_science_building",
        "weijinlu_medical_teaching_building",
        "weijinlu_comprehensive_experiment_building",
        "weijinlu_internal_combustion_engine_building",
        "weijinlu_electrical_engineering_team",
        "weijinlu_power_station_3_5kv",
    )
    for node_id in batch:
        node = next(node for node in campus_map.nodes if node.id == node_id)
        assert node.node_kind == "poi"
        assert node.category == "academic_research_building"
        assert node.precision in {"precise", "approximate"}
        assert node.provenance and node.source and node.aliases is not None
        assert campus_map.resolve_node_id(node.name) == node_id
        assert any(node_id + "__road_access" in (edge.from_id, edge.to_id) for edge in campus_map.edges)
        for mode in (TransportMode.WALK, TransportMode.BIKE):
            route = plan_route(campus_map, node_id, "weijinlu_building_9", mode)
            assert route.status is RouteStatus.COMPUTED
            assert all(other + "__road_access" not in route.path for other in batch if other != node_id)

    beiyang_to_science = plan_route(
        campus_map, "weijinlu_beiyang_science_building", "weijinlu_science_library", TransportMode.WALK
    )
    assert "weijinlu_road_south_research_access" in beiyang_to_science.path
    assert "weijinlu_junction_jingye_huadi" in beiyang_to_science.path
    assert "weijinlu_road_south_research_access" in plan_route(
        campus_map, "weijinlu_beiyang_science_building", "weijinlu_building_19", TransportMode.BIKE
    ).path
    for origin, destination in (
        ("weijinlu_medical_teaching_building", "weijinlu_building_9"),
        ("weijinlu_comprehensive_experiment_building", "weijinlu_building_1"),
        ("weijinlu_electrical_engineering_team", "weijinlu_building_12"),
        ("weijinlu_power_station_3_5kv", "weijinlu_lecture_hall"),
        ("weijinlu_internal_combustion_engine_building", "weijinlu_medical_teaching_building"),
    ):
        for mode in (TransportMode.WALK, TransportMode.BIKE):
            route = plan_route(campus_map, origin, destination, mode)
            assert route.status is RouteStatus.COMPUTED
            assert route.total_distance_m is not None and route.total_distance_m <= 700


def test_public_campus_service_building_batch_seven_meets_deterministic_done_criterion():
    campus_map = load_campus_map_data(MAP_PATH)
    batch = (
        "weijinlu_student_activity_center",
        "weijinlu_union",
        "weijinlu_staff_home",
        "weijinlu_alumni_home",
        "weijinlu_liuyuan",
        "weijinlu_tianan_building",
        "weijinlu_school_hospital",
    )
    for node_id in batch:
        node = next(node for node in campus_map.nodes if node.id == node_id)
        assert node.node_kind == "poi"
        assert node.category == "campus_service"
        assert node.precision in {"precise", "approximate"}
        assert node.provenance and node.source and node.aliases is not None
        assert campus_map.resolve_node_id(node.name) == node_id
        assert any(node_id + "__road_access" in (edge.from_id, edge.to_id) for edge in campus_map.edges)
        for mode in (TransportMode.WALK, TransportMode.BIKE):
            route = plan_route(campus_map, node_id, "weijinlu_building_9", mode)
            assert route.status is RouteStatus.COMPUTED
            assert all(other + "__road_access" not in route.path for other in batch if other != node_id)

    assert campus_map.resolve_node_id("\u5b66\u751f\u6d3b\u52a8\u4e2d\u5fc3") == "weijinlu_student_activity_center"
    assert "weijinlu_road_youth_lake_west" in plan_route(
        campus_map, "weijinlu_student_activity_center", "weijinlu_building_20", TransportMode.WALK
    ).path
    assert "weijinlu_road_west_service_access" in plan_route(
        campus_map, "weijinlu_alumni_home", "weijinlu_building_23", TransportMode.BIKE
    ).path
    for mode in (TransportMode.WALK, TransportMode.BIKE):
        hospital_to_remote = plan_route(
            campus_map, "weijinlu_school_hospital", "weijinlu_remote_education_college", mode
        )
        assert hospital_to_remote.total_distance_m is not None and hospital_to_remote.total_distance_m <= 700
        assert "weijinlu_road_north_math_access" in hospital_to_remote.path
    for origin, destination in (
        ("weijinlu_union", "weijinlu_building_18"),
        ("weijinlu_alumni_home", "weijinlu_building_23"),
        ("weijinlu_liuyuan", "weijinlu_building_21"),
        ("weijinlu_staff_home", "weijinlu_feng_jicai_literature_art_institute"),
        ("weijinlu_tianan_building", "weijinlu_building_35"),
    ):
        for mode in (TransportMode.WALK, TransportMode.BIKE):
            route = plan_route(campus_map, origin, destination, mode)
            assert route.status is RouteStatus.COMPUTED
            assert route.total_distance_m is not None


def test_youth_lake_west_to_core_corridor_avoids_building_nine_access_for_non_nine_routes():
    campus_map = load_campus_map_data(MAP_PATH)
    for mode in (TransportMode.WALK, TransportMode.BIKE):
        route = plan_route(
            campus_map, "weijinlu_student_activity_center", "weijinlu_building_20", mode
        )
        assert route.status is RouteStatus.COMPUTED
        assert "weijinlu_building_9__road_access" not in route.path
        assert route.path == (
            "weijinlu_student_activity_center__road_access",
            "weijinlu_road_youth_lake_west",
            "weijinlu_junction_youth_lake_southwest",
            "weijinlu_road_huadi_north",
            "weijinlu_building_20__road_access",
        )
    for origin, destination in (
        ("weijinlu_student_activity_center", "weijinlu_building_13"),
        ("weijinlu_student_activity_center", "weijinlu_library_main"),
        ("weijinlu_dorm_31", "weijinlu_building_20"),
        ("weijinlu_applied_math_center", "weijinlu_building_20"),
    ):
        for mode in (TransportMode.WALK, TransportMode.BIKE):
            route = plan_route(campus_map, origin, destination, mode)
            assert route.status is RouteStatus.COMPUTED
            assert "weijinlu_building_9__road_access" not in route.path
            assert "weijinlu_road_youth_lake_west" in route.path


def test_public_service_second_batch_inventory_has_one_honest_final_status_per_target():
    targets = {
        "配楼": "BLOCKED",
        "会议楼": "DONE",
        "校史博物馆": "DONE",
        "出版社": "DONE",
        "期刊中心": "DONE",
        "体育部": "DONE",
    }
    coverage = COVERAGE_PATH.read_text(encoding="utf-8")
    for name, status in targets.items():
        assert "| {} | {} |".format(name, status) in coverage
    assert "| 配楼 | PARTIAL |" not in coverage
    assert "| 会议楼 | PARTIAL |" not in coverage
    assert "| 校史博物馆 | PARTIAL |" not in coverage
    assert "| 出版社 | PARTIAL |" not in coverage
    assert "| 期刊中心 | PARTIAL |" not in coverage
    assert "| 体育部 | PARTIAL |" not in coverage


def test_public_service_second_batch_done_entities_have_auditable_shared_access_and_routes():
    campus_map = load_campus_map_data(MAP_PATH)
    done = (
        "weijinlu_school_history_museum",
        "weijinlu_press",
        "weijinlu_periodicals_center",
    )
    for node_id in done:
        node = next(node for node in campus_map.nodes if node.id == node_id)
        assert node.node_kind == "poi"
        assert node.category == "campus_service"
        assert node.precision in {"precise", "approximate"}
        assert node.provenance and node.source
        assert campus_map.resolve_node_id(node.name) == node_id
        assert any(node_id + "__road_access" in (edge.from_id, edge.to_id) for edge in campus_map.edges)
        for mode in (TransportMode.WALK, TransportMode.BIKE):
            route = plan_route(campus_map, node_id, "weijinlu_building_9", mode)
            assert route.status is RouteStatus.COMPUTED
            assert "weijinlu_building_9__road_access" not in route.path[:-1]


def test_press_and_periodicals_center_are_distinct_resolver_entities_with_one_real_shared_anchor():
    raw = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    nodes = {node["id"]: node for node in raw["nodes"]}
    press = nodes["weijinlu_press"]
    periodicals = nodes["weijinlu_periodicals_center"]
    assert press["name"] != periodicals["name"]
    assert press["aliases"] == [] and periodicals["aliases"] == []
    assert (press["latitude"], press["longitude"]) == (periodicals["latitude"], periodicals["longitude"])
    assert press["provenance"] == periodicals["provenance"] == "tju_official_co_located_anchor"
    assert press["physical_anchor_id"] == periodicals["physical_anchor_id"] == "weijinlu_building_19_east_annex"
    local_edges = {
        edge["from"]: edge
        for edge in raw["edges"]
        if edge["from"] in {"weijinlu_press", "weijinlu_periodicals_center"}
    }
    assert local_edges["weijinlu_press"]["to"] == local_edges["weijinlu_periodicals_center"]["to"] == "weijinlu_junction_jingye_huadi"
    assert local_edges["weijinlu_press"]["distance_by_mode"] == local_edges["weijinlu_periodicals_center"]["distance_by_mode"] == {"walk": 247, "bike": 247}

    campus_map = load_campus_map_data(MAP_PATH)
    assert campus_map.resolve_node_id("出版社") == "weijinlu_press"
    assert campus_map.resolve_node_id("期刊中心") == "weijinlu_periodicals_center"
    for mode in (TransportMode.WALK, TransportMode.BIKE):
        press_route = plan_route(campus_map, "weijinlu_press", "weijinlu_science_library", mode)
        periodicals_route = plan_route(campus_map, "weijinlu_periodicals_center", "weijinlu_science_library", mode)
        assert press_route.status is periodicals_route.status is RouteStatus.COMPUTED
        assert press_route.total_distance_m == periodicals_route.total_distance_m


def test_meeting_building_and_sports_area_close_the_deferred_local_access_facts():
    campus_map = load_campus_map_data(MAP_PATH)
    targets = {
        "会议楼": "weijinlu_meeting_building",
        "体育场": "weijinlu_stadium",
        "体育部": "weijinlu_sports_department",
    }
    for name, node_id in targets.items():
        node = next(node for node in campus_map.nodes if node.id == node_id)
        assert node.node_kind == "poi"
        assert node.precision == "precise"
        assert node.provenance and node.source
        assert campus_map.resolve_node_id(name) == node_id
        assert any(node_id + "__road_access" in (edge.from_id, edge.to_id) for edge in campus_map.edges)
        for mode in (TransportMode.WALK, TransportMode.BIKE):
            route = plan_route(campus_map, node_id, "weijinlu_building_9", mode)
            assert route.status is RouteStatus.COMPUTED
            assert route.total_distance_m is not None

    for mode in (TransportMode.WALK, TransportMode.BIKE):
        meeting_to_core = plan_route(campus_map, "weijinlu_meeting_building", "weijinlu_building_20", mode)
        assert meeting_to_core.status is RouteStatus.COMPUTED
        assert "weijinlu_road_hubin_jingye_west" in meeting_to_core.path
        assert "weijinlu_building_9__road_access" not in meeting_to_core.path

    for mode in (TransportMode.WALK, TransportMode.BIKE):
        stadium = plan_route(campus_map, "weijinlu_stadium", "weijinlu_building_17", mode)
        sports = plan_route(campus_map, "weijinlu_sports_department", "weijinlu_building_17", mode)
        assert stadium.status is sports.status is RouteStatus.COMPUTED
        assert "weijinlu_road_sports_taile_north" in stadium.path
        assert "weijinlu_road_sports_taile_north" in sports.path
        assert stadium.total_distance_m == sports.total_distance_m


def test_stadium_and_sports_department_are_distinct_entities_with_shared_sports_access():
    campus_map = load_campus_map_data(MAP_PATH)
    assert campus_map.resolve_node_id("体育场") == "weijinlu_stadium"
    assert campus_map.resolve_node_id("体育部") == "weijinlu_sports_department"
    assert campus_map.resolve_node_id("网球场") is None
    stadium_node = next(node for node in campus_map.nodes if node.id == "weijinlu_stadium")
    sports_node = next(node for node in campus_map.nodes if node.id == "weijinlu_sports_department")
    assert stadium_node.physical_anchor_id == sports_node.physical_anchor_id == "weijinlu_stadium_sports_area"
    for mode in (TransportMode.WALK, TransportMode.BIKE):
        stadium = plan_route(campus_map, "weijinlu_stadium", "weijinlu_road_sports_taile_north", mode)
        sports = plan_route(campus_map, "weijinlu_sports_department", "weijinlu_road_sports_taile_north", mode)
        assert stadium.status is sports.status is RouteStatus.COMPUTED
        assert stadium.total_distance_m == sports.total_distance_m == 191
        co_located = plan_route(campus_map, "weijinlu_sports_department", "weijinlu_stadium", mode)
        assert co_located.total_distance_m == 0
        assert co_located.path == ("weijinlu_sports_department", "weijinlu_stadium")


def test_weijinlu_gate_inventory_has_five_final_statuses_and_campus_scoped_entities():
    targets = {
        "三村门": "weijinlu_gate_sancun",
        "铭德道门": "weijinlu_gate_mingde",
        "西门": "weijinlu_gate_west",
        "北门": "weijinlu_gate_north",
        "东门": "weijinlu_gate_east",
    }
    coverage = COVERAGE_PATH.read_text(encoding="utf-8")
    raw = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    nodes = {node["id"]: node for node in raw["nodes"]}
    campus_map = load_campus_map_data(MAP_PATH)

    for name, node_id in targets.items():
        assert "| {} | DONE |".format(name) in coverage or "| {} | BLOCKED |".format(name) in coverage
        assert "| {} | PARTIAL |".format(name) not in coverage
        node = nodes[node_id]
        assert node["category"] == "gate"
        assert node_id.startswith("weijinlu_gate_")
        assert node["precision"] in {"precise", "approximate"}
        assert node["provenance"] and node["source"]
        assert campus_map.resolve_node_id(name) == node_id
        # Internal IDs remain available to route-provider tests, but they must
        # never be offered as normal resolver catalog destinations.
        _, resolver_catalog = build_location_resolver_prompt(campus_map, name)
        assert node_id + "__road_access" not in resolver_catalog
        assert any(
            node_id + "__road_access" in (edge.from_id, edge.to_id)
            for edge in campus_map.edges
        )
        for mode in (TransportMode.WALK, TransportMode.BIKE):
            route = plan_route(campus_map, node_id, "weijinlu_building_9", mode)
            assert route.status is RouteStatus.COMPUTED
            assert route.total_distance_m is not None
            assert node_id not in route.path


def test_gate_accesses_are_boundary_terminals_not_normal_campus_route_junctions():
    campus_map = load_campus_map_data(MAP_PATH)
    gate_accesses = {
        "weijinlu_gate_sancun__road_access",
        "weijinlu_gate_mingde__road_access",
        "weijinlu_gate_west__road_access",
        "weijinlu_gate_north__road_access",
        "weijinlu_gate_east__road_access",
    }

    # Each gate's derived access has exactly one campus-side edge.  This makes
    # the gate a boundary terminal: it cannot become a shortcut between two
    # ordinary campus destinations.
    for gate_access in gate_accesses:
        assert sum(gate_access in (edge.from_id, edge.to_id) for edge in campus_map.edges) == 1

    for origin, destination in (
        ("weijinlu_dorm_31", "weijinlu_building_20"),
        ("weijinlu_pengxiang_student_apartment", "weijinlu_canteen_5"),
        ("weijinlu_student_activity_center", "weijinlu_library_main"),
        ("weijinlu_dorm_23", "weijinlu_building_35"),
    ):
        for mode in (TransportMode.WALK, TransportMode.BIKE):
            route = plan_route(campus_map, origin, destination, mode)
            assert route.status is RouteStatus.COMPUTED
            assert not gate_accesses.intersection(route.path)

    for mode in (TransportMode.WALK, TransportMode.BIKE):
        east = plan_route(campus_map, "weijinlu_gate_east", "weijinlu_building_35", mode)
        assert east.status is RouteStatus.COMPUTED
        assert "weijinlu_junction_beiyang_taile_east" in east.path
        north = plan_route(campus_map, "weijinlu_gate_north", "weijinlu_dorm_34", mode)
        assert north.status is RouteStatus.COMPUTED
        assert "weijinlu_junction_anshanxidao_taile" in north.path


def test_four_weijinlu_lakes_are_resolvable_shoreline_terminals_without_crossing_topology():
    targets = {
        "青年湖": "weijinlu_lake_youth",
        "爱晚湖": "weijinlu_lake_aiwan",
        "友谊湖": "weijinlu_lake_youyi",
        "敬业湖": "weijinlu_lake_jingye",
    }
    raw = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    campus_map = load_campus_map_data(MAP_PATH)
    coverage = COVERAGE_PATH.read_text(encoding="utf-8")
    raw_edges = raw["edges"]
    lake_accesses = {node_id + "__road_access" for node_id in targets.values()}
    for name, node_id in targets.items():
        node = next(node for node in campus_map.nodes if node.id == node_id)
        assert "| {} | DONE |".format(name) in coverage
        assert node.category == "landmark" and node.precision == "approximate"
        assert node.provenance and node.source
        assert campus_map.resolve_node_id(name) == node_id
        assert sum(node_id in (edge["from"], edge["to"]) for edge in raw_edges) == 1
        for mode in (TransportMode.WALK, TransportMode.BIKE):
            assert plan_route(campus_map, node_id, "weijinlu_building_9", mode).status is RouteStatus.COMPUTED

    # Cross-shore audit: routes use audited corridors and cannot traverse a lake terminal.
    youth_pairs = (
        ("weijinlu_applied_math_center", "weijinlu_building_20"),
        ("weijinlu_dorm_25", "weijinlu_building_13"),
        ("weijinlu_dorm_31", "weijinlu_international_education_college"),
        ("weijinlu_student_activity_center", "weijinlu_information_network_center"),
    )
    jingye_pairs = (
        ("weijinlu_library_main", "weijinlu_science_library"),
        ("weijinlu_building_6", "weijinlu_building_19"),
        ("weijinlu_building_7", "weijinlu_building_26"),
        ("weijinlu_strategy_institute", "weijinlu_science_library"),
    )
    for origin, destination in youth_pairs + jingye_pairs:
        for mode in (TransportMode.WALK, TransportMode.BIKE):
            route = plan_route(campus_map, origin, destination, mode)
            assert route.status is RouteStatus.COMPUTED
            assert not lake_accesses.intersection(route.path)
            assert "weijinlu_building_9__road_access" not in route.path

    aiwan = next(node for node in campus_map.nodes if node.id == "weijinlu_lake_aiwan")
    youyi = next(node for node in campus_map.nodes if node.id == "weijinlu_lake_youyi")
    assert aiwan.name != youyi.name
    assert aiwan.aliases == youyi.aliases == ()
    assert not any({edge["from"], edge["to"]} == {"weijinlu_lake_aiwan", "weijinlu_lake_youyi"} for edge in raw_edges)


def test_weijinlu_landscapes_are_distinct_terminal_destinations():
    campus_map = load_campus_map_data(MAP_PATH)
    targets = {
        "北洋园": "weijinlu_landmark_beiyang_garden",
        "北洋广场": "weijinlu_landmark_beiyang_square",
        "求是亭": "weijinlu_landmark_qiushi_pavilion",
    }
    for name, node_id in targets.items():
        node = next(node for node in campus_map.nodes if node.id == node_id)
        assert node.category == "landmark" and node.provenance and node.source
        assert campus_map.resolve_node_id(name) == node_id
        for mode in (TransportMode.WALK, TransportMode.BIKE):
            assert plan_route(campus_map, node_id, "weijinlu_building_9", mode).status is RouteStatus.COMPUTED
    assert campus_map.resolve_node_id("北洋园") != "beiyangyuan_north_gate"
    pavilion_access = "weijinlu_landmark_qiushi_pavilion__road_access"
    for mode in (TransportMode.WALK, TransportMode.BIKE):
        route = plan_route(campus_map, "weijinlu_building_6", "weijinlu_building_19", mode)
        assert pavilion_access not in route.path


def test_linear_road_and_weijin_river_inventory_is_explicit_and_not_a_point_resolver_layer():
    raw = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    features = {item["name"]: item for item in raw["metadata"]["linear_features"]}
    roads = ("玉泉路", "求是路", "金晖路", "铭德道", "湖滨道", "鞍山西道", "集贤道", "花堤路", "益智道", "敬业道", "北洋道", "太雷路", "旭东路", "卫津路")
    assert set(roads).issubset(features)
    assert features["卫津河"]["kind"] == "water_boundary"
    assert features["卫津河"]["role"] == "boundary-obstacle-only"
    for name in roads:
        feature = features[name]
        assert feature["status"] in {"DONE", "BLOCKED"}
        assert feature["source"]
        if feature["status"] == "DONE" and feature["role"] != "boundary-only":
            assert feature["segments"]
    campus_map = load_campus_map_data(MAP_PATH)
    assert campus_map.resolve_node_id("求是路") is None
    assert campus_map.resolve_node_id("卫津河") is None


def test_cross_campus_routes_use_shared_roads_not_the_nine_building_or_terminal_accesses_as_relays():
    campus_map = load_campus_map_data(MAP_PATH)
    checks = (
        ("三村门", "东门"),
        ("北门", "26教"),
        ("34斋", "35教"),
        ("鹏翔学生公寓", "体育场"),
        ("31斋", "科学图书馆"),
        ("大学生活动中心", "天南楼"),
        ("西门", "东门"),
    )
    for origin, destination in checks:
        for mode in (TransportMode.WALK, TransportMode.BIKE):
            route = plan_route(campus_map, origin, destination, mode)
            assert route.status is RouteStatus.COMPUTED
            interior = route.path[1:-1]
            assert "weijinlu_building_9__road_access" not in interior
            assert not any("__road_access" in node_id for node_id in interior)
            assert not any(node_id.startswith("weijinlu_gate_") for node_id in interior)
            assert not any(node_id.startswith("weijinlu_lake_") for node_id in interior)
            assert any(
                node_id.startswith("weijinlu_road_") or node_id.startswith("weijinlu_junction_")
                for node_id in interior
            )


def test_weijinlu_master_inventory_reconciliation_and_all_point_connectivity():
    """Final P3f accounting: no stale or scope-excluded POI can hide in the map."""
    dorms = (
        "一斋", "二斋", "三斋", "四斋", "五斋", "23斋", "24斋", "25斋", "26斋", "28斋", "29斋", "30斋",
        "31斋", "32斋", "33斋", "34斋", "35斋", "36斋", "37斋", "38斋", "39斋", "40斋", "41斋",
        "43斋", "44斋", "45斋", "46斋", "47斋", "48斋", "49斋", "50斋", "51斋", "52斋", "53斋", "57斋",
        "鹏翔学生公寓", "友园",
    )
    teaching = (
        "第一教学楼", "第二教学楼", "第三教学楼", "第四教学楼", "第五教学楼", "第六教学楼", "第七教学楼",
        "第八教学楼", "第九教学楼", "第十教学楼", "第十一教学楼", "第十二教学楼", "第十三教学楼", "第十四教学楼",
        "第十五教学楼", "第十六教学楼", "第十七教学楼", "第十八教学楼", "第十九教学楼", "第二十教学楼",
        "第二十一教学楼", "第二十三教学楼", "第二十四教学楼", "第二十六教学楼", "第三十五教学楼",
    )
    others = (
        "学一食堂", "学三食堂", "学四食堂", "学五食堂", "图书馆", "科学图书馆", "春水图书馆", "阶梯教室", "东阶梯教室", "西阶梯教室",
        "远教学院", "国教学院", "应数中心", "信息与网络中心", "冯骥才文学艺术研究院", "王学仲艺术研究所", "战略院",
        "北洋科学楼", "医学部教学楼", "综合实验楼", "内燃机大楼", "电工队", "3.5KV电站",
        "大学生活动中心", "校医院", "工会", "员工之家", "校友之家", "留园", "会议楼", "校史博物馆", "出版社", "期刊中心", "体育部", "天南楼",
        "体育场", "青年湖", "爱晚湖", "友谊湖", "敬业湖", "北洋园", "北洋广场", "求是亭",
        "三村门", "铭德道门", "西门", "北门", "东门",
    )
    roads = (
        "玉泉路", "求是路", "金晖路", "铭德道", "湖滨道", "鞍山西道", "集贤道", "花堤路", "益智道", "敬业道", "北洋道", "太雷路", "旭东路", "卫津路",
    )
    raw = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    campus_map = load_campus_map_data(MAP_PATH)
    expected_done = set(dorms + teaching + others)
    actual_pois = {node.name for node in campus_map.nodes if node.node_kind == "poi"}
    assert len(dorms) == 37 and len(teaching) == 25
    assert len(expected_done) == 110
    assert actual_pois == expected_done
    assert campus_map.resolve_node_id("配楼") is None
    assert campus_map.resolve_node_id("28教") is None
    assert campus_map.resolve_node_id("13教") == "weijinlu_building_13"
    assert campus_map.resolve_node_id("北馆") == "weijinlu_chunshui_library"
    assert campus_map.resolve_node_id("春水馆") == "weijinlu_chunshui_library"
    assert campus_map.resolve_node_id("图书馆") == "weijinlu_library_main"
    for internal in ("weijinlu_road_taile_south", "weijinlu_junction_east_core_roadside", "weijinlu_building_9__road_access"):
        assert campus_map.resolve_node_id(internal) is None
    for excluded in ("网球场", "篮球场", "排球场", "篮球馆", "游泳馆", "体育馆", "海棠", "张太雷像", "百年校庆纪念亭", "牛顿苹果树", "梦成真石"):
        assert campus_map.resolve_node_id(excluded) is None
    features = {feature["name"]: feature for feature in raw["metadata"]["linear_features"]}
    assert set(roads).issubset(features)
    assert all(features[road]["status"] == "DONE" for road in roads)
    assert features["卫津河"]["kind"] == "water_boundary"
    assert features["卫津河"]["status"] == "DONE"
    for node in (node for node in campus_map.nodes if node.node_kind == "poi"):
        assert campus_map.resolve_node_id(node.name) == node.id
        for mode in (TransportMode.WALK, TransportMode.BIKE):
            assert plan_route(campus_map, node.id, "weijinlu_building_9", mode).status is RouteStatus.COMPUTED
    by_anchor = {}
    for node in campus_map.nodes:
        if node.node_kind == "poi" and node.physical_anchor_id:
            by_anchor.setdefault(node.physical_anchor_id, []).append(node.id)
    assert by_anchor == {
        "weijinlu_building_19_east_annex": ["weijinlu_press", "weijinlu_periodicals_center"],
        "weijinlu_stadium_sports_area": ["weijinlu_stadium", "weijinlu_sports_department"],
        "weijinlu_jingye_lake_northeast_shore": ["weijinlu_landmark_qiushi_pavilion"],
    }


def test_master_pair_audit_has_no_unreachable_mode_outlier_or_terminal_relay():
    campus_map = load_campus_map_data(MAP_PATH)
    pois = [node for node in campus_map.nodes if node.node_kind == "poi"]
    terminal_accesses = {
        "weijinlu_gate_sancun__road_access", "weijinlu_gate_mingde__road_access", "weijinlu_gate_west__road_access",
        "weijinlu_gate_north__road_access", "weijinlu_gate_east__road_access",
        "weijinlu_lake_youth__road_access", "weijinlu_lake_aiwan__road_access", "weijinlu_lake_youyi__road_access", "weijinlu_lake_jingye__road_access",
        "weijinlu_landmark_beiyang_garden__road_access", "weijinlu_landmark_beiyang_square__road_access", "weijinlu_landmark_qiushi_pavilion__road_access",
    }
    approved_zero_pairs = {
        frozenset(("weijinlu_press", "weijinlu_periodicals_center")),
        frozenset(("weijinlu_stadium", "weijinlu_sports_department")),
    }
    seen_zero_pairs = set()
    suspicious = []
    for left, right in combinations(pois, 2):
        routes = {
            mode: plan_route(campus_map, left.id, right.id, mode)
            for mode in (TransportMode.WALK, TransportMode.BIKE)
        }
        assert all(route.status is RouteStatus.COMPUTED for route in routes.values())
        for route in routes.values():
            assert not terminal_accesses.intersection(route.path[1:-1])
            if route.total_distance_m == 0:
                pair = frozenset((left.id, right.id))
                assert pair in approved_zero_pairs
                seen_zero_pairs.add(pair)
        walk = routes[TransportMode.WALK].total_distance_m
        bike = routes[TransportMode.BIKE].total_distance_m
        if walk and bike:
            ratio = max(walk / bike, bike / walk)
            if ratio >= 2.5 or abs(walk - bike) >= 600:
                suspicious.append((left.id, right.id, walk, bike))
    assert seen_zero_pairs == approved_zero_pairs
    assert suspicious == []
