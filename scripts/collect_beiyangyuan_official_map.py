"""P3d 开发脚本：按天津大学北洋园官方新版地图（分区图示）批量补全缺失地点。

数据来源分级：
- AMAP_VERIFIED                 ：高德精确核验（既有节点）
- TJU_OFFICIAL_MAP_APPROXIMATE  ：官方地图 + 多控制点仿射校准的近似定位
- UNAVAILABLE                   ：官方图与高德均无法可靠定位（保留缺口）

本脚本只处理官方地图主体可辨认标注的地点，近似坐标绝不冒充高德精确数据：
- 用既有高德真实节点（信息与网络中心/行政服务中心/幼儿园/太雷广场/书田广场/
  三问桥/宣怀广场/北洋广场/体育场/大通学生中心）作为控制点拟合 像素->经纬度 仿射映射；
- 新增节点坐标全部来自该映射，source 明确标注官方图近似；
- 青园餐厅/教师公寓额外用高德 POI 地址引用交叉核验（海棠餐厅地址=青园餐厅一楼；
  顺丰地址=青年教师公寓1号楼）；
- 新增边距离为 haversine 近似值，标注为近似，不伪装高德 direction 结果。

仅开发阶段使用；运行时（src/）不得 import 本脚本。
"""
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "data" / "beiyangyuan_map.json"

# 官方地图控制点：(像素x, 像素y, 既有高德节点id)
CONTROL_POINTS = [
    (1084, 690, "network_center"),
    (1416, 569, "administrative_service_center"),
    (1430, 922, "kindergarten"),
    (928, 733, "datong_student_center"),
    (936, 819, "tailei_square"),
    (1168, 746, "shutian_square"),
    (1374, 751, "sanwen_bridge"),
    (1485, 755, "xuanhuai_square"),
    (1584, 825, "beiyang_plaza"),
    (1460, 423, "beiyang_stadium"),
]

APPROX_SOURCE = "TJU 官方地图近似（多控制点仿射校准，非高德精确坐标）"

# (node_id, 显示名, 像素x, 像素y 或 None, category, aliases, provenance, source)
NEW_NODES = [
    ("beiyangyuan_north_gate", "天津大学北洋园校区北门", 992, 332, "gate",
     ["北门"], "tju_official_map_approximate", APPROX_SOURCE),
    ("beiyangyuan_northeast_gate", "天津大学北洋园校区东北门", 1543, 296, "gate",
     ["东北门"], "tju_official_map_approximate", APPROX_SOURCE),
    ("beiyangyuan_northwest_gate", "天津大学北洋园校区西北门", 626, 483, "gate",
     ["西北门", "西北门（二期）"], "tju_official_map_approximate", APPROX_SOURCE),
    ("beiyangyuan_southwest_gate", "天津大学北洋园校区西南门", 584, 937, "gate",
     ["西南门"], "tju_official_map_approximate", APPROX_SOURCE),
    ("beiyangyuan_southeast_gate", "天津大学北洋园校区东南门", 1356, 1211, "gate",
     ["东南门"], "tju_official_map_approximate", APPROX_SOURCE),
    ("beiyangyuan_east_gate", "天津大学北洋园校区东门", 1704, 802, "gate",
     ["东门"], "tju_official_map_approximate", APPROX_SOURCE),
    ("qingnian_lake", "天津大学北洋园校区青年湖", 804, 715, "landmark",
     ["青年湖"], "tju_official_map_approximate", APPROX_SOURCE),
    ("zhengyuan_9zhai", "天津大学北洋园校区正园9斋", 1234, 690, "dormitory",
     ["正园9斋", "正园九斋", "9斋", "九斋"], "tju_official_map_approximate", APPROX_SOURCE),
    ("zhengyuan_10zhai", "天津大学北洋园校区正园10斋", 1178, 690, "dormitory",
     ["正园10斋", "正园十斋", "10斋", "十斋"], "tju_official_map_approximate", APPROX_SOURCE),
    ("qiyuan_13zhai", "天津大学北洋园校区齐园13斋", 1227, 921, "dormitory",
     ["齐园13斋", "齐园十三斋", "13斋", "十三斋"], "tju_official_map_approximate", APPROX_SOURCE),
    ("qiyuan_14zhai", "天津大学北洋园校区齐园14斋", 1235, 964, "dormitory",
     ["齐园14斋", "齐园十四斋", "14斋", "十四斋"], "tju_official_map_approximate", APPROX_SOURCE),
    ("building_37", "天津大学北洋园校区37教学楼", 1320, 946, "teaching",
     ["37教", "37教学楼", "37楼", "37号教学楼"], "tju_official_map_approximate", APPROX_SOURCE),
    ("teacher_apartment", "天津大学北洋园校区教师公寓", 1422, 321, "service",
     ["教师公寓", "青年教师公寓"], "tju_official_map_approximate",
     "TJU 官方地图近似（多控制点仿射校准）+ 高德地址交叉核验（顺丰 POI 地址=青年教师公寓1号楼）"),
]

