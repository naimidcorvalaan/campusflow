"""P3d 扩充后真实地图数据完整性 / 别名 / 寻路测试（Python 3.8 兼容，只读本地数据）。"""
from pathlib import Path

import pytest

from src.p3_map_loader import load_campus_map_data
from src.p3_map_schema import MapDataProvenance, RouteStatus, TransportMode
from src.p3_route_provider import plan_route

REAL_MAP_PATH = Path(__file__).parent.parent / "data" / "beiyangyuan_map.json"


@pytest.fixture(scope="module")
def real_map():
    return load_campus_map_data(REAL_MAP_PATH)


def test_provenance_is_real_map(real_map):
    assert real_map.provenance is MapDataProvenance.REAL_MAP
    assert real_map.data_status == "routable"


def test_expanded_scale(real_map):
    assert len(real_map.nodes) >= 60
    assert len(real_map.edges) >= 130


def test_p3d_round2_scale(real_map):
    # 第二轮官方 checklist 补全后：86 节点 / 272 边
    assert len(real_map.nodes) >= 86
    assert len(real_map.edges) >= 270


def test_new_teaching_building_aliases_resolve(real_map):
    expected = {
        "33教": "building_33",
        "41教": "building_41",
        "42教": "building_42",
        "48教": "building_48",
        "49教": "building_49",
        "51教": "building_51",
        "52教": "building_52",
        "53教": "building_53",
    }
    for alias, node_id in expected.items():
        assert real_map.resolve_node_id(alias) == node_id


def test_p3d_round2_teaching_buildings_resolve(real_map):
    expected = {
        "35教": "building_35",
        "35楼": "building_35",
        "机械工程实践教学中心": "building_35",
        "39教": "building_39",
        "港口与海岸工程实验室": "building_39",
        "40教": "building_40",
        "深水结构实验室": "building_40",
        "水利馆": "building_42",
        "水利实验室": "building_42",
        "船舶与海洋工程馆": "building_41",
        "岩土工程研究所": "building_41",
        "电气电子实验教学中心": "building_48",
        "先进高分子材料研究所": "building_53",
        "国际示范性软件学院": "building_55",
    }
    for alias, node_id in expected.items():
        assert real_map.resolve_node_id(alias) == node_id


def test_new_dorm_aliases_resolve(real_map):
    expected = {
        "正园": "zhengyuan_dorm",
        "齐园": "qiyuan_dorm",
        "留园": "liuyuan_dorm",
        "平园21斋A座": "pingyuan_21zhai_a",
        "诚园6斋": "chengyuan_6zhai",
        "三问园30斋": "sanwenyuan_30zhai",
        "知园四斋B座": "zhiyuan_4zhai_b",
    }
    for alias, node_id in expected.items():
        assert real_map.resolve_node_id(alias) == node_id


def test_p3d_round2_dorm_resolve(real_map):
    expected = {
        "修园": "xiuyuan_dorm",
        "修园宿舍": "xiuyuan_dorm",
        "留园": "liuyuan_dorm",
        "三问园": "sanwenyuan_dorm",
    }
    for alias, node_id in expected.items():
        assert real_map.resolve_node_id(alias) == node_id


def test_p3d_round2_function_buildings_resolve(real_map):
    expected = {
        "信息与网络中心": "network_center",
        "网络中心": "network_center",
        "学生生活园区管理服务中心": "dorm_service_center",
        "学服中心": "dorm_service_center",
        "行政服务中心": "administrative_service_center",
        "安保中心": "security_center",
        "保卫处": "security_center",
        "北洋门诊部": "beiyang_clinic",
        "门诊部": "beiyang_clinic",
        "幼儿园": "kindergarten",
    }
    for alias, node_id in expected.items():
        assert real_map.resolve_node_id(alias) == node_id


def test_p3d_round2_canteen_aliases_resolve(real_map):
    expected = {
        "棠园餐厅": "xuesan_canteen",
        "棠园": "xuesan_canteen",
        "桃园餐厅": "xuewu_canteen",
        "桃园": "xuewu_canteen",
        "留园餐厅": "liuyuan_canteen",
        "留学生食堂": "liuyuan_canteen",
        "竹园餐厅": "xuesi_cafeteria",
        "菊园餐厅": "juyuan_canteen",
        "清真食堂": "juyuan_canteen",
    }
    for alias, node_id in expected.items():
        assert real_map.resolve_node_id(alias) == node_id


