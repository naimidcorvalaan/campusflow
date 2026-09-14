"""Result-to-Task regression: real UI and publication, only model responses are fake."""
import json
from dataclasses import replace

import pytest
from streamlit.testing.v1 import AppTest

from src.material_inbox import MaterialInbox, MATERIAL_INBOX_KEY, extract_material
from src.material_planning import plan_fingerprint
from src.p2_session import load_live_final_turn
from tests.test_material_estimate_recovery import draft_for, payload, action_payload, NOW
from tests.test_workload_engine import estimate_response


def result_for(kind="text", mode="complete"):
    draft, kw = draft_for(kind)
    obj = payload(False) if mode == "complete" else action_payload()
    obj["coverage"] = dict(level="whole" if mode == "complete" else "partial",
        reason="测试材料范围", uncovered_content="" if mode == "complete" else "其他内容")
    estimate = obj["items"][0]["estimate"] if mode == "complete" else obj["estimate"]
    estimate.update(min_focus_minutes=20, max_focus_minutes=40, recommended_minutes=30)
    if kind == "pdf_text":
        if mode == "complete":
            obj["items"][0]["evidence"] = "Experiment one"
        else:
            obj["actionability"]["evidence"] = "Experiment one"
    if mode == "ambiguous":
        obj["actionability"]["confirmation_required"] = ["identity"]
        obj["actionability"]["short_scope"] = '待确认需要填写哪些栏目'
        obj["estimate"]["clarification_question"] = "补充需要填写的栏目和完成范围"
    elif mode == "partial":
        obj["estimate"]["clarification_question"] = "其他未读部分是否还有要求？"
    elif mode == "recovery":
        obj = payload()
    elif mode == "workload":
        obj = dict(schema_version="campusflow.material-text.v2", items=[],
            reference_date=None, reference_evidence=None)
    result = extract_material(draft, lambda *a: json.dumps(obj),
        workload_caller=lambda system, user: estimate_response(json.loads(user), 4), **kw)
    return result


APP = """
import streamlit as st
from tests.test_material_inbox import live, NOW
from src.material_ui import render_material_inbox
from src.material_inbox import MATERIAL_INBOX_KEY
from scripts.low_input_model import LowInputModel
model = st.session_state.setdefault('_adoption_test_model', LowInputModel('b'))
_, session, _, _ = live(model=model, store=st.session_state)
render_material_inbox(st, session, model, (), NOW)
"""


def app_for(result, existing=False):
    from tests.test_material_inbox import prepared, update_row
    from src.material_ui import handle_material_action
    store = {}
    if existing:
        st, session, model, _ = prepared()
        update_row(st, minutes="60")
        assert handle_material_action(st, session, model, "confirm", NOW)
        store = st.session_state
    result = replace(result, plan_fingerprint=plan_fingerprint(store, "beiyangyuan"))
    app = AppTest.from_string(APP)
    for key, value in store.items():
        app.session_state[key] = value
    app.session_state[MATERIAL_INBOX_KEY] = MaterialInbox(result)
    app.run(timeout=20)
    assert not app.exception
    return app


def adopt_button(app, minutes):
    return next(b for b in app.button if b.label == "按 {} 分钟加入计划".format(minutes))


@pytest.mark.parametrize("kind", ["text", "docx", "pdf_text"])
def test_complete_result_exposes_minutes_and_publishes_user_override(kind):
    app = app_for(result_for(kind))
    minute = next(n for n in app.number_input if n.label == "采用分钟")
    assert minute.value == 30
    minute.set_value(45).run(timeout=20)
    assert app.session_state['_adoption_test_model'].calls == []
    adopt_button(app, 45).click().run(timeout=20)
    assert not app.exception
    task = load_live_final_turn(app.session_state.filtered_state).state.tasks[0]
    assert task.total_minutes == 45 and task.completed_minutes == 0


@pytest.mark.parametrize("mode", ["partial", "workload", "recovery"])
def test_partial_and_estimate_only_results_expose_adoption(mode):
    app = app_for(result_for("docx", mode))
    assert any("仅估算已识别内容" in m.value for m in app.markdown)
    assert any(n.label == "采用分钟" for n in app.number_input)
    assert any("分钟加入计划" in b.label for b in app.button)


