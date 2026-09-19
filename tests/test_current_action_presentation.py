"""Read-only presentation of the same published plan and stable widget IDs."""
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace

import pytest

from src.p2_main import CurrentPlanDisplay, PlanDisplayEntry, _hero_html, render_page_streamlit
from src.p2_session import load_live_final_turn
from src.workspace_ui import view_label, place_display_name, place_display_text, select_view
from tests.test_p2_live_main import _StubSt, _run_main, CountingCaller


def test_navigation_labels_keep_existing_internal_ids():
    assert [view_label(v) for v in ('今天', '时间线', '材料估时')] == ['制定计划', '今日计划表', '任务估时']


def test_building_label_changes_display_not_aliases_or_map():
    from src.p3_campus_registry import DEFAULT_CAMPUS_REGISTRY
    data = DEFAULT_CAMPUS_REGISTRY.get_campus_map('beiyangyuan')
    before = repr(data)
    node_id = data.resolve_node_id('31教')
    full = '天津大学北洋园校区31教学楼'
    assert place_display_name(data.campus_id, node_id, '31教') == full
    assert place_display_text('去31教，13:31出发', data.campus_id) == '去' + full + '，13:31出发'
    assert place_display_text(full, data.campus_id) == full
    assert place_display_text('131教', data.campus_id) == '131教'
    assert place_display_name('weijinlu', node_id, '31教') == '31教'
    assert repr(data) == before and data.resolve_node_id('31教') == node_id


@pytest.mark.parametrize('body,kind', [
    ('写作业，做到15:30', 'task'), ('收拾东西，准备出发', 'transition'),
    ('暂时休息，等待下一项安排', 'gap'), ('步行去图书馆', 'movement'),
])
def test_all_action_states_keep_same_hero_without_inventing_progress(body, kind):
    current = PlanDisplayEntry('现在', body, True, True, kind)
    next_step = PlanDisplayEntry('15:30–16:00', '背单词', True, False, 'task')
    display = CurrentPlanDisplay('按已确认的顺序安排', (), (current, next_step), '', ())
    turn = SimpleNamespace(result=SimpleNamespace(updated_state=SimpleNamespace(now=datetime(2026, 9, 18, 14))), execution_context=None)
    before = repr((display, turn))
    rendered = _hero_html(display, turn=turn)
    assert 'cf-plan-hero' in rendered and 'cf-focus-next' not in rendered and '背单词' not in rendered
    assert body.split('，')[0] in rendered
    assert '已经开始' not in rendered and '已完成' not in rendered
    assert repr((display, turn)) == before


def test_pending_confirmation_preserves_question_and_does_not_publish_summary():
    st = _StubSt().set_inputs(intake='今天写作业', intake_submitted=True)
    caller = CountingCaller()
    _run_main(st, caller)
    turn = load_live_final_turn(st.session_state).turn
    st.markdown_calls.clear()
    question = '这20分钟休息，你希望放在哪两个学习任务之间？'
    render_page_streamlit(st, turn, extra_questions=(question,), part='focus')
    output = '\n'.join(st.markdown_calls)
    assert '还需要你确认一件事' in output and question in output
    assert '<div class="cf-plan-summary">' not in output


def test_home_question_heading_does_not_rename_navigation_or_route():
    st = _StubSt().set_inputs()
    _run_main(st, CountingCaller())
    assert '<h1 class="cf-empty-title">今天打算做什么？</h1>' in ''.join(st.markdown_calls)
    assert view_label('今天') == '制定计划'


def test_summary_reads_current_turn_and_home_does_not_show_second_schedule():
    from scripts.spacetime_preview import SpacetimePreviewModel
    model = SpacetimePreviewModel('weijinlu', 'moving')
    st = _StubSt().set_inputs(intake=model.story, intake_submitted=True)
    st.session_state['p2_live_campus_select'] = '卫津路校区'
    _run_main(st, model.agent_caller)
    turn = load_live_final_turn(st.session_state).turn
    before, calls = repr(turn), len(model.calls)
    st.markdown_calls.clear()
    render_page_streamlit(st, turn, part='focus', map_data=st.session_state['p2_live_map'])
    output = '\n'.join(st.markdown_calls)
    assert output.index('class="cf-plan-summary"') < output.index('<section class="cf-current-plan cf-plan-hero')
    assert '<section class="cf-current-plan cf-timeline-card"' not in output
    assert '<details class="cf-route-details">' not in output
    assert '查看地图与路线详情' not in output
    assert 'cf-map-network' not in output.split('</style>')[-1]
    assert '<div class="cf-campus-imprint"' not in output
    assert len(model.calls) == calls and repr(turn) == before
    st.markdown_calls.clear()
    render_page_streamlit(st, turn, part='summary')
    assert '<section class="cf-current-plan cf-timeline-card"' not in ''.join(st.markdown_calls)


