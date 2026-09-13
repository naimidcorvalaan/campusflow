"""P3d 最后一轮：25 个高频缺口补全（官方图标注 / 园区几何推断）。

本脚本只处理用户指定的 25 项中的可定位项：
- 教学楼 5 项：38教(土木馆)、44教、45教、46教、47教(计算机实验教学中心)
- 宿舍 11 项：格园1/2斋、知园5斋、诚园8斋、修园11/12斋、齐园16斋、
  治园19/20斋、平园22/24斋
- 仍无法可靠定位（不编造）：56教、57教、三问园25~29斋、篮球场、排球场

定位来源分级：
- AMAP_VERIFIED                 ：高德精确核验
- TJU_OFFICIAL_MAP_APPROXIMATE  ：官方完整图/分块高清图标注 + 多控制点仿射校准
- 园区几何推断                    ：官方图园区标注 + 同园区已知斋号几何关系
近似数据绝不冒充高德精确坐标。

仅开发阶段使用；运行时（src/）不得 import 本脚本。
"""
import json
import math
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

ROOT = Path(__file__).resolve().parent.parent
ENV_DEV = ROOT / ".env.dev"
MAP = ROOT / "data" / "beiyangyuan_map.json"
AMAP_PLACE_TEXT = "https://restapi.amap.com/v3/place/text"
AMAP_WALK = "https://restapi.amap.com/v3/direction/walking"
AMAP_BIKE = "https://restapi.amap.com/v4/direction/bicycling"
REQUEST_DELAY_SECONDS = 0.4
MAX_CANDIDATE_M = 900
NEIGHBORS = 4

APPROX_SOURCE = "TJU 官方地图近似（多控制点仿射校准，非高德精确坐标）"
INFER_SOURCE = "TJU 官方地图园区标注 + 同园区已知斋号几何推断（园区内近似，非楼栋级精确坐标）"

