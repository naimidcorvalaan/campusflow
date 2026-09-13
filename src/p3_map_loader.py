"""P3a 地图 JSON loader / validator（Python 3.8 兼容）。

职责：
- 读取并校验本地真实地图 JSON；
- 兼容 P0 locations 旧字段（locations / source / target）；
- 显式判定 provenance，绝不因为文件名是 beiyangyuan 就判真实；
- 不编造道路、距离或坐标。
"""
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from src.p3_map_schema import (
    P3_MAP_SCHEMA_VERSION,
    CampusMapData,
    CampusNode,
    MapDataProvenance,
    RoadEdge,
    TransportMode,
    parse_transport_mode,
)

REAL_PROVENANCE = "real_map"
SYNTHETIC_PROVENANCE = "synthetic_test"


def load_campus_map_data(path: Union[str, Path]) -> CampusMapData:
    """从 JSON 文件加载并校验真实地图数据。"""
    file_path = Path(path)
    try:
        with file_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError as exc:
        raise ValueError("地图文件不存在：{}".format(file_path)) from exc
    except json.JSONDecodeError as exc:
        raise ValueError("地图 JSON 格式错误：{}".format(file_path)) from exc
    except OSError as exc:
        raise ValueError("无法读取地图文件：{}".format(file_path)) from exc
    return parse_campus_map_data(payload)


def parse_campus_map_data(payload: Any) -> CampusMapData:
    """校验字典结构并构造 CampusMapData。"""
    if not isinstance(payload, dict):
        raise ValueError("地图数据必须是 JSON 对象。")
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError("metadata 必须是对象。")

    notes = []

    schema_version = metadata.get("schema_version")
    if schema_version is None:
        schema_version = P3_MAP_SCHEMA_VERSION
        notes.append("缺少 metadata.schema_version，按当前版本解释")
    elif schema_version != P3_MAP_SCHEMA_VERSION:
        raise ValueError("不支持的 schema_version：{}".format(schema_version))

    campus = metadata.get("campus")
    if not isinstance(campus, str) or not campus.strip():
        raise ValueError("metadata.campus 必须是非空字符串。")
    campus = campus.strip()

    campus_id = metadata.get("campus_id")
    if campus_id is not None:
        if not isinstance(campus_id, str) or not campus_id.strip():
            raise ValueError("metadata.campus_id 必须是非空字符串。")
        campus_id = campus_id.strip()

    data_status = metadata.get("data_status")
    if not isinstance(data_status, str) or not data_status.strip():
        data_status = "unknown"
        notes.append("缺少 metadata.data_status")
    else:
        data_status = data_status.strip()

    raw_nodes = payload.get("nodes") if "nodes" in payload else payload.get("locations")
    nodes = _parse_nodes(raw_nodes)
    edges = _parse_edges(payload.get("edges", []))
    _validate_edge_references(nodes, edges)
    # A destination anchor is not a road junction.  The JSON keeps POI-to-POI
    # evidence compact, while the in-memory graph materializes a separate
    # road-side access node for every user POI and rewires every road edge to
    # those internal nodes.  No distance is split or invented: each recorded
    # measured edge keeps its original total distance and provenance.
    if metadata.get("routing_node_model") == "poi_road_access_v1":
        nodes, edges = _materialize_dormitory_road_access_nodes(nodes, edges)

    provenance = _derive_provenance(metadata, nodes, edges, notes)

    identifier_index = _build_identifier_index(nodes)
    result = CampusMapData(
        schema_version=schema_version,
        campus=campus,
        data_status=data_status,
        provenance=provenance,
        nodes=nodes,
        edges=edges,
        notes=tuple(notes),
        metadata=dict(metadata),
        campus_id=campus_id,
    )
    object.__setattr__(result, "_identifier_index", identifier_index)
    return result


def _materialize_dormitory_road_access_nodes(
    nodes: Tuple[CampusNode, ...], edges: Tuple[RoadEdge, ...]
) -> Tuple[Tuple[CampusNode, ...], Tuple[RoadEdge, ...]]:
    anchors = {}
    extra_nodes = []
    for node in nodes:
        if node.node_kind != "poi":
            continue
        anchor_id = "{}__road_access".format(node.id)
        anchors[node.id] = anchor_id
        extra_nodes.append(
            CampusNode(
                id=anchor_id,
                name="{}道路侧入口".format(node.name),
                aliases=(),
                category="road",
                latitude=node.latitude,
                longitude=node.longitude,
                source="由 {} 的受信入口 anchor 本地派生；保留原路线距离与 provenance，不作为用户地点".format(node.id),
                precision=node.precision,
                provenance="derived_road_access_anchor",
                node_kind="road_waypoint",
            )
        )
    if not anchors:
        return nodes, edges
    rewired = []
    for edge in edges:
        rewired.append(
            RoadEdge(
                from_id=anchors.get(edge.from_id, edge.from_id),
                to_id=anchors.get(edge.to_id, edge.to_id),
                distance_m=edge.distance_m,
                modes=edge.modes,
                bidirectional=edge.bidirectional,
                source=edge.source,
                distance_by_mode=edge.distance_by_mode,
            )
        )
    return tuple(nodes) + tuple(extra_nodes), tuple(rewired)


