"""可替换路线提供者；本模块不访问网络或真实地图服务。"""
from abc import ABC, abstractmethod
from typing import Optional

from src.campus_map import CampusMap
from src.p1_route_models import (LocationResolution, LocationResolutionStatus,
                                 ProviderRoute, ProviderRouteStatus,
                                 RouteDataTrust)
from src.shortest_path import ShortestPathError, find_shortest_path


class RouteProvider(ABC):
    @property
    @abstractmethod
    def route_data_trust(self):
        pass

    @abstractmethod
    def resolve_location(self, location_text):
        pass

    @abstractmethod
    def calculate_route(self, start_location_id, end_location_id):
        pass


class CampusMapRouteProvider(RouteProvider):
    """把 P0 的本地 CampusMap 包装为接口；可信度由调用方显式声明。"""
    def __init__(self, campus_map: CampusMap, route_data_trust=RouteDataTrust.SYNTHETIC_TEST):
        self._campus_map = campus_map
        self._route_data_trust = route_data_trust

    @property
    def route_data_trust(self):
        return self._route_data_trust

    def resolve_location(self, location_text):
        if not isinstance(location_text, str) or not location_text.strip():
            return LocationResolution(LocationResolutionStatus.UNKNOWN, None, None)
        location_id = self._campus_map.resolve_location_id(location_text)
        if location_id is None:
            return LocationResolution(LocationResolutionStatus.UNKNOWN, None, None)
        location = self._campus_map.location_by_id[location_id]
        return LocationResolution(LocationResolutionStatus.RESOLVED, location_id, location.name)

    def calculate_route(self, start_location_id, end_location_id):
        if start_location_id == end_location_id:
            return ProviderRoute(ProviderRouteStatus.COMPUTED, 0, (start_location_id,))
        try:
            result = find_shortest_path(self._campus_map, start_location_id, end_location_id)
        except ShortestPathError:
            return ProviderRoute(ProviderRouteStatus.UNREACHABLE, None, ())
        return ProviderRoute(ProviderRouteStatus.COMPUTED, result.total_minutes, result.path)