def test_ambiguous_result_gives_specific_scope_next_step():
    app = app_for(result_for("docx", "ambiguous"))
    assert any("补充需要填写的栏目和完成范围" == b.label for b in app.button)
    assert not any("分钟加入计划" in b.label for b in app.button)


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("mode", ["complete", "partial", "workload", "recovery"])
def test_adoption_updates_existing_plan_or_starts_new_once(existing, mode):
    from src.p1_models import SourceKind
    from src.material_ui import handle_material_action
    from tests.test_material_inbox import live
    app = app_for(result_for("docx", mode), existing=existing)
    before = load_live_final_turn(app.session_state.filtered_state)
    prior = tuple((t.task_ref, t.total_minutes, t.completed_minutes) for t in before.state.tasks) if before else ()
    minute = next(n for n in app.number_input if n.label == "采用分钟")
    minute.set_value(45).run(timeout=20)
    app.run(timeout=20)
    assert next(n for n in app.number_input if n.label == "采用分钟").value == 45
    if mode != "complete":
        button = adopt_button(app, 45)
        assert not button.disabled
    assert app.session_state['_adoption_test_model'].calls == []
    adopt_button(app, 45).click().run(timeout=20)
    assert not app.exception
    after = load_live_final_turn(app.session_state.filtered_state)
    assert after is not None, [w.value for w in app.warning]
    assert len(after.state.tasks) == len(prior) + 1
    assert tuple((t.task_ref, t.total_minutes, t.completed_minutes) for t in after.state.tasks[:-1]) == prior
    new = after.state.tasks[-1]
    assert new.total_minutes == 45 and new.completed_minutes == 0
    assert new.total_source is SourceKind.USER_CONFIRMED
    assert after.execution_context.binding_for(new.task_ref).effective_duration_minutes == 45
    assert app.session_state[MATERIAL_INBOX_KEY].draft.status == 'confirmed'
    calls = list(app.session_state['_adoption_test_model'].calls)
    # Reopening the page must not offer another publication of the receipt.
    reopened = AppTest.from_string(APP)
    for key, value in app.session_state.filtered_state.items():
        reopened.session_state[key] = value
    reopened.run(timeout=20)
    assert not any("分钟加入计划" in b.label for b in reopened.button)
    # A duplicate submit reaches the real receipt/status gate, not just a hidden button.
    st, session, model, _ = live(model=app.session_state['_adoption_test_model'], store=app.session_state.filtered_state)
    assert not handle_material_action(st, session, model, 'confirm', NOW)
    assert model.calls == calls
    assert load_live_final_turn(st.session_state) == after


def test_manual_minutes_survive_review_and_widget_remount():
    app = app_for(result_for())
    next(n for n in app.number_input if n.label == '采用分钟').set_value(45).run(timeout=20)
    next(b for b in app.button if b.label == '核对任务详情').click().run(timeout=20)
    assert next(n for n in app.number_input if n.label == '采用分钟').value == 45
    adopt_button(app, 45).click().run(timeout=20)
    assert not app.exception
    assert load_live_final_turn(app.session_state.filtered_state).state.tasks[0].total_minutes == 45


def test_ambiguous_scope_button_opens_existing_supplement_without_model_call():
    app = app_for(result_for('docx', 'ambiguous'))
    next(b for b in app.button if b.label == '补充需要填写的栏目和完成范围').click().run(timeout=20)
    assert any(t.key == 'cf_material_supplement' for t in app.text_area)
    assert any(b.label == '重新估算' for b in app.button)
    assert app.session_state['_adoption_test_model'].calls == []
    assert load_live_final_turn(app.session_state.filtered_state) is None


@pytest.mark.parametrize('field, label', [('deadline', '截止时间'), ('location', '执行地点'),
    ('fixed_arrangement', '固定安排时间'), ('existing_task', '新任务或已有任务')])
def test_constraint_recovery_cannot_use_scoped_shortcut(field, label):
    from src.material_estimate_recovery import confirm_minimal_task
    from src.material_inbox import MaterialError
    draft, kw = draft_for('docx')
    obj = action_payload()
    obj['actionability']['confirmation_required'] = [field]
    result = extract_material(draft, lambda *a: json.dumps(obj), **kw)
    entry = result.estimate_fallbacks[0]
    with pytest.raises(MaterialError, match=label):
        confirm_minimal_task(result, entry['item_id'], '填写申请表', 45, True, confirmed_scope='填写已识别栏目')
    app = app_for(result)
    assert any(label in b.label and '加入计划' in b.label for b in app.button)
    assert not any('分钟加入计划' in b.label for b in app.button)