# 青园餐厅：无直接高德 POI；海棠餐厅高德地址注明“青园餐厅一楼”，坐标取该地址引用
QINGYUAN_CANTEEN = {
    "id": "qingyuan_canteen",
    "name": "天津大学北洋园校区青园餐厅",
    "category": "dining",
    "aliases": ["青园餐厅", "学者公寓食堂"],
    "provenance": "tju_official_map_approximate",
    "source": "TJU 官方地图图例确认存在 + 高德地址交叉核验（海棠餐厅 POI 地址=青园餐厅一楼，坐标与海棠餐厅同址）",
    "anchor_node": "haitang_canteen",
}

# 新增边的就近锚点（haversine 近似距离，标注近似）
EDGE_ANCHORS = {
    "beiyangyuan_north_gate": ["beiyangyuan_north_parking_entrance", "geyuan_dorm", "liuyuan_dorm"],
    "beiyangyuan_northeast_gate": ["campus_hospital", "beiyang_clinic", "beiyang_stadium"],
    "beiyangyuan_northwest_gate": ["sanwenyuan_30zhai", "sanwenyuan_dorm", "rishin_park"],
    "beiyangyuan_southwest_gate": ["beiyangyuan_metro_station", "yuyuan_garden", "tailei_square"],
    "beiyangyuan_southeast_gate": ["building_34", "building_35", "sports_park"],
    "beiyangyuan_east_gate": ["beiyang_plaza", "qiushui_hall", "xuanhuai_square"],
    "qingnian_lake": ["datong_student_center", "sanwenyuan_dorm", "tailei_square"],
    "zhengyuan_9zhai": ["zhengyuan_dorm", "lan_yuan_canteen", "dorm_service_center"],
    "zhengyuan_10zhai": ["zhengyuan_dorm", "shutian_square", "beiyang_memorial_forest"],
    "qiyuan_13zhai": ["qiyuan_dorm", "qiyuan_express_station", "xuesi_cafeteria"],
    "qiyuan_14zhai": ["building_31", "qiyuan_dorm", "xuesi_cafeteria"],
    "building_37": ["building_36", "building_31", "qiyuan_dorm"],
    "teacher_apartment": ["beiyang_clinic", "haitang_canteen", "campus_hospital"],
    "qingyuan_canteen": ["beiyang_clinic", "school_of_math", "building_52"],
}


def _haversine(lat1, lng1, lat2, lng2):
    radius = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2
    return 2.0 * radius * math.asin(math.sqrt(a))


def _fit_affine(points, values):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    n = len(points)
    sx = sum(xs)
    sy = sum(ys)
    sxx = sum(x * x for x in xs)
    syy = sum(y * y for y in ys)
    sxy = sum(x * y for x, y in zip(xs, ys))
    sv = sum(values)
    sxv = sum(x * v for x, v in zip(xs, values))
    syv = sum(y * v for y, v in zip(ys, values))
    det = n * (sxx * syy - sxy * sxy) - sx * (sx * syy - sxy * sy) + sy * (sx * sxy - sxx * sy)
    a0 = (sv * (sxx * syy - sxy * sxy) - sx * (sxv * syy - syv * sxy) + sy * (sxv * sxy - syv * sxx)) / det
    a1 = (n * (sxv * syy - syv * sxy) - sx * (sv * syy - syv * sy) + sy * (sv * sxy - sxv * sy)) / det
    a2 = (n * (sxx * syv - sxy * sxv) - sx * (sx * syv - sxy * sv) + sy * (sx * sxv - sxx * sv)) / det
    return a0, a1, a2


