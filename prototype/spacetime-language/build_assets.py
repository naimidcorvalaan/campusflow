"""Rebuild isolated design assets from the existing CampusFlow map and router.
Run from the repository: .venv/Scripts/python.exe -B prototype/spacetime-language/build_assets.py
No network, model, profile, database, source-map or production UI writes.
"""
import hashlib
import html
import json
import math
from datetime import datetime, timedelta
from pathlib import Path
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
from src.p3_map_loader import load_campus_map_data
from src.p3_map_schema import TransportMode
from src.p3_route_provider import plan_route, _build_adjacency
from src.p3_time_estimator import estimate_travel_time
from src.p3_route_planner import TRANSITION_BUFFER_MINUTES
from src.p3_class_prep import CLASS_ARRIVAL_LEAD_MINUTES

REFERENCE = datetime(2026, 9, 13, 14, 0)
FOCUS_MINUTES = 90
NEXT_TASK_MINUTES = 60
CLASS_START = REFERENCE.replace(hour=19)

SCENARIOS = {
    "beiyangyuan": {
        "label": "北洋园", "code": "BYY",
        "stops": ["zhengdong_library", "xuesi_cafeteria", "building_45"],
        "short": ["郑东图书馆", "竹园餐厅", "45教"],
        "mode": TransportMode.WALK,
    },
    "weijinlu": {
        "label": "卫津路", "code": "WJL",
        "stops": ["weijinlu_library_main", "weijinlu_canteen_3", "weijinlu_building_9"],
        "short": ["图书馆", "学三食堂", "9教"],
        "mode": TransportMode.WALK,
    },
}


def approximate(node):
    evidence = " ".join(str(node.get(k, "")) for k in ("precision", "provenance", "source"))
    return any(mark in evidence.lower() for mark in ("approximate", "近似", "仿射"))


def base_id(node_id):
    return node_id.removesuffix("__road_access") if hasattr(str, "removesuffix") else node_id.replace("__road_access", "")


def asset_svg(campus, ribbon=False):
    nodes = campus["nodes"]
    w, h = (1000, 220) if ribbon else (800, 620)
    span_x = max(n["x"] for n in nodes) - min(n["x"] for n in nodes)
    span_y = max(n["y"] for n in nodes) - min(n["y"] for n in nodes)
    scale = min((w - 70) / span_x, (h - 70) / span_y) if not ribbon else (w - 80) / span_x
    center_x = (max(n["x"] for n in nodes) + min(n["x"] for n in nodes)) / 2
    center_y = (max(n["y"] for n in nodes) + min(n["y"] for n in nodes)) / 2
    points = {n["id"]: ((n["x"] - center_x) * scale + w / 2, (n["y"] - center_y) * scale + h / 2) for n in nodes}
    paths = []
    for a, b in campus["textureEdges"]:
        ax, ay = points[a]
        bx, by = points[b]
        paths.append("M{:.2f},{:.2f}L{:.2f},{:.2f}".format(ax, ay, bx, by))
    dots = "".join('<circle cx="{:.2f}" cy="{:.2f}" r="1.8"/>'.format(*points[n["id"]]) for n in nodes)
    title = html.escape(campus["label"] + "校园路网连接纹理")
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {} {}" role="img">'
            '<title>{}</title><desc>原始坐标等比例投影与现有连接关系；非道路实形。裁切不改变节点连接。</desc>'
            '<path d="{}" fill="none" stroke="#2157a0" stroke-opacity=".35" stroke-width="1.1"/>'
            '<g fill="#2157a0" fill-opacity=".6">{}</g></svg>').format(w, h, title, "".join(paths), dots)


