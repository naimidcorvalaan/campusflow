"""P3b 开发采集脚本：用高德 Web API 采集北洋园真实校园地图数据。

仅开发阶段使用；只读取 .env.dev 中的 AMAP_API_KEY。
运行时（src/）不得 import 本脚本，最终 CampusFlow 运行时不得请求高德。

产出：data/beiyangyuan_map.json（静态本地数据，供 P3a loader 直接加载寻路）。

第二轮（P3b-2）扩充：
- 节点从 11 个扩到约 49 个（教学楼、学院、宿舍、食堂、快递点、体育设施、
  服务设施、地标/道路连接节点）；
- 边按最近邻候选生成，全部用高德步行/骑行方向接口核验；
- 对“路线距离明显小于直线距离”的异常结果拒绝保留（可验证原则）；
- 同名前缀 POI（如留园 vs 留园餐厅、两个菜鸟驿站、多个北洋超市）
  用 POI 类型提示与中心点提示消歧。

用法：
  python scripts/collect_beiyangyuan_map.py --dry-run   # 只检索 POI 坐标，不调方向 API
  python scripts/collect_beiyangyuan_map.py             # 完整采集并写 data/beiyangyuan_map.json
"""
import argparse
import json
import math
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

ROOT = Path(__file__).resolve().parent.parent
ENV_DEV = ROOT / ".env.dev"
OUTPUT = ROOT / "data" / "beiyangyuan_map.json"
SCHEMA_VERSION = "p3.campus-map.v1"

AMAP_PLACE_TEXT = "https://restapi.amap.com/v3/place/text"
AMAP_WALK = "https://restapi.amap.com/v3/direction/walking"
AMAP_BIKE = "https://restapi.amap.com/v4/direction/bicycling"

# 现有 data/beiyangyuan_locations.json 的核心地点（id 保持稳定）。
CORE_NODES = [
    ("main_building", "1895行政大楼"),
    ("qiushui_hall", "求实会堂"),
    ("zhengdong_library", "郑东图书馆"),
    ("datong_student_center", "大通学生中心"),
    ("sports_hall", "北洋园综合体育馆"),
    ("xuesi_cafeteria", "北洋园学四食堂"),
    ("tailei_square", "太雷广场"),
    ("xuanhuai_square", "宣怀广场"),
    ("building_50", "50号教学楼"),
]

# 必要路口 / 交通连接点（可路由的真实 POI）。
EXTRA_NODES = [
    ("beiyangyuan_south_gate", "北洋园校区南门"),
    ("beiyangyuan_metro_station", "北洋园校区地铁站"),
]

