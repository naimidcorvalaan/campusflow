"""P3a 真实地图寻路（Python 3.8 兼容）。

复用 P0 Dijkstra 思路，但：
- 权重是真实距离（米），不是步行分钟；
- 按交通方式过滤可用边（walk / bike）；
- 支持有向边；
- 无路径 / 数据不可用时明确返回 unavailable，绝不用直线距离冒充。
"""
import heapq
from typing import Dict, List, Optional, Tuple

from src.p3_map_schema import (
    CampusMapData,
    MapDataProvenance,
    RouteResult,
    RouteStatus,
    TransportMode,
    parse_transport_mode,
)


def plan_route(
    map_data: CampusMapData,
    origin: str,
    destination: str,
    mode: object,
) -> RouteResult:
    """在本地真实地图上按指定交通方式计算最短路线。

    origin / destination 可以是稳定 id、名称或别名；
    结果为 RouteResult，只包含距离（米）与路径。
    """
    if not isinstance(map_data, CampusMapData):
        raise TypeError("map_data 必须是 CampusMapData")
    if not isinstance(origin, str) or not origin.strip():
        raise ValueError("origin 必须是非空字符串")
    if not isinstance(destination, str) or not destination.strip():
        raise ValueError("destination 必须是非空字符串")
    parsed_mode = parse_transport_mode(mode)

    if map_data.provenance is MapDataProvenance.UNAVAILABLE:
        return RouteResult(
            status=RouteStatus.UNAVAILABLE,
            origin=origin,
            destination=destination,
            mode=parsed_mode,
            total_distance_m=None,
            path=(),
            provenance=MapDataProvenance.UNAVAILABLE,
            note="当前地图无可用道路数据，不提供路线",
        )

    start_id = map_data.resolve_node_id(origin, include_internal=True)
    if start_id is None:
        return RouteResult(
            status=RouteStatus.UNAVAILABLE,
            origin=origin,
            destination=destination,
            mode=parsed_mode,
            total_distance_m=None,
            path=(),
            provenance=MapDataProvenance.UNAVAILABLE,
            note="无法识别起点地点",
        )
    end_id = map_data.resolve_node_id(destination, include_internal=True)
    if end_id is None:
        return RouteResult(
            status=RouteStatus.UNAVAILABLE,
            origin=start_id,
            destination=destination,
            mode=parsed_mode,
            total_distance_m=None,
            path=(),
            provenance=MapDataProvenance.UNAVAILABLE,
            note="无法识别终点地点",
        )

    if start_id == end_id:
        return RouteResult(
            status=RouteStatus.COMPUTED,
            origin=start_id,
            destination=end_id,
            mode=parsed_mode,
            total_distance_m=0,
            path=(start_id,),
            provenance=map_data.provenance,
        )

    # Distinct user-facing entities can share one real building entrance.  Do
    # not force a spurious out-and-back road trip between such entities.
    if _same_physical_anchor(map_data, start_id, end_id):
        return RouteResult(
            status=RouteStatus.COMPUTED,
            origin=start_id,
            destination=end_id,
            mode=parsed_mode,
            total_distance_m=0,
            path=(start_id, end_id),
            provenance=map_data.provenance,
            note="起终点共享同一物理入口",
        )

    graph_start_id = _route_anchor_id(map_data, start_id)
    graph_end_id = _route_anchor_id(map_data, end_id)
    adjacency = _build_adjacency(map_data, parsed_mode)
    distances, predecessors = _dijkstra(adjacency, graph_start_id, graph_end_id)

    if graph_end_id not in distances:
        return RouteResult(
            status=RouteStatus.UNREACHABLE,
            origin=start_id,
            destination=end_id,
            mode=parsed_mode,
            total_distance_m=None,
            path=(),
            provenance=map_data.provenance,
            note="该交通方式下无可用路径",
        )

    path = _reconstruct_path(predecessors, graph_start_id, graph_end_id)
    return RouteResult(
        status=RouteStatus.COMPUTED,
        origin=start_id,
        destination=end_id,
        mode=parsed_mode,
        total_distance_m=distances[graph_end_id],
        path=path,
        provenance=map_data.provenance,
    )


def _same_physical_anchor(map_data: CampusMapData, first_id: str, second_id: str) -> bool:
    first = next((node for node in map_data.nodes if node.id == first_id), None)
    second = next((node for node in map_data.nodes if node.id == second_id), None)
    return bool(
        first is not None
        and second is not None
        and first.physical_anchor_id
        and first.physical_anchor_id == second.physical_anchor_id
    )


def _route_anchor_id(map_data: CampusMapData, node_id: str) -> str:
    """Map user destinations to their non-resolvable road-side access node."""
    node = next((item for item in map_data.nodes if item.id == node_id), None)
    if node is not None and node.node_kind == "poi":
        candidate = "{}__road_access".format(node_id)
        if any(item.id == candidate for item in map_data.nodes):
            return candidate
    return node_id


def _build_adjacency(
    map_data: CampusMapData, mode: TransportMode
) -> Dict[str, List[Tuple[str, int]]]:
    adjacency: Dict[str, List[Tuple[str, int]]] = {
        node.id: [] for node in map_data.nodes
    }
    for edge in map_data.edges:
        if mode not in edge.modes:
            continue
        if mode is TransportMode.WALK and _superseded_weijinlu_legacy_edge(edge):
            continue
        distance = edge.distance_by_mode.get(mode, edge.distance_m)
        adjacency[edge.from_id].append((edge.to_id, distance))
        if edge.bidirectional:
            adjacency[edge.to_id].append((edge.from_id, distance))
    return adjacency


def _superseded_weijinlu_legacy_edge(edge) -> bool:
    """Keep historic measured edges for audit/bike, but avoid their old walk-only
    access-to-access topology once step-derived shared backbone segments exist."""
    source = edge.source or ""
    return (
        "两个官方北部宿舍 approximate anchor" in source
        or "28斋 approximate anchor至第九教学楼" in source
        or "29斋 approximate anchor至第九教学楼" in source
        or "30斋 approximate anchor至第九教学楼" in source
        or "东南宿舍组接入第九教学楼" in source
        or "鹏翔学生公寓至第九教学楼" in source
    )


def _dijkstra(
    adjacency: Dict[str, List[Tuple[str, int]]],
    start_id: str,
    end_id: str,
) -> Tuple[Dict[str, int], Dict[str, str]]:
    distances: Dict[str, int] = {start_id: 0}
    predecessors: Dict[str, str] = {}
    queue: List[Tuple[int, str]] = [(0, start_id)]
    while queue:
        current_distance, current_id = heapq.heappop(queue)
        if current_distance != distances.get(current_id):
            continue
        if current_id == end_id:
            break
        for neighbor_id, edge_distance in adjacency.get(current_id, ()):
            candidate = current_distance + edge_distance
            if candidate < distances.get(neighbor_id, float("inf")):
                distances[neighbor_id] = candidate
                predecessors[neighbor_id] = current_id
                heapq.heappush(queue, (candidate, neighbor_id))
    return distances, predecessors


def _reconstruct_path(
    predecessors: Dict[str, str], start_id: str, end_id: str
) -> Tuple[str, ...]:
    reversed_path = [end_id]
    current_id = end_id
    while current_id != start_id:
        current_id = predecessors.get(current_id)
        if current_id is None:
            return (start_id, end_id)
        reversed_path.append(current_id)
    return tuple(reversed(reversed_path))