def _parse_nodes(raw: Any) -> Tuple[CampusNode, ...]:
    """校验地点节点；支持新字段 nodes 与 P0 旧字段 locations。"""
    if not isinstance(raw, list):
        raise ValueError("nodes/locations 必须是列表。")
    if not raw:
        raise ValueError("nodes/locations 不能为空。")
    nodes = []
    seen_ids = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError("nodes[{}] 必须是对象。".format(index))
        node_id = _required_text(item.get("id"), "nodes[{}].id".format(index))
        if node_id in seen_ids:
            raise ValueError("地点 id 重复：{}".format(node_id))
        seen_ids.add(node_id)
        name = _required_text(item.get("name"), "地点 {} 的 name".format(node_id))
        category = _required_text(item.get("category"), "地点 {} 的 category".format(node_id))
        aliases = _parse_aliases(item.get("aliases"), node_id)
        latitude = _optional_float(
            item.get("latitude"), "地点 {} 的 latitude".format(node_id), -90.0, 90.0
        )
        longitude = _optional_float(
            item.get("longitude"), "地点 {} 的 longitude".format(node_id), -180.0, 180.0
        )
        source = _optional_text(item.get("source"), "地点 {} 的 source".format(node_id))
        precision = _optional_text(
            item.get("precision"), "地点 {} 的 precision".format(node_id)
        ) or "precise"
        if precision not in ("precise", "approximate"):
            raise ValueError(
                "地点 {} 的 precision 必须是 precise 或 approximate".format(node_id)
            )
        node_provenance = _optional_text(
            item.get("provenance"), "地点 {} 的 provenance".format(node_id)
        )
        node_kind = _optional_text(item.get("node_kind"), "地点 {} 的 node_kind".format(node_id)) or "poi"
        if node_kind not in ("poi", "road_waypoint"):
            raise ValueError("地点 {} 的 node_kind 必须是 poi 或 road_waypoint".format(node_id))
        physical_anchor_id = _optional_text(
            item.get("physical_anchor_id"), "地点 {} 的 physical_anchor_id".format(node_id)
        )
        nodes.append(
            CampusNode(
                id=node_id,
                name=name,
                aliases=aliases,
                category=category,
                latitude=latitude,
                longitude=longitude,
                source=source,
                precision=precision,
                provenance=node_provenance,
                node_kind=node_kind,
                physical_anchor_id=physical_anchor_id,
            )
        )
    return tuple(nodes)


def _parse_aliases(raw: Any, node_id: str) -> Tuple[str, ...]:
    if raw is None:
        raw = []
    if not isinstance(raw, list) or any(not isinstance(alias, str) for alias in raw):
        raise ValueError("地点 {} 的 aliases 必须是字符串列表。".format(node_id))
    aliases = tuple(alias.strip() for alias in raw)
    if any(not alias for alias in aliases):
        raise ValueError("地点 {} 的 alias 不能为空。".format(node_id))
    normalized = [alias.casefold() for alias in aliases]
    if len(normalized) != len(set(normalized)):
        raise ValueError("地点 {} 内部的 alias 重复。".format(node_id))
    # alias 与自身 id 相同是冗余但无害（解析到同一节点）；
    # 跨节点歧义由 _build_identifier_index 拦截。
    return aliases


def _parse_edges(raw: Any) -> Tuple[RoadEdge, ...]:
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise ValueError("edges 必须是列表。")
    edges = []
    seen = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError("edges[{}] 必须是对象。".format(index))
        from_id = _required_text(
            item.get("from", item.get("source")), "edges[{}].from".format(index)
        )
        to_id = _required_text(
            item.get("to", item.get("target")), "edges[{}].to".format(index)
        )
        if from_id == to_id:
            raise ValueError("edges[{}] 不能是自环：{}".format(index, from_id))
        distance = item.get("distance_m")
        if type(distance) is not int or distance <= 0:
            raise ValueError("edges[{}].distance_m 必须是大于 0 的整数（米）。".format(index))
        modes_raw = item.get("modes")
        if not isinstance(modes_raw, list) or not modes_raw:
            raise ValueError("edges[{}].modes 必须是非空列表。".format(index))
        modes = tuple(parse_transport_mode(mode) for mode in modes_raw)
        if len(modes) != len(set(modes)):
            raise ValueError("edges[{}].modes 不能重复。".format(index))
        bidirectional = item.get("bidirectional", True)
        if type(bidirectional) is not bool:
            raise ValueError("edges[{}].bidirectional 必须是布尔值。".format(index))
        source = _optional_text(item.get("source"), "edges[{}].source".format(index))
        mode_distances = _parse_mode_distances(
            item.get("distance_by_mode"), modes, index
        )
        key = (from_id, to_id)
        if key in seen:
            raise ValueError("重复的有向边：{} -> {}".format(from_id, to_id))
        seen.add(key)
        edges.append(
            RoadEdge(
                from_id=from_id,
                to_id=to_id,
                distance_m=distance,
                modes=modes,
                bidirectional=bidirectional,
                source=source,
                distance_by_mode=mode_distances,
            )
        )
    return tuple(edges)