# P3b-2 扩充节点：(node_id, 检索关键词, POI 类型提示, 中心点提示(纬度,经度))
EXPANDED_NODES = [
    # 教学楼
    ("building_31", "齐园31教学楼", "学校", None),
    ("building_34", "34教学楼", "学校", None),
    ("building_36", "36教学楼", "学校", None),
    ("building_43", "43教学楼", "学校", None),
    ("building_54", "54教学楼", "学校", None),
    ("building_55", "55教学楼", "学校", None),
    ("building_58", "58教学楼", "学校", None),
    ("building_59", "第五十九教学楼", "学校", None),
    # 学院 / 学部
    ("college_of_science", "理学院", "高等院校", None),
    ("college_of_chemical_engineering", "化工学院", "高等院校", None),
    ("college_of_mechanical_engineering", "机械工程学院", "高等院校", None),
    ("school_of_math", "数学学院", "高等院校", None),
    ("school_of_computing", "智能与计算学部", "高等院校", None),
    # 宿舍区
    ("pingyuan_dorm", "北洋园校区平园", "宿舍", None),
    ("chengyuan_dorm", "北洋园校区诚园", "宿舍", None),
    ("zhiyuan_dorm", "北洋园校区知园", "宿舍", None),
    ("geyuan_dorm", "北洋园校区格园", "宿舍", None),
    ("sanwenyuan_dorm", "北洋园校区三问园", "宿舍", None),
    # 食堂
    ("mei_yuan_canteen", "梅园餐厅", "餐饮", None),
    ("lan_yuan_canteen", "兰园餐厅", "餐饮", None),
    ("xuesan_canteen", "第三学生食堂", "餐饮", None),
    ("xuewu_canteen", "第五学生食堂", "餐饮", None),
    ("haitang_canteen", "海棠餐厅", "餐饮", None),
    ("juyuan_canteen", "菊园餐厅", "餐饮", None),
    # 快递点
    ("beiyangyuan_north_cainiao", "北洋园 菜鸟驿站 天津大学", "物流", (39.0007, 117.3134)),
    ("beiyangyuan_south_cainiao", "北洋园 菜鸟驿站 天津大学", "物流", (38.9925, 117.3123)),
    # 体育设施
    ("beiyang_stadium", "北洋园校区体育场", "运动", None),
    ("football_field", "北洋园 足球场 天津大学", "运动", None),
    ("swimming_pool", "北洋园校区 游泳馆", "运动", None),
    ("sports_park", "天津大学 体育公园", "公园", None),
    # 服务设施
    ("campus_hospital", "北洋园校医院", "医疗", None),
    ("service_hall", "天津大学 综合服务大厅", "学校", None),
    ("beiyang_supermarket_pingyuan", "北洋超市", "超市", (39.0001, 117.3122)),
    # 地标 / 道路连接节点
    ("beiyang_plaza", "北洋广场", "广场", None),
    ("shutian_square", "书田广场", "广场", None),
    ("sanwen_bridge", "三问桥", None, None),
    ("beiyangyuan_north_parking_entrance", "北洋园校区 停车场出入口", "停车场出入口", (39.0033, 117.3122)),
    ("tongde_yazheng_intersection", "同德路与雅正路交叉口", None, None),
]

ALL_NODES = CORE_NODES + EXTRA_NODES + EXPANDED_NODES

# 北洋园校区近似包围盒（排除跨校区/外区域 POI，如卫津路校区、彩得体育馆、红桥区北洋园）。
CAMPUS_BBOX = ((38.985, 39.015), (117.295, 117.335))

DEFAULT_NEIGHBORS = 4
DEFAULT_MAX_EDGE_M = 1500
DEFAULT_MAX_EDGE_AFTER_VERIFY_M = 2500
REQUEST_DELAY_SECONDS = 0.4


def load_amap_key() -> str:
    """只从 .env.dev 读取 AMAP_API_KEY；不读取 .env，不读环境变量。"""
    if not ENV_DEV.exists():
        raise SystemExit(
            "缺少 {}，请先在 .env.dev 配置 AMAP_API_KEY。".format(ENV_DEV)
        )
    for line in ENV_DEV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("AMAP_API_KEY="):
            key = line.split("=", 1)[1].strip()
            if key:
                return key
    raise SystemExit(".env.dev 中未找到 AMAP_API_KEY。")


def _get_json(url: str, params: Dict[str, str]) -> dict:
    """请求高德接口；任何异常都被转换为不含 URL/参数/Key 的安全信息。"""
    try:
        response = requests.get(url, params=params, timeout=25)
    except requests.RequestException:
        raise RuntimeError("高德接口请求失败（网络或超时）") from None
    try:
        response.raise_for_status()
    except requests.HTTPError:
        raise RuntimeError("高德接口返回 HTTP 错误：{}".format(response.status_code)) from None
    try:
        return response.json()
    except ValueError:
        raise RuntimeError("高德接口返回非 JSON 内容") from None


