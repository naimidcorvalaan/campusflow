"""Published movement facts, real local graph geometry and state isolation."""
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
import xml.etree.ElementTree as ET

import pytest

from src.p3_campus_registry import DEFAULT_CAMPUS_REGISTRY
from src.p3_map_schema import MapDataProvenance, TransportMode
from src.p3_route_provider import plan_route
from src.p3_route_planner import MovementBlock
from src.p3_time_estimator import estimate_travel_time
from src.spacetime_ui import build_next_route, route_map_html, campus_texture_html
from src.p2_main import _side_card_html, render_page_streamlit, build_current_plan_display


@pytest.fixture(params=[('beiyangyuan', 'walk'), ('weijinlu', 'walk'), ('beiyangyuan', 'bike'), ('weijinlu', 'bike')])
def published(request):
    campus, mode = request.param
    data = DEFAULT_CAMPUS_REGISTRY.get_campus_map(campus)
    # Deliberately different from the approved prototype's meal examples.
    start, end = ('45教', '郑东图书馆') if campus == 'beiyangyuan' else ('9教', '图书馆')
    origin, destination = data.resolve_node_id(start), data.resolve_node_id(end)
    route = plan_route(data, origin, destination, mode)
    estimate = estimate_travel_time(route.total_distance_m, mode, caller=None)
    block = MovementBlock(window_ref='actual_window_42', window_start=datetime(2026, 9, 14, 16, 35),
        origin_text=start, destination_text=end, origin_node_id=origin, destination_node_id=destination,
        origin_name=start, destination_name=end, mode=TransportMode(mode), distance_m=route.total_distance_m,
        estimated_minutes=estimate.estimated_minutes, low_minutes=estimate.min_minutes,
        high_minutes=estimate.max_minutes, method=estimate.method, approximate=False, transition_minutes=5)
    turn = SimpleNamespace(result=SimpleNamespace(updated_state=SimpleNamespace(now=datetime(2026, 9, 14, 14), commitments=())),
        movement_blocks=(block,), execution_context=None, companion_copy=None)
    return data, turn, block, route


def test_real_geometry_uses_published_endpoints_and_preserves_turn(published):
    data, turn, block, route = published
    before = repr(turn)
    visual = build_next_route(turn, data)
    assert visual.block is block
    assert visual.path == route.path
    assert len(visual.points) == len(route.path)
    svg = ET.fromstring(route_map_html(visual))
    assert svg.attrib['data-distance-m'] == str(block.distance_m)
    assert svg.attrib['data-minutes'] == str(block.estimated_minutes)
    assert svg.attrib['data-mode'] == block.mode.value
    assert block.origin_name in ''.join(svg.itertext()) and block.destination_name in ''.join(svg.itertext())
    assert repr(turn) == before


def test_published_estimate_is_never_replaced_by_a_new_time_call(published, monkeypatch):
    import src.p3_time_estimator as estimator
    data, turn, block, _ = published
    turn.movement_blocks = (replace(block, estimated_minutes=37, low_minutes=33, high_minutes=40),)
    forbidden = Mock(side_effect=AssertionError('Presentation must not re-estimate time'))
    monkeypatch.setattr(estimator, 'estimate_travel_time', forbidden)
    rendered = route_map_html(build_next_route(turn, data))
    assert 'data-minutes="37"' in rendered and '17:12' in rendered
    forbidden.assert_not_called()


def test_unavailable_next_route_does_not_show_a_later_or_example_route(published):
    data, turn, block, _ = published
    invalid = replace(block, distance_m=block.distance_m + 1)
    later = replace(block, window_start=block.window_start + timedelta(hours=1))
    turn.movement_blocks = (later, invalid)
    assert build_next_route(turn, data) is None
    output = _side_card_html(turn, data)
    assert 'cf-mini-route' not in output and '16:35' in output


def test_completed_movement_is_hidden_and_next_future_block_is_selected(published):
    data, turn, block, _ = published
    past = replace(block, window_start=turn.result.updated_state.now - timedelta(minutes=block.estimated_minutes))
    turn.movement_blocks = (past, block)
    assert build_next_route(turn, data).block is block
    turn.movement_blocks = (past,)
    assert build_next_route(turn, data) is None
    assert _side_card_html(turn, data) == ''
    turn.movement_blocks = ()
    assert _side_card_html(turn, data) == ''


def test_cross_campus_and_unknown_nodes_cannot_generate_map(published):
    data, turn, block, _ = published
    other = 'weijinlu' if data.campus_id == 'beiyangyuan' else 'beiyangyuan'
    assert build_next_route(turn, DEFAULT_CAMPUS_REGISTRY.get_campus_map(other)) is None
    turn.execution_context = SimpleNamespace(current_location=SimpleNamespace(location=SimpleNamespace(campus_id=other)), bindings=())
    assert build_next_route(turn, data) is None
    turn.execution_context = None
    turn.movement_blocks = (replace(block, origin_node_id='missing_source_node'),)
    assert build_next_route(turn, data) is None


def test_synthetic_missing_coordinates_and_disconnected_maps_are_not_visualized(published):
    data, turn, block, route = published
    assert build_next_route(turn, None) is None
    assert build_next_route(turn, replace(data, provenance=MapDataProvenance.SYNTHETIC_TEST)) is None
    disconnected = replace(data, edges=())
    object.__setattr__(disconnected, '_identifier_index', data._identifier_index)
    assert build_next_route(turn, disconnected) is None
    absent = replace(data, nodes=tuple(replace(n, longitude=None) if n.id == route.path[0] else n for n in data.nodes))
    # Preserve the loader's read-only identifier index for this damaged-map audit.
    object.__setattr__(absent, '_identifier_index', data._identifier_index)
    assert build_next_route(turn, absent) is None


