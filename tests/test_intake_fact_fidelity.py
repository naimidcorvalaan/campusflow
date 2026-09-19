"""Endpoint evidence, distinct spatial roles and the formal two-turn UI transaction."""
import json
from dataclasses import replace
from types import SimpleNamespace
import pytest
from src.intake_time_evidence import supports_commitment_end
from src.p2_day_intake import IntakeCommitment, IntakeTask, DayIntakeProposal, apply_day_intake
from src.p4_execution_context import CurrentLocationSource
from src.p2_session import load_live_final_turn
from src.p2_live_main import LIVE_SESSION_KEY, LIVE_MAP_KEY, _submit_planning_confirmation
from src.planning_confirmation_ui import bind_confirmation, verify_answer_result
from scripts.confirmation_preview_model import LocationConfirmationPreviewModel
from tests.test_p2_live_main import _StubSt, _run_main, dt

VARIANTS = (
    '下午四点到六点在46教学楼有课，课后想吃饭',
    '16:00到18:00在46教上课，下课后去吃饭',
    '下午4点—6点有课，地点46教学楼，之后想吃晚饭',
    '下午四点至六点在46教有课，课后吃饭',
    '16点上课，18点下课，在46教学楼，之后吃饭',
)

@pytest.mark.parametrize('text', VARIANTS)
def test_all_surface_clocks_preserve_extracted_range_in_formal_state(text):
    proposal=DayIntakeProposal(schema_version='p2.day-intake.v1',day_end=None,questions=(),commitments=(IntakeCommitment('上课',starts_at='16:00',ends_at='18:00',location_text='46教学楼',commitment_kind='class'),),
        tasks=(IntakeTask('吃饭',activity_kind='meal',after_commitment_index=1),))
    applied=apply_day_intake(dt(14),proposal,user_text=text)
    course=applied.state.commitments[0]
    assert (course.starts_at,course.ends_at,course.location_text)==(dt(16),dt(18),'46教学楼')
    assert not any('结束' in q for q in applied.questions)

@pytest.mark.parametrize('text,start,end',[
    ('上午九点半至十一点一刻实验','09:30','11:15'),
    ('晚上七点到九点半组会','19:00','21:30'),
    ('14：15—15：45开会','14:15','15:45'),
    ('下午两点上课，下午四点下课','14:00','16:00'),
])
def test_generic_clock_equivalence(text,start,end):
    assert supports_commitment_end(start,end,text)

@pytest.mark.parametrize('text', ['16点上课', '16点上课，18点吃饭', '上午四点到六点上课'])
def test_guard_still_rejects_invented_or_unrelated_end(text):
    assert not supports_commitment_end('16:00','18:00',text)


def start(text=VARIANTS[0],current=None):
    model=LocationConfirmationPreviewModel(current)
    st=_StubSt().set_inputs(intake=text,intake_submitted=True)
    _run_main(st,model.agent_caller,now=dt(14))
    assert not st.errors
    bundle=load_live_final_turn(st.session_state)
    assert bundle is not None
    return st,model,bundle

@pytest.mark.parametrize('answer',['诚园7斋','不是，我在诚园7斋'])
def test_two_turn_location_only_preserves_course_and_after_meal(answer):
    st,model,before=start()
    assert before.state.commitments[0].ends_at==dt(18)
    assert before.execution_context.current_location.source is CurrentLocationSource.UNKNOWN
    assert before.execution_context.current_location.location is None
    binding=bind_confirmation(before,VARIANTS[0])
    assert binding.field=='current_location'
    assert binding.question=='你现在在哪里？'
    _submit_planning_confirmation(st,st.session_state[LIVE_SESSION_KEY],binding,answer,(),dt(14),st.session_state[LIVE_MAP_KEY],'beiyangyuan',dt(14))
    after=load_live_final_turn(st.session_state)
    assert after is not before,(st.errors,st.warnings)
    assert after.state.commitments==before.state.commitments
    assert after.state.tasks==before.state.tasks
    assert after.execution_context.current_location.source is CurrentLocationSource.USER
    assert '7' in after.execution_context.current_location.location.display_name
    assert after.execution_context.bindings[0].not_before_commitment_ref==after.state.commitments[0].commitment_ref
    assert bind_confirmation(after) is None
    assert model.bindings[0]['field']=='current_location'
    assert model.calls.count('location_confirmation')==1
    meal_entries=[a for a in after.result.allocation_plan.allocations if a.task_ref==after.state.tasks[0].task_ref]
    assert meal_entries
    windows={w.window_ref:w for w in after.state.windows}
    assert all(windows[a.window_ref].starts_at >= dt(18) for a in meal_entries)