def test_new_college_and_lab_aliases_resolve(real_map):
    expected = {
        "材料学院": "college_of_materials",
        "建工学院": "college_of_civil_engineering",
        "教育学院": "college_of_education",
        "马克思主义学院": "college_of_marxism",
        "外国语学院": "college_of_foreign_languages",
        "国际工程师学院": "college_of_international_engineers",
        "水利馆": "building_42",
    }
    for alias, node_id in expected.items():
        assert real_map.resolve_node_id(alias) == node_id


def test_p3d_round2_merged_lab_removed(real_map):
    node_ids = {node.id for node in real_map.nodes}
    assert "water_engineering_lab" not in node_ids
    # 被合并节点不再有残余边
    for edge in real_map.edges:
        assert edge.from_id != "water_engineering_lab"
        assert edge.to_id != "water_engineering_lab"


def test_new_landmark_and_logistics_aliases_resolve(real_map):
    assert real_map.resolve_node_id("御园") == "yuyuan_garden"
    assert real_map.resolve_node_id("日新园") == "rishin_park"
    assert real_map.resolve_node_id("北洋纪念林") == "beiyang_memorial_forest"
    assert real_map.resolve_node_id("南区快递站") == "beiyangyuan_south_express_station"


def test_existing_aliases_enriched(real_map):
    assert real_map.resolve_node_id("北菜") == "beiyangyuan_north_cainiao"
    assert real_map.resolve_node_id("南菜") == "beiyangyuan_south_cainiao"
    assert real_map.resolve_node_id("计算机科学与技术学院") == "school_of_computing"


def test_documented_gaps_not_fabricated(real_map):
    # 46 教学楼：本轮已由官方分块高清图“公共教学楼（46教）”标注独立建节点
    assert real_map.resolve_node_id("46教") == "building_46"
    assert real_map.resolve_node_id("46教学楼") == "building_46"
    # 9 斋：已由官方图主体标注独立建节点（见 test_official_map_dorm_zhai_resolve）
    assert real_map.resolve_node_id("正园9斋") == "zhengyuan_9zhai"


def test_all_nodes_reachable_walk_and_bike(real_map):
    for node in real_map.nodes:
        for mode in ("walk", "bike"):
            result = plan_route(real_map, "beiyangyuan_south_gate", node.id, mode)
            assert result.status is RouteStatus.COMPUTED, "{} {} 不可达".format(node.id, mode)


def test_all_edges_positive_distance_and_valid_refs(real_map):
    node_ids = {node.id for node in real_map.nodes}
    for edge in real_map.edges:
        assert edge.from_id in node_ids
        assert edge.to_id in node_ids
        assert edge.distance_m > 0
        assert edge.modes
        assert edge.source


def test_identifier_no_cross_node_conflict(real_map):
    owner = {}
    for node in real_map.nodes:
        for identifier in (node.id, node.name) + node.aliases:
            key = identifier.casefold()
            if key in owner:
                assert owner[key] == node.id, "标识冲突：{}".format(identifier)
            else:
                owner[key] = node.id


def test_category_coverage_expanded(real_map):
    by_category = {}
    for node in real_map.nodes:
        by_category.setdefault(node.category, []).append(node.id)
    assert len(by_category.get("dormitory", [])) >= 16
    assert len(by_category.get("teaching", [])) >= 24
    assert len(by_category.get("logistics", [])) >= 3
    assert len(by_category.get("landmark", [])) >= 8
    assert len(by_category.get("service", [])) >= 6
    assert len(by_category.get("dining", [])) >= 7
    assert len(by_category.get("medical", [])) >= 2


def test_route_to_new_building(real_map):
    result = plan_route(real_map, "平园", "33教", "walk")
    assert result.status is RouteStatus.COMPUTED
    assert result.total_distance_m > 0
    assert result.mode is TransportMode.WALK


def test_route_to_new_dorm_zhai(real_map):
    result = plan_route(real_map, "诚园", "诚园6斋", "walk")
    assert result.status is RouteStatus.COMPUTED
    assert result.total_distance_m > 0


def test_route_to_p3d_round2_new_nodes(real_map):
    for origin, destination in (("南门", "35教"), ("南门", "修园"), ("北区菜鸟", "行政服务中心")):
        result = plan_route(real_map, origin, destination, "walk")
        assert result.status is RouteStatus.COMPUTED, "{} -> {} 不可达".format(origin, destination)
        assert result.total_distance_m > 0