def _parse_mode_distances(raw: Any, modes: Tuple[TransportMode, ...], index: int) -> Dict[TransportMode, int]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("edges[{}].distance_by_mode 必须是对象。".format(index))
    parsed = {}
    for raw_mode, distance in raw.items():
        mode = parse_transport_mode(raw_mode)
        if mode not in modes:
            raise ValueError("edges[{}].distance_by_mode 包含未启用交通方式。".format(index))
        if type(distance) is not int or distance <= 0:
            raise ValueError("edges[{}].distance_by_mode 距离必须是正整数。".format(index))
        parsed[mode] = distance
    return parsed


def _validate_edge_references(
    nodes: Tuple[CampusNode, ...], edges: Tuple[RoadEdge, ...]
) -> None:
    node_ids = {node.id for node in nodes}
    for edge in edges:
        if edge.from_id not in node_ids:
            raise ValueError("边引用了未知地点 from：{}".format(edge.from_id))
        if edge.to_id not in node_ids:
            raise ValueError("边引用了未知地点 to：{}".format(edge.to_id))


def _build_identifier_index(nodes: Tuple[CampusNode, ...]) -> Dict[str, str]:
    identifier_index = {}
    for node in nodes:
        for identifier in (node.id, node.name) + node.aliases:
            normalized = identifier.casefold()
            owner = identifier_index.get(normalized)
            if owner is not None and owner != node.id:
                raise ValueError(
                    "地点解析标识冲突：{} 同时指向 {} 和 {}".format(
                        identifier, owner, node.id
                    )
                )
            identifier_index[normalized] = node.id
    return identifier_index


def _derive_provenance(
    metadata: Dict[str, Any],
    nodes: Tuple[CampusNode, ...],
    edges: Tuple[RoadEdge, ...],
    notes: List[str],
) -> MapDataProvenance:
    """根据显式声明与内容判定数据来源，绝不自动判真实。"""
    raw = metadata.get("provenance")
    if raw is not None and not isinstance(raw, str):
        raise ValueError("metadata.provenance 必须是字符串。")

    if not edges:
        if raw == REAL_PROVENANCE:
            raise ValueError("声明为真实地图但没有道路边，不能作为真实路线数据。")
        if raw == SYNTHETIC_PROVENANCE:
            notes.append("synthetic fixture 没有道路边，不可路由")
        else:
            notes.append("没有道路边，判定为不可用（不编造道路或距离）")
        return MapDataProvenance.UNAVAILABLE

    if raw == REAL_PROVENANCE:
        missing_edge_source = [edge for edge in edges if not edge.source]
        if missing_edge_source:
            raise ValueError(
                "声明为真实地图时每条边必须提供 source，缺失：{}".format(
                    "、".join(edge.from_id + "->" + edge.to_id for edge in missing_edge_source)
                )
            )
        missing_node_source = [node for node in nodes if not node.source]
        if missing_node_source:
            raise ValueError(
                "声明为真实地图时每个地点必须提供 source，缺失：{}".format(
                    "、".join(node.id for node in missing_node_source)
                )
            )
        missing_coords = [
            node for node in nodes
            if node.latitude is None or node.longitude is None
        ]
        if missing_coords:
            notes.append(
                "真实地图部分地点缺少经纬度（仅展示/校验用，寻路以边距离为准）：{}".format(
                    "、".join(node.id for node in missing_coords)
                )
            )
        return MapDataProvenance.REAL_MAP

    if raw is None:
        notes.append("缺少 metadata.provenance，路由数据按 synthetic_test 处理，不冒充真实地图")
        return MapDataProvenance.SYNTHETIC_TEST

    if raw == SYNTHETIC_PROVENANCE:
        return MapDataProvenance.SYNTHETIC_TEST

    notes.append("未知 provenance：{}，按 synthetic_test 处理".format(raw))
    return MapDataProvenance.SYNTHETIC_TEST


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("{} 必须是非空字符串。".format(label))
    return value.strip()


def _optional_text(value: Any, label: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("{} 必须是非空字符串。".format(label))
    return value.strip()


def _optional_float(value: Any, label: str, low: float, high: float) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("{} 必须是数值。".format(label))
    number = float(value)
    if not (low <= number <= high):
        raise ValueError("{} 超出合法范围 [{}, {}]。".format(label, low, high))
    return number
