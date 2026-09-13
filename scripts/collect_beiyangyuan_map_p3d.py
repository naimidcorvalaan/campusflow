"""P3d 开发采集脚本：在现有 49 节点 / 118 边基础上增量扩充北洋园真实地图。

仅开发阶段使用；只读取 .env.dev 中的 AMAP_API_KEY。
运行时（src/）不得 import 本脚本，最终 CampusFlow 运行时不得请求高德。

原则：
- 保留现有节点与边，不重新核验既有 118 条边；
- 新增节点全部来自高德 POI 检索，查不到可靠结果就留缺口，不编造；
- 新增边只连接至少一端为新节点的候选对，并逐个经高德步行/骑行方向 API 核验；
- 拒绝“路线距离明显小于直线距离”的异常结果；
- 新增/补充别名必须可验证且不冲突（loader 校验会兜底）。

用法：
  python scripts/collect_beiyangyuan_map_p3d.py --dry-run   # 只检索 POI，不调方向 API
  python scripts/collect_beiyangyuan_map_p3d.py             # 增量采集并写回 data/beiyangyuan_map.json
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

CENTER = (39.0, 117.314)  # 北洋园中心近似（用于过滤无关 POI）
MAX_CENTER_M = 2500
DEFAULT_NEIGHBORS = 5
DEFAULT_MAX_EDGE_M = 1500
REQUEST_DELAY_SECONDS = 0.4


# (node_id, 检索关键词, 显示名, category, aliases, tokens, exact, exclude, proximity_ref)
# exact: 优先精确命中的 POI 名；exclude: 拒绝包含的子串；proximity_ref: 就近参考节点（如所属园区）
NEW_NODES = [
    # 教学楼（高德可验证的）
    ("building_33", "天津大学北洋园校区33教学楼", "天津大学北洋园校区33教学楼", "teaching",
     ["33楼", "33教学楼", "33教"], ["33"], [], ["停车场", "公交站", "地铁站"], None),
    ("building_41", "天津大学北洋园校区41教学楼", "天津大学北洋园校区41教学楼", "teaching",
     ["41楼", "41教学楼", "41教"], ["41"], [], ["停车场", "公交站", "地铁站"], None),
    ("building_42", "天津大学北洋园校区42教学楼", "天津大学北洋园校区42教学楼", "teaching",
     ["42楼", "42教学楼", "42教"], ["42"], [], ["停车场", "公交站", "地铁站"], None),
    ("building_48", "天津大学北洋园校区48教学楼", "天津大学北洋园校区48教学楼", "teaching",
     ["48楼", "48教学楼", "48教"], ["48"], [], ["停车场", "公交站", "地铁站"], None),
    ("building_49", "天津大学北洋园校区49教学楼", "天津大学北洋园校区49教学楼", "teaching",
     ["49楼", "49教学楼", "49教", "物理实验中心"], ["49"], [], ["停车场", "公交站", "地铁站"], None),
    ("building_51", "天津大学北洋园校区51教学楼", "天津大学北洋园校区51教学楼", "teaching",
     ["51楼", "51教学楼", "51教"], ["51"], [], ["停车场", "公交站", "地铁站"], None),
    ("building_52", "天津大学北洋园校区52教学楼", "天津大学北洋园校区52教学楼", "teaching",
     ["52楼", "52教学楼", "52教"], ["52"], [], ["停车场", "公交站", "地铁站"], None),
    ("building_53", "天津大学北洋园校区53教学楼", "天津大学北洋园校区53教学楼", "teaching",
     ["53楼", "53教学楼", "53教"], ["53"], [], ["停车场", "公交站", "地铁站"], None),
    # 学院 / 科研
    ("college_of_materials", "天津大学北洋园校区材料科学与工程学院", "天津大学北洋园校区材料科学与工程学院", "teaching",
     ["材料学院", "材料科学与工程学院"], ["材料科学与工程学院"], ["天津大学北洋园校区材料科学与工程学院"], ["停车场"], None),
    ("college_of_civil_engineering", "天津大学北洋园校区建筑工程学院", "天津大学北洋园校区建筑工程学院", "teaching",
     ["建工学院", "建筑工程学院"], ["建筑工程学院"], ["天津大学北洋园校区建筑工程学院"], ["停车场"], None),
    ("college_of_education", "天津大学北洋园校区教育学院", "天津大学北洋园校区教育学院", "teaching",
     ["教育学院"], ["教育学院"], ["天津大学北洋园校区教育学院"], ["停车场"], None),
    ("college_of_marxism", "天津大学北洋园校区马克思主义学院", "天津大学北洋园校区马克思主义学院", "teaching",
     ["马克思主义学院", "马院"], ["马克思主义学院"], ["天津大学北洋园校区马克思主义学院"], ["停车场"], None),
    ("college_of_foreign_languages", "天津大学北洋园校区外国语学院", "天津大学北洋园校区外国语学院", "teaching",
     ["外国语学院", "外语学院"], ["外国语学院"], ["天津大学北洋园校区外国语学院"], ["停车场"], None),
    ("college_of_international_engineers", "天津大学北洋园校区国际工程师学院", "天津大学北洋园校区国际工程师学院", "teaching",
     ["国际工程师学院"], ["国际工程师学院"], ["天津大学北洋园校区国际工程师学院"], ["停车场"], None),
    ("water_engineering_lab", "天津大学北洋园校区水利馆", "天津大学北洋园校区水利馆", "teaching",
     ["水利馆", "水利实验室"], ["水利馆"], ["天津大学北洋园校区水利馆"], ["停车场"], None),
    # 宿舍区 / 斋号
    ("zhengyuan_dorm", "天津大学北洋园校区正园", "天津大学北洋园校区正园", "dormitory",
     ["正园", "正园宿舍"], ["正园"], ["天津大学北洋园校区正园"], ["餐厅", "咖啡", "店"], None),
    ("qiyuan_dorm", "天津大学北洋园校区齐园", "天津大学北洋园校区齐园", "dormitory",
     ["齐园", "齐园宿舍"], ["齐园"], ["齐园"], ["咖啡", "零食", "餐吧", "店"], None),
    ("liuyuan_dorm", "天津大学北洋园校区留园", "天津大学北洋园校区留园", "dormitory",
     ["留园", "留园宿舍"], ["留园"], ["天津大学北洋园校区留园"], ["餐厅"], None),
    ("pingyuan_21zhai_a", "天津大学(北洋园校区)平园21斋A座", "天津大学北洋园校区平园21斋A座", "dormitory",
     ["平园21斋A座", "平园21斋", "21斋A座"], ["平园21斋A座"], [], ["停车场"], "pingyuan_dorm"),
    ("chengyuan_6zhai", "天津大学北洋园校区诚园6斋", "天津大学北洋园校区诚园6斋", "dormitory",
     ["诚园6斋", "诚园六斋"], ["诚园6斋"], [], [], "chengyuan_dorm"),
    ("sanwenyuan_30zhai", "天津大学北洋园校区三问园30斋", "天津大学北洋园校区三问园30斋", "dormitory",
     ["三问园30斋", "三问园三十斋", "30斋"], ["三问园30斋"], [], [], "sanwenyuan_dorm"),
    ("zhiyuan_4zhai_b", "天津大学北洋园校区知园四斋B座", "天津大学北洋园校区知园四斋B座", "dormitory",
     ["知园四斋B座", "知园四斋", "知园4斋"], ["知园四斋B座"], [], [], "zhiyuan_dorm"),
    # 地标 / 园区
    ("yuyuan_garden", "天津大学北洋园校区御园", "天津大学北洋园校区御园", "landmark",
     ["御园"], ["御园"], ["天津大学北洋园校区御园"], [], None),
    ("rishin_park", "天津大学北洋园校区日新园", "天津大学北洋园校区日新园", "landmark",
     ["日新园"], ["日新园"], ["天津大学北洋园校区日新园"], [], None),
    ("beiyang_memorial_forest", "天津大学北洋园校区北洋纪念林", "天津大学北洋园校区北洋纪念林", "landmark",
     ["北洋纪念林"], ["北洋纪念林"], ["天津大学北洋园校区北洋纪念林"], [], None),
    # 快递点
    ("beiyangyuan_south_express_station", "天津大学北洋园校区南区快递站", "天津大学北洋园校区南区快递站", "logistics",
     ["南区快递站"], ["南区快递站"], ["天津大学北洋园校区南区快递站"], [], None),
    ("qiyuan_express_station", "天津大学北洋园校区快递站", "天津大学北洋园校区快递站(齐园)", "logistics",
     ["齐园快递站", "快递站"], ["快递站"], ["天津大学北洋园校区快递站"], [], None),
    # ===== P3d 第二轮：官方地图 checklist 补全 =====
    ("building_35", "天津大学北洋园校区机械工程实践教学中心", "天津大学北洋园校区35教学楼", "teaching",
     ["35楼", "35教学楼", "35教", "机械工程实践教学中心"], ["机械工程实践教学中心"], ["天津大学北洋园校区机械工程实践教学中心"], ["停车场"], None),
    ("building_39", "天津大学北洋园校区39教学楼", "天津大学北洋园校区39教学楼", "teaching",
     ["39楼", "39教学楼", "39教", "港口与海岸工程实验室"], ["39教"], [], ["地铁站", "公交站", "停车场"], None),
    ("building_40", "天津大学北洋园校区深水结构实验室", "天津大学北洋园校区40教学楼", "teaching",
     ["40楼", "40教学楼", "40教", "深水结构实验室"], ["深水结构实验室"], ["天津大学北洋园校区深水结构实验室"], [], None),
    ("xiuyuan_dorm", "天津大学北洋园校区修园", "天津大学北洋园校区修园", "dormitory",
     ["修园", "修园宿舍"], ["修园"], ["天津大学北洋园校区修园"], ["餐厅", "咖啡", "店"], None),
    ("network_center", "天津大学北洋园校区信息与网络中心", "天津大学北洋园校区信息与网络中心", "service",
     ["信息与网络中心", "网络中心"], ["信息与网络中心"], ["天津大学北洋园校区信息与网络中心"], [], None),
    ("dorm_service_center", "天津大学北洋园校区学生生活园区管理服务中心", "学生生活园区管理服务中心", "service",
     ["学生生活园区管理服务中心", "学服中心"], ["学生生活园区管理服务中心"], ["学生生活园区管理服务中心"], [], None),
    ("administrative_service_center", "天津大学北洋园校区行政服务中心", "天津大学北洋园校区行政服务中心", "service",
     ["行政服务中心"], ["行政服务中心"], ["天津大学北洋园校区行政服务中心"], [], None),
    ("security_center", "天津大学北洋园校区安保中心", "天津大学北洋园校区安保中心", "service",
     ["安保中心", "保卫处"], ["安保中心"], ["天津大学北洋园校区安保中心"], [], None),
    ("beiyang_clinic", "天津大学北洋园校区北洋门诊部", "北洋门诊部", "medical",
     ["北洋门诊部", "门诊部"], ["北洋门诊部"], ["北洋门诊部"], [], None),
    ("kindergarten", "天津大学北洋园校区幼儿园", "天津大学幼儿园", "service",
     ["幼儿园", "天大幼儿园"], ["幼儿园"], ["天津大学幼儿园"], [], None),
    ("liuyuan_canteen", "天津大学北洋园校区留园餐厅", "天津大学北洋园校区留园餐厅", "dining",
     ["留园餐厅", "留学生食堂"], ["留园餐厅"], ["天津大学北洋园校区留园餐厅"], ["烤排骨", "餐吧"], None),
]

# 为既有节点补充的常用别名]

# 为既有节点补充的常用别名：(node_id, alias)，必须可验证、不与其他节点冲突。
NEW_ALIASES = [
    ("beiyangyuan_north_cainiao", "北菜"),
    ("beiyangyuan_south_cainiao", "南菜"),
    ("school_of_computing", "计算机科学与技术学院"),
    # P3d 第二轮：官方清单异名合并
    ("xuesan_canteen", "棠园餐厅"),
    ("xuesan_canteen", "棠园"),
    ("xuewu_canteen", "桃园餐厅"),
    ("xuewu_canteen", "桃园"),
    ("mei_yuan_canteen", "梅园"),
    ("lan_yuan_canteen", "兰园"),
    ("building_41", "船舶与海洋工程馆"),
    ("building_41", "岩土工程研究所"),
    ("building_42", "水利馆"),
    ("building_42", "水利实验室"),
    ("building_48", "电气电子实验教学中心"),
    ("building_53", "先进高分子材料研究所"),
    ("building_55", "国际示范性软件学院"),
]

# 同地点异名合并：(被合并节点, 目标节点, 目标节点新增别名)
# 官方清单：42教 = 水利馆；water_engineering_lab 与 building_42 为同一栋楼。
MERGES = [
    ("water_engineering_lab", "building_42", ["水利馆", "水利实验室"]),
]


def load_amap_key() -> str:
    if not ENV_DEV.exists():
        raise SystemExit("缺少 {}，请先在 .env.dev 配置 AMAP_API_KEY。".format(ENV_DEV))
    for line in ENV_DEV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("AMAP_API_KEY="):
            key = line.split("=", 1)[1].strip()
            if key:
                return key
    raise SystemExit(".env.dev 中未找到 AMAP_API_KEY。")


def _get_json(url: str, params: Dict[str, str]) -> dict:
    response = requests.get(url, params=params, timeout=20)
    response.raise_for_status()
    return response.json()


def haversine_m(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371000.0 * 2.0 * math.asin(math.sqrt(h))


def search_poi(
    key: str,
    keyword: str,
    tokens: List[str],
    exact: List[str],
    exclude: List[str],
    center_hint: Optional[Tuple[float, float]] = None,
    proximity_ref: Optional[str] = None,
    nodes: Optional[Dict[str, dict]] = None,
) -> Optional[dict]:
    params = {
        "key": key,
        "keywords": keyword,
        "city": "天津",
        "citylimit": "true",
        "offset": "20",
        "extensions": "base",
    }
    for attempt in range(3):
        try:
            data = _get_json(AMAP_PLACE_TEXT, params)
        except (RuntimeError, requests.RequestException):
            time.sleep(1.0)
            continue
        if data.get("status") != "1":
            time.sleep(1.0)
            continue
        pois = data.get("pois") or []
        matches = []
        for poi in pois:
            location = poi.get("location") or ""
            try:
                lng_text, lat_text = location.split(",", 1)
                lat, lng = float(lat_text), float(lng_text)
            except (ValueError, TypeError):
                continue
            if center_hint is not None:
                center = center_hint
            else:
                center = CENTER
            meters = haversine_m((lat, lng), center)
            if meters > MAX_CENTER_M:
                continue
            name = poi.get("name") or ""
            if not any(token in name for token in tokens):
                continue
            if any(token in name for token in exclude):
                continue
            reference = None
            if proximity_ref is not None and nodes is not None and proximity_ref in nodes:
                reference = (nodes[proximity_ref]["latitude"], nodes[proximity_ref]["longitude"])
            anchor = reference if reference is not None else center
            matches.append((haversine_m((lat, lng), anchor), poi))
        if not matches:
            time.sleep(1.0)
            continue
        exact_matches = []
        for anchor_meters, poi in matches:
            name = (poi.get("name") or "").strip().casefold()
            if any(exp.casefold() == name for exp in exact):
                exact_matches.append((anchor_meters, poi))
        pool = exact_matches or matches
        pool.sort(key=lambda item: item[0])
        return pool[0][1]
    return None


def poi_coords(poi: dict) -> Tuple[float, float]:
    lng_text, lat_text = poi["location"].split(",", 1)
    return float(lat_text), float(lng_text)


def load_existing() -> Dict[str, dict]:
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    nodes = {item["id"]: item for item in payload["nodes"]}
    return nodes


def verify_walk(key: str, origin: str, destination: str) -> Optional[int]:
    params = {"key": key, "origin": origin, "destination": destination}
    try:
        data = _get_json(AMAP_WALK, params)
    except (RuntimeError, requests.RequestException):
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
    params = {"key": key, "origin": origin, "destination": destination}
    try:
        data = _get_json(AMAP_BIKE, params)
    except (RuntimeError, requests.RequestException):
        return None
    if data.get("errcode") not in (0, None) and data.get("status") != "1":
        return None
    paths = ((data.get("data") or {}).get("paths")) or []
    if not paths:
        return None
    distance = paths[0].get("distance")
    if distance is None:
        return None
    return int(float(distance))


def route_plausible(node_a: dict, node_b: dict, walk_distance: int) -> bool:
    straight = haversine_m(
        (node_a["latitude"], node_a["longitude"]),
        (node_b["latitude"], node_b["longitude"]),
    )
    if straight <= 10:
        return True
    if walk_distance < 0.5 * straight:
        return False
    return True


def to_lnglat(node: dict) -> str:
    return "{:.6f},{:.6f}".format(node["longitude"], node["latitude"])


def build_candidate_edges(nodes: Dict[str, dict], new_ids: set, neighbors: int, max_m: int) -> List[Tuple[str, str]]:
    pairs = set()
    for node_id in new_ids:
        position = (nodes[node_id]["latitude"], nodes[node_id]["longitude"])
        distances = []
        for other_id, other in nodes.items():
            if other_id == node_id:
                continue
            other_position = (other["latitude"], other["longitude"])
            meters = haversine_m(position, other_position)
            if meters <= max_m:
                distances.append((meters, other_id))
        distances.sort()
        for _, other_id in distances[:neighbors]:
            pairs.add(tuple(sorted((node_id, other_id))))
    return sorted(pairs)


def components(node_ids: set, edges: List[dict]) -> List[set]:
    adjacency = {node_id: set() for node_id in node_ids}
    for edge in edges:
        adjacency[edge["from"]].add(edge["to"])
        adjacency[edge["to"]].add(edge["from"])
    visited = set()
    result = []
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
        result.append(component)
    return result


def verify_edge(key: str, nodes: Dict[str, dict], from_id: str, to_id: str) -> Optional[dict]:
    origin = to_lnglat(nodes[from_id])
    destination = to_lnglat(nodes[to_id])
    walk_distance = verify_walk(key, origin, destination)
    time.sleep(REQUEST_DELAY_SECONDS)
    if walk_distance is None or walk_distance <= 0:
        return None
    if not route_plausible(nodes[from_id], nodes[to_id], walk_distance):
        return None
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
    source = "高德步行路线 API v3（origin={} destination={}）distance_m={}；骑行 v4 可用={}".format(
        origin, destination, walk_distance, bike_distance if bike_distance is not None else "N/A"
    )
    return {
        "from": from_id,
        "to": to_id,
        "distance_m": walk_distance,
        "modes": modes,
        "bidirectional": True,
        "source": source,
    }


def ensure_connectivity(key: str, nodes: Dict[str, dict], edges: List[dict], max_bridge_m: int) -> List[dict]:
    node_ids = set(nodes.keys())
    tried = set()
    while True:
        parts = components(node_ids, edges)
        if len(parts) <= 1:
            break
        best = None
        for part in parts:
            for node_id in part:
                position = (nodes[node_id]["latitude"], nodes[node_id]["longitude"])
                for other_id in node_ids - part:
                    pair = tuple(sorted((node_id, other_id)))
                    if pair in tried:
                        continue
                    other_position = (nodes[other_id]["latitude"], nodes[other_id]["longitude"])
                    meters = haversine_m(position, other_position)
                    if best is None or meters < best[0]:
                        best = (meters, node_id, other_id)
        if best is None or best[0] > max_bridge_m:
            break
        _, from_id, to_id = best
        tried.add(tuple(sorted((from_id, to_id))))
        edge = verify_edge(key, nodes, from_id, to_id)
        if edge is None:
            continue
        edges.append(edge)
        print("[BRIDGE] {} <-> {} {}m modes={}".format(
            from_id, to_id, edge["distance_m"], ",".join(edge["modes"])
        ))
    return edges


def main() -> None:
    parser = argparse.ArgumentParser(description="P3d 增量采集北洋园真实地图数据（高德）")
    parser.add_argument("--dry-run", action="store_true", help="只检索 POI，不调用方向 API")
    parser.add_argument("--neighbors", type=int, default=DEFAULT_NEIGHBORS)
    parser.add_argument("--max-edge-m", type=int, default=DEFAULT_MAX_EDGE_M)
    args = parser.parse_args()

    key = load_amap_key()
    nodes = load_existing()
    print("[LOAD] 现有节点 {} 个".format(len(nodes)))

    merged_ids = set()
    for source_id, target_id, extra_aliases in MERGES:
        if source_id not in nodes:
            continue
        if target_id not in nodes:
            raise RuntimeError("合并目标不存在：{}".format(target_id))
        source = nodes.pop(source_id)
        for alias in extra_aliases:
            if alias not in nodes[target_id]["aliases"]:
                nodes[target_id]["aliases"].append(alias)
        merged_ids.add(source_id)
        print("[MERGE] {} 并入 {}（别名 += {}）".format(source_id, target_id, ",".join(extra_aliases)))

    new_ids = set()
    for item in NEW_NODES:
        node_id, keyword, display_name, category, aliases, tokens, exact, exclude, proximity_ref = item
        if node_id in nodes or node_id in merged_ids:
            print("[SKIP] 已存在/已合并：{}".format(node_id))
            continue
        poi = search_poi(
            key,
            keyword,
            tokens,
            exact,
            exclude,
            center_hint=None,
            proximity_ref=proximity_ref,
            nodes=nodes,
        )
        if poi is None:
            print("[WARN] 未检索到可靠 POI（留缺口，不编造）：{} ({})".format(node_id, keyword))
            continue
        latitude, longitude = poi_coords(poi)
        nodes[node_id] = {
            "id": node_id,
            "name": display_name,
            "aliases": aliases,
            "category": category,
            "latitude": latitude,
            "longitude": longitude,
            "source": "高德 POI 检索（id={}，关键词={}，POI名={}）".format(
                poi.get("id"), keyword, poi.get("name")
            ),
        }
        new_ids.add(node_id)
        print("[POI] {} {} ({:.6f}, {:.6f})".format(node_id, display_name, latitude, longitude))
        time.sleep(REQUEST_DELAY_SECONDS)

    # 为既有节点补充别名（不冲突由 loader 校验兜底）
    for node_id, alias in NEW_ALIASES:
        node = nodes.get(node_id)
        if node is None or alias in node["aliases"]:
            continue
        node["aliases"].append(alias)
        print("[ALIAS] {} += {}".format(node_id, alias))

    if args.dry_run:
        print("\n[dry-run] 新增节点 {} 个；候选边（未调用方向 API）：".format(len(new_ids)))
        for pair in build_candidate_edges(nodes, new_ids, args.neighbors, args.max_edge_m):
            print("  {} <-> {}".format(pair[0], pair[1]))
        return

    edges = [e for e in load_existing_edges() if e["from"] not in merged_ids and e["to"] not in merged_ids]
    existing_pairs = {tuple(sorted((e["from"], e["to"]))) for e in edges}
    candidates = build_candidate_edges(nodes, new_ids, args.neighbors, args.max_edge_m)
    for from_id, to_id in candidates:
        if (from_id, to_id) in existing_pairs:
            continue
        edge = verify_edge(key, nodes, from_id, to_id)
        if edge is None:
            continue
        edges.append(edge)
        existing_pairs.add((from_id, to_id))
        print("[EDGE] {} <-> {} {}m modes={}".format(
            from_id, to_id, edge["distance_m"], ",".join(edge["modes"])
        ))

    edges = ensure_connectivity(key, nodes, edges, max_bridge_m=args.max_edge_m + 300)

    payload = {
        "metadata": {
            "schema_version": SCHEMA_VERSION,
            "campus": "天津大学北洋园校区",
            "data_status": "routable",
            "provenance": "real_map",
            "source_note": "高德 Web API 开发阶段采集；运行时静态加载，不依赖高德",
            "note": "；".join([
                "距离来自高德步行路线 API（v3/direction/walking），单位米；骑行可用性来自高德骑行路线 API（v4/direction/bicycling）。",
                "边均为双向，按单方向高德路线核验后对称处理。",
                "对高德返回明显小于直线距离的异常边按可验证原则拒绝保留。",
                "采集时间为开发阶段，最终运行时静态加载本地 JSON，不依赖高德。",
                "P3d：在既有 49 节点基础上增量新增教学楼/学院/宿舍斋号/地标/快递点节点及新连接，全部来自高德 POI 与方向 API 核验；查不到可靠数据的 32/35/37/40/44/45/46/47/56/57 教学楼、正园九斋等保留缺口，不编造。",
            ]),
        },
        "nodes": sorted(nodes.values(), key=lambda item: item["id"]),
        "edges": sorted(edges, key=lambda item: (item["from"], item["to"])),
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("\n[OK] 已写入 {}".format(OUTPUT))
    print("节点 {} 个，边 {} 条".format(len(payload["nodes"]), len(payload["edges"])))


def load_existing_edges() -> List[dict]:
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    return list(payload["edges"])


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print("[FAIL] {}".format(exc))
        raise SystemExit(1)