def test_official_map_gates_resolve(real_map):
    expected = {
        "北门": "beiyangyuan_north_gate",
        "东北门": "beiyangyuan_northeast_gate",
        "西北门": "beiyangyuan_northwest_gate",
        "西南门": "beiyangyuan_southwest_gate",
        "东南门": "beiyangyuan_southeast_gate",
        "东门": "beiyangyuan_east_gate",
    }
    for alias, node_id in expected.items():
        assert real_map.resolve_node_id(alias) == node_id


def test_official_map_dorm_zhai_resolve(real_map):
    expected = {
        "正园9斋": "zhengyuan_9zhai",
        "正园九斋": "zhengyuan_9zhai",
        "9斋": "zhengyuan_9zhai",
        "九斋": "zhengyuan_9zhai",
        "正园10斋": "zhengyuan_10zhai",
        "10斋": "zhengyuan_10zhai",
        "齐园13斋": "qiyuan_13zhai",
        "13斋": "qiyuan_13zhai",
        "齐园14斋": "qiyuan_14zhai",
        "14斋": "qiyuan_14zhai",
    }
    for alias, node_id in expected.items():
        assert real_map.resolve_node_id(alias) == node_id


def test_official_map_building_and_function_resolve(real_map):
    expected = {
        "37教": "building_37",
        "37教学楼": "building_37",
        "37号教学楼": "building_37",
        "教师公寓": "teacher_apartment",
        "青年教师公寓": "teacher_apartment",
        "青园餐厅": "qingyuan_canteen",
        "学者公寓食堂": "qingyuan_canteen",
        "青年湖": "qingnian_lake",
    }
    for alias, node_id in expected.items():
        assert real_map.resolve_node_id(alias) == node_id


def test_official_map_approx_provenance(real_map):
    approx_ids = {
        "beiyangyuan_north_gate", "beiyangyuan_northeast_gate",
        "beiyangyuan_northwest_gate", "beiyangyuan_southwest_gate",
        "beiyangyuan_southeast_gate", "beiyangyuan_east_gate",
        "qingnian_lake", "zhengyuan_9zhai", "zhengyuan_10zhai",
        "qiyuan_13zhai", "qiyuan_14zhai", "building_37",
        "teacher_apartment",
    }
    node_map = {n.id: n for n in real_map.nodes}
    for node_id in approx_ids:
        assert node_id in node_map, node_id
    for node_id in approx_ids:
        source = node_map[node_id].source or ""
        assert "官方" in source or "高德" in source, node_id


def test_official_map_scale(real_map):
    assert len(real_map.nodes) >= 123
    assert len(real_map.edges) >= 390


def test_official_map_new_nodes_connected(real_map):
    new_ids = [
        "beiyangyuan_north_gate", "beiyangyuan_northeast_gate",
        "beiyangyuan_northwest_gate", "beiyangyuan_southwest_gate",
        "beiyangyuan_southeast_gate", "beiyangyuan_east_gate",
        "qingnian_lake", "zhengyuan_9zhai", "zhengyuan_10zhai",
        "qiyuan_13zhai", "qiyuan_14zhai", "building_37",
        "teacher_apartment", "qingyuan_canteen",
    ]
    for node_id in new_ids:
        for mode in ("walk", "bike"):
            result = plan_route(real_map, "beiyangyuan_south_gate", node_id, mode)
            assert result.status is RouteStatus.COMPUTED, "{} {} 不可达".format(node_id, mode)


def test_p3d_name_fix_new_dorm_nodes_resolve(real_map):
    expected = {
        "治园": "zhiyuan_garden",
        "治园17斋": "zhiyuan_garden_17zhai",
        "17斋": "zhiyuan_garden_17zhai",
        "治园18斋": "zhiyuan_garden_18zhai",
        "治园19斋增1": "zhiyuan_garden_19zhai_add1",
        "19斋增1": "zhiyuan_garden_19zhai_add1",
        "格园3斋": "geyuan_3zhai",
        "格3斋": "geyuan_3zhai",
        "诚园7斋": "chengyuan_7zhai",
        "7斋": "chengyuan_7zhai",
        "齐园15斋": "qiyuan_15zhai",
        "15斋": "qiyuan_15zhai",
        "平园23斋": "pingyuan_23zhai",
        "23斋": "pingyuan_23zhai",
    }
    for alias, node_id in expected.items():
        assert real_map.resolve_node_id(alias) == node_id