def test_partial_task_requires_scope_and_consent_before_draft_creation():
    from src.material_estimate_recovery import confirm_minimal_task
    from src.material_inbox import MaterialError
    result = result_for('docx', 'workload')
    entry = result.estimate_fallbacks[0]
    for consent, scope in [(False, '填写已读栏目'), (True, None), (True, '')]:
        with pytest.raises(MaterialError):
            confirm_minimal_task(result, entry['item_id'], '填写表格已识别部分', 15, consent, confirmed_scope=scope)
    updated = confirm_minimal_task(result, entry['item_id'], '填写表格已识别部分', 15, True,
        confirmed_scope='填写已识别的姓名和分数栏目')
    assert updated.estimate_coverage == 'partial'
    assert updated.items[0].scope == '填写已识别的姓名和分数栏目'
    assert updated.status == 'ready'


def test_workload_known_deadline_is_not_dropped_by_empty_estimate_response():
    from scripts.material_demo_model import timing
    from src.material_estimate_recovery import minimal_confirmation_mode
    draft, kw = draft_for('docx')
    obj = payload(deadline=timing('明天', days=1))
    obj['items'][0]['estimate']['recommended_minutes'] = None
    result = extract_material(draft, lambda *a: json.dumps(obj),
        workload_caller=lambda system, user: estimate_response(json.loads(user), 4), **kw)
    assert minimal_confirmation_mode(result.estimate_fallbacks[0]) == 'blocked'


def test_adopted_minutes_persist_with_draft_not_only_widget(tmp_path):
    from src.local_persistence import LocalProfileStore
    for mode in ('complete', 'workload'):
        app = app_for(result_for('docx', mode))
        next(n for n in app.number_input if n.label == '采用分钟').set_value(45).run(timeout=20)
        store = LocalProfileStore(tmp_path / (mode + '.sqlite3'))
        store.save(0, material_inbox=app.session_state[MATERIAL_INBOX_KEY])
        reopened = app_for(store.load().material_inbox.draft)
        assert next(n for n in reopened.number_input if n.label == '采用分钟').value == 45
        assert reopened.session_state['_adoption_test_model'].calls == []


def test_exact_four_minute_partial_action_can_be_adopted_as_fifteen():
    from tests.test_material_estimate_recovery import quality_payload
    from src.material_inbox import update_source
    text = '填写德、智、体、美、劳各项分数'
    draft = update_source(MaterialInbox(), text, '', NOW, 'plan', 'beiyangyuan').draft
    obj = quality_payload()
    obj['estimate']['min_focus_minutes'] = 2
    result = extract_material(draft, lambda *a: json.dumps(obj))
    app = app_for(result)
    assert next(n for n in app.number_input if n.label == '采用分钟').value == 4
    next(n for n in app.number_input if n.label == '采用分钟').set_value(15).run(timeout=20)
    assert any('2–5' in m.value and '<strong>4 ' in m.value for m in app.markdown)
    adopt_button(app, 15).click().run(timeout=20)
    task = load_live_final_turn(app.session_state.filtered_state).state.tasks[0]
    assert task.total_minutes == 15 and '填写' in task.title


def test_failed_atomic_adoption_keeps_old_plan_and_editable_minutes(monkeypatch):
    from src import p2_live_main as main
    from src.material_ui import handle_material_action, save_material_edit
    from src.local_persistence import LocalPersistenceError
    from tests.test_material_inbox import prepared, update_row
    st, session, model, _ = prepared()
    update_row(st, minutes='60')
    assert handle_material_action(st, session, model, 'confirm', NOW)
    before = load_live_final_turn(st.session_state)
    result = result_for('docx', 'workload')
    result = replace(result, plan_fingerprint=plan_fingerprint(st.session_state, 'beiyangyuan'))
    assert save_material_edit(st, MaterialInbox(result), NOW)
    entry = result.estimate_fallbacks[0]
    assert handle_material_action(st, session, model, 'prepare_estimate', NOW,
        item_id=entry['item_id'], confirmed_title='填写已识别栏目', confirmed_minutes=15,
        simple_confirmed=True, confirmed_scope=entry['estimate']['short_scope'])
    prepared_inbox = st.session_state[MATERIAL_INBOX_KEY]
    def fail(*a, **kw):
        raise LocalPersistenceError('synthetic write failure')
    monkeypatch.setattr(main, '_persist_local_profile', fail)
    assert not handle_material_action(st, session, model, 'confirm', NOW)
    assert st.session_state[MATERIAL_INBOX_KEY] == prepared_inbox
    assert prepared_inbox.draft.items[0].user_edits['minutes'] == '15'
    assert load_live_final_turn(st.session_state) == before
    assert not prepared_inbox.receipts
