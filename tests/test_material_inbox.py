"""Real live handlers, with model calls replaced only at the lowest adapter."""
from datetime import datetime
from types import SimpleNamespace
from dataclasses import replace
import json
import sqlite3
import pytest

from scripts.low_input_model import LowInputModel
from src.p2_live_main import _handle_intake_submit, make_live_session
from src.p2_session import load_live_final_turn
from src.p3_campus_registry import DEFAULT_CAMPUS_REGISTRY
from scripts.material_demo_model import MaterialDemoModel, NOTICES
from src.material_inbox import (MATERIAL_INBOX_KEY, MaterialInbox, update_source, edited, item_values,
    resolved_time, checked_item, MaterialError, apply_notice_date, material_issues,
    update_image_source)
from src.material_planning import plan_fingerprint
from src.material_ui import handle_material_action, save_material_edit
from src.p1_models import SourceKind

NOTICE = '@全体同学 高数第三章第3—12题，第8题写完整过程，2026-09-11 22:00前交。'
NOW = datetime(2026, 9, 7, 14)


def live(model=None, store=None):
    store = {} if store is None else store
    model = model or LowInputModel('b')
    map_data = DEFAULT_CAMPUS_REGISTRY.get_campus_map('beiyangyuan')
    session = make_live_session(store, model, map_data=map_data, campus_id='beiyangyuan',
        companion_enabled=True, agent_intelligence_enabled=True)
    messages = []
    st = SimpleNamespace(session_state=store, warning=messages.append, error=messages.append, write=messages.append)
    return st, session, model, map_data


def test_existing_main_input_is_direct_planning_not_a_material_preview():
    st, session, model, map_data = live()
    assert _handle_intake_submit(st, session, (), NOW, NOTICE, map_data, 'beiyangyuan',
        current_location_text='', rerun_after_commit=False)
    assert load_live_final_turn(st.session_state) is not None
    assert not st.session_state.get('material_inbox')
    # This proves entry semantics, not how real Qwen would interpret the notice.


def prepared(story='a', notice_date='', store=None):
    st, session, model, map_data = live(MaterialDemoModel(story),store)
    inbox = update_source(st.session_state.get(MATERIAL_INBOX_KEY,MaterialInbox()),NOTICES[story],notice_date,
        NOW,plan_fingerprint(session.store,'beiyangyuan'),'beiyangyuan')
    assert save_material_edit(st,inbox,NOW)
    assert handle_material_action(st,session,model,'extract',NOW)
    return st,session,model,map_data


def update_row(st,index=0,**changes):
    inbox = st.session_state[MATERIAL_INBOX_KEY]
    rows = list(inbox.draft.items)
    rows[index] = edited(rows[index],**changes)
    assert save_material_edit(st,replace(inbox,draft=replace(inbox.draft,items=tuple(rows))),NOW)


def test_notice_estimate_confirm_uses_original_scope_and_single_task_no_hidden_estimate():
    st,session,model,_ = prepared()
    draft = st.session_state[MATERIAL_INBOX_KEY].draft
    assert draft.items[0].minutes is None and load_live_final_turn(st.session_state) is None
    assert model.calls == ['material','material']  # missing estimate consumes the one bounded correction
    assert resolved_time(draft.items[0].deadline,draft.reference_date) == datetime(2026,9,11,22)
    assert handle_material_action(st,session,model,'estimate',NOW,draft.items[0].item_id)
    update_row(st,minutes='40')
    estimates = model.calls.count('estimate')
    assert handle_material_action(st,session,model,'confirm',NOW)
    bundle = load_live_final_turn(st.session_state)
    task = bundle.state.tasks[0]
    assert task.total_minutes == 40 and task.completed_minutes == 0
    assert task.total_source is SourceKind.USER_CONFIRMED
    binding = bundle.execution_context.binding_for(task.task_ref)
    assert binding.scope_summary == '第三章第3—12题'
    assert binding.deadline_at == datetime(2026,9,11,22)
    assert model.calls.count('estimate') == estimates
    calls = len(model.calls)
    assert not handle_material_action(st,session,model,'confirm',NOW)
    assert len(model.calls) == calls and len(bundle.state.tasks) == 1


