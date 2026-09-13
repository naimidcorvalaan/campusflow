"""Explicit P3f campus registry; selection is a product state, never inference."""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple

from src.p3_location_resolver import LocationResolution, resolve_location
from src.p3_map_loader import load_campus_map_data
from src.p3_map_schema import CampusMapData
from src.p3_route_provider import plan_route
from src.p3_campus_scope import CrossCampusRouteUnsupportedError


class UnknownCampusError(ValueError):
    """The caller supplied no registered stable campus identifier."""


@dataclass(frozen=True)
class CampusRegistration:
    campus_id: str
    display_name: str
    map_path: Path


class CampusMapRegistry:
    """One authoritative campus_id -> local static map entry point."""

    def __init__(self, registrations: Tuple[CampusRegistration, ...]):
        self._registrations = {item.campus_id: item for item in registrations}
        self._maps: Dict[str, CampusMapData] = {}
        if len(self._registrations) != len(registrations):
            raise ValueError("campus_id 重复")

    def list_campuses(self) -> Tuple[CampusRegistration, ...]:
        return tuple(self._registrations[key] for key in sorted(self._registrations))

    def get_registration(self, campus_id: str) -> CampusRegistration:
        try:
            return self._registrations[campus_id]
        except (KeyError, TypeError):
            raise UnknownCampusError("未知 campus_id：{}".format(campus_id)) from None

    def get_campus_map(self, campus_id: str) -> CampusMapData:
        registration = self.get_registration(campus_id)
        cached = self._maps.get(campus_id)
        if cached is None:
            cached = load_campus_map_data(registration.map_path)
            if cached.campus_id != campus_id:
                raise ValueError("地图 campus_id 与 registry 不一致：{}".format(campus_id))
            self._maps[campus_id] = cached
        return cached


_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_CAMPUS_REGISTRY = CampusMapRegistry((
    CampusRegistration("beiyangyuan", "北洋园校区", _DATA_DIR / "beiyangyuan_map.json"),
    CampusRegistration("weijinlu", "卫津路校区", _DATA_DIR / "weijinlu_map.json"),
))


def resolve_location_in_campus(registry: CampusMapRegistry, campus_id: str, text: str, **kwargs) -> LocationResolution:
    """Resolve only inside the explicitly selected campus; never fallback."""
    return resolve_location(registry.get_campus_map(campus_id), text, **kwargs)


def plan_route_in_campus(
    registry: CampusMapRegistry,
    campus_id: str,
    origin: str,
    destination: str,
    mode: object,
    destination_campus_id: str = None,
):
    """Route only inside one selected map; cross-campus transport is unsupported."""
    registry.get_registration(campus_id)
    if destination_campus_id is not None and destination_campus_id != campus_id:
        raise CrossCampusRouteUnsupportedError("当前不支持跨校区路线")
    return plan_route(registry.get_campus_map(campus_id), origin, destination, mode)