def search_poi(
    key: str,
    keyword: str,
    type_hint: Optional[str] = None,
    center_hint: Optional[Tuple[float, float]] = None,
    retries: int = 2,
) -> Optional[dict]:
    """用 place/text 检索 POI；返回最匹配的 POI（name/location 等）。"""
    params = {
        "key": key,
        "keywords": keyword,
        "city": "天津",
        "citylimit": "true",
        "offset": "20",
        "extensions": "base",
    }
    for attempt in range(retries + 1):
        try:
            data = _get_json(AMAP_PLACE_TEXT, params)
        except RuntimeError:
            time.sleep(1.0)
            continue
        if data.get("status") != "1":
            info = data.get("info") or "未知错误"
            if "10009" in str(data.get("infocode")):
                raise RuntimeError("高德 Key 类型或权限不正确（infocode=10009）")
            time.sleep(1.0)
            continue
        pois = data.get("pois") or []
        chosen = _pick_poi(pois, keyword, type_hint, center_hint)
        if chosen is not None:
            return chosen
        time.sleep(1.0)
    return None


def _pick_poi(
    pois: List[dict],
    keyword: str,
    type_hint: Optional[str],
    center_hint: Optional[Tuple[float, float]],
) -> Optional[dict]:
    """评分选取：关键词命中 > 北洋园/天大标记 > 津南 > POI 类型提示，再按中心点距离近者优先。"""
    candidates = []
    for poi in pois:
        location = poi.get("location") or ""
        if "," not in location:
            continue
        lng_text, lat_text = location.split(",", 1)
        try:
            latitude, longitude = float(lat_text), float(lng_text)
        except ValueError:
            continue
        if not _in_campus_bbox(latitude, longitude):
            continue
        name = poi.get("name") or ""
        poi_type = poi.get("type") or ""
        score = 0
        tokens = [token for token in keyword.split()] if keyword else []
        if any(token in name for token in tokens):
            score += 3
        if any(token in name for token in ("北洋园", "天大", "天津大学")):
            score += 2
        if "津南" in (poi.get("adname") or ""):
            score += 1
        if type_hint and type_hint in poi_type:
            score += 2
        if center_hint is not None:
            meters = haversine_m((latitude, longitude), center_hint)
        else:
            meters = 0.0
        candidates.append((score, meters, poi))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return candidates[0][2]


def _in_campus_bbox(latitude: float, longitude: float) -> bool:
    lat_lo, lat_hi = CAMPUS_BBOX[0]
    lng_lo, lng_hi = CAMPUS_BBOX[1]
    return lat_lo <= latitude <= lat_hi and lng_lo <= longitude <= lng_hi


def poi_coords(poi: dict) -> Tuple[float, float]:
    """location 为 "lng,lat"。返回 (latitude, longitude)。"""
    lng_text, lat_text = poi["location"].split(",", 1)
    return float(lat_text), float(lng_text)