@pytest.mark.parametrize('notice_date,expected', [('',None),('2026-09-07',datetime(2026,9,11,22))])
def test_relative_deadline_requires_reliable_notice_reference(notice_date,expected):
    st,session,model,_ = prepared('relative',notice_date)
    draft = st.session_state[MATERIAL_INBOX_KEY].draft
    assert resolved_time(draft.items[0].deadline,draft.reference_date) == expected
    if expected is None:
        assert not handle_material_action(st,session,model,'confirm',NOW)
        assert load_live_final_turn(st.session_state) is None
    else:
        assert handle_material_action(st,session,model,'confirm',NOW)
        assert load_live_final_turn(st.session_state).state.tasks[0].total_minutes is None


def test_multiple_items_explicit_duration_select_batch_and_fixed_constraints():
    st,session,model,_ = prepared('c')
    draft = st.session_state[MATERIAL_INBOX_KEY].draft
    assert [i.kind for i in draft.items] == ['task','task','fixed_commitment']
    assert draft.items[1].minutes == 120 and draft.items[1].duration_source == 'material_explicit'
    update_row(st,0,selected=False)
    assert handle_material_action(st,session,model,'confirm',NOW), st.warning.__self__
    bundle = load_live_final_turn(st.session_state)
    assert len(bundle.state.tasks) == len(bundle.state.commitments) == 1
    assert bundle.state.tasks[0].total_minutes == 120
    assert bundle.state.tasks[0].total_source is SourceKind.AI_EXTRACTED_FROM_USER_TEXT
    assert bundle.state.commitments[0].starts_at == datetime(2026,9,7,16)


def test_supplement_preserves_ref_progress_and_requires_conflict_confirmation():
    st,session,model,_ = prepared()
    update_row(st,minutes='90')
    assert handle_material_action(st,session,model,'confirm',NOW)
    # Actual feedback rebuild with only the lowest model response mocked.
    session.rebuild_feedback_atomic('高数作业我已经做了20分钟。')
    task = load_live_final_turn(st.session_state).state.tasks[0]
    assert task.completed_minutes == 20
    st,session,model,_ = prepared('b',store=st.session_state)
    draft = st.session_state[MATERIAL_INBOX_KEY].draft
    assert draft.items[0].possible_task_ref == task.task_ref
    assert not handle_material_action(st,session,model,'confirm',NOW)  # undecided target
    update_row(st,target=task.task_ref,minutes='120',scope='第三章第3—12题并附检查记录')
    assert not handle_material_action(st,session,model,'confirm',NOW)
    update_row(st,reviewed=True)
    assert handle_material_action(st,session,model,'confirm',NOW)
    tasks = load_live_final_turn(st.session_state).state.tasks
    assert len(tasks)==1 and tasks[0].task_ref==task.task_ref
    assert tasks[0].completed_minutes == 20 and tasks[0].total_minutes == 120


def test_changed_material_and_failed_batch_leave_reliable_plan_and_preview_untouched():
    st,session,model,_ = prepared()
    update_row(st,minutes='30')
    assert handle_material_action(st,session,model,'confirm',NOW)
    before = load_live_final_turn(st.session_state)
    st,session,model,_ = prepared('c',store=st.session_state)
    update_row(st,2,end='2026-09-07 15:00')
    assert not handle_material_action(st,session,model,'confirm',NOW)
    assert load_live_final_turn(st.session_state) is before
    inbox = st.session_state[MATERIAL_INBOX_KEY]
    assert save_material_edit(st,update_source(inbox,'新通知','',NOW,
        plan_fingerprint(session.store,'beiyangyuan'),'beiyangyuan'),NOW)
    assert st.session_state[MATERIAL_INBOX_KEY].draft.status == 'stale'
    assert not handle_material_action(st,session,model,'confirm',NOW)
    assert load_live_final_turn(st.session_state) is before