def test_p3d_name_fix_32jiao_resolves_to_science_college(real_map):
    # 高德地址核验：理学院 POI 地址=第32教学楼，故 32教 = 理学院（college_of_science）
    for alias in ("32教", "32教学楼", "32号教学楼", "32楼"):
        assert real_map.resolve_node_id(alias) == "college_of_science"


def test_p3d_name_fix_official_names_resolve(real_map):
    expected = {
        "外国语言与文学学院": "college_of_foreign_languages",
        "求是学部": "building_43",
        "环境科学与工程学院": "building_43",
        "理学院化学系": "building_51",
        "化工材料创新平台": "building_58",
        "环境与资源科技创新平台": "building_59",
    }
    for alias, node_id in expected.items():
        assert real_map.resolve_node_id(alias) == node_id


def test_haitang_merged_into_qingyuan(real_map):
    node_ids = {node.id for node in real_map.nodes}
    assert "haitang_canteen" not in node_ids
    assert "qingyuan_canteen" in node_ids
    assert real_map.resolve_node_id("海棠餐厅") == "qingyuan_canteen"
    assert real_map.resolve_node_id("海棠食堂") == "qingyuan_canteen"
    assert real_map.resolve_node_id("青园餐厅") == "qingyuan_canteen"


def test_boxue_and_sanwen_independent(real_map):
    # 用户确认：博学园（25/26斋）与三问园（27~30斋）是两个相距较远的独立园区，
    # 互不为 alias；官方图把三问园28/29写成博学园属地图标注错误，不采用
    assert real_map.resolve_node_id("三问园") == "sanwenyuan_dorm"
    assert real_map.resolve_node_id("博学园") == "boxueyuan_dorm"
    assert real_map.resolve_node_id("三问园30斋") == "sanwenyuan_30zhai"
    assert real_map.resolve_node_id("博学园30斋") is None  # 官方图误标，不采用
    assert real_map.resolve_node_id("三问园25斋") is None  # 25斋属博学园
    assert real_map.resolve_node_id("三问园26斋") is None  # 26斋属博学园


def test_sanwen_27_28_29_zhai_resolve(real_map):
    # 三问园27/28/29斋独立节点（园区内近似）；官方图将28/29误标博学园，不采用该名称归属
    expected = {
        "三问园27斋": "sanwenyuan_27zhai", "三问园二十七斋": "sanwenyuan_27zhai",
        "27斋": "sanwenyuan_27zhai", "二十七斋": "sanwenyuan_27zhai",
        "三问园28斋": "sanwenyuan_28zhai", "三问园二十八斋": "sanwenyuan_28zhai",
        "28斋": "sanwenyuan_28zhai", "二十八斋": "sanwenyuan_28zhai",
        "三问园29斋": "sanwenyuan_29zhai", "三问园二十九斋": "sanwenyuan_29zhai",
        "29斋": "sanwenyuan_29zhai", "二十九斋": "sanwenyuan_29zhai",
    }
    for alias, node_id in expected.items():
        assert real_map.resolve_node_id(alias) == node_id, alias


def test_boxue_25_26_zhai_resolve(real_map):
    # 博学园25/26斋：旧官方图“博学园”标注 + 高德“25A/平原26斋”锚点 → 独立节点
    expected = {
        "博学园": "boxueyuan_dorm", "博学园宿舍": "boxueyuan_dorm",
        "博学园25斋": "boxueyuan_25zhai", "博学园二十五斋": "boxueyuan_25zhai",
        "25斋": "boxueyuan_25zhai", "二十五斋": "boxueyuan_25zhai",
        "博学园26斋": "boxueyuan_26zhai", "博学园二十六斋": "boxueyuan_26zhai",
        "26斋": "boxueyuan_26zhai", "二十六斋": "boxueyuan_26zhai",
    }
    for alias, node_id in expected.items():
        assert real_map.resolve_node_id(alias) == node_id, alias


def test_boxue_sanwen_no_cross_alias(real_map):
    # 不允许博学园XX斋与三问园XX斋交叉 alias（官方图28/29误标博学园不采用）
    for text in ("博学园27斋", "博学园28斋", "博学园29斋", "博学园30斋"):
        assert real_map.resolve_node_id(text) is None, text
    for text in ("三问园25斋", "三问园26斋"):
        assert real_map.resolve_node_id(text) is None, text


