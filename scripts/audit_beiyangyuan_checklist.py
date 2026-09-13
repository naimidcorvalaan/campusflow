"""P3d 北洋园地点母表逐项审计脚本（仅开发阶段，不面向产品前端）。

依据用户母表对天津大学北洋园地点逐项判定覆盖状态：
- COVERED_EXACT ：清单地点文本本身可直接解析到节点
- COVERED_ALIAS ：清单地点通过别名解析到同地点节点（异名合并）
- COARSE_ONLY   ：只有粗粒度节点（如园区），细粒度（如斋号）未独立建节点
- MISSING       ：确认存在但当前地图无法解析 / 位置无法可靠定位
- UNVERIFIED    ：存在性或位置仍无法可靠确认
- NOT_REAL      ：用户确认不存在（龙园、书园），不得作为真实 POI

用法：
  python scripts/audit_beiyangyuan_checklist.py
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAP = ROOT / "data" / "beiyangyuan_map.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.p3_map_loader import load_campus_map_data  # noqa: E402
from src.p3_route_provider import plan_route  # noqa: E402


# 清单项：(条目id, 类别, 规范名, [学生常用说法], 说明)
# 类别：building / dorm / dorm_area / function / canteen / landmark / gate / logistics
CHECKLIST = [
    # A. 教学楼（31~59 教的常用说法）
    *[("j%02d" % n, "building", "%d教" % n, ["%d教" % n, "%d教学楼" % n, "%d号教学楼" % n, "%d楼" % n], None) for n in range(31, 60)],
    # B. 功能建筑
    ("lib", "function", "图书馆", ["图书馆", "郑东图书馆"], "官方图“图书馆”=郑东图书馆"),
    ("student_center", "function", "学生中心", ["学生中心", "大通学生中心"], "官方图“学生中心”=大通学生中心"),
    ("admin_service", "function", "行政服务中心", ["行政服务中心"], None),
    ("network_center", "function", "信息与网络中心", ["信息与网络中心", "网络中心"], None),
    ("dorm_service", "function", "学生生活园区管理服务中心", ["学生生活园区管理服务中心", "学服中心"], None),
    ("security", "function", "安保中心", ["安保中心", "保卫处"], None),
    ("sports_hall", "function", "综合体育馆", ["综合体育馆", "体育馆"], None),
    ("sports_park", "function", "体育公园", ["体育公园"], None),
    ("clinic", "function", "北洋门诊部", ["北洋门诊部", "门诊部"], "与校医院为不同 POI（相距约100m，高德两个独立 POI）"),
    ("teacher_apt", "function", "教师公寓", ["教师公寓"], None),
    ("kindergarten", "function", "幼儿园", ["幼儿园", "天大幼儿园"], None),
    # C. 食堂
    ("c1", "canteen", "梅园餐厅", ["梅园餐厅", "梅园", "学一", "学一食堂"], "梅园=学一食堂"),
    ("c2", "canteen", "兰园餐厅", ["兰园餐厅", "兰园", "学二", "学二食堂"], "兰园=学二食堂"),
    ("c3", "canteen", "棠园餐厅", ["棠园餐厅", "棠园", "学三", "学三食堂"], "棠园=学三食堂"),
    ("c4", "canteen", "竹园餐厅", ["竹园餐厅", "学四", "学四食堂"], "竹园=学四食堂"),
    ("c5", "canteen", "桃园餐厅", ["桃园餐厅", "桃园", "学五", "学五食堂"], "桃园=学五食堂"),
    ("c6", "canteen", "菊园餐厅", ["菊园餐厅", "清真食堂"], None),
    ("c7", "canteen", "留园餐厅", ["留园餐厅", "留学生食堂"], None),
    ("c8", "canteen", "青园餐厅", ["青园餐厅"], "海棠餐厅（高德地址=青园餐厅一楼）已并入青园餐厅"),
    # D. 宿舍园区（粗粒度）
    ("d_geyuan", "dorm_area", "格园", ["格园"], None),
    ("d_zhiyuan", "dorm_area", "知园", ["知园"], None),
    ("d_chengyuan", "dorm_area", "诚园", ["诚园"], None),
    ("d_zhengyuan", "dorm_area", "正园", ["正园"], None),
    ("d_xiuyuan", "dorm_area", "修园", ["修园"], None),
    ("d_qiyuan", "dorm_area", "齐园", ["齐园"], None),
    ("d_zhiyuan2", "dorm_area", "治园", ["治园"], None),
    ("d_pingyuan", "dorm_area", "平园", ["平园"], None),
    ("d_liuyuan", "dorm_area", "留园", ["留园"], None),
    ("d_boxue", "dorm_area", "博学园", ["博学园"], "用户确认：博学园=25/26斋，与三问园（27~30斋）为两个独立园区"),
    ("d_sanwen", "dorm_area", "三问园", ["三问园"], "用户确认：三问园=27/28/29/30斋，与博学园（25/26斋）为两个独立园区"),
    # D. 学生宿舍斋号 1~30
    ("zhai01", "dorm", "格园1斋", ["格园1斋", "格园一斋", "1斋"], None),
    ("zhai02", "dorm", "格园2斋", ["格园2斋", "格园二斋", "2斋"], None),
    ("zhai03", "dorm", "格园3斋", ["格园3斋", "格园三斋", "3斋"], None),
    ("zhai04", "dorm", "知园4斋", ["知园4斋", "知园四斋", "4斋"], "知园四斋B座已独立建节点"),
    ("zhai05", "dorm", "知园5斋", ["知园5斋", "知园五斋", "5斋"], None),
    ("zhai06", "dorm", "诚园6斋", ["诚园6斋", "诚园六斋", "6斋"], None),
    ("zhai07", "dorm", "诚园7斋", ["诚园7斋", "诚园七斋", "7斋"], None),
    ("zhai08", "dorm", "诚园8斋", ["诚园8斋", "诚园八斋", "8斋"], None),
    ("zhai09", "dorm", "正园9斋", ["正园9斋", "正园九斋", "9斋"], None),
    ("zhai10", "dorm", "正园10斋", ["正园10斋", "正园十斋", "10斋"], None),
    ("zhai11", "dorm", "修园11斋", ["修园11斋", "修园十一斋", "11斋"], None),
    ("zhai12", "dorm", "修园12斋", ["修园12斋", "修园十二斋", "12斋"], None),
    ("zhai13", "dorm", "齐园13斋", ["齐园13斋", "齐园十三斋", "13斋"], None),
    ("zhai14", "dorm", "齐园14斋", ["齐园14斋", "齐园十四斋", "14斋"], None),
    ("zhai15", "dorm", "齐园15斋", ["齐园15斋", "齐园十五斋", "15斋"], None),
    ("zhai16", "dorm", "齐园16斋", ["齐园16斋", "齐园十六斋", "16斋"], None),
    ("zhai17", "dorm", "治园17斋", ["治园17斋", "治园十七斋", "17斋"], None),
    ("zhai18", "dorm", "治园18斋", ["治园18斋", "治园十八斋", "18斋"], None),
    ("zhai19", "dorm", "治园19斋", ["治园19斋", "治园十九斋", "19斋"], None),
    ("zhai19a", "dorm", "治园19斋增1", ["治园19斋增1", "治园十九斋增1", "19斋增1"], None),
    ("zhai20", "dorm", "治园20斋", ["治园20斋", "治园二十斋", "20斋"], None),
    ("zhai21", "dorm", "平园21斋", ["平园21斋", "21斋"], "平园21斋A座已独立建节点"),
    ("zhai22", "dorm", "平园22斋", ["平园22斋", "平园二十二斋", "22斋"], None),
    ("zhai23", "dorm", "平园23斋", ["平园23斋", "平园二十三斋", "23斋"], None),
    ("zhai24", "dorm", "平园24斋", ["平园24斋", "平园二十四斋", "24斋"], None),
    ("zhai25", "dorm", "博学园25斋", ["博学园25斋", "25斋"], None),
    ("zhai26", "dorm", "博学园26斋", ["博学园26斋", "26斋"], None),
    ("zhai27", "dorm", "三问园27斋", ["三问园27斋", "27斋"], None),
    ("zhai28", "dorm", "三问园28斋", ["三问园28斋", "28斋"], None),
    ("zhai29", "dorm", "三问园29斋", ["三问园29斋", "29斋"], None),
    ("zhai30", "dorm", "三问园30斋", ["三问园30斋", "30斋"], None),
    # E. 地标 / 户外（用户确认：龙园、书园不存在；敬业湖/校训石/篮球场/排球场待核验）
    ("lm_qingnian", "landmark", "青年湖", ["青年湖"], None),
    ("lm_jingye", "landmark", "敬业湖", ["敬业湖"], "高德无独立 POI，官方图未可靠标注位置"),
    ("lm_rixin", "landmark", "日新园", ["日新园"], "高德 POI 核验存在"),
    ("lm_yu", "landmark", "御园", ["御园"], "高德 POI 核验存在"),
    ("lm_tailei", "landmark", "太雷广场", ["太雷广场"], None),
    ("lm_xuanhuai", "landmark", "宣怀广场", ["宣怀广场"], None),
    ("lm_beiyang", "landmark", "北洋广场", ["北洋广场"], None),
    ("lm_xiaoxun", "landmark", "校训石", ["校训石"], "高德无独立 POI"),
    ("lm_stadium", "landmark", "体育场", ["体育场"], None),
    ("lm_sportspark", "landmark", "体育公园", ["体育公园"], None),
    ("lm_gym", "landmark", "综合体育馆", ["综合体育馆"], None),
    ("lm_football", "landmark", "足球场", ["足球场"], None),
    ("lm_basketball", "landmark", "篮球场", ["篮球场"], "高德无独立 POI，官方图未标注，不凑覆盖率"),
    ("lm_volleyball", "landmark", "排球场", ["排球场"], "高德无独立 POI，官方图未标注，不凑覆盖率"),
    ("lm_long", "landmark", "龙园", ["龙园"], "用户确认不存在，不得作为真实 POI"),
    ("lm_shu", "landmark", "书园", ["书园"], "用户确认不存在，不得作为真实 POI"),
    # F. 校门
    *[("gate_%s" % g, "gate", g, [g], None) for g in ("北门", "西北门", "西南门", "南门", "东南门", "东门", "东北门")],
    # G. 学生高频地点
    ("lg_north", "logistics", "北区菜鸟驿站", ["北区菜鸟驿站", "北区菜鸟", "北菜"], None),
    ("lg_south", "logistics", "南区菜鸟驿站", ["南区菜鸟驿站", "南菜"], None),
    ("lg_qiyuan", "logistics", "齐园快递站", ["齐园快递站"], None),
    ("lg_south_express", "logistics", "南区快递站", ["南区快递站"], None),
    ("sv_market", "logistics", "北洋超市", ["北洋超市", "超市"], None),
    ("md_hospital", "function", "校医院", ["校医院", "医院"], "与北洋门诊部为不同 POI（相距约100m）"),
    ("tr_metro", "function", "北洋园地铁站", ["北洋园地铁站", "地铁站"], None),
]

# 斋号 -> 所属园区（用于 COARSE_ONLY 判定）
DORM_PARENT = {
    "zhai01": "d_geyuan", "zhai02": "d_geyuan", "zhai03": "d_geyuan",
    "zhai04": "d_zhiyuan", "zhai05": "d_zhiyuan",
    "zhai06": "d_chengyuan", "zhai07": "d_chengyuan", "zhai08": "d_chengyuan",
    "zhai09": "d_zhengyuan", "zhai10": "d_zhengyuan",
    "zhai11": "d_xiuyuan", "zhai12": "d_xiuyuan",
    "zhai13": "d_qiyuan", "zhai14": "d_qiyuan", "zhai15": "d_qiyuan", "zhai16": "d_qiyuan",
    "zhai17": "d_zhiyuan2", "zhai18": "d_zhiyuan2", "zhai19": "d_zhiyuan2",
    "zhai19a": "d_zhiyuan2", "zhai20": "d_zhiyuan2",
    "zhai21": "d_pingyuan", "zhai22": "d_pingyuan", "zhai23": "d_pingyuan", "zhai24": "d_pingyuan",
    "zhai25": "d_boxue", "zhai26": "d_boxue",
    "zhai27": "d_sanwen", "zhai28": "d_sanwen",
    "zhai29": "d_sanwen", "zhai30": "d_sanwen",
}

# 用户确认不存在的地点（不进入未完成清单，不建节点）
NOT_REAL = {"lm_long", "lm_shu"}

# 语义偏差：别名解析到节点，但清单语义与节点语义不符（不标 COVERED）
# 用户确认博学园=25/26斋、三问园=27~30斋；25/26 已按博学园建节点，不再列入语义缺口。
SEMANTIC_GAP = {}


def build_index(payload):
    # 分别记录 id/名称 与 别名，用于区分 COVERED_EXACT / COVERED_ALIAS
    exact_index = {}
    alias_index = {}
    for node in payload["nodes"]:
        for identifier in [node["id"], node["name"]]:
            exact_index[identifier.strip().casefold()] = node["id"]
        for identifier in node.get("aliases", []):
            alias_index[identifier.strip().casefold()] = node["id"]
    return exact_index, alias_index


def load_head_payload():
    try:
        raw = subprocess.check_output(
            ["git", "show", "HEAD:data/beiyangyuan_map.json"],
            cwd=str(ROOT), stderr=subprocess.DEVNULL,
        )
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


def audit(payload):
    exact_index, alias_index = build_index(payload)
    nodes_by_id = {n["id"]: n for n in payload["nodes"]}
    names = {n["id"]: n["name"] for n in payload["nodes"]}

    def resolve(text):
        key = text.strip().casefold()
        if key in exact_index:
            return exact_index[key], "EXACT"
        if key in alias_index:
            return alias_index[key], "ALIAS"
        return None, None

    status_by_item = {}
    detail = []  # (status, category, item_id, canonical_name, node_id, node_name, note, node_aliases, node_source, action)
    by_category = {}
    for item_id, category, canonical, surface_names, note in CHECKLIST:
        resolved = []
        for name in surface_names:
            node_id, mode = resolve(name)
            resolved.append((name, node_id, mode))
        node_ids = sorted({node_id for _, node_id, _ in resolved if node_id is not None})
        hit_modes = {mode for _, _, mode in resolved if mode is not None}
        gap_note = SEMANTIC_GAP.get(item_id)
        if item_id in NOT_REAL:
            status = "NOT_REAL"
        elif item_id in SEMANTIC_GAP:
            status = "UNVERIFIED"
        elif node_ids and "EXACT" in hit_modes:
            status = "COVERED_EXACT"
        elif node_ids:
            status = "COVERED_ALIAS"
        elif category == "dorm":
            parent_item = DORM_PARENT.get(item_id)
            parent_ok = False
            if parent_item is not None:
                parent_entry = next(
                    (i for i, c, cn, s, _ in CHECKLIST if i == parent_item and c == "dorm_area"), None
                )
                if parent_entry is not None:
                    parent_surfaces = next(s for i, c, cn, s, _ in CHECKLIST if i == parent_item)
                    parent_ok = resolve(parent_surfaces[0])[0] is not None
            status = "COARSE_ONLY" if parent_ok else "MISSING"
        else:
            status = "MISSING"

        node_id = node_ids[0] if node_ids else None
        node = nodes_by_id.get(node_id) if node_id else None
        node_aliases = list(node.get("aliases", [])) if node else []
        node_source = (node.get("source") or "") if node else ""
        action = ""
        if status in ("COVERED_EXACT", "COVERED_ALIAS"):
            action = "已覆盖（节点 {}）".format(node_id)
        elif status == "COARSE_ONLY":
            action = "仅园区级节点，斋号未独立建节点（高德无独立 POI）"
        elif status == "MISSING":
            action = "保持缺口，未编造"
        elif status == "UNVERIFIED":
            action = "存在性/位置未可靠确认，未建节点"
        elif status == "NOT_REAL":
            action = "用户确认不存在，未建节点"
        effective_note = gap_note or note or ""
        if status == "UNVERIFIED" and gap_note is None:
            effective_note = note or "存在性/位置未可靠确认"
        status_by_item[item_id] = status
        by_category.setdefault(category, {})
        by_category[category][status] = by_category[category].get(status, 0) + 1
        detail.append((
            status, category, item_id, canonical,
            node_id or "-", names.get(node_id, "") if node_id else "-",
            effective_note, node_aliases, node_source, action,
        ))
    return status_by_item, detail, by_category


def main() -> None:
    payload = json.loads(MAP.read_text(encoding="utf-8"))
    status_by_item, detail, by_category = audit(payload)

    # ===== 完整覆盖审计表（J 节第一份清单）=====
    print("=== 完整覆盖审计表 ===")
    print("canonical_name | category | status | node_id | aliases | location_source | action_taken | remaining_issue")
    for status, category, item_id, canonical, node_id, node_name, note, node_aliases, node_source, action in detail:
        aliases_text = "、".join(node_aliases) if node_aliases else "-"
        source_short = (node_source or "-").replace("\n", " ")[:60]
        print("{} | {} | {} | {} | {} | {} | {} | {}".format(
            canonical, category, status, node_id, aliases_text, source_short, action, note or "无"
        ))

    def count(pred):
        return sum(1 for s in status_by_item.values() if pred(s))

    counts = {
        "COVERED_EXACT": count(lambda s: s == "COVERED_EXACT"),
        "COVERED_ALIAS": count(lambda s: s == "COVERED_ALIAS"),
        "COARSE_ONLY": count(lambda s: s == "COARSE_ONLY"),
        "MISSING": count(lambda s: s == "MISSING"),
        "UNVERIFIED": count(lambda s: s == "UNVERIFIED"),
        "NOT_REAL": count(lambda s: s == "NOT_REAL"),
    }
    print()
    print("=== 汇总 ===")
    for status in ("COVERED_EXACT", "COVERED_ALIAS", "COARSE_ONLY", "MISSING", "UNVERIFIED", "NOT_REAL"):
        print(status, counts[status])

    # ===== 未完成地点清单（J 节第二份清单）=====
    print()
    print("=== 仍未完成地点清单 ===")
    incomplete = [d for d in detail if d[0] in ("COARSE_ONLY", "MISSING", "UNVERIFIED")]
    for status, category, item_id, canonical, node_id, node_name, note, _, _, action in incomplete:
        print("  [{}] {}（{}）——{}".format(status, canonical, category, action))
    if not incomplete:
        print("  （无）")

    # ===== 统计 =====
    cat_total = {}
    cat_covered = {}
    for cat_name in ("building", "dorm", "canteen", "function", "gate", "logistics", "landmark", "dorm_area"):
        items = [i for i, c, cn, s, _ in CHECKLIST if c == cat_name]
        cat_total[cat_name] = len(items)
        cat_covered[cat_name] = sum(
            1 for i in items if status_by_item[i] in ("COVERED_EXACT", "COVERED_ALIAS")
        )
    print()
    print("=== 类别完成统计 ===")
    for cat_name in ("building", "dorm", "canteen", "function", "gate", "logistics", "landmark", "dorm_area"):
        print("  %s: %d/%d" % (cat_name, cat_covered[cat_name], cat_total[cat_name]))

    head = load_head_payload()
    head_covered = None
    added_nodes = []
    if head is not None:
        head_status, _, _ = audit(head)
        head_covered = sum(1 for s in head_status.values() if s in ("COVERED_EXACT", "COVERED_ALIAS"))
        head_ids = {n["id"] for n in head["nodes"]}
        node_ids = {n["id"] for n in payload["nodes"]}
        added_nodes = sorted(node_ids - head_ids)

    data = load_campus_map_data(MAP)
    unreachable = []
    for node in data.nodes:
        for mode in ("walk", "bike"):
            r = plan_route(data, "beiyangyuan_south_gate", node.id, mode)
            if r.status.name != "COMPUTED":
                unreachable.append((node.id, mode))
    connected = len(unreachable) == 0

    print()
    print("=== 最终覆盖报告 ===")
    print("1. 官方 checklist 总地点数:", len(CHECKLIST))
    print("2. 原本已覆盖数(HEAD):", head_covered)
    print("3. 本轮新增节点数:", len(added_nodes), added_nodes)
    amap_count = sum(1 for n in payload["nodes"] if n.get("provenance") != "tju_official_map_approximate")
    approx_count = sum(1 for n in payload["nodes"] if n.get("provenance") == "tju_official_map_approximate")
    print("4. AMAP_VERIFIED 节点:", amap_count, "；TJU_OFFICIAL_MAP_APPROXIMATE 节点:", approx_count)
    print("5. coarse-only 数:", counts["COARSE_ONLY"])
    print("6. 仍未完成数(MISSING+UNVERIFIED+COARSE_ONLY):", counts["MISSING"] + counts["UNVERIFIED"] + counts["COARSE_ONLY"])
    print("   未完成清单见上（共 %d 项）" % len(incomplete))
    print("12. 最终节点/边:", len(payload["nodes"]), "/", len(payload["edges"]))
    print("13. walk/bike 连通性:", "全连通(%d/%d)" % (len(payload["nodes"]), len(payload["nodes"])) if connected else "存在不可达: %s" % (unreachable,))

    print()
    print("=== 特别报告 ===")
    for key in ("zhai09", "j46", "j32", "j38", "j44", "j45", "j47", "j56", "j57"):
        item = next((x for x in detail if x[2] == key), None)
        if item is not None:
            print(" ", key, item[0], item[3], "| node:", item[4], "|", item[6])
    print("1~30 斋覆盖:")
    for x in [d for d in detail if d[1] == "dorm" and d[2].startswith("zhai")]:
        print("  %s %s" % (x[2], x[0]))
    # 用户确认：博学园（25/26斋）与三问园（27~30斋）为两个独立园区，互不为 alias
    alias_index = {}
    for n in payload["nodes"]:
        for a in n.get("aliases", []):
            alias_index.setdefault(a.strip().casefold(), []).append(n.get("id"))
    def owner(text):
        return alias_index.get(text.strip().casefold(), [])
    boxue_nodes = [n for n in payload["nodes"] if "博学园" in (n.get("name") or "")]
    sanwen_nodes = [n for n in payload["nodes"] if "三问园" in (n.get("name") or "")]
    boxue_ok = all("三问园" not in (n.get("name") or "") for n in boxue_nodes) and \
        all("博学园" not in (n.get("name") or "") for n in sanwen_nodes)
    cross = (
        ["博学园%d斋" % i for i in (27, 28, 29, 30)] +
        ["三问园%d斋" % i for i in (25, 26)] +
        ["博学园30斋"]
    )
    cross_ok = all(not owner(x) for x in cross)
    print("博学园/三问园独立检查:",
          "通过（独立节点，无交叉 alias）" if boxue_ok and cross_ok and boxue_nodes and sanwen_nodes
          else "异常（boxue=%s sanwen=%s cross=%s）" % (boxue_nodes, sanwen_nodes, cross))
    print("龙园/书园残留检查:", "无" if not any(
        (n.get("name") or "") in ("龙园", "书园") or "龙园" in (n.get("name") or "") or "书园" in (n.get("name") or "")
        or any(a in ("龙园", "书园") for a in n.get("aliases", []))
        for n in payload["nodes"]
    ) else "存在残留！")


if __name__ == "__main__":
    main()
