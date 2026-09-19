"""Offline regressions for shared constraints found by the real API smoke run."""
import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from src.p2_day_intake import DayIntakeProposal, IntakeTask, IntakeCommitment, apply_day_intake, parse_day_intake
from src.p2_live_main import make_live_session
from src.p3_campus_registry import DEFAULT_CAMPUS_REGISTRY
from src.p3_location_resolver import resolve_location, build_location_resolver_prompt
from src.p4_event_semantics import RawEvent, RawEventExtraction, EventSemanticGraph, materialize_day_intake
from src.p4_execution_context import save_execution_context
from src.p4_execution_enrichment import enrich_intake_execution_context
from src.p4_execution_movement import execution_timeline
from src.p4_intake_auditor import _proposal_payload


@pytest.mark.parametrize('campus,qualified,alias', [
    ('beiyangyuan', '北洋园图书馆', '图书馆'),
    ('beiyangyuan', '天津大学北洋园校区学一食堂', '学一食堂'),
    ('beiyangyuan', '第31教学楼', '31教学楼'),
    ('weijinlu', '卫津路图书馆', '图书馆'),
    ('weijinlu', '卫津路校区第9教学楼', '第9教学楼'),
])
def test_campus_qualification_preserves_exact_place_without_model(campus, qualified, alias):
    m = DEFAULT_CAMPUS_REGISTRY.get_campus_map(campus)
    def unexpected(*args):
        pytest.fail('exact map fact must not call the model')
    assert resolve_location(m, qualified, unexpected).node_id == m.resolve_node_id(alias)


def test_location_catalog_covers_all_destination_nodes_without_cross_campus_matching():
    m = DEFAULT_CAMPUS_REGISTRY.get_campus_map('beiyangyuan')
    _, prompt = build_location_resolver_prompt(m, '郑东馆')
    assert all('id=' + n.id + '；' in prompt for n in m.nodes if n.node_kind == 'poi')
    assert not resolve_location(m, '卫津路图书馆').usable
    w = DEFAULT_CAMPUS_REGISTRY.get_campus_map('weijinlu')
    assert resolve_location(w, '图书馆').node_id != resolve_location(w, '春水图书馆').node_id


@pytest.mark.parametrize('kind', ['task', 'meal'])
def test_explicit_event_window_survives_materialization_audit_and_parsing(kind):
    event = RawEvent('event_a', kind, '活动', 1, starts_at='18:10', ends_at='19:20', explicit_duration_minutes=35)
    raw = RawEventExtraction((event,))
    parsed = materialize_day_intake(raw, EventSemanticGraph())
    roundtrip = parse_day_intake(json.dumps(_proposal_payload(parsed)))
    assert roundtrip.tasks[0].earliest_start_time == '18:10'
    assert roundtrip.tasks[0].latest_end_time == '19:20'
    assert roundtrip.tasks[0].total_minutes == 35


def _plan(campus, current, tasks, commitments=()):
    m = DEFAULT_CAMPUS_REGISTRY.get_campus_map(campus)
    proposal = DayIntakeProposal('p2.day-intake.v1', tuple(commitments), tuple(tasks), '21:00', (), current, 'walk')
    applied = apply_day_intake(datetime(2026, 9, 15, 16), proposal)
    context = enrich_intake_execution_context(proposal, applied, m, current_location_text=current)
    store = {}
    save_execution_context(store, context)
    def caller(system, user):
        if 'day-review' in system:
            return json.dumps(dict(schema_version='p2.day-review.v1', decision='accept', reason=None,
                                   suggested_task_order=None, include_low_attention=None))
        return json.dumps(dict(schema_version='p2.day-plan-intent.v1', task_order=list(applied.new_task_refs),
                               include_low_attention=False, task_estimates=[], rationale=None))
    session = make_live_session(store, SimpleNamespace(agent_caller=caller), map_data=m, campus_id=campus,
                                companion_enabled=False, agent_intelligence_enabled=True)
    # Isolate deterministic execution from P5 decision quality in this test.
    session.agent_intelligence_enabled = False
    turn = session.start_day(applied.state)
    events = execution_timeline(turn.result.updated_state, turn.result.allocation_plan, turn.execution_context, m)
    return turn, events


def test_qualified_origin_and_ordinal_building_produce_route():
    turn, events = _plan('beiyangyuan', '北洋园图书馆', [
        IntakeTask('练习', 35, location_text='图书馆', activity_kind='generic'),
    ], [IntakeCommitment('课程', starts_at='18:00', ends_at='19:00', location_text='第31教学楼', commitment_kind='class')])
    assert len(turn.movement_blocks) == 1
    block = turn.movement_blocks[0]
    assert block.origin_node_id != block.destination_node_id
    assert block.end_time <= datetime(2026, 9, 15, 17, 50)


@pytest.mark.parametrize('start,end', [('17:30', '18:30'), ('18:10', '19:20')])
def test_meal_window_and_route_survive_formal_reallocation(start, end):
    turn, events = _plan('beiyangyuan', '北洋园图书馆', [
        IntakeTask('复习', 45, location_text='图书馆', activity_kind='generic'),
        IntakeTask('用餐', 30, location_text='北洋园学一食堂', activity_kind='meal',
                   duration_source='user_explicit', earliest_start_time=start, latest_end_time=end),
    ])
    meal = [e for e in events if e.activity_ref == 'day_task_002']
    assert len(meal) == 1
    assert meal[0].starts_at.strftime('%H:%M') >= start
    assert meal[0].ends_at.strftime('%H:%M') <= end
    assert any(b.destination_activity_ref == 'day_task_002' for b in turn.movement_blocks)
    assert all(t.completed_minutes == 0 for t in turn.result.updated_state.tasks)


def test_same_library_qualified_and_short_names_have_no_movement():
    turn, _ = _plan('weijinlu', '卫津路图书馆', [
        IntakeTask('阅读', 35, location_text='图书馆', activity_kind='generic'),
        IntakeTask('笔记', 25, location_text='卫津路校区图书馆', activity_kind='generic'),
    ])
    assert not turn.movement_blocks


def test_invalid_explicit_window_is_rejected():
    with pytest.raises(ValueError):
        IntakeTask('活动', 30, earliest_start_time='19:00', latest_end_time='18:00')