def build():
    assets = HERE / "assets"
    assets.mkdir(exist_ok=True)
    campuses, manifest = {}, {
        "baseline_head": "8dcaa9fc29c1f69ce720f14cdc6cc4ef43fd64d6",
        "route_function": "src.p3_route_provider.plan_route",
        "time_function": "src.p3_time_estimator.estimate_travel_time(distance, mode, caller=None)",
        "source_code_sha256": {}, "campuses": {},
    }
    for name in ("p3_map_loader.py", "p3_route_provider.py", "p3_time_estimator.py", "p3_route_planner.py", "p3_class_prep.py"):
        manifest["source_code_sha256"]["src/" + name] = hashlib.sha256((ROOT / "src" / name).read_bytes()).hexdigest()
    for key, scenario in SCENARIOS.items():
        path = ROOT / "data" / (key + "_map.json")
        source = path.read_bytes()
        raw = json.loads(source)
        model = load_campus_map_data(path)
        mean_lat = sum(n["latitude"] for n in raw["nodes"]) / len(raw["nodes"])
        mean_lon = sum(n["longitude"] for n in raw["nodes"]) / len(raw["nodes"])
        lon_factor = 111320 * math.cos(math.radians(mean_lat))
        nodes = [{
            "id": n["id"], "name": n["name"], "category": n["category"],
            "lon": n["longitude"], "lat": n["latitude"],
            "x": round((n["longitude"] - mean_lon) * lon_factor, 3),
            "y": round(-(n["latitude"] - mean_lat) * 111320, 3),
            "approximate": approximate(n),
        } for n in raw["nodes"]]
        by_id = {n["id"]: n for n in nodes}
        active = _build_adjacency(model, scenario["mode"])
        active_pairs = sorted({tuple(sorted((base_id(a), base_id(b)))) for a, neighbors in active.items() for b, _ in neighbors if base_id(a) != base_id(b)})
        texture_pairs = sorted({tuple(sorted((e["from"], e["to"]))) for e in raw["edges"] if e["from"] != e["to"]})
        campus = {
            "id": key, "label": scenario["label"], "code": scenario["code"],
            "rawNodeCount": len(raw["nodes"]), "rawEdgeCount": len(raw["edges"]),
            "loadedNodeCount": len(model.nodes), "activePairCount": len(active_pairs),
            "nodes": nodes, "edges": active_pairs, "textureEdges": texture_pairs,
            "source": "data/" + path.name, "sha256": hashlib.sha256(source).hexdigest(),
            "geometry": "node_connections_only", "routes": [],
            "schedule": {
                "reference": REFERENCE.strftime("%H:%M"),
                "focusMinutes": FOCUS_MINUTES,
                "focusUntil": (REFERENCE + timedelta(minutes=FOCUS_MINUTES)).strftime("%H:%M"),
                "nextUntil": (REFERENCE + timedelta(minutes=FOCUS_MINUTES + NEXT_TASK_MINUTES)).strftime("%H:%M"),
                "classStarts": CLASS_START.strftime("%H:%M"),
                "classPreparationMinutes": CLASS_ARRIVAL_LEAD_MINUTES,
            },
        }
        for node_id, label in zip(scenario["stops"], scenario["short"]):
            if model.resolve_node_id(label) != node_id:
                raise ValueError("Display label does not resolve to the selected POI: " + label)
        for index, (origin, destination) in enumerate(zip(scenario["stops"], scenario["stops"][1:])):
            result = plan_route(model, origin, destination, scenario["mode"])
            if result.status.value != "computed" or result.total_distance_m is None:
                raise ValueError("Demonstration route unavailable: " + origin + " -> " + destination)
            path_ids = [base_id(item) for item in result.path]
            if any(item not in by_id for item in path_ids):
                raise ValueError("Route contains an unexported source node")
            edge_distances = []
            for a, b in zip(result.path, result.path[1:]):
                distances = [distance for neighbor, distance in active[a] if neighbor == b]
                if not distances:
                    raise ValueError("Route segment absent from production walking adjacency")
                edge_distances.append(min(distances))
            if sum(edge_distances) != result.total_distance_m:
                raise ValueError("Production distance does not match path")
            estimate = estimate_travel_time(result.total_distance_m, result.mode, caller=None)
            minutes = estimate.estimated_minutes
            if index == 0:
                pack = REFERENCE + timedelta(minutes=FOCUS_MINUTES + NEXT_TASK_MINUTES)
                depart = pack + timedelta(minutes=TRANSITION_BUFFER_MINUTES)
                arrive = depart + timedelta(minutes=minutes)
            else:
                arrive = CLASS_START - timedelta(minutes=CLASS_ARRIVAL_LEAD_MINUTES)
                depart = arrive - timedelta(minutes=minutes)
                pack = depart - timedelta(minutes=TRANSITION_BUFFER_MINUTES)
            campus["routes"].append({
                "id": key + "-" + str(index + 1), "origin": origin, "destination": destination,
                "originLabel": scenario["short"][index], "destinationLabel": scenario["short"][index + 1],
                "path": path_ids, "productionPath": list(result.path),
                "distance": result.total_distance_m, "segmentDistances": edge_distances,
                "originName": by_id[origin]["name"], "destinationName": by_id[destination]["name"],
                "mode": result.mode.value,
                "modeLabel": "步行" if result.mode is TransportMode.WALK else "骑行",
                "pack": pack.strftime("%H:%M"), "depart": depart.strftime("%H:%M"), "arrive": arrive.strftime("%H:%M"),
                "estimatedMinutes": minutes,
                "minMinutes": estimate.min_minutes, "maxMinutes": estimate.max_minutes,
                "transitionMinutes": TRANSITION_BUFFER_MINUTES,
                "timeSource": estimate.method.value,
                "timeFunction": "src.p3_time_estimator.estimate_travel_time",
                "approximate": any(by_id[item]["approximate"] for item in path_ids),
            })
        campuses[key] = campus
        (assets / (key + "-topology.svg")).write_text(asset_svg(campus), encoding="utf-8")
        (assets / (key + "-ribbon.svg")).write_text(asset_svg(campus, ribbon=True), encoding="utf-8")
        manifest["campuses"][key] = {
            "source": campus["source"], "sha256": campus["sha256"],
            "raw_nodes": campus["rawNodeCount"], "raw_edges": campus["rawEdgeCount"],
            "loaded_nodes": campus["loadedNodeCount"], "active_walking_connection_pairs": len(active_pairs),
            "approximate_by_source_evidence": sum(n["approximate"] for n in nodes),
            "polyline_fields": 0, "routes": campus["routes"],
            "schedule": campus["schedule"],
        }
    (assets / "networks.js").write_text("window.CAMPUSFLOW_ATLAS = " + json.dumps(campuses, ensure_ascii=False, separators=(",", ":")) + ";\n", encoding="utf-8")
    (assets / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Built 2 source-derived topologies, 2 ribbons and 4 routes with production time estimates.")
    for key, campus in campuses.items():
        print(key, "raw:", campus["rawNodeCount"], campus["rawEdgeCount"], "route distances:", [r["distance"] for r in campus["routes"]])
        print("estimated walking minutes:", [r["estimatedMinutes"] for r in campus["routes"]])


if __name__ == "__main__":
    build()