@pytest.mark.parametrize('mode,calls', [('network',1),('format',2)])
def test_failure_keeps_material_without_task_or_unbounded_calls(mode,calls):
    st,session,model,_ = live(MaterialDemoModel())
    inbox = update_source(MaterialInbox(),NOTICE,'',NOW,plan_fingerprint(session.store,'beiyangyuan'),'beiyangyuan')
    save_material_edit(st,inbox,NOW)
    model.failure_mode = mode
    handled = handle_material_action(st,session,model,'extract',NOW)
    assert handled == (mode == 'format')  # needs-input result, not formal success
    if handled:
        assert st.session_state[MATERIAL_INBOX_KEY].draft.diagnostics['result_level']=='estimate_unavailable'
    assert len(model.calls)==calls and load_live_final_turn(st.session_state) is None
    assert st.session_state[MATERIAL_INBOX_KEY].draft.original_text == NOTICE


def test_persist_reopen_isolated_profiles_stale_draft_and_atomic_write_failure(tmp_path):
    from src.local_persistence import LocalProfileStore, LocalPersistenceError, PROFILE_SCHEMA_VERSION
    from src.p2_live_main import (LOCAL_PROFILE_STORE_KEY, LOCAL_PROFILE_REVISION_KEY, _apply_profile_snapshot,
        _clear_profile_session)
    path = tmp_path/'profiles.sqlite3'
    store = {LOCAL_PROFILE_STORE_KEY:LocalProfileStore(path,'person-a'),LOCAL_PROFILE_REVISION_KEY:0}
    st,session,model,_ = prepared(store=store)
    update_row(st,minutes='40')
    restored = LocalProfileStore(path,'person-a').load()
    other = LocalProfileStore(path,'person-b').load()
    assert other.material_inbox is None
    fresh = {}
    _apply_profile_snapshot(fresh,restored,datetime(2026,9,8,14))
    assert fresh[MATERIAL_INBOX_KEY].draft.items[0].user_edits['minutes']=='40'
    assert load_live_final_turn(fresh) is None
    _clear_profile_session(fresh)
    assert MATERIAL_INBOX_KEY not in fresh
    class Broken:
        def save(self,*args,**kwargs):
            raise LocalPersistenceError('保存失败，原记录保留。')
    st.session_state[LOCAL_PROFILE_STORE_KEY]=Broken()
    assert not handle_material_action(st,session,model,'confirm',NOW)
    assert load_live_final_turn(st.session_state) is None
    assert LocalProfileStore(path,'person-a').load().material_inbox.draft.status=='ready'
    st.session_state[LOCAL_PROFILE_STORE_KEY]=LocalProfileStore(path,'person-a')
    assert handle_material_action(st,session,model,'confirm',NOW)
    saved = LocalProfileStore(path,'person-a').load()
    reopened = {}
    _apply_profile_snapshot(reopened,saved,NOW)
    formal = load_live_final_turn(reopened)
    assert formal.state.tasks[0].total_minutes==40
    assert reopened[MATERIAL_INBOX_KEY].receipts == saved.material_inbox.receipts
    assert reopened[MATERIAL_INBOX_KEY].draft.status=='confirmed'
    assert LocalProfileStore(path,'person-b').load().latest_plan is None
    with sqlite3.connect(str(path)) as db:
        db.execute('UPDATE profile_meta SET schema_version=4')
        # A real previous schema has no material table; all older roots remain.
        db.execute('DROP TABLE material_snapshot')
    migrated = LocalProfileStore(path,'person-a').load()
    assert migrated.material_inbox is None
    assert migrated.latest_plan == saved.latest_plan
    with sqlite3.connect(str(path)) as db:
        assert db.execute('SELECT schema_version FROM profile_meta LIMIT 1').fetchone()[0]==PROFILE_SCHEMA_VERSION


def test_unknown_minutes_confirm_no_estimate_call_and_anonymous_no_disk_record(tmp_path,monkeypatch):
    monkeypatch.setenv('CAMPUSFLOW_DATA_DIR',str(tmp_path))
    st,session,model,_ = prepared()
    assert handle_material_action(st,session,model,'confirm',NOW)
    assert load_live_final_turn(st.session_state).state.tasks[0].total_minutes is None
    assert 'estimate' not in model.calls and 'plan' not in model.calls
    assert not list(tmp_path.iterdir())


