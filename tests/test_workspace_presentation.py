"""Workbench navigation reads the published turn; it is not a planner."""
from datetime import datetime, timedelta
from types import SimpleNamespace

from src.workspace_ui import select_view, VIEWS, VIEW_KEY
from src.p2_main import render_page_streamlit, _side_card_html
from src.p2_session import load_live_final_turn
from tests.test_p2_live_main import _StubSt, _run_main, CountingCaller


def test_navigation_keeps_published_identity_progress_and_call_count():
    st = _StubSt().set_inputs(intake='今天写作业', intake_submitted=True)
    caller = CountingCaller()
    _run_main(st, caller)
    published = load_live_final_turn(st.session_state)
    assert published is not None
    count = caller.count
    for view in (*VIEWS, '今天'):
        st.set_inputs()
        select_view(st.session_state, view)
        _run_main(st, caller)
        assert load_live_final_turn(st.session_state) is published
        assert caller.count == count
        assert st.session_state[VIEW_KEY] == view
        assert not st.errors


def test_complete_timeline_includes_current_and_does_not_mutate_plan():
    st = _StubSt().set_inputs(intake='今天写作业', intake_submitted=True)
    _run_main(st, CountingCaller())
    turn = load_live_final_turn(st.session_state).turn
    before = repr(turn)
    st.markdown_calls.clear()
    render_page_streamlit(st, turn, part='timeline')
    page = '\n'.join(st.markdown_calls)
    assert 'cf-current"' in page
    assert '当天安排概览' in page
    assert 'class="cf-plan-hero' not in page
    assert repr(turn) == before


def test_sidebar_departure_comes_from_published_movement_not_fixed_start():
    start = datetime(2026, 9, 12, 19)
    fixed = SimpleNamespace(starts_at=start, title='实验课', location_text='46教', commitment_ref='course_42')
    leg = SimpleNamespace(window_start=start-timedelta(minutes=22), end_time=start-timedelta(minutes=10),
        transition_start=start-timedelta(minutes=27), transition_minutes=5,
        estimated_minutes=12, origin_name='食堂', destination_name='46教', destination_activity_ref='course_42')
    turn = SimpleNamespace(result=SimpleNamespace(updated_state=SimpleNamespace(now=start-timedelta(hours=1), commitments=(fixed,))),
        movement_blocks=(leg,), companion_copy=None)
    result = _side_card_html(turn)
    assert '18:38 出发 · 18:50 到达' in result
    assert '18:33 开始收拾 · 5 分钟准备' in result
    assert '19:00' in result
    assert 'course_42' not in result


def test_auxiliary_navigation_preserves_all_unrelated_values():
    source = object()
    store = {'cf_material_image': source, 'cf_material_supplement': '填表', 'cf_material_flow': {'phase': 'result'}}
    before = dict(store)
    for view in VIEWS:
        select_view(store, view)
    assert {k:v for k,v in store.items() if k != VIEW_KEY} == before


def test_environment_disclosure_closes_on_navigation_without_resetting_inputs():
    from src.workspace_ui import toggle_environment
    source = object()
    store = {'p2_live_campus_select': '北洋园校区',
        'p2_live_reference_hour': 20, 'p2_live_reference_minute': 33,
        'cf_material_image': source, 'cf_material_supplement': '填表'}
    before = dict(store)
    toggle_environment(store)
    assert store['cf_environment_open'] is True
    select_view(store, '材料估时')
    assert store['cf_environment_open'] is False
    assert {key:store[key] for key in before} == before
    toggle_environment(store)
    toggle_environment(store)
    assert store['cf_environment_open'] is False
    assert {key:store[key] for key in before} == before


def test_environment_entries_open_independently_and_navigation_closes_both():
    from src.workspace_ui import toggle_environment, view_label
    upload = object()
    store = {'cf_material_image': upload, 'cf_material_supplement': '填表'}
    toggle_environment(store, 'campus')
    assert store['cf_environment_campus_open'] and not store.get('cf_environment_time_open')
    toggle_environment(store, 'time')
    assert store['cf_environment_campus_open'] and store['cf_environment_time_open']
    toggle_environment(store, 'campus')
    assert not store['cf_environment_campus_open'] and store['cf_environment_time_open']
    select_view(store, '材料估时')  # Existing internal ID survives the label change.
    assert view_label(store[VIEW_KEY]) == '任务估时'
    assert not any(store['cf_environment_' + name + '_open'] for name in ('campus', 'time'))
    assert store['cf_material_image'] is upload
    assert store['cf_material_supplement'] == '填表'


def test_optional_location_does_not_nest_inside_replanning_disclosure():
    from contextlib import contextmanager
    st = _StubSt().set_inputs(intake='今天写作业', intake_submitted=True)
    caller = CountingCaller()
    opened = []
    @contextmanager
    def expander(label, **kwargs):
        assert not opened, 'Streamlit does not support nested expanders'
        opened.append(label)
        try:
            yield
        finally:
            opened.pop()
    st.expander = expander
    _run_main(st, caller)
    assert load_live_final_turn(st.session_state) is not None
    st.set_inputs()
    _run_main(st, caller)
    assert not st.errors


def test_drawer_rerun_waits_for_native_upload_mount(monkeypatch):
    import src.p2_live_main as live
    import src.material_ui as material
    events = []
    st = _StubSt().set_inputs()
    def drawer(surface, **kwargs):
        events.append('existing settings action')
        surface.rerun()
        events.append('must not render obsolete drawer widgets')
    def uploader(*args, **kwargs):
        events.append('native upload mounted')
        return False
    original_rerun = st.rerun
    def rerun():
        events.append('page rerun')
        original_rerun()
    st.rerun = rerun
    monkeypatch.setattr(live, '_render_personal_settings_panel', drawer)
    monkeypatch.setattr(material, 'render_material_inbox', uploader)
    _run_main(st, CountingCaller())
    assert events == ['existing settings action', 'native upload mounted', 'page rerun']
    assert st.rerun_called == 1


def test_clock_reset_keeps_native_inputs_mounted_before_rerun(monkeypatch):
    import src.material_ui as material
    events = []
    st = _StubSt().set_inputs()
    original_button = st.button
    st.button = lambda label, key=None, **kwargs: key == 'p2_reference_use_now' or original_button(label, key, **kwargs)
    def mount(*args, **kwargs):
        events.append('upload mounted')
        return False
    monkeypatch.setattr(material, 'render_material_inbox', mount)
    original_rerun = st.rerun
    def rerun():
        events.append('rerun')
        original_rerun()
    st.rerun = rerun
    caller = CountingCaller()
    _run_main(st, caller)
    assert events == ['upload mounted', 'rerun']
    assert caller.count == 0
    assert st.session_state['p2_reference_reset_requested'] is True
    assert not st.errors


def test_reopening_restores_retained_dates_over_removed_widget_defaults():
    from datetime import date
    from src.p2_live_main import _initialize_settings_draft, _capture_settings_draft_widgets
    store = {}
    _initialize_settings_draft(store)
    key = 'personal_settings_draft_semester_first'
    store[key] = date(2026, 8, 31)
    _capture_settings_draft_widgets(store)
    store[key] = date(2026, 9, 12)  # removed date widget's deserialized default
    _initialize_settings_draft(store, reopening=True)
    assert store[key] == date(2026, 8, 31)
    store[key] = date(2026, 9, 7)
    _initialize_settings_draft(store)
    assert store[key] == date(2026, 9, 7)  # ordinary rerun keeps fresh edits