def haversine_m(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    """近似球面距离，仅用于候选边排序（真实距离以高德方向 API 为准）。"""
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2.0 * 6371000.0 * math.asin(math.sqrt(h))


def resolve_nodes(key: str) -> Dict[str, dict]:
    results = {}
    for item in ALL_NODES:
        node_id, keyword = item[0], item[1]
        type_hint = item[2] if len(item) > 2 else None
        center_hint = item[3] if len(item) > 3 else None
        poi = search_poi(key, keyword, type_hint, center_hint)
        if poi is None:
            print("[WARN] 未检索到 POI：{} ({})".format(node_id, keyword))
            continue
        latitude, longitude = poi_coords(poi)
        display_name = poi.get("name") or keyword
        if node_id == "beiyangyuan_north_cainiao":
            display_name = "菜鸟驿站(北洋园北区)"
        elif node_id == "beiyangyuan_south_cainiao":
            display_name = "菜鸟驿站(北洋园南区)"
        results[node_id] = {
            "id": node_id,
            "name": display_name,
            "aliases": _aliases_for(node_id, keyword),
            "category": _category_for(node_id),
            "latitude": latitude,
            "longitude": longitude,
            "source": "高德 POI 检索（id={}，关键词={}）".format(poi.get("id"), keyword),
        }
        print(
            "[POI] {} {} ({:.6f}, {:.6f})".format(
                node_id, results[node_id]["name"], latitude, longitude
            )
        )
        time.sleep(REQUEST_DELAY_SECONDS)
    return results


def _aliases_for(node_id: str, keyword: str) -> List[str]:
    aliases = []
    if node_id == "beiyangyuan_south_gate":
        aliases = ["南门", "北洋园南门"]
    elif node_id == "beiyangyuan_metro_station":
        aliases = ["地铁站", "北洋园地铁站"]
    elif node_id == "main_building":
        aliases = ["主楼", "校区主楼", "行政楼"]
    elif node_id == "qiushui_hall":
        aliases = ["会堂", "求实会堂"]
    elif node_id == "zhengdong_library":
        aliases = ["图书馆", "郑东图书馆"]
    elif node_id == "datong_student_center":
        aliases = ["学生中心", "大通中心", "大通学生中心"]
    elif node_id == "sports_hall":
        aliases = ["体育馆", "综合体育馆"]
    elif node_id == "xuesi_cafeteria":
        aliases = ["学四", "学四食堂", "竹园餐厅"]
    elif node_id == "tailei_square":
        aliases = ["太雷广场"]
    elif node_id == "xuanhuai_square":
        aliases = ["宣怀广场", "宣怀"]
    elif node_id == "building_50":
        aliases = ["50楼", "50号教学楼", "化工楼"]
    elif node_id == "building_31":
        aliases = ["31楼", "31教学楼", "齐园31教学楼"]
    elif node_id == "building_34":
        aliases = ["34楼", "34教学楼"]
    elif node_id == "building_36":
        aliases = ["36楼", "36教学楼"]
    elif node_id == "building_43":
        aliases = ["43楼", "43教学楼"]
    elif node_id == "building_54":
        aliases = ["54楼", "54教学楼"]
    elif node_id == "building_55":
        aliases = ["55楼", "55教学楼"]
    elif node_id == "building_58":
        aliases = ["58楼", "58教学楼"]
    elif node_id == "building_59":
        aliases = ["59楼", "59教学楼", "第五十九教学楼"]
    elif node_id == "college_of_science":
        aliases = ["理学院"]
    elif node_id == "college_of_chemical_engineering":
        aliases = ["化工学院", "化工学院楼"]
    elif node_id == "college_of_mechanical_engineering":
        aliases = ["机械学院", "机械工程学院"]
    elif node_id == "school_of_math":
        aliases = ["数学学院"]
    elif node_id == "school_of_computing":
        aliases = ["智能与计算学部", "智算学部", "计算机学院"]
    elif node_id == "pingyuan_dorm":
        aliases = ["平园", "平园宿舍"]
    elif node_id == "chengyuan_dorm":
        aliases = ["诚园", "诚园宿舍"]
    elif node_id == "zhiyuan_dorm":
        aliases = ["知园", "知园宿舍"]
    elif node_id == "geyuan_dorm":
        aliases = ["格园", "格园宿舍"]
    elif node_id == "sanwenyuan_dorm":
        aliases = ["三问园", "三问园宿舍"]
    elif node_id == "mei_yuan_canteen":
        aliases = ["学一", "学一食堂", "梅园食堂", "梅园餐厅"]
    elif node_id == "lan_yuan_canteen":
        aliases = ["学二", "学二食堂", "兰园食堂", "兰园餐厅"]
    elif node_id == "xuesan_canteen":
        aliases = ["学三", "学三食堂"]
    elif node_id == "xuewu_canteen":
        aliases = ["学五", "学五食堂"]
    elif node_id == "haitang_canteen":
        aliases = ["海棠餐厅", "海棠食堂"]
    elif node_id == "juyuan_canteen":
        aliases = ["菊园餐厅", "清真食堂"]
    elif node_id == "beiyangyuan_north_cainiao":
        aliases = ["北区菜鸟驿站", "菜鸟驿站"]
    elif node_id == "beiyangyuan_south_cainiao":
        aliases = ["南区菜鸟驿站"]
    elif node_id == "beiyang_stadium":
        aliases = ["体育场", "操场", "田径场"]
    elif node_id == "football_field":
        aliases = ["足球场"]
    elif node_id == "swimming_pool":
        aliases = ["游泳馆"]
    elif node_id == "sports_park":
        aliases = ["体育公园"]
    elif node_id == "campus_hospital":
        aliases = ["校医院", "医院"]
    elif node_id == "service_hall":
        aliases = ["综合服务大厅"]
    elif node_id == "beiyang_supermarket_pingyuan":
        aliases = ["北洋超市", "超市"]
    elif node_id == "beiyang_plaza":
        aliases = ["北洋广场"]
    elif node_id == "shutian_square":
        aliases = ["书田广场"]
    elif node_id == "sanwen_bridge":
        aliases = ["三问桥", "桥"]
    elif node_id == "beiyangyuan_north_parking_entrance":
        aliases = ["北区停车场入口", "停车场出入口"]
    elif node_id == "tongde_yazheng_intersection":
        aliases = ["同德路与雅正路交叉口", "同德路雅正路口"]
    return aliases


def _category_for(node_id: str) -> str:
    if node_id == "beiyangyuan_metro_station":
        return "transit"
    if "_gate" in node_id or "parking_entrance" in node_id:
        return "gate"
    if node_id in ("main_building", "qiushui_hall"):
        return "teaching"
    if "library" in node_id:
        return "library"
    if node_id == "datong_student_center":
        return "student_service"
    if "sports" in node_id or "stadium" in node_id or "football" in node_id or "swimming" in node_id or "pool" in node_id:
        return "sports"
    if "canteen" in node_id or "cafeteria" in node_id:
        return "dining"
    if "dorm" in node_id:
        return "dormitory"
    if "cainiao" in node_id:
        return "logistics"
    if "hospital" in node_id:
        return "medical"
    if "supermarket" in node_id or "service_hall" in node_id:
        return "service"
    if "plaza" in node_id or "square" in node_id or "bridge" in node_id or "intersection" in node_id:
        return "landmark"
    if "college" in node_id or "school" in node_id or "building" in node_id:
        return "teaching"
    return "building"


def build_candidate_edges(
    nodes: Dict[str, dict],
    neighbors: int = DEFAULT_NEIGHBORS,
    max_m: int = DEFAULT_MAX_EDGE_M,
) -> List[Tuple[str, str]]:
    """每个节点连接最近 k 个邻居（按近似距离），得到候选无向边。"""
    node_ids = list(nodes.keys())
    pairs = set()
    for node_id in node_ids:
        position = (nodes[node_id]["latitude"], nodes[node_id]["longitude"])
        distances = []
        for other in node_ids:
            if other == node_id:
                continue
            other_position = (nodes[other]["latitude"], nodes[other]["longitude"])
            meters = haversine_m(position, other_position)
            if meters <= max_m:
                distances.append((meters, other))
        distances.sort()
        for _, other in distances[:neighbors]:
            pairs.add(tuple(sorted((node_id, other))))
    return sorted(pairs)


def verify_walk(key: str, origin: str, destination: str) -> Optional[int]:
    """高德 v3 步行路线 API；返回距离米数，失败返回 None。"""
    params = {
        "key": key,
        "origin": origin,
        "destination": destination,
    }
    try:
        data = _get_json(AMAP_WALK, params)
    except RuntimeError:
        return None
    if data.get("status") != "1":
        return None
    paths = (data.get("route") or {}).get("paths") or []
    if not paths:
        return None
    distance = paths[0].get("distance")
    if distance is None:
        return None
    return int(float(distance))


def verify_bike(key: str, origin: str, destination: str) -> Optional[int]:
    """高德 v4 骑行路线 API；返回距离米数，失败返回 None。"""
    params = {
        "key": key,
        "origin": origin,
        "destination": destination,
    }
    try:
        data = _get_json(AMAP_BIKE, params)
    except RuntimeError:
        return None
    if data.get("errcode") != 0 and data.get("status") != "1":
        return None
    paths = ((data.get("data") or {}).get("paths")) or []
    if not paths:
        return None
    distance = paths[0].get("distance")
    if distance is None:
        return None
    return int(float(distance))


def _to_lnglat(node: dict) -> str:
    return "{:.7f},{:.7f}".format(node["longitude"], node["latitude"])


def _route_plausible(node_a: dict, node_b: dict, walk_distance: int) -> bool:
    """路线距离不能明显小于两点直线距离；否则视为高德近距离接口异常。"""
    straight = haversine_m(
        (node_a["latitude"], node_a["longitude"]),
        (node_b["latitude"], node_b["longitude"]),
    )
    if straight <= 10:
        return True
    if walk_distance < 0.5 * straight:
        return False
    return True


def verify_edges(
    key: str, nodes: Dict[str, dict], pairs: List[Tuple[str, str]]
) -> List[dict]:
    edges = []
    for index, (from_id, to_id) in enumerate(pairs):
        origin = _to_lnglat(nodes[from_id])
        destination = _to_lnglat(nodes[to_id])
        walk_distance = verify_walk(key, origin, destination)
        time.sleep(REQUEST_DELAY_SECONDS)
        if walk_distance is None or walk_distance <= 0:
            print("[SKIP] 步行不可达或失败：{} <-> {}".format(from_id, to_id))
            continue
        if not _route_plausible(nodes[from_id], nodes[to_id], walk_distance):
            print(
                "[SKIP] 距离异常（高德返回远小于直线距离）：{} <-> {} {}m".format(
                    from_id, to_id, walk_distance
                )
            )
            continue
        modes = ["walk"]
        distance_m = walk_distance
        bike_distance = verify_bike(key, origin, destination)
        time.sleep(REQUEST_DELAY_SECONDS)
        if bike_distance is not None and bike_distance > 0:
            straight = haversine_m(
                (nodes[from_id]["latitude"], nodes[from_id]["longitude"]),
                (nodes[to_id]["latitude"], nodes[to_id]["longitude"]),
            )
            if straight <= 10 or bike_distance >= 0.5 * straight:
                modes.append("bike")
        modes.sort()
        edges.append({
            "from": from_id,
            "to": to_id,
            "distance_m": distance_m,
            "modes": modes,
            "bidirectional": True,
            "source": "高德步行路线 API v3（origin={} destination={}）distance_m={}；骑行 v4 {}={}".format(
                origin, destination, distance_m,
                "可用" if "bike" in modes else "不可用",
                bike_distance if bike_distance is not None else "-",
            ),
        })
        print(
            "[EDGE] {} <-> {} {}m modes={} ({} / {})".format(
                from_id, to_id, distance_m, ",".join(modes), index + 1, len(pairs)
            )
        )
    return edges


def ensure_connectivity(
    key: str, nodes: Dict[str, dict], edges: List[dict]
) -> List[dict]:
    """若边图不连通，按最近跨分量对补边（仍以高德结果为准）。

    已尝试但失败的跨分量候选对被记录，避免死循环反复重试同一对。
    """
    node_ids = set(nodes.keys())
    tried = set()
    while True:
        components = _components(node_ids, edges)
        if len(components) <= 1:
            break
        best = None
        for component in components:
            for node_id in component:
                position = (nodes[node_id]["latitude"], nodes[node_id]["longitude"])
                for other in node_ids - component:
                    pair = tuple(sorted((node_id, other)))
                    if pair in tried:
                        continue
                    other_position = (
                        nodes[other]["latitude"],
                        nodes[other]["longitude"],
                    )
                    meters = haversine_m(position, other_position)
                    if best is None or meters < best[0]:
                        best = (meters, node_id, other)
        if best is None or best[0] > DEFAULT_MAX_EDGE_AFTER_VERIFY_M:
            break
        _, from_id, to_id = best
        tried.add(tuple(sorted((from_id, to_id))))
        origin = _to_lnglat(nodes[from_id])
        destination = _to_lnglat(nodes[to_id])
        walk_distance = verify_walk(key, origin, destination)
        time.sleep(REQUEST_DELAY_SECONDS)
        if walk_distance is None or walk_distance <= 0:
            continue
        if not _route_plausible(nodes[from_id], nodes[to_id], walk_distance):
            continue
        bike_distance = verify_bike(key, origin, destination)
        time.sleep(REQUEST_DELAY_SECONDS)
        modes = ["walk"]
        if bike_distance is not None and bike_distance > 0:
            straight = haversine_m(
                (nodes[from_id]["latitude"], nodes[from_id]["longitude"]),
                (nodes[to_id]["latitude"], nodes[to_id]["longitude"]),
            )
            if straight <= 10 or bike_distance >= 0.5 * straight:
                modes.append("bike")
        modes.sort()
        edges.append({
            "from": from_id,
            "to": to_id,
            "distance_m": walk_distance,
            "modes": modes,
            "bidirectional": True,
            "source": "高德步行路线 API v3 连通性补边（origin={} destination={}）distance_m={}".format(
                origin, destination, walk_distance,
            ),
        })
        print(
            "[BRIDGE] {} <-> {} {}m modes={}".format(
                from_id, to_id, walk_distance, ",".join(modes),
            )
        )
    return edges


def _components(node_ids: set, edges: List[dict]) -> List[set]:
    adjacency = {node_id: set() for node_id in node_ids}
    for edge in edges:
        adjacency[edge["from"]].add(edge["to"])
        adjacency[edge["to"]].add(edge["from"])
    visited = set()
    components = []
    for node_id in node_ids:
        if node_id in visited:
            continue
        stack = [node_id]
        component = set()
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            component.add(current)
            stack.extend(adjacency[current] - visited)
        components.append(component)
    return components


def write_map(nodes: Dict[str, dict], edges: List[dict], notes: List[str]) -> None:
    payload = {
        "metadata": {
            "schema_version": SCHEMA_VERSION,
            "campus": "天津大学北洋园校区",
            "data_status": "routable",
            "provenance": "real_map",
            "source_note": "高德 Web API 开发阶段采集；运行时静态加载，不依赖高德",
            "note": "；".join(notes),
        },
        "nodes": sorted(nodes.values(), key=lambda item: item["id"]),
        "edges": sorted(edges, key=lambda item: (item["from"], item["to"])),
    }
    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("\n[OK] 已写入 {}".format(OUTPUT))
    print("节点 {} 个，边 {} 条".format(len(payload["nodes"]), len(payload["edges"])))


def main() -> None:
    parser = argparse.ArgumentParser(description="采集北洋园真实地图数据（高德）")
    parser.add_argument("--dry-run", action="store_true", help="只检索 POI 坐标，不调用方向 API")
    parser.add_argument("--neighbors", type=int, default=DEFAULT_NEIGHBORS)
    parser.add_argument("--max-edge-m", type=int, default=DEFAULT_MAX_EDGE_M)
    args = parser.parse_args()

    key = load_amap_key()
    nodes = resolve_nodes(key)
    if not nodes:
        raise SystemExit("未能解析任何节点坐标，终止。")
    missing = [item[0] for item in ALL_NODES if item[0] not in nodes]
    if missing:
        print("[WARN] 以下节点未解析（留缺口，不编造）：{}".format(", ".join(missing)))
    if args.dry_run:
        print("\n[dry-run] 节点 {} 个；候选边（未调用方向 API）：".format(len(nodes)))
        for pair in build_candidate_edges(nodes, args.neighbors, args.max_edge_m):
            print("  {} <-> {}".format(pair[0], pair[1]))
        return

    pairs = build_candidate_edges(nodes, args.neighbors, args.max_edge_m)
    edges = verify_edges(key, nodes, pairs)
    edges = ensure_connectivity(key, nodes, edges)
    notes = [
        "距离来自高德步行路线 API（v3/direction/walking），单位米；骑行可用性来自高德骑行路线 API（v4/direction/bicycling）。",
        "边均为双向，按单方向高德路线核验后对称处理。",
        "对高德返回明显小于直线距离的异常边按可验证原则拒绝保留。",
        "采集时间为开发阶段，最终运行时静态加载本地 JSON，不依赖高德。",
    ]
    write_map(nodes, edges, notes)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print("[FAIL] {}".format(exc))
        raise SystemExit(1)