# (id, name, category, aliases, latitude, longitude, provenance, source)
NEW_NODES = [
    # ---- 教学楼（分块高清图/完整图标注，仿射校准）----
    ("building_38", "天津大学北洋园校区38教学楼(土木馆)", "teaching",
     ["38教", "38教学楼", "38号教学楼", "38楼", "土木馆"],
     38.992632, 117.316332, "tju_official_map_approximate",
     "TJU 官方地图标注：分块高清图“土木馆”+完整图“38教”双源交叉（多控制点仿射校准，两源相差约45m，取高清图标注）"),
    ("building_44", "天津大学北洋园校区44教学楼", "teaching",
     ["44教", "44教学楼", "44号教学楼", "44楼", "公共教学楼(44教)"],
     38.996201, 117.314020, "tju_official_map_approximate",
     "TJU 官方地图标注：分块高清图“公共教学楼（44教）”（多控制点仿射校准）"),
    ("building_45", "天津大学北洋园校区45教学楼", "teaching",
     ["45教", "45教学楼", "45号教学楼", "45楼", "公共教学楼(45教)"],
     38.997210, 117.315866, "tju_official_map_approximate",
     "TJU 官方地图标注：分块高清图“公共教学楼（45教）”（多控制点仿射校准）"),
    ("building_46", "天津大学北洋园校区46教学楼", "teaching",
     ["46教", "46教学楼", "46号教学楼", "46楼", "公共教学楼(46教)"],
     38.997951, 117.315843, "tju_official_map_approximate",
     "TJU 官方地图标注：分块高清图“公共教学楼（46教）”（多控制点仿射校准）"),
    ("building_47", "天津大学北洋园校区47教学楼(计算机实验教学中心)", "teaching",
     ["47教", "47教学楼", "47号教学楼", "47楼", "计算机实验教学中心"],
     38.996800, 117.318364, "tju_official_map_approximate",
     "TJU 官方地图标注：分块高清图“计算机实验教学中心”（以相邻48教/求实会堂几何关系校正，非高德精确坐标）"),
    # ---- 宿舍：官方完整图直接标注（与高德锚点交叉验证，偏差15~70m）----
    ("geyuan_1zhai", "天津大学北洋园校区格园1斋", "dormitory",
     ["格园1斋", "格园一斋", "1斋"],
     39.003289, 117.314267, "tju_official_map_approximate",
     "TJU 官方完整图标注“格园1斋”（多控制点仿射校准；与格园3斋高德POI相对位置吻合）"),
    ("geyuan_2zhai", "天津大学北洋园校区格园2斋", "dormitory",
     ["格园2斋", "格园二斋", "2斋"],
     39.003100, 117.314850, "tju_official_map_approximate",
     INFER_SOURCE + "（格园1斋官方图标注 + 格园3斋高德POI，2斋取两点之间近似位置）"),
    ("zhiyuan_5zhai", "天津大学北洋园校区知园5斋", "dormitory",
     ["知园5斋", "知园五斋", "5斋"],
     39.001414, 117.314461, "tju_official_map_approximate",
     "TJU 官方完整图标注“知园5斋”（多控制点仿射校准；与知园4斋B座高德POI相对位置吻合）"),
    ("chengyuan_8zhai", "天津大学北洋园校区诚园8斋", "dormitory",
     ["诚园8斋", "诚园八斋", "8斋"],
     38.999500, 117.316800, "tju_official_map_approximate",
     INFER_SOURCE + "（诚园6斋/7斋高德POI，8斋取园区南侧近似位置）"),
    ("xiuyuan_11zhai", "天津大学北洋园校区修园11斋", "dormitory",
     ["修园11斋", "修园十一斋", "11斋"],
     38.996900, 117.315800, "tju_official_map_approximate",
     INFER_SOURCE + "（修园12斋官方图标注，11斋取同园区北侧近似位置）"),
    ("xiuyuan_12zhai", "天津大学北洋园校区修园12斋", "dormitory",
     ["修园12斋", "修园十二斋", "12斋"],
     38.996507, 117.315748, "tju_official_map_approximate",
     "TJU 官方完整图标注“修园12斋”（多控制点仿射校准）"),
    ("qiyuan_16zhai", "天津大学北洋园校区齐园16斋", "dormitory",
     ["齐园16斋", "齐园十六斋", "16斋"],
     38.994300, 117.316200, "tju_official_map_approximate",
     INFER_SOURCE + "（齐园13/14/15斋已知，16斋取园区内近似位置）"),
    ("zhiyuan_garden_19zhai", "天津大学北洋园校区治园19斋", "dormitory",
     ["治园19斋", "治园十九斋", "19斋"],
     38.994963, 117.311298, "tju_official_map_approximate",
     "TJU 官方完整图标注“治园19斋”（与治园19斋增1高德POI相邻吻合，高德另有“19斋”POI位于新元南路南端，判定为异址编号，不采用）"),
    ("zhiyuan_garden_20zhai", "天津大学北洋园校区治园20斋", "dormitory",
     ["治园20斋", "治园二十斋", "20斋"],
     38.994205, 117.311174, "tju_official_map_approximate",
     "TJU 官方完整图标注“治园20斋”（多控制点仿射校准）"),
    ("pingyuan_22zhai", "天津大学北洋园校区平园22斋", "dormitory",
     ["平园22斋", "平园二十二斋", "22斋"],
     38.999743, 117.312383, "tju_official_map_approximate",
     "TJU 官方完整图标注“平园22斋”（与平园21A/23斋高德POI相对位置吻合）"),
    ("pingyuan_24zhai", "天津大学北洋园校区平园24斋", "dormitory",
     ["平园24斋", "平园二十四斋", "24斋"],
     38.999730, 117.311176, "tju_official_map_approximate",
     "TJU 官方完整图标注“平园24斋”（与平园23斋高德POI相对位置吻合）"),
]


def load_amap_key() -> str:
    if not ENV_DEV.exists():
        raise SystemExit("缺少 .env.dev，请先配置 AMAP_API_KEY。")
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