def test_boxue_nodes_connected_and_far_from_sanwen(real_map):
    node_map = {n.id: n for n in real_map.nodes}
    for node_id in ("boxueyuan_dorm", "boxueyuan_25zhai", "boxueyuan_26zhai"):
        node = node_map[node_id]
        assert "官方" in (node.source or ""), node_id
        assert node.latitude is not None and node.longitude is not None
        for mode in ("walk", "bike"):
            result = plan_route(real_map, "beiyangyuan_south_gate", node_id, mode)
            assert result.status is RouteStatus.COMPUTED, "{} {} 不可达".format(node_id, mode)
    # 两园区相距约590m，路由必须走真实路网，不能被当作邻近
    for mode in ("walk", "bike"):
        result = plan_route(real_map, "博学园25斋", "三问园27斋", mode)
        assert result.status is RouteStatus.COMPUTED
        assert result.total_distance_m > 500, mode
        assert len(result.path) >= 4, mode  # 经过中间路网节点


def test_sanwen_27_28_29_zhai_approx_provenance_and_connected(real_map):
    node_map = {n.id: n for n in real_map.nodes}
    for node_id in ("sanwenyuan_27zhai", "sanwenyuan_28zhai", "sanwenyuan_29zhai"):
        node = node_map[node_id]
        assert "官方" in (node.source or ""), node_id
        assert node.latitude is not None and node.longitude is not None
        for mode in ("walk", "bike"):
            result = plan_route(real_map, "beiyangyuan_south_gate", node_id, mode)
            assert result.status is RouteStatus.COMPUTED, "{} {} 不可达".format(node_id, mode)


def test_long_yuan_and_shu_yuan_absent(real_map):
    # 用户确认：龙园、书园不存在
    for text in ("龙园", "书园"):
        assert real_map.resolve_node_id(text) is None


def test_documented_building_gaps_not_fabricated(real_map):
    # 38/44/45/47 教：本轮已由官方分块高清图标注独立建节点（见 test_p3d_final25_teaching_resolve）
    expected = {"38教": "building_38", "44教": "building_44", "45教": "building_45", "47教": "building_47"}
    for alias, node_id in expected.items():
        assert real_map.resolve_node_id(alias) == node_id
    # 56/57 教：官方图与高德均无位置标注 → 必须保持缺口，不编造
    for text in ("56教", "57教"):
        assert real_map.resolve_node_id(text) is None


def test_p3d_name_fix_new_nodes_connected(real_map):
    new_ids = [
        "zhiyuan_garden", "zhiyuan_garden_17zhai", "zhiyuan_garden_18zhai",
        "zhiyuan_garden_19zhai_add1", "geyuan_3zhai", "chengyuan_7zhai",
        "qiyuan_15zhai", "pingyuan_23zhai",
    ]
    for node_id in new_ids:
        for mode in ("walk", "bike"):
            result = plan_route(real_map, "beiyangyuan_south_gate", node_id, mode)
            assert result.status is RouteStatus.COMPUTED, "{} {} 不可达".format(node_id, mode)
def test_p3d_final25_teaching_resolve(real_map):
    expected = {
        "38教": "building_38", "38教学楼": "building_38", "土木馆": "building_38",
        "44教": "building_44", "44教学楼": "building_44", "44号教学楼": "building_44",
        "45教": "building_45", "45教学楼": "building_45",
        "46教": "building_46", "46教学楼": "building_46", "46号教学楼": "building_46",
        "47教": "building_47", "47教学楼": "building_47", "计算机实验教学中心": "building_47",
    }
    for alias, node_id in expected.items():
        assert real_map.resolve_node_id(alias) == node_id, alias


