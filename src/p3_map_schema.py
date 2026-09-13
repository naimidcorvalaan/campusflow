"""P3a 本地真实校园地图数据模型（Python 3.8 兼容）。

地图层只回答“从哪里到哪里、经过哪些边、总距离多少米”。
不把“步行几分钟”写死进数据，也不做任何时间估计；
交通时间估计留给后续 Qwen 环节。
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Optional, Tuple

P3_MAP_SCHEMA_VERSION = "p3.campus-map.v1"


class TransportMode(str, Enum):
    WALK = "walk"
    BIKE = "bike"


class MapDataProvenance(str, Enum):
    """路线数据来源：真实地图 / 合成测试 / 不可用。

    绝不因为文件名包含 beiyangyuan 就自动判定为 REAL_MAP；
    必须由 metadata.provenance 与内容（是否存在带来源的道路边）共同决定。
    """
    REAL_MAP = "real_map"
    SYNTHETIC_TEST = "synthetic_test"
    UNAVAILABLE = "unavailable"


class RouteStatus(str, Enum):
    COMPUTED = "computed"
    UNREACHABLE = "unreachable"
    UNAVAILABLE = "unavailable"


def parse_transport_mode(value) -> TransportMode:
    """把 walk / bike（或 TransportMode）解析为枚举；其它值一律拒绝。"""
    if isinstance(value, TransportMode):
        return value
    if isinstance(value, str):
        try:
            return TransportMode(value.strip().lower())
        except ValueError:
            pass
    raise ValueError("交通方式必须是 walk 或 bike")


@dataclass(frozen=True)
class CampusNode:
    """地点节点：稳定 id、名称、别名、类别、经纬度（可选）、来源。"""

    id: str
    name: str
    aliases: Tuple[str, ...]
    category: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    source: Optional[str] = None
    # `approximate` is a first-class, auditable map fact: it means the anchor
    # was established from cross-checked official spatial evidence rather than
    # a POI's exact building centroid.  It is not a synthetic coordinate.
    precision: str = "precise"
    provenance: Optional[str] = None
    # User destinations and routing-only road nodes are deliberately distinct.
    node_kind: str = "poi"
    # Optional physical co-location key for distinct user-facing entities that
    # share one real building/entrance; it is not a resolver alias.
    physical_anchor_id: Optional[str] = None


@dataclass(frozen=True)
class RoadEdge:
    """道路连接：真实距离（米）、可通行方式、是否双向、来源。"""

    from_id: str
    to_id: str
    distance_m: int
    modes: Tuple[TransportMode, ...]
    bidirectional: bool
    source: Optional[str] = None
    distance_by_mode: Mapping[TransportMode, int] = field(default_factory=dict)


@dataclass(frozen=True)
class CampusMapData:
    """已校验的真实地图数据；provenance 由 loader 显式判定。"""

    schema_version: str
    campus: str
    data_status: str
    provenance: MapDataProvenance
    nodes: Tuple[CampusNode, ...]
    edges: Tuple[RoadEdge, ...]
    notes: Tuple[str, ...]
    metadata: Mapping = field(default_factory=dict)
    # Stable registry key.  Display text remains in ``campus`` and must never
    # be used as an implementation identifier (卫津路的“北洋园”是普通 landmark).
    campus_id: Optional[str] = None
    _identifier_index: Mapping = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    def resolve_node_id(self, value, include_internal: bool = False) -> Optional[str]:
        """解析 user-facing POI；开发期路由可显式请求内部节点。"""
        if not isinstance(value, str):
            return None
        normalized = value.strip().casefold()
        if not normalized:
            return None
        node_id = self._identifier_index.get(normalized)
        if node_id is None or include_internal:
            return node_id
        return next(
            (node.id for node in self.nodes if node.id == node_id and node.node_kind == "poi"),
            None,
        )


@dataclass(frozen=True)
class RouteResult:
    """一次寻路结果；只包含距离（米）与路径，不做时间估计。

    origin / destination 为已解析的稳定 node id（未知时保留原始文本）；
    total_distance_m 为 None 表示没有可用路线。
    """

    status: RouteStatus
    origin: str
    destination: str
    mode: TransportMode
    total_distance_m: Optional[int]
    path: Tuple[str, ...]
    provenance: MapDataProvenance
    note: Optional[str] = None
