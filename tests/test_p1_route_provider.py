from pathlib import Path

from src.campus_map import load_campus_map
from src.p1_route_models import (LocationResolutionStatus, ProviderRouteStatus,
                                 RouteDataTrust)
from src.p1_route_provider import CampusMapRouteProvider


def test_synthetic_fixture_provider_resolves_and_calculates_without_real_claim():
    path = Path(__file__).parent / "fixtures" / "sample_campus_map.json"
    provider = CampusMapRouteProvider(load_campus_map(str(path)), RouteDataTrust.SYNTHETIC_TEST)
    start = provider.resolve_location("宿舍")
    end = provider.resolve_location("图书馆")
    route = provider.calculate_route(start.location_id, end.location_id)
    assert start.status is LocationResolutionStatus.RESOLVED
    assert route.status is ProviderRouteStatus.COMPUTED and route.walking_minutes == 8
    assert provider.route_data_trust is RouteDataTrust.SYNTHETIC_TEST


def test_provider_reports_unknown_location_and_unreachable_safely():
    path = Path(__file__).parent / "fixtures" / "sample_campus_map.json"
    provider = CampusMapRouteProvider(load_campus_map(str(path)))
    assert provider.resolve_location("不存在地点").status is LocationResolutionStatus.UNKNOWN
    assert provider.calculate_route("missing", "library").status is ProviderRouteStatus.UNREACHABLE
