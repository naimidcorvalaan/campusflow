import json
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CampusLocation:
    id: str
    name: str
    aliases: Tuple[str, ...]
    category: str


@dataclass(frozen=True)
class WalkingEdge:
    source: str
    target: str
    walk_minutes: int


@dataclass
class CampusMap:
    campus: str
    data_status: str
    locations: List[CampusLocation]
    edges: List[WalkingEdge]
    metadata: Dict[str, Any] = field(default_factory=dict)
    _location_id_by_identifier: Dict[str, str] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    @property
    def location_by_id(self) -> Dict[str, CampusLocation]:
        return {location.id: location for location in self.locations}

    def resolve_location_id(self, value: str) -> Optional[str]:
        if not isinstance(value, str):
            return None

        normalized = value.strip().casefold()
        if not normalized:
            return None
        return self._location_id_by_identifier.get(normalized)

    @property
    def is_routable(self) -> bool:
        if not self.locations or self.data_status == "locations_only":
            return False
        if len(self.locations) > 1 and not self.edges:
            return False
        return self.is_connected()

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "CampusMap":
        if not isinstance(payload, dict):
            raise ValueError("地图数据必须是JSON对象。")

        metadata = payload.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError("metadata 必须是对象。")

        campus = metadata.get("campus")
        data_status = metadata.get("data_status")
        if not isinstance(campus, str) or not campus.strip():
            raise ValueError("metadata.campus 必须是非空字符串。")
        if not isinstance(data_status, str) or not data_status.strip():
            raise ValueError("metadata.data_status 必须是非空字符串。")

        locations_payload = payload.get("locations", [])
        if not isinstance(locations_payload, list):
            raise ValueError("locations 必须是列表。")
        if not locations_payload:
            raise ValueError("locations 不能为空。")

        locations: List[CampusLocation] = []
        seen_ids = set()
        for index, item in enumerate(locations_payload):
            if not isinstance(item, dict):
                raise ValueError(f"locations[{index}] 必须是对象。")

            raw_location_id = item.get("id")
            if not isinstance(raw_location_id, str) or not raw_location_id.strip():
                raise ValueError(f"locations[{index}].id 必须是非空字符串。")
            location_id = raw_location_id.strip()
            if location_id in seen_ids:
                raise ValueError(f"地点 id 重复：{location_id}")
            seen_ids.add(location_id)

            raw_name = item.get("name")
            if not isinstance(raw_name, str) or not raw_name.strip():
                raise ValueError(f"地点 {location_id} 的 name 不能为空。")
            name = raw_name.strip()

            aliases_payload = item.get("aliases", [])
            if not isinstance(aliases_payload, list) or any(
                not isinstance(alias, str) for alias in aliases_payload
            ):
                raise ValueError(f"地点 {location_id} 的 aliases 必须是字符串列表。")
            aliases = tuple(alias.strip() for alias in aliases_payload)
            if any(not alias for alias in aliases):
                raise ValueError(f"地点 {location_id} 的 alias 不能为空。")

            normalized_aliases = [alias.casefold() for alias in aliases]
            if len(normalized_aliases) != len(set(normalized_aliases)):
                raise ValueError(f"地点 {location_id} 内部的 alias 重复。")
            own_identifiers = {location_id.casefold(), name.casefold()}
            if any(alias in own_identifiers for alias in normalized_aliases):
                raise ValueError(f"地点 {location_id} 的 alias 不能与自身 id 或 name 重复。")

            raw_category = item.get("category")
            if not isinstance(raw_category, str) or not raw_category.strip():
                raise ValueError(f"地点 {location_id} 的 category 必须是非空字符串。")
            category = raw_category.strip()

            locations.append(
                CampusLocation(
                    id=location_id,
                    name=name,
                    aliases=aliases,
                    category=category,
                )
            )

        identifier_index: Dict[str, str] = {}
        for location in locations:
            for identifier in (location.id, location.name) + location.aliases:
                normalized_identifier = identifier.casefold()
                owner = identifier_index.get(normalized_identifier)
                if owner is not None and owner != location.id:
                    raise ValueError(
                        f"地点解析标识冲突：{identifier} 同时指向 {owner} 和 {location.id}"
                    )
                identifier_index[normalized_identifier] = location.id

        location_ids = {loc.id for loc in locations}
        edges_payload = payload.get("edges", payload.get("walking_edges", []))
        if edges_payload is None:
            edges_payload = []
        if not isinstance(edges_payload, list):
            raise ValueError("edges 必须是列表。")

        edges: List[WalkingEdge] = []
        seen_pairs: Set[Tuple[str, str]] = set()
        for index, item in enumerate(edges_payload):
            if not isinstance(item, dict):
                raise ValueError(f"edges[{index}] 必须是对象。")

            source = item.get("source")
            target = item.get("target")
            walk_minutes = item.get("walk_minutes")

            if not isinstance(source, str) or not source.strip():
                raise ValueError(f"edges[{index}].source 必须是非空字符串。")
            if not isinstance(target, str) or not target.strip():
                raise ValueError(f"edges[{index}].target 必须是非空字符串。")
            if source not in location_ids:
                raise ValueError(f"边 {index} 引用了未知地点 source: {source}")
            if target not in location_ids:
                raise ValueError(f"边 {index} 引用了未知地点 target: {target}")
            if source == target:
                raise ValueError(f"边 {index} 不能是自环：{source}")
            if type(walk_minutes) is not int or walk_minutes <= 0:
                raise ValueError(f"edges[{index}].walk_minutes 必须是大于0的整数。")

            undirected_pair = tuple(sorted((source, target)))
            if undirected_pair in seen_pairs:
                raise ValueError(f"重复的无向边：{source} - {target}")
            seen_pairs.add(undirected_pair)

            edges.append(
                WalkingEdge(
                    source=source,
                    target=target,
                    walk_minutes=walk_minutes,
                )
            )

        campus_map = cls(
            campus=campus,
            data_status=data_status,
            locations=locations,
            edges=edges,
            metadata=metadata,
        )
        campus_map._location_id_by_identifier = identifier_index

        if data_status == "locations_only" and edges:
            raise ValueError("locations_only 地点目录不能包含边。")

        if len(locations) > 1 and data_status != "locations_only" and not campus_map.is_connected():
            raise ValueError("地图必须连通。")

        return campus_map

    @classmethod
    def from_json_file(cls, path: Union[str, Path]) -> "CampusMap":
        file_path = Path(path)
        try:
            with file_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except FileNotFoundError as exc:
            raise ValueError(f"地图文件不存在：{file_path}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"地图JSON格式错误：{file_path}") from exc
        except OSError as exc:
            raise ValueError(f"无法读取地图文件：{file_path}") from exc

        return cls.from_dict(payload)

    def is_connected(self) -> bool:
        if not self.locations:
            return True
        if len(self.locations) == 1:
            return True

        adjacency: Dict[str, List[str]] = {location.id: [] for location in self.locations}
        for edge in self.edges:
            adjacency.setdefault(edge.source, []).append(edge.target)
            adjacency.setdefault(edge.target, []).append(edge.source)

        visited: Set[str] = set()
        queue = deque([self.locations[0].id])

        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            for neighbor in adjacency.get(current, []):
                if neighbor not in visited:
                    queue.append(neighbor)

        return len(visited) == len(self.locations)


def load_campus_map(path: Union[str, Path]) -> CampusMap:
    return CampusMap.from_json_file(path)
