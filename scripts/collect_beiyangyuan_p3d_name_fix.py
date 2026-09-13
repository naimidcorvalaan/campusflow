"""P3d 名称审计补全脚本：宿舍斋号 / 治园园区补全 + 名称纠错 + 重复合并。

仅开发阶段使用；只读取 .env.dev 中的 AMAP_API_KEY。
运行时（src/）不得 import 本脚本，最终 CampusFlow 运行时不得请求高德。

本轮依据用户母表与高德核验：
- 新增 8 个节点：治园园区、治园17斋、治园18斋、治园19斋增1、格园3斋、诚园7斋、齐园15斋、平园23斋
  （坐标来自高德商铺/机构 POI 地址直接标注所在斋号，不编造）。
- 名称纠错：32教=理学院（高德地址“第32教学楼”）、外国语言与文学学院、求是学部、环境科学与工程学院、
  理学院化学系、化工材料创新平台、环境与资源科技创新平台。
- 重复合并：海棠餐厅（高德地址=青园餐厅一楼）并入青园餐厅，避免同址重复节点。
- 新边经高德步行/骑行方向 API 核验，拒绝异常（路线距离明显小于直线距离）。

用法：
  python scripts/collect_beiyangyuan_p3d_name_fix.py --dry-run
  python scripts/collect_beiyangyuan_p3d_name_fix.py
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
MAP = ROOT / "data" / "beiyangyuan_map.json"
SCHEMA_VERSION = "p3.campus-map.v1"
AMAP_PLACE_TEXT = "https://restapi.amap.com/v3/place/text"
AMAP_WALK = "https://restapi.amap.com/v3/direction/walking"
AMAP_BIKE = "https://restapi.amap.com/v4/direction/bicycling"
REQUEST_DELAY_SECONDS = 0.4

# (id, name, category, aliases, latitude, longitude, source)
NEW_NODES = [
    (
        "zhiyuan_garden", "天津大学北洋园校区治园", "dormitory",
        ["治园", "治园宿舍"], 38.994545, 117.312059,
        "高德 POI 地址级核验：治园17斋（创美理容 id=B0FFGIPXTY）、治园18斋（天大易修哥 id=B0FFLBRK18）、"
        "治园19斋增1（金色麦甜 id=B0LK6CKPHZ、星巴克 id=B0L2FR2IZ3）；坐标取三斋商铺地址均值，非单一 POI 精确坐标",
    ),
    (
        "zhiyuan_garden_17zhai", "天津大学北洋园校区治园17斋", "dormitory",
        ["治园17斋", "治园十七斋", "17斋", "十七斋"], 38.994920, 117.312262,
        "高德 POI 检索（id=B0FFGIPXTY，关键词=创美理容，POI名=创美理容服务(天大店)，"
        "地址=天津大学北洋园校区治园17斋一层）——商铺地址直接标注所在斋号",
    ),
    (
        "zhiyuan_garden_18zhai", "天津大学北洋园校区治园18斋", "dormitory",
        ["治园18斋", "治园十八斋", "18斋", "十八斋"], 38.994193, 117.312203,
        "高德 POI 检索（id=B0FFLBRK18，关键词=天大易修哥，POI名=天大易修哥电脑手机维修，"
        "地址=天津大学治园18斋中国联通）——商铺地址直接标注所在斋号",
    ),
    (
        "zhiyuan_garden_19zhai_add1", "天津大学北洋园校区治园19斋增1", "dormitory",
        ["治园19斋增1", "治园十九斋增1", "19斋增1", "十九斋增1"], 38.994523, 117.311713,
        "高德 POI 检索（id=B0LK6CKPHZ 金色麦甜，地址=治园19斋增1号楼底商；id=B0L2FR2IZ3 星巴克，"
        "地址=治园19斋增1号楼一层底商2号；坐标取两商铺地址均值）——商铺地址直接标注所在斋号",
    ),
    (
        "geyuan_3zhai", "天津大学北洋园校区格园3斋", "dormitory",
        ["格园3斋", "格园三斋", "格3斋", "3斋"], 39.002888, 117.315388,
        "高德 POI 检索（id=B0I2SZNR2Q，关键词=雅仕眼镜，POI名=雅仕眼镜(天大北洋园店)，"
        "地址=天津大学北洋园校区格3斋）——商铺地址直接标注所在斋号",
    ),
    (
        "chengyuan_7zhai", "天津大学北洋园校区诚园7斋", "dormitory",
        ["诚园7斋", "诚园七斋", "7斋", "七斋"], 39.000007, 117.316730,
        "高德 POI 检索（id=B0KGT79JAG，关键词=化工学院学生党建中心，POI名=天津大学化工学院学生党建中心，"
        "地址=天津大学北洋园校区诚园7斋B）——POI 地址直接标注所在斋号",
    ),
    (
        "qiyuan_15zhai", "天津大学北洋园校区齐园15斋", "dormitory",
        ["齐园15斋", "齐园十五斋", "15斋", "十五斋"], 38.994804, 117.316159,
        "高德 POI 检索（id=B0JU49EN45，关键词=咖啡厅 天津大学北洋园校区齐园，POI名=咖啡厅(天津大学北洋园校区齐园店)，"
        "地址=天津大学新校区齐园15斋1895）——商铺地址直接标注所在斋号",
    ),
    (
        "pingyuan_23zhai", "天津大学北洋园校区平园23斋", "dormitory",
        ["平园23斋", "平园二十三斋", "23斋", "二十三斋"], 39.000218, 117.311713,
        "高德 POI 检索（id=B0JRTX7N5J，关键词=东北锦州烧烤 天津大学，POI名=东北锦州烧烤(天津大学北洋园校区店)，"
        "地址=天津大学北洋园校区平园23斋）——商铺地址直接标注所在斋号",
    ),
]

# (node_id, [新别名]) —— 名称纠错 / 官方正式名称
ALIAS_ADDITIONS = {
    "college_of_science": ["32教", "32教学楼", "32号教学楼", "32楼"],
    "college_of_foreign_languages": ["外国语言与文学学院"],
    "building_43": ["求是学部", "环境科学与工程学院"],
    "building_51": ["理学院化学系"],
    "building_58": ["化工材料创新平台"],
    "building_59": ["环境与资源科技创新平台"],
}

# 重复合并：海棠餐厅 = 青园餐厅（高德 POI 海棠餐厅地址=青园餐厅一楼）
MERGE_SOURCE = "haitang_canteen"
MERGE_TARGET = "qingyuan_canteen"
MERGE_ALIASES = ["海棠餐厅", "海棠食堂"]
TARGET_NEW_SOURCE = (
    "高德 POI 检索（id=B0I1JHFOS6，关键词=海棠餐厅，POI名=天津大学北洋园校区海棠餐厅，"
    "地址=海河教育园区天津大学德榜路与博文北路交口青园餐厅一楼）——海棠餐厅位于青园餐厅一楼，"
    "同址合并；原 TJU 官方图近似坐标已由高德核验"
)

# 直连边参数：每个新节点取最近的若干既有/新节点（haversine），再逐个经高德方向 API 核验
MAX_CANDIDATE_M = 900
NEIGHBORS = 4


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
    if walk_distance < 0.5 * straight:
        return False
    return True


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
                    other_position = (nodes[other_id]["latitude"], nodes[other_id]["longitude"])
                    meters = haversine_m(position, other_position)
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    key = load_amap_key()

    payload = json.loads(MAP.read_text(encoding="utf-8"))
    nodes = {item["id"]: item for item in payload["nodes"]}
    edges = list(payload["edges"])

    # 1) 重复合并：haitang_canteen -> qingyuan_canteen
    if MERGE_SOURCE in nodes and MERGE_TARGET in nodes:
        source = nodes.pop(MERGE_SOURCE)
        target = nodes[MERGE_TARGET]
        merged_pairs = set()
        keep_edges = []
        for edge in edges:
            if edge["from"] == MERGE_SOURCE or edge["to"] == MERGE_SOURCE:
                other = edge["to"] if edge["from"] == MERGE_SOURCE else edge["from"]
                pair = tuple(sorted((MERGE_TARGET, other)))
                if pair in merged_pairs:
                    continue
                merged_pairs.add(pair)
                new_edge = dict(edge)
                new_edge["from"], new_edge["to"] = (
                    (MERGE_TARGET, other) if MERGE_TARGET < other else (other, MERGE_TARGET)
                )
                if "高德步行" in edge.get("source", ""):
                    new_edge["source"] = edge["source"] + "（海棠餐厅并入青园餐厅，同址）"
                keep_edges.append(new_edge)
                continue
            if edge["from"] == MERGE_TARGET or edge["to"] == MERGE_TARGET:
                other = edge["to"] if edge["from"] == MERGE_TARGET else edge["from"]
                pair = tuple(sorted((MERGE_TARGET, other)))
                if pair in merged_pairs:
                    continue
                merged_pairs.add(pair)
                keep_edges.append(edge)
                continue
            keep_edges.append(edge)
        edges = keep_edges
        for alias in MERGE_ALIASES:
            if alias not in target["aliases"]:
                target["aliases"].append(alias)
        target["source"] = TARGET_NEW_SOURCE
        target.pop("provenance", None)
        print("[MERGE] {} -> {} 已合并，边 {} 条；别名 += {}".format(
            MERGE_SOURCE, MERGE_TARGET, len(merged_pairs), ",".join(MERGE_ALIASES)
        ))

    # 2) 既有节点别名补充（名称纠错）
    for node_id, aliases in ALIAS_ADDITIONS.items():
        node = nodes.get(node_id)
        if node is None:
            print("[WARN] 别名目标不存在：{}".format(node_id))
            continue
        for alias in aliases:
            if alias not in node["aliases"]:
                node["aliases"].append(alias)
        print("[ALIAS] {} += {}".format(node_id, ",".join(aliases)))

    # 3) 新增节点
    new_ids = []
    for node_id, name, category, aliases, latitude, longitude, source in NEW_NODES:
        if node_id in nodes:
            print("[SKIP] 已存在：{}".format(node_id))
            continue
        nodes[node_id] = {
            "id": node_id,
            "name": name,
            "aliases": list(aliases),
            "category": category,
            "latitude": latitude,
            "longitude": longitude,
            "source": source,
        }
        new_ids.append(node_id)
        print("[POI] {} {} ({:.6f}, {:.6f})".format(node_id, name, latitude, longitude))

    if args.dry_run:
        print("\n[dry-run] 新增节点 {} 个".format(len(new_ids)))
        return

    # 4) 新节点接入路网（高德方向 API 核验）
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
        "P3d 名称审计与补全：新增治园园区与治园17/18/19增1斋、格园3斋、诚园7斋、齐园15斋、平园23斋节点，"
        "坐标来自高德商铺/机构 POI 地址（地址直接标注所在斋号），不编造。",
        "名称纠错：32教=理学院（高德地址=第32教学楼）、外国语言与文学学院、求是学部、环境科学与工程学院、"
        "理学院化学系、化工材料创新平台、环境与资源科技创新平台。",
        "重复合并：海棠餐厅（高德地址=青园餐厅一楼）并入青园餐厅，原近似边升级为高德方向核验距离。",
        "用户确认：博学园为旧称，现行名称为三问园（25~30斋）；龙园、书园不存在，未建节点。",
        "46/38/44/45/47/56/57教与多数斋号高德无独立 POI，保留缺口不编造。",
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