def main():
    payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    existing = {n["id"]: n for n in payload["nodes"]}
    node_ids = set(existing)

    pts = [(x, y) for x, y, _ in CONTROL_POINTS]
    coef_lat = _fit_affine(pts, [existing[i]["latitude"] for _, _, i in CONTROL_POINTS])
    coef_lng = _fit_affine(pts, [existing[i]["longitude"] for _, _, i in CONTROL_POINTS])

    def predict(x, y):
        return (coef_lat[0] + coef_lat[1] * x + coef_lat[2] * y,
                coef_lng[0] + coef_lng[1] * x + coef_lng[2] * y)

    added = []
    for node_id, name, px, py, category, aliases, provenance, source in NEW_NODES:
        if node_id in node_ids:
            raise SystemExit("重复节点 id：{}".format(node_id))
        lat, lng = predict(px, py)
        payload["nodes"].append({
            "id": node_id,
            "name": name,
            "aliases": aliases,
            "category": category,
            "latitude": round(lat, 6),
            "longitude": round(lng, 6),
            "source": source,
            "provenance": provenance,
        })
        node_ids.add(node_id)
        added.append(node_id)

    # 青园餐厅（坐标取高德地址引用锚点）
    qc = QINGYUAN_CANTEEN
    if qc["id"] in node_ids:
        raise SystemExit("重复节点 id：{}".format(qc["id"]))
    anchor = existing[qc["anchor_node"]]
    payload["nodes"].append({
        "id": qc["id"],
        "name": qc["name"],
        "aliases": qc["aliases"],
        "category": qc["category"],
        "latitude": anchor["latitude"],
        "longitude": anchor["longitude"],
        "source": qc["source"],
        "provenance": qc["provenance"],
    })
    node_ids.add(qc["id"])
    added.append(qc["id"])

    # 新增边（haversine 近似）
    edge_source = "TJU 官方地图近似节点接入既有路网（haversine 近似距离，非高德 direction 结果）"
    edge_keys = {(e.get("from"), e.get("to")) for e in payload.get("edges", [])}
    for node_id, anchors in EDGE_ANCHORS.items():
        node = next(n for n in payload["nodes"] if n["id"] == node_id)
        for anchor_id in anchors:
            if anchor_id not in node_ids:
                raise SystemExit("锚点不存在：{}".format(anchor_id))
            if (node_id, anchor_id) in edge_keys or (anchor_id, node_id) in edge_keys:
                continue
            an = next(n for n in payload["nodes"] if n["id"] == anchor_id)
            dist = int(round(_haversine(node["latitude"], node["longitude"],
                                         an["latitude"], an["longitude"])))
            if dist <= 0:
                continue
            payload["edges"].append({
                "from": node_id,
                "to": anchor_id,
                "distance_m": dist,
                "modes": ["walk", "bike"],
                "bidirectional": True,
                "source": edge_source,
            })

    metadata = payload.setdefault("metadata", {})
    note = ("P3d 官方图补充：新增 {} 个 TJU_OFFICIAL_MAP_APPROXIMATE 节点（6 校门、青年湖、"
            "正园9/10斋、齐园13/14斋、37教、教师公寓、青园餐厅），坐标来自官方图多控制点仿射校准，"
            "近似数据不冒充高德精确数据。".format(len(added)))
    metadata["note"] = (metadata.get("note", "") + "；" + note).strip("；")

    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("added nodes:", len(added))
    print("total nodes:", len(payload["nodes"]), "edges:", len(payload["edges"]))


if __name__ == "__main__":
    main()