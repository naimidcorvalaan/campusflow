import pytest

from src.p3_campus_registry import (
    DEFAULT_CAMPUS_REGISTRY,
    CrossCampusRouteUnsupportedError,
    UnknownCampusError,
    plan_route_in_campus,
    resolve_location_in_campus,
)
from src.p3_campus_scope import CampusMapMismatchError, require_campus_map
from src.p3_campus_session import (
    CAMPUS_BOUND_STATE_KEYS,
    CampusSelectionRequiredError,
    SELECTED_CAMPUS_ID_KEY,
    require_selected_campus,
    select_campus,
)
from src.p3_location_resolver import LocationResolutionStatus
from src.p3_map_schema import RouteStatus, TransportMode
from src.p3_route_planner import plan_movement


def test_registry_has_two_stable_ids_and_loads_matching_static_maps():
    registrations = DEFAULT_CAMPUS_REGISTRY.list_campuses()
    assert [(item.campus_id, item.display_name) for item in registrations] == [
        ("beiyangyuan", "北洋园校区"),
        ("weijinlu", "卫津路校区"),
    ]
    for item in registrations:
        assert DEFAULT_CAMPUS_REGISTRY.get_campus_map(item.campus_id).campus_id == item.campus_id
    with pytest.raises(UnknownCampusError):
        DEFAULT_CAMPUS_REGISTRY.get_campus_map("unknown-campus")


def test_resolver_is_strictly_selected_campus_scoped_without_fallback():
    weijin = resolve_location_in_campus(DEFAULT_CAMPUS_REGISTRY, "weijinlu", "北洋园")
    assert weijin.status is LocationResolutionStatus.RESOLVED
    assert weijin.node_id == "weijinlu_landmark_beiyang_garden"
    assert weijin.campus_id == "weijinlu"
    beiyang = resolve_location_in_campus(DEFAULT_CAMPUS_REGISTRY, "beiyangyuan", "友园")
    assert beiyang.status is LocationResolutionStatus.UNRESOLVED
    assert beiyang.node_id is None
    assert beiyang.campus_id == "beiyangyuan"


def test_route_registry_never_creates_a_cross_campus_dijkstra_request():
    local = plan_route_in_campus(
        DEFAULT_CAMPUS_REGISTRY, "weijinlu", "第九教学楼", "春水图书馆", TransportMode.WALK
    )
    assert local.status is RouteStatus.COMPUTED
    with pytest.raises(CrossCampusRouteUnsupportedError):
        plan_route_in_campus(
            DEFAULT_CAMPUS_REGISTRY, "weijinlu", "第九教学楼", "春水图书馆",
            TransportMode.WALK, destination_campus_id="beiyangyuan",
        )


def test_explicit_session_selection_invalidates_all_campus_bound_state():
    store = {key: object() for key in CAMPUS_BOUND_STATE_KEYS}
    store["p2_live_intake"] = "保留用户原始任务文本"
    with pytest.raises(CampusSelectionRequiredError):
        require_selected_campus(store)
    assert select_campus(store, "beiyangyuan", DEFAULT_CAMPUS_REGISTRY) is True
    assert store[SELECTED_CAMPUS_ID_KEY] == "beiyangyuan"
    assert not any(key in store for key in CAMPUS_BOUND_STATE_KEYS)
    store["p3_current_location"] = "administrative_service_center"
    assert select_campus(store, "weijinlu", DEFAULT_CAMPUS_REGISTRY) is True
    assert store[SELECTED_CAMPUS_ID_KEY] == "weijinlu"
    assert "p3_current_location" not in store
    assert store["p2_live_intake"] == "保留用户原始任务文本"
    store["p3_current_location"] = "weijinlu_youyuan"
    assert select_campus(store, "beiyangyuan", DEFAULT_CAMPUS_REGISTRY) is True
    assert "p3_current_location" not in store
    assert select_campus(store, "beiyangyuan", DEFAULT_CAMPUS_REGISTRY) is False


def test_selected_campus_and_map_must_match_at_live_boundaries():
    beiyang_map = DEFAULT_CAMPUS_REGISTRY.get_campus_map("beiyangyuan")
    weijin_map = DEFAULT_CAMPUS_REGISTRY.get_campus_map("weijinlu")
    with pytest.raises(CampusMapMismatchError):
        require_campus_map("weijinlu", beiyang_map)

    origin = resolve_location_in_campus(DEFAULT_CAMPUS_REGISTRY, "weijinlu", "友园")
    destination = resolve_location_in_campus(DEFAULT_CAMPUS_REGISTRY, "beiyangyuan", "9斋")
    with pytest.raises(CrossCampusRouteUnsupportedError):
        plan_movement(weijin_map, origin, destination, TransportMode.WALK)