def test_empty_material_result_and_successful_single_format_repair():
    from src.material_inbox import extract_material
    st,session,model,_ = live(MaterialDemoModel())
    draft = update_source(MaterialInbox(),NOTICE,'',NOW,plan_fingerprint(session.store,'beiyangyuan'),'beiyangyuan').draft
    calls=[]
    def caller(system,user):
        calls.append(system)
        if len(calls)==1:
            return 'not json'
        return json.dumps(dict(schema_version='campusflow.material-text.v2',reference_date=None,
            reference_evidence=None,items=[]))
    result = extract_material(draft,caller)
    assert result.model_calls==2 and result.items==()
    assert result.diagnostics['result_level']=='estimate_unavailable'
    assert '你准备怎么处理这份材料' not in result.message


def test_changed_scope_invalidates_ai_estimate_without_losing_user_correction():
    st,session,model,_ = prepared()
    item=st.session_state[MATERIAL_INBOX_KEY].draft.items[0]
    assert handle_material_action(st,session,model,'estimate',NOW,item.item_id)
    update_row(st,scope='只做第3—6题')
    assert not handle_material_action(st,session,model,'confirm',NOW)
    # A deliberate different adopted value does not require model consent.
    update_row(st,minutes='25')
    assert handle_material_action(st,session,model,'confirm',NOW)
    assert load_live_final_turn(st.session_state).state.tasks[0].total_source is SourceKind.USER_CONFIRMED


def test_batch_rejects_other_campus_and_stale_formal_progress():
    st,session,model,_ = prepared()
    update_row(st,minutes='60')
    assert handle_material_action(st,session,model,'confirm',NOW)
    st,session,model,_ = prepared('b',store=st.session_state)
    ref=load_live_final_turn(st.session_state).state.tasks[0].task_ref
    update_row(st,target=ref,campus_id='weijinlu')
    assert not handle_material_action(st,session,model,'confirm',NOW)
    update_row(st,campus_id='beiyangyuan')
    session.rebuild_feedback_atomic('已经写了20分钟。')
    before=load_live_final_turn(st.session_state)
    assert not handle_material_action(st,session,model,'confirm',NOW)
    assert load_live_final_turn(st.session_state) is before


def test_estimate_supplement_is_remaining_work_and_reextract_has_fresh_widget_generation():
    st,session,model,_ = prepared()
    update_row(st,minutes='90')
    assert handle_material_action(st,session,model,'confirm',NOW)
    session.rebuild_feedback_atomic('高数作业我已经做了20分钟。')
    ref = load_live_final_turn(st.session_state).state.tasks[0].task_ref
    st,session,model,_ = prepared('b',store=st.session_state)
    row = st.session_state[MATERIAL_INBOX_KEY].draft.items[0]
    assert not handle_material_action(st,session,model,'estimate',NOW,row.item_id)
    assert 'estimate' not in model.calls
    update_row(st,target=ref,reviewed=True,supplement='前两题已经完成')
    assert handle_material_action(st,session,model,'estimate',NOW,row.item_id)
    row = st.session_state[MATERIAL_INBOX_KEY].draft.items[0]
    assert row.estimate_completed_minutes == 20
    assert '实际已投入20分钟' in row.estimate.material.text
    assert int(row.user_edits['minutes']) == row.estimate.adopted_minutes + 20
    assert handle_material_action(st,session,model,'confirm',NOW)
    task = load_live_final_turn(st.session_state).state.tasks[0]
    assert task.task_ref == ref and task.completed_minutes == 20
    assert task.total_minutes == row.estimate.adopted_minutes + 20
    # Same material re-extraction must not inherit old widget edits.
    st,session,model,_ = prepared('c',store=st.session_state)
    before = st.session_state[MATERIAL_INBOX_KEY].draft
    update_row(st,title='用户旧编辑')
    assert handle_material_action(st,session,model,'extract',NOW)
    after = st.session_state[MATERIAL_INBOX_KEY].draft
    assert after.extraction_revision == before.extraction_revision + 1
    assert not after.items[0].user_edits