def test_p3d_final25_dorm_resolve(real_map):
    expected = {
        "格园1斋": "geyuan_1zhai", "格园一斋": "geyuan_1zhai", "1斋": "geyuan_1zhai",
        "格园2斋": "geyuan_2zhai", "格园二斋": "geyuan_2zhai", "2斋": "geyuan_2zhai",
        "知园5斋": "zhiyuan_5zhai", "知园五斋": "zhiyuan_5zhai", "5斋": "zhiyuan_5zhai",
        "诚园8斋": "chengyuan_8zhai", "诚园八斋": "chengyuan_8zhai", "8斋": "chengyuan_8zhai",
        "修园11斋": "xiuyuan_11zhai", "修园十一斋": "xiuyuan_11zhai", "11斋": "xiuyuan_11zhai",
        "修园12斋": "xiuyuan_12zhai", "修园十二斋": "xiuyuan_12zhai", "12斋": "xiuyuan_12zhai",
        "齐园16斋": "qiyuan_16zhai", "齐园十六斋": "qiyuan_16zhai", "16斋": "qiyuan_16zhai",
        "治园19斋": "zhiyuan_garden_19zhai", "治园十九斋": "zhiyuan_garden_19zhai", "19斋": "zhiyuan_garden_19zhai",
        "治园20斋": "zhiyuan_garden_20zhai", "治园二十斋": "zhiyuan_garden_20zhai", "20斋": "zhiyuan_garden_20zhai",
        "平园22斋": "pingyuan_22zhai", "平园二十二斋": "pingyuan_22zhai", "22斋": "pingyuan_22zhai",
        "平园24斋": "pingyuan_24zhai", "平园二十四斋": "pingyuan_24zhai", "24斋": "pingyuan_24zhai",
    }
    for alias, node_id in expected.items():
        assert real_map.resolve_node_id(alias) == node_id, alias


def test_p3d_final25_gaps_still_not_fabricated(real_map):
    # 25/26 斋已按用户确认归属博学园（见 test_boxue_25_26_zhai_resolve）；
    # “三问园25/26斋”是不存在名称，不解析
    for text in ("三问园25斋", "三问园26斋"):
        assert real_map.resolve_node_id(text) is None, text
    # 56/57 教：无位置标注 → 不编造
    for text in ("56教", "56教学楼", "57教", "57教学楼"):
        assert real_map.resolve_node_id(text) is None, text
    # 篮球场 / 排球场：无可靠 POI 与官方图标注 → 不编造
    for text in ("篮球场", "排球场"):
        assert real_map.resolve_node_id(text) is None, text


def test_p3d_final25_new_nodes_connected(real_map):
    new_ids = [
        "building_38", "building_44", "building_45", "building_46", "building_47",
        "geyuan_1zhai", "geyuan_2zhai", "zhiyuan_5zhai", "chengyuan_8zhai",
        "xiuyuan_11zhai", "xiuyuan_12zhai", "qiyuan_16zhai",
        "zhiyuan_garden_19zhai", "zhiyuan_garden_20zhai",
        "pingyuan_22zhai", "pingyuan_24zhai",
    ]
    for node_id in new_ids:
        for mode in ("walk", "bike"):
            result = plan_route(real_map, "beiyangyuan_south_gate", node_id, mode)
            assert result.status is RouteStatus.COMPUTED, "{} {} 不可达".format(node_id, mode)


def test_p3d_final25_approx_provenance(real_map):
    node_map = {n.id: n for n in real_map.nodes}
    approx_ids = [
        "building_38", "building_44", "building_45", "building_46", "building_47",
        "geyuan_1zhai", "geyuan_2zhai", "zhiyuan_5zhai", "chengyuan_8zhai",
        "xiuyuan_11zhai", "xiuyuan_12zhai", "qiyuan_16zhai",
        "zhiyuan_garden_19zhai", "zhiyuan_garden_20zhai",
        "pingyuan_22zhai", "pingyuan_24zhai",
    ]
    for node_id in approx_ids:
        node = node_map[node_id]
        source = node.source or ""
        assert "官方" in source, node_id


def test_building_31_has_no_qiyuan_alias(real_map):
    """P3e 地点验收：齐园与31教无任何从属/命名关系，禁止“齐园31教学楼”别名。"""
    node = next(n for n in real_map.nodes if n.id == "building_31")
    assert node.name == "31教"
    assert "齐园31教学楼" not in node.aliases
    assert all("齐园" not in alias for alias in node.aliases)
    assert all("齐园" not in node.name for name in [node.name])
    # “齐园31教学楼”不再是合法地点
    assert real_map.resolve_node_id("齐园31教学楼") is None
    # 31教 canonical 名称与常用别名仍可解析
    assert real_map.resolve_node_id("31教") == "building_31"
    assert real_map.resolve_node_id("31楼") == "building_31"
    assert real_map.resolve_node_id("31教学楼") == "building_31"
    # 全图审计：不存在“齐园”+数字教学楼 的错误拼接名称/别名
    for n in real_map.nodes:
        for alias in (n.name,) + tuple(n.aliases):
            assert "齐园教" not in alias
            assert "齐园31" not in alias
