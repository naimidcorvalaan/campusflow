"""A partial estimate is adoptable by confirming the already visible scope."""
import json
from dataclasses import replace

import pytest

from src.material_inbox import MaterialInbox, update_source, extract_material, MATERIAL_INBOX_KEY, MaterialError
from src.material_estimate_recovery import minimal_confirmation_mode, confirm_minimal_task
from src.material_ui import handle_material_action
from src.p2_session import load_live_final_turn
from tests.test_material_adoption_regression import app_for, adopt_button
from tests.test_material_estimate_recovery import action_payload, NOW
from tests.test_material_inbox import live

TITLE = '填写并签署版权合规承诺书'
SCOPE = '填写承诺人姓名、签字、填写日期，并准备扫描上传'
QUESTION = '本次需要阅读条款，还是填写并签署表格？'


def declaration_result(ambiguous=False, question=None, fields=('identity',)):
    draft = update_source(MaterialInbox(), TITLE+'：'+SCOPE, '', NOW, 'plan', 'beiyangyuan').draft
    obj = action_payload()
    obj['actionability'].update(task_name=TITLE,
        short_scope='尚未确定是阅读条款还是填写并签署表格' if ambiguous else SCOPE,
        evidence=TITLE, reason='已识别填写姓名、签字、日期和扫描步骤',
        confirmation_required=list(fields), waiting_note='')
    obj['estimate'].update(min_focus_minutes=4,max_focus_minutes=13,recommended_minutes=9,
        clarification_question=QUESTION if ambiguous else question)
    obj['coverage'] = dict(level='partial',reason='只估计当前已识别步骤',uncovered_content='其他未读内容')
    return extract_material(draft,lambda *args:json.dumps(obj,ensure_ascii=False))


@pytest.mark.parametrize('fields',[(),('identity',),('multiple_actions',)])
@pytest.mark.parametrize('question',[None,'未读部分是否还有其他要求？'])
def test_clear_partial_scope_click_is_confirmation(fields, question):
    result = declaration_result(fields=fields,question=question)
    assert result.estimate_coverage == 'partial'
    app = app_for(result)
    assert any('仅估算已识别内容' in m.value for m in app.markdown)
    assert adopt_button(app,9).disabled is False
    assert not app.checkbox
    assert not any('补充任务范围' in b.label for b in app.button)
    assert app.session_state['_adoption_test_model'].calls == []
    adopt_button(app,9).click().run(timeout=20)
    bundle = load_live_final_turn(app.session_state.filtered_state)
    assert bundle is not None
    task = bundle.state.tasks[0]
    assert task.title == TITLE and task.total_minutes == 9
    assert bundle.execution_context.binding_for(task.task_ref).scope_summary == SCOPE


def test_clear_partial_nine_to_fifteen_keeps_original_suggestion_and_single_receipt():
    app = app_for(declaration_result())
    next(n for n in app.number_input if n.label=='采用分钟').set_value(15).run(timeout=20)
    app.run(timeout=20)
    assert adopt_button(app,15).disabled is False
    assert any('4–13' in m.value and '<strong>9 ' in m.value for m in app.markdown)
    assert app.session_state['_adoption_test_model'].calls == []
    adopt_button(app,15).click().run(timeout=20)
    store = app.session_state.filtered_state
    bundle = load_live_final_turn(store)
    task = bundle.state.tasks[0]
    assert task.total_minutes==15 and task.completed_minutes==0
    assert bundle.execution_context.binding_for(task.task_ref).scope_summary==SCOPE
    st,session,model,_ = live(store=store,model=app.session_state['_adoption_test_model'])
    calls = list(model.calls)
    assert not handle_material_action(st,session,model,'confirm',NOW)
    assert model.calls == calls
    assert load_live_final_turn(store)==bundle


def test_real_scope_ambiguity_asks_the_retained_specific_question():
    result = declaration_result(ambiguous=True)
    entry = result.estimate_fallbacks[0]
    assert minimal_confirmation_mode(entry)=='blocked'
    app = app_for(result)
    assert any(QUESTION in b.label for b in app.button)
    assert not any('分钟加入计划' in b.label for b in app.button)
    next(b for b in app.button if QUESTION in b.label).click().run(timeout=20)
    assert any(v.key=='cf_material_supplement' for v in app.text_area)
    assert app.session_state['_adoption_test_model'].calls == []
    with pytest.raises(MaterialError,match='阅读条款'):
        confirm_minimal_task(result,entry['item_id'],TITLE,15,True,confirmed_scope=entry['estimate']['short_scope'])


@pytest.mark.parametrize('fields',[('scope',),()])
def test_already_saved_scope_flag_does_not_require_repeating_clear_scope(fields):
    result = declaration_result()
    entry = dict(result.estimate_fallbacks[0])
    entry.pop('scope_clarification',None)
    entry.update(simple_confirmation_allowed=False,scope_confirmation_allowed=False,
        confirmation_fields=fields,scope_unresolved=True)
    app = app_for(replace(result,estimate_fallbacks=(entry,)))
    assert adopt_button(app,9).disabled is False


def test_hard_fact_gate_is_not_overridden_by_clear_scope():
    result = declaration_result(fields=('identity','deadline'))
    assert minimal_confirmation_mode(result.estimate_fallbacks[0])=='blocked'
    app = app_for(result)
    assert not any('分钟加入计划' in b.label for b in app.button)
    assert any('截止时间' in b.label for b in app.button)


def test_explicit_unknown_scope_blocks_even_without_coarse_identity_flag():
    result = declaration_result(ambiguous=True,fields=())
    app = app_for(result)
    assert not any('分钟加入计划' in b.label for b in app.button)
    assert any(QUESTION in b.label for b in app.button)


def test_coarse_scope_confirmation_never_clears_a_retained_hard_fact():
    result = declaration_result(fields=('deadline',))
    entry = dict(result.estimate_fallbacks[0],scope_confirmation_allowed=True)
    assert minimal_confirmation_mode(entry)=='blocked'
