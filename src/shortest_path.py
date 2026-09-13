"""校园地图上两个地点之间的最短步行路径。"""

import heapq
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from src.campus_map import CampusMap


class ShortestPathError(Exception):
    """最短路径查询中的可预期输入或地图错误。"""


@dataclass(frozen=True)
class ShortestPathResult:
    start_id: str
    destination_id: str
    path: Tuple[str, ...]
    total_minutes: int


def find_shortest_path(
    campus_map: CampusMap,
    start: str,
    destination: str,
) -> ShortestPathResult:
    """使用 Dijkstra 算法计算两个地点间的最短步行路径。

    V（Vertex，顶点）是地点数量，E（Edge，边）是步行连接数量。
    使用邻接表和优先队列时，时间复杂度为 O((V + E) log V)。
    """
    if not isinstance(campus_map, CampusMap):
        raise ShortestPathError("campus_map 必须是有效的 CampusMap。")
    if not isinstance(start, str):
        raise ShortestPathError("起点必须是字符串。")
    if not isinstance(destination, str):
        raise ShortestPathError("终点必须是字符串。")

    try:
        is_routable = campus_map.is_routable
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise ShortestPathError("当前地图数据无效，无法规划路线。") from exc
    if not is_routable:
        raise ShortestPathError("当前地图缺少可用步行边，无法规划路线。")

    start_id = campus_map.resolve_location_id(start)
    if start_id is None:
        raise ShortestPathError(f"无法识别起点：{start}")

    destination_id = campus_map.resolve_location_id(destination)
    if destination_id is None:
        raise ShortestPathError(f"无法识别终点：{destination}")

    if start_id == destination_id:
        return ShortestPathResult(
            start_id=start_id,
            destination_id=destination_id,
            path=(start_id,),
            total_minutes=0,
        )

    location_ids = set(campus_map.location_by_id)
    adjacency: Dict[str, List[Tuple[str, int]]] = {
        location_id: [] for location_id in location_ids
    }
    try:
        for edge in campus_map.edges:
            if edge.source not in location_ids or edge.target not in location_ids:
                raise ShortestPathError("地图步行边引用了未知地点，无法规划路线。")
            if type(edge.walk_minutes) is not int or edge.walk_minutes <= 0:
                raise ShortestPathError("地图步行边时间无效，无法规划路线。")
            adjacency[edge.source].append((edge.target, edge.walk_minutes))
            adjacency[edge.target].append((edge.source, edge.walk_minutes))
    except (AttributeError, TypeError) as exc:
        raise ShortestPathError("地图步行边数据无效，无法规划路线。") from exc

    distances: Dict[str, int] = {start_id: 0}
    predecessors: Dict[str, str] = {}
    queue: List[Tuple[int, str]] = [(0, start_id)]

    while queue:
        current_minutes, current_id = heapq.heappop(queue)
        if current_minutes != distances.get(current_id):
            continue
        if current_id == destination_id:
            break

        for neighbor_id, walk_minutes in adjacency[current_id]:
            candidate_minutes = current_minutes + walk_minutes
            if candidate_minutes < distances.get(neighbor_id, float("inf")):
                distances[neighbor_id] = candidate_minutes
                predecessors[neighbor_id] = current_id
                heapq.heappush(queue, (candidate_minutes, neighbor_id))

    if destination_id not in distances:
        raise ShortestPathError("终点不可达，无法规划路线。")

    reversed_path = [destination_id]
    current_id: Optional[str] = destination_id
    while current_id != start_id:
        current_id = predecessors.get(current_id)
        if current_id is None:
            raise ShortestPathError("终点不可达，无法还原路线。")
        reversed_path.append(current_id)

    return ShortestPathResult(
        start_id=start_id,
        destination_id=destination_id,
        path=tuple(reversed(reversed_path)),
        total_minutes=distances[destination_id],
    )