def test_uncertain_language_never_becomes_a_hard_deadline_without_user_decision():
    st,session,model,_ = prepared('dirty_b')
    inbox = st.session_state[MATERIAL_INBOX_KEY]
    row = inbox.draft.items[0]
    assert [x['field'] for x in material_issues(row)] == ['deadline','completion']
    assert resolved_time(row.deadline,inbox.draft.reference_date) is None
    assert not handle_material_action(st,session,model,'confirm',NOW)
    assert load_live_final_turn(st.session_state) is None
    calls = len(model.calls)
    update_row(st,scope='用户核对后的实验范围')
    inbox = st.session_state[MATERIAL_INBOX_KEY]
    inbox = apply_notice_date(inbox,'2026-09-07')
    assert save_material_edit(st,inbox,NOW)
    assert len(model.calls) == calls  # deterministic date attachment, not a re-extraction
    assert st.session_state[MATERIAL_INBOX_KEY].draft.items[0].user_edits['scope'] == '用户核对后的实验范围'
    update_row(st,deadline_decision='omit')
    assert handle_material_action(st,session,model,'confirm',NOW)
    binding = load_live_final_turn(st.session_state).execution_context.bindings[0]
    assert binding.deadline_at is None  # completion uncertainty did not block
    st,session,model,_ = prepared('dirty_b')
    update_row(st,deadline='2026-09-14 22:00')
    assert handle_material_action(st,session,model,'confirm',NOW)
    assert load_live_final_turn(st.session_state).execution_context.bindings[0].deadline_at == datetime(2026,9,14,22)


def test_irrelevant_chat_is_ignored_and_future_format_note_can_be_deferred():
    st,session,model,_ = prepared('dirty_none')
    assert st.session_state[MATERIAL_INBOX_KEY].draft.items == ()
    assert load_live_final_turn(st.session_state) is None
    st,session,model,_ = prepared('dirty_d')
    row = st.session_state[MATERIAL_INBOX_KEY].draft.items[0]
    assert row.title == '小论文' and row.scope == '自选主题，约3000字'
    from src.material_inbox import unresolved_relative_fields
    assert unresolved_relative_fields(row,st.session_state[MATERIAL_INBOX_KEY].draft) == ()
    update_row(st,deadline_decision='omit')
    assert handle_material_action(st,session,model,'confirm',NOW)
    binding = load_live_final_turn(st.session_state).execution_context.bindings[0]
    assert binding.completion_criteria == '格式尚未发布'
    assert binding.deadline_at is None


def test_dirty_multi_item_extraction_keeps_independent_facts_without_shared_deadlines():
    st,session,model,_ = prepared('dirty_c')
    rows = st.session_state[MATERIAL_INBOX_KEY].draft.items
    assert [x.kind for x in rows] == ['task','task','fixed_commitment']
    assert rows[0].deadline.text != rows[1].deadline.text
    assert rows[2].minutes == 60 and rows[2].location_text == '33教'
    assert rows[0].minutes is None and rows[1].minutes is None


def test_existing_task_conflict_can_keep_old_deadline_without_duplicate_or_progress_reset():
    st,session,model,_ = prepared('existing_old')
    update_row(st,minutes='60')
    assert handle_material_action(st,session,model,'confirm',NOW)
    session.rebuild_feedback_atomic('已经写了20分钟。')
    original = load_live_final_turn(st.session_state)
    task = original.state.tasks[0]
    old_deadline = original.execution_context.binding_for(task.task_ref).deadline_at
    st,session,model,_ = prepared('dirty_e',store=st.session_state)
    inbox = apply_notice_date(st.session_state[MATERIAL_INBOX_KEY],'2026-09-07')
    assert save_material_edit(st,inbox,NOW)
    assert material_issues(st.session_state[MATERIAL_INBOX_KEY].draft.items[0]) == ()
    update_row(st,target=task.task_ref,deadline_conflict='existing')
    assert handle_material_action(st,session,model,'confirm',NOW)
    updated = load_live_final_turn(st.session_state)
    assert len(updated.state.tasks) == 1
    assert updated.state.tasks[0].task_ref == task.task_ref
    assert updated.state.tasks[0].completed_minutes == 20
    assert updated.execution_context.binding_for(task.task_ref).deadline_at == old_deadline