def test_home_hides_complete_schedule_but_navigation_preserves_it():
    st = _StubSt().set_inputs(intake='今天写作业', intake_submitted=True)
    caller = CountingCaller()
    _run_main(st, caller)
    published = load_live_final_turn(st.session_state)
    calls = caller.count
    for view, marker in [('今天', 'cf-region-timeline cf-region-hidden'), ('时间线', 'cf-region-timeline "')]:
        select_view(st.session_state, view)
        st.set_inputs()
        st.markdown_calls.clear()
        _run_main(st, caller)
        assert marker in ''.join(st.markdown_calls)
        assert load_live_final_turn(st.session_state) is published and caller.count == calls


def test_real_waiting_plan_has_a_nonempty_hero_and_a_real_next_commitment():
    from src.p2_main import DemoMockCaller, build_demo_state, build_current_plan_display
    from src.p2_session import P2SessionController
    controller = P2SessionController({}, DemoMockCaller())
    controller.start_day(replace(build_demo_state(), tasks=()))
    turn = controller.last_turn()
    before = repr(turn)
    rendered = _hero_html(build_current_plan_display(turn), turn=turn)
    assert '现在暂时没有安排' in rendered
    assert 'cf-focus-next' not in rendered
    assert any('上课' in entry.body for entry in build_current_plan_display(turn).entries)
    assert 'aria-level="1"></div>' not in rendered
    assert repr(turn) == before


@pytest.mark.parametrize('snapshot', [False, True])
def test_current_and_saved_opening_share_summary_component(snapshot):
    from scripts.spacetime_preview import SpacetimePreviewModel
    model = SpacetimePreviewModel('weijinlu', 'moving')
    st = _StubSt().set_inputs(intake=model.story, intake_submitted=True)
    st.session_state['p2_live_campus_select'] = '卫津路校区'
    _run_main(st, model.agent_caller)
    turn = load_live_final_turn(st.session_state).turn
    st.markdown_calls.clear()
    render_page_streamlit(st, turn, saved_snapshot=snapshot, part='focus')
    output = '\n'.join(st.markdown_calls)
    assert output.count('<div class="cf-plan-summary">') == 1
    assert output.index('<div class="cf-plan-summary">') < output.index('cf-plan-hero', output.index('</style>'))
    assert '查看地图与路线详情' not in output
    assert '按当前信息重新安排' not in output


def test_timeline_keeps_distance_and_all_published_movement_times():
    from src.p2_main import _with_movement_details
    block = SimpleNamespace(window_start=datetime(2026, 9, 18, 18, 47),
                            end_time=datetime(2026, 9, 18, 18, 50), distance_m=216)
    entry = PlanDisplayEntry('18:47–18:50', '步行去教学楼，约3分钟', True, False, 'movement')
    result = _with_movement_details((entry,), SimpleNamespace(movement_blocks=(block,)))
    assert result[0].time_text == entry.time_text
    assert result[0].body == entry.body + ' · 216米'
    assert entry.body == '步行去教学楼，约3分钟'


def test_pending_reviewed_feedback_summary_uses_same_component(monkeypatch):
    import src.p2_main as presentation
    from src.p2_companion_copy import CONTEXT_FEEDBACK
    st, model = __import__('tests.test_planning_confirmation_ui', fromlist=['start']).start()
    turn = load_live_final_turn(st.session_state).turn
    # Supply an already reviewed receipt, like the existing feedback path.
    turn = replace(turn, companion_copy=SimpleNamespace(context_type=CONTEXT_FEEDBACK, copy_reviewed=True))
    display = CurrentPlanDisplay('已按你的反馈更新', (), (), '', ())
    monkeypatch.setattr(presentation, 'build_current_plan_display', lambda _: display)
    st.markdown_calls.clear()
    render_page_streamlit(st, turn, extra_questions=('上课大约几点结束？',), part='focus')
    output = '\n'.join(st.markdown_calls)
    assert '<div class="cf-plan-summary"><p>已按你的反馈更新</p>' in output
    assert '<div class="cf-persist-notice">' not in output