def test_names_are_escaped_and_never_executed(published):
    data, turn, block, _ = published
    turn.movement_blocks = (replace(block, origin_name='<img onerror="bad">&起点'),)
    rendered = route_map_html(build_next_route(turn, data))
    assert '<img' not in rendered and '&lt;img' in rendered
    ET.fromstring(rendered)


def test_coordinate_projection_keeps_real_relative_geometry(published):
    data, turn, _, route = published
    visual = build_next_route(turn, data)
    by_id = {node.id: node for node in data.nodes}
    import math
    latitude = sum(n.latitude for n in data.nodes) / len(data.nodes)
    raw = [(by_id[i].longitude * math.cos(math.radians(latitude)), -by_id[i].latitude) for i in route.path]
    distances = []
    for i in range(1, len(raw)):
        source = math.dist(raw[0], raw[i])
        if source > 0:
            distances.append(math.dist(visual.points[0], visual.points[i]) / source)
    assert max(distances) == pytest.approx(min(distances), rel=1e-7)


@pytest.mark.parametrize('campus', ['beiyangyuan', 'weijinlu'])
def test_sidebar_texture_has_only_source_edges_and_is_decorative(campus):
    data = DEFAULT_CAMPUS_REGISTRY.get_campus_map(campus)
    rendered = campus_texture_html(data)
    root = ET.fromstring(rendered)
    assert root.attrib['aria-hidden'] == 'true'
    assert root.attrib['data-campus-id'] == campus
    assert 'script' not in rendered and 'http' not in rendered.replace('http://www.w3.org/2000/svg', '')
    assert campus_texture_html(replace(data, provenance=MapDataProvenance.SYNTHETIC_TEST)) == ''
    other = 'weijinlu' if campus == 'beiyangyuan' else 'beiyangyuan'
    assert rendered != campus_texture_html(DEFAULT_CAMPUS_REGISTRY.get_campus_map(other))


@pytest.mark.parametrize('campus', ['beiyangyuan', 'weijinlu'])
@pytest.mark.parametrize('scene', ['moving', 'stationary'])
def test_live_publish_navigation_and_saved_snapshot_keep_formal_facts(campus, scene):
    from scripts.spacetime_preview import SpacetimePreviewModel
    from tests.test_p2_live_main import _StubSt, _run_main
    from src.p2_session import load_live_final_turn
    from src.workspace_ui import select_view
    model = SpacetimePreviewModel(campus, scene)
    st = _StubSt().set_inputs(intake=model.story, intake_submitted=True)
    st.session_state['p2_live_campus_select'] = DEFAULT_CAMPUS_REGISTRY.get_registration(campus).display_name
    now = datetime(2026, 9, 14, 14)
    _run_main(st, model.agent_caller, now=now)
    bundle = load_live_final_turn(st.session_state)
    assert bundle is not None and not st.errors
    before, count = repr(bundle), len(model.calls)
    original_entries = build_current_plan_display(bundle.turn).entries
    for view in ('今天', '时间线', '材料估时', '今天'):
        select_view(st.session_state, view)
        st.set_inputs()
        st.markdown_calls.clear()
        _run_main(st, model.agent_caller, now=now)
        assert load_live_final_turn(st.session_state) is bundle
        assert len(model.calls) == count and repr(bundle) == before and not st.errors
    assert build_current_plan_display(bundle.turn).entries == original_entries
    page = '\n'.join(st.markdown_calls)
    if scene == 'moving':
        assert '<section class="cf-mini-route"' in page
        visual = build_next_route(bundle.turn, st.session_state['p2_live_map'])
        map_data = st.session_state['p2_live_map']
        assert map_data.resolve_node_id(visual.origin_label) == visual.block.origin_node_id
        assert map_data.resolve_node_id(visual.destination_label) == visual.block.destination_node_id
        assert 'data-campus-id="{}"'.format(campus) in page
    else:
        assert not bundle.movement_blocks
        assert '<section class="cf-mini-route"' not in page
    st.markdown_calls.clear()
    render_page_streamlit(st, bundle.turn, saved_snapshot=True, map_data=st.session_state['p2_live_map'])
    assert '<section class="cf-mini-route"' not in '\n'.join(st.markdown_calls)


@pytest.mark.parametrize('error', [FileNotFoundError('map absent'), ValueError('invalid map')])
def test_optional_texture_failure_keeps_setup_and_navigation_available(monkeypatch, error):
    from contextlib import nullcontext
    from src.workspace_ui import render_navigation
    from src.campusflow_ui import BRAND_HEADER_HTML
    from tests.test_p2_live_main import _StubSt
    st = _StubSt()
    st.sidebar = nullcontext()
    monkeypatch.setattr(DEFAULT_CAMPUS_REGISTRY, 'get_campus_map', Mock(side_effect=error))
    render_navigation(st, BRAND_HEADER_HTML, 'personal_settings_panel_open')
    rendered = '\n'.join(st.markdown_calls)
    assert 'cf-brand-header' in rendered and 'CampusFlow / 今天' not in rendered
    assert 'cf-campus-imprint' not in rendered
    assert not st.errors