def test_material_without_an_identifiable_thing_cannot_create_a_placeholder_task():
    st,session,model,_ = prepared('dirty_f')
    update_row(st,deadline_decision='omit')
    assert not handle_material_action(st,session,model,'confirm',NOW)
    assert load_live_final_turn(st.session_state) is None
    update_row(st,title='数据结构实验一',scope='完成实验一')
    assert handle_material_action(st,session,model,'confirm',NOW)
    assert load_live_final_turn(st.session_state).state.tasks[0].title == '数据结构实验一'


def test_image_material_uses_same_draft_and_one_call_estimate_without_persisting_bytes(tmp_path):
    st,session,_,_ = live()
    image = b'\x89PNG\r\n\x1a\n' + b'synthetic-image-body'
    calls = []
    response = json.dumps(dict(schema_version='campusflow.material-text.v2',
        reference_date=None,reference_evidence=None,items=[dict(
            kind='task',title='大学物理热学习题',scope='完成截图中可见的选择、填空和计算题',
            completion='写出计算过程并核对图示题',evidence='截图中可见一页热学习题',
            deadline=None,start=None,end=None,location_text=None,campus_id=None,
            commitment_kind=None,minutes=None,duration_evidence=None,uncertainties=[
                dict(field='completion',status='missing',message='底部小题较模糊，请确认是否也要完成',
                     evidence='截图底部文字较模糊')],possible_task_ref=None,
            estimate=dict(min_focus_minutes=45,max_focus_minutes=70,recommended_minutes=60,
                basis='包含概念判断、图像阅读和分步计算',assumptions=['只估算截图中可见范围'],
                clarification_question=None))]),ensure_ascii=False)
    class ImageAdapter:
        supports_image_inputs = True
        agent_caller = staticmethod(lambda system,user: response)
        @staticmethod
        def task_estimation_caller(system,user,image_mime=None,image_bytes=None):
            calls.append((system,user,image_mime,image_bytes))
            return response
    inbox = update_image_source(MaterialInbox(),'physics.png','image/png',image,'',NOW,
        plan_fingerprint(session.store,'beiyangyuan'),'beiyangyuan','这部分刚学',
        source_note='底部模糊的小题也需要完成')
    st.session_state[MATERIAL_INBOX_KEY] = inbox
    assert handle_material_action(st,session,ImageAdapter(),'extract',NOW,
        image_mime='image/png',image_bytes=image)
    draft = st.session_state[MATERIAL_INBOX_KEY].draft
    assert len(calls) == 1 and calls[0][2:] == ('image/png',image)
    assert draft.source_type == 'image' and draft.original_text == '底部模糊的小题也需要完成'
    assert json.loads(calls[0][1])['user_note_for_image'] == '底部模糊的小题也需要完成'
    assert draft.items[0].minutes == 60
    assert draft.items[0].duration_source == 'ai_estimated'
    assert (draft.items[0].estimate_min_minutes,draft.items[0].estimate_max_minutes) == (45,70)
    # Persistent structured draft contains source metadata, never raw bytes/Base64.
    from src.local_persistence import LocalProfileStore
    store = LocalProfileStore(tmp_path/'profile.sqlite3','image-user')
    store.save(0,material_inbox=st.session_state[MATERIAL_INBOX_KEY])
    restored = store.load().material_inbox.draft
    assert restored.source_type == 'image' and not hasattr(restored,'image_bytes')
    assert restored.items[0].title == '大学物理热学习题'


def test_changing_image_invalidates_previous_structured_result_without_model_call():
    st,session,_,_ = live()
    first = b'\x89PNG\r\n\x1a\nfirst'
    second = b'\x89PNG\r\n\x1a\nsecond'
    inbox = update_image_source(MaterialInbox(),'one.png','image/png',first,'',NOW,
        plan_fingerprint(session.store,'beiyangyuan'),'beiyangyuan')
    inbox = replace(inbox,draft=replace(inbox.draft,status='ready',items=()))
    changed = update_image_source(inbox,'two.png','image/png',second,'',NOW,
        plan_fingerprint(session.store,'beiyangyuan'),'beiyangyuan')
    assert changed.draft.status == 'unprocessed'
    assert changed.draft.source_fingerprint != inbox.draft.source_fingerprint