def to_lnglat(node: dict) -> str:
    return "{:.6f},{:.6f}".format(node["longitude"], node["latitude"])


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
    return walk_distance >= 0.5 * straight


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
                    meters = haversine_m(position, (nodes[other_id]["latitude"], nodes[other_id]["longitude"]))
                    if best is None or meters < best[0]:
                        best = (meters, node_id, other_id)
        if best is None:
            break
        _, from_id, to_id = best
        tried.add(tuple(sorted((from_id, to_id))))
        edge = verify_edge(key, nodes, from_id, to_id)
        if edge is None:
            continue
        edges.append(edge)
        print("[BRIDGE] {} <-> {} {}m".format(from_id, to_id, edge["distance_m"]))
    return edges


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    key = load_amap_key()

    payload = json.loads(MAP.read_text(encoding="utf-8"))
    nodes = {item["id"]: item for item in payload["nodes"]}
    edges = list(payload["edges"])
    existing_aliases = set()
    for node in nodes.values():
        existing_aliases.add(node["id"].casefold())
        existing_aliases.add(node["name"].casefold())
        for alias in node.get("aliases", []):
            existing_aliases.add(alias.casefold())

    new_ids = []
    for node_id, name, category, aliases, latitude, longitude, provenance, source in NEW_NODES:
        if node_id in nodes:
            print("[SKIP] 已存在：{}".format(node_id))
            continue
        for alias in aliases:
            key_alias = alias.casefold()
            if key_alias in existing_aliases:
                raise SystemExit("alias 冲突：{}（{}）".format(alias, node_id))
        nodes[node_id] = {
            "id": node_id,
            "name": name,
            "aliases": list(aliases),
            "category": category,
            "latitude": latitude,
            "longitude": longitude,
            "source": source,
            "provenance": provenance,
        }
        existing_aliases.add(node_id.casefold())
        existing_aliases.add(name.casefold())
        for alias in aliases:
            existing_aliases.add(alias.casefold())
        new_ids.append(node_id)
        print("[POI] {} {} ({:.6f}, {:.6f})".format(node_id, name, latitude, longitude))

    if args.dry_run:
        print("\n[dry-run] 新增节点 {} 个".format(len(new_ids)))
        return

    existing_pairs = {tuple(sorted((e["from"], e["to"]))) for e in edges}
    for node_id in new_ids:
        position = (nodes[node_id]["latitude"], nodes[node_id]["longitude"])
        candidates = []
        for other_id, other in nodes.items():
            if other_id == node_id:
                continue
            meters = haversine_m(position, (other["latitude"], other["longitude"]))
            if meters <= MAX_CANDIDATE_M:
                candidates.append((meters, other_id))
        candidates.sort()
        for meters, other_id in candidates[:NEIGHBORS]:
            pair = tuple(sorted((node_id, other_id)))
            if pair in existing_pairs:
                continue
            edge = verify_edge(key, nodes, node_id, other_id)
            if edge is None:
                continue
            edges.append(edge)
            existing_pairs.add(pair)
            print("[EDGE] {} <-> {} {}m modes={}".format(
                node_id, other_id, edge["distance_m"], ",".join(edge["modes"])
            ))

    edges = ensure_connectivity(key, nodes, edges, max_bridge_m=MAX_CANDIDATE_M + 500)

    note = "；".join([
        "P3d 最后一轮（25项高频缺口）：新增38/44/45/46/47教、格园1/2斋、知园5斋、诚园8斋、修园11/12斋、齐园16斋、治园19/20斋、平园22/24斋共{}个节点。".format(len(new_ids)),
        "坐标来源：38/44/45/46/47教来自官方分块高清图/完整图标注（仿射校准）；斋号中官方完整图直接标注的（格园1、知园5、修园12、治园19/20、平园22/24）与高德锚点交叉验证偏差15~70m。",
        "园区几何推断（格园2、诚园8、修园11、齐园16）仅取园区内近似位置，provenance=tju_official_map_approximate，不冒充高德精确坐标。",
        "仍无法可靠定位（不编造）：56教、57教（官方图与高德均无位置标注）、三问园25~29斋（官方图仅图例列名、地图无楼栋标注，且各源园区位置存在矛盾）、篮球场、排球场（无可靠POI/官方图标注）。",
        "高德“19斋”POI（新元南路南端）与官方图治园19斋位置矛盾，判定为异址编号，采用官方图位置。",
    ])
    metadata = dict(payload.get("metadata") or {})
    metadata["note"] = note
    output = {
        "metadata": metadata,
        "nodes": sorted(nodes.values(), key=lambda item: item["id"]),
        "edges": sorted(edges, key=lambda item: (item["from"], item["to"])),
    }
    MAP.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("\n[OK] 已写入 {}".format(MAP))
    print("节点 {} 个，边 {} 条".format(len(output["nodes"]), len(output["edges"])))


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print("[FAIL] {}".format(exc))
        raise SystemExit(1)