def test_explicit_current_is_not_course_or_selected_canteen():
    _,_,bundle=start('我现在在诚园7斋，'+VARIANTS[0],current='诚园7斋')
    assert bundle.execution_context.current_location.source is CurrentLocationSource.USER
    assert '7' in bundle.execution_context.current_location.location.display_name
    assert bundle.state.commitments[0].location_text=='46教学楼'
    assert bundle.state.commitments[0].ends_at==dt(18)
    assert bind_confirmation(bundle) is None

@pytest.mark.parametrize('fault',['end','location','task','relation'])
def test_bound_location_rejects_other_field_mutation(fault):
    _,_,before=start()
    _,_,valid=start('我现在在诚园7斋，'+VARIANTS[0],current='诚园7斋')
    state=valid.state; context=valid.execution_context
    if fault=='end': state=replace(state,commitments=(replace(state.commitments[0],ends_at=None),))
    if fault=='location': state=replace(state,commitments=(replace(state.commitments[0],location_text='其他楼'),))
    if fault=='task': state=replace(state,tasks=())
    if fault=='relation': context=replace(context,bindings=(replace(context.bindings[0],not_before_commitment_ref=None),))
    candidate=SimpleNamespace(turn=SimpleNamespace(result=SimpleNamespace(updated_state=state)),execution_context=context)
    with pytest.raises(ValueError): verify_answer_result(bind_confirmation(before),before,candidate)


@pytest.mark.parametrize('source,explicit,expected', [
    ('user',None,'诚园7斋'),
    ('user','图书馆','图书馆'),
    ('assumed',None,None),
])
def test_confirmed_session_location_is_fallback_but_never_overrides_explicit(source,explicit,expected):
    from src.p2_live_main import run_live_intake
    from src.p2_session import load_execution_context,save_execution_context
    from src.p3_campus_registry import DEFAULT_CAMPUS_REGISTRY
    st,_,bundle=start('我现在在诚园7斋，'+VARIANTS[0],current='诚园7斋')
    context=bundle.execution_context
    save_execution_context(st.session_state,context.with_current_location(
        replace(context.current_location,source=CurrentLocationSource(source))))
    model=LocationConfirmationPreviewModel(explicit)
    text=('我现在在'+explicit+'，' if explicit else '')+VARIANTS[0]
    result=run_live_intake(st.session_state,dt(14),text,model.agent_caller,
        map_data=DEFAULT_CAMPUS_REGISTRY.get_campus_map('beiyangyuan'),campus_id='beiyangyuan')
    assert result.applied is not None
    current=load_execution_context(st.session_state).current_location
    if expected is None:
        assert current.source is CurrentLocationSource.UNKNOWN and current.location is None
    else:
        assert current.source is CurrentLocationSource.USER
        assert ('7' if expected=='诚园7斋' else '图书馆') in current.location.display_name
    assert result.applied.state.commitments[0].ends_at==dt(18)


def test_future_movement_does_not_answer_current_location_question():
    from src.p2_question_filter import filter_pending_questions
    _,_,bundle=start()
    assert bundle.movement_blocks  # future course-to-meal route already exists
    assert filter_pending_questions(('你现在在哪里？','课大约几点结束？'),bundle.state,
        bundle.movement_blocks,bundle.execution_context)==('你现在在哪里？',)
