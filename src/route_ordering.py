"""少量任务地点的最短访问顺序。"""

from dataclasses import dataclass
from itertools import permutations
from typing import Dict, List, Optional, Tuple

from src.campus_map import CampusMap
from src.shortest_path import (
    ShortestPathError,
    ShortestPathResult,
    find_shortest_path,
)


MAX_TASKS = 8


class RouteOrderingError(Exception):
    """多任务路线排序中的可预期输入或地图错误。"""


@dataclass(frozen=True)
class RouteOrderingResult:
    visit_order: Tuple[str, ...]
    segments: Tuple[ShortestPathResult, ...]
    total_minutes: int


def find_optimal_route(
    campus_map: CampusMap,
    current_location: str,
    destination: str,
    task_locations: List[str],
) -> RouteOrderingResult:
    """暴力枚举任务地点顺序，返回总步行时间最短的路线。

    该最小版本只处理地点访问顺序和步行时间，不考虑截止时间、
    停留时间、任务取舍或动态重规划。任务数最多为 8。
    """
    if not isinstance(campus_map, CampusMap):
        raise RouteOrderingError("campus_map 必须是有效的 CampusMap。")
    if not isinstance(current_location, str):
        raise RouteOrderingError("起点必须是字符串。")
    if not isinstance(destination, str):
        raise RouteOrderingError("终点必须是字符串。")
    if not isinstance(task_locations, list):
        raise RouteOrderingError("任务地点必须是列表。")
    if len(task_locations) > MAX_TASKS:
        raise RouteOrderingError("当前版本最多支持8个任务。")
    if any(not isinstance(location, str) for location in task_locations):
        raise RouteOrderingError("每个任务地点都必须是字符串。")

    try:
        is_routable = campus_map.is_routable
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise RouteOrderingError("当前地图数据无效，无法排序路线。") from exc
    if not is_routable:
        raise RouteOrderingError("当前地图缺少可用步行边，无法排序路线。")

    start_id = campus_map.resolve_location_id(current_location)
    if start_id is None:
        raise RouteOrderingError(f"无法识别起点：{current_location}")

    destination_id = campus_map.resolve_location_id(destination)
    if destination_id is None:
        raise RouteOrderingError(f"无法识别终点：{destination}")

    task_ids = []
    for index, task_location in enumerate(task_locations):
        task_id = campus_map.resolve_location_id(task_location)
        if task_id is None:
            raise RouteOrderingError(
                f"无法识别第{index + 1}个任务地点：{task_location}"
            )
        task_ids.append(task_id)

    if len(task_ids) != len(set(task_ids)):
        raise RouteOrderingError("任务地点不能重复。")

    segment_cache: Dict[Tuple[str, str], ShortestPathResult] = {}

    def get_segment(source_id: str, target_id: str) -> ShortestPathResult:
        cache_key = (source_id, target_id)
        if cache_key not in segment_cache:
            try:
                segment_cache[cache_key] = find_shortest_path(
                    campus_map, source_id, target_id
                )
            except ShortestPathError as exc:
                raise RouteOrderingError(f"无法计算路线分段：{exc}") from exc
        return segment_cache[cache_key]

    best_order: Optional[Tuple[str, ...]] = None
    best_segments: Optional[Tuple[ShortestPathResult, ...]] = None
    best_total: Optional[int] = None

    for task_order in permutations(task_ids):
        visit_order = (start_id,) + task_order + (destination_id,)
        segments = tuple(
            get_segment(source_id, target_id)
            for source_id, target_id in zip(visit_order, visit_order[1:])
        )
        total_minutes = sum(segment.total_minutes for segment in segments)

        if best_total is None or total_minutes < best_total:
            best_order = visit_order
            best_segments = segments
            best_total = total_minutes

    if best_order is None or best_segments is None or best_total is None:
        raise RouteOrderingError("无法生成任务访问顺序。")

    return RouteOrderingResult(
        visit_order=best_order,
        segments=best_segments,
        total_minutes=best_total,
    )
