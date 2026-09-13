"""Small invariants for explicit, single-campus P3 execution."""

from src.p3_map_schema import CampusMapData


class CampusMapMismatchError(ValueError):
    """A selected campus_id and the supplied static map disagree."""


class CrossCampusRouteUnsupportedError(ValueError):
    """P3f intentionally has no cross-campus transport graph."""


def require_campus_map(campus_id, map_data):
    """Validate an explicit campus scope at the map hand-off boundary.

    Legacy callers that pass only a map remain supported; live callers must
    pass both values and are rejected before location resolution if they drift.
    """
    if not isinstance(map_data, CampusMapData):
        raise TypeError("map_data must be a CampusMapData")
    if not isinstance(campus_id, str) or not campus_id.strip():
        raise ValueError("campus_id 必须是非空字符串")
    if map_data.campus_id != campus_id:
        raise CampusMapMismatchError("selected campus_id 与 map_data 不一致")
    return map_data
