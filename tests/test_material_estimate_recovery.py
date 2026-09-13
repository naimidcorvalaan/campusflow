"""Real extraction/confirmation seams, temporary stores, no live requests."""
import json
from dataclasses import replace
from pathlib import Path
import pytest
from scripts.material_demo_model import row, timing
from tests.document_fixtures import docx_bytes, pdf_bytes, TASK_TEXT
from tests.test_material_inbox import live, NOW
from src.material_inbox import (MaterialInbox, MATERIAL_INBOX_KEY, update_source,
    update_file_source, update_image_source, extract_material, MaterialError)
from src.file_material import read_file_material, DOCX_MIME, PDF_MIME
from src.material_estimate_recovery import confirm_minimal_task, validate_estimate
from src.material_ui import handle_material_action, save_material_edit
from src.material_planning import plan_fingerprint
from src.p2_session import load_live_final_turn

FORM='人文学术讲座学分申请表：填写姓名、学号、学院、讲座名称、日期及学分申请；核对证明材料。'


def payload(broken=True, invalid=False, **changes):
    item=row('填写人文学术讲座学分申请表','人文学术讲座学分申请表',
        scope='填写申请信息、核对讲座记录与证明材料',completion='核对后提交',
        estimate=dict(min_focus_minutes=15,max_focus_minutes=30,recommended_minutes=20,
            basis='填写字段与检查材料需要专注时间',assumptions=['证明材料已备齐'],clarification_question=None))
    if broken:
        del item['completion']
    if invalid:
        item['estimate']['recommended_minutes']=300
    item.update(changes)
    return dict(schema_version='campusflow.material-text.v2',reference_date=None,reference_evidence=None,items=[item])


def draft_for(kind):
    kw={}
    if kind=='text':
        inbox=update_source(MaterialInbox(),FORM,'',NOW,'plan','beiyangyuan')
    elif kind=='image':
        import io
        from PIL import Image
        data=io.BytesIO(); Image.new('RGB',(32,32),'white').save(data,format='PNG')
        inbox=update_image_source(MaterialInbox(),'task.png','image/png',data.getvalue(),'',NOW,'plan','beiyangyuan')
        kw=dict(image_mime='image/png',image_bytes=data.getvalue(),image_supported=True)
    else:
        data=docx_bytes(FORM) if kind=='docx' else pdf_bytes((TASK_TEXT,) if kind=='pdf_text' else (None,))
        src=read_file_material('task.docx' if kind=='docx' else 'task.pdf',DOCX_MIME if kind=='docx' else PDF_MIME,data,render=True)
        inbox=update_file_source(MaterialInbox(),src,'',NOW,'plan','beiyangyuan')
        kw=dict(file_source=src,image_supported=True)
    return inbox.draft,kw


def action_payload(items=None):
    obj=payload(False)
    item=obj['items'][0]
    obj['actionability']=dict(is_estimatable=True,task_name=item['title'],short_scope=item['scope'],
        reason='有填写字段及证明材料核对步骤',evidence=item['evidence'],confirmation_required=[],
        waiting_note='等待老师签字和审批，耗时取决于对方安排')
    obj['estimate']=item['estimate']
    obj['items']=[] if items is None else items
    return obj


def quality_payload():
    obj=action_payload()
    obj['actionability'].update(task_name='填写德、智、体、美、劳各项分数',
        short_scope='将已确定的五项分数填入表中，并检查是否漏填。',
        evidence='填写德、智、体、美、劳各项分数',reason='已读到五项评分栏',
        confirmation_required=['multiple_actions'],waiting_note='')
    obj['estimate'].update(min_focus_minutes=3,max_focus_minutes=5,recommended_minutes=4,
        basis='填写五项已有分数，再逐项核对是否漏填。',
        assumptions=['各项分数已经确定，无需另行计算'])
    obj['coverage']=dict(level='partial',reason='只识别到评分栏',uncovered_content='其他评价内容尚未明确')
    obj['items']=[{'kind':'task','title':'其他评价内容'}, {'kind':'task','title':'其他要求'}]
    return obj


@pytest.mark.parametrize('supplement',['','填表'])
def test_readable_form_empty_formal_response_is_completed_with_submitted_intent(supplement):
    source=read_file_material('本科生综合素质测评考核表【主观评价部分】.docx',DOCX_MIME,
        docx_bytes('本科生综合素质测评考核表：填写德、智、体、美、劳各项分数。',complex_content=True))
    draft=update_file_source(MaterialInbox(),source,'',NOW,'plan','beiyangyuan',supplement).draft
    requests=[]
    def caller(system,user):
        request=json.loads(user)
        requests.append(request)
        assert request['supplemental_context']==(supplement or None)
        if request.get('schema_version')=='campusflow.workload-estimate.v1':
            from tests.test_workload_engine import estimate_response
            assert request['workloads'] and 'items' not in request['output']
            return estimate_response(request,4)
        assert '填写德、智、体、美、劳各项分数' in request['material']
        if request.get('request_stage')=='estimate_completion':
            assert request['output']['items']==[]  # formal structure cannot block time
            return json.dumps(quality_payload(),ensure_ascii=False)
        return json.dumps(dict(schema_version='campusflow.material-text.v2',reference_date=None,
            reference_evidence=None,items=[]))
    result=extract_material(draft,caller,file_source=source)
    assert len(requests)==result.model_calls==2
    assert result.estimate_fallbacks[0]['estimate']['recommended_minutes']==4
    assert result.estimate_coverage=='partial' and not result.items
    assert result.supplemental_context==supplement and '你准备怎么处理' not in result.message
    assert result.diagnostics['request_outcome']=='result'


def test_only_evidenced_reference_material_may_repeat_intent_question():
    draft=update_source(MaterialInbox(),'学校简介：介绍校园历史。','',NOW,'plan','beiyangyuan').draft
    obj=dict(schema_version='campusflow.material-text.v2',reference_date=None,reference_evidence=None,
        items=[],actionability=dict(is_estimatable=False,reason='只有背景介绍',evidence='学校简介'))
    result=extract_material(draft,lambda *a:json.dumps(obj))
    assert result.diagnostics['request_outcome']=='needs_input' and '你准备怎么处理' in result.message
    draft=update_source(MaterialInbox(),'学校简介：介绍校园历史。','',NOW,'plan','beiyangyuan','阅读').draft
    result=extract_material(draft,lambda *a:json.dumps(obj))
    assert result.diagnostics['request_outcome']=='result' and result.status=='ready'
    assert '你准备怎么处理' not in result.message and result.estimate_fallbacks


@pytest.mark.parametrize('independent',[False,True])
@pytest.mark.parametrize('supplement',['','填表'])
def test_evidenced_partial_minutes_survive_a_question_about_remaining_work(independent,supplement):
    source=read_file_material('综合素质测评表.docx',DOCX_MIME,docx_bytes(FORM))
    draft=update_file_source(MaterialInbox(),source,'',NOW,'plan','beiyangyuan',supplement).draft
    obj=action_payload() if independent else payload()
    estimate=obj['estimate'] if independent else obj['items'][0]['estimate']
    estimate['clarification_question']='其他评价内容需要写多少字？'
    obj['coverage']=dict(level='whole',reason='可见字段已识别',uncovered_content='')
    result=extract_material(draft,lambda *a:json.dumps(obj),file_source=source)
    assert result.estimate_fallbacks[0]['estimate']['recommended_minutes']==20
    assert result.estimate_coverage=='partial'
    assert not result.estimate_fallbacks[0]['simple_confirmation_allowed']
    assert result.diagnostics['request_outcome']=='result'
    assert '你准备怎么处理' not in result.message
    assert not result.items


def test_initial_request_makes_formal_work_optional_for_every_source():
    for kind in ('text','image','docx','pdf_text','pdf_vision'):
        draft,kw=draft_for(kind)
        obj=action_payload()
        if kind=='pdf_text':obj['actionability']['evidence']='Experiment one'
        requests=[]
        def caller(system,user,*images):
            request=json.loads(user);requests.append(request)
            assert request['output']['items']==[]
            assert request['formal_item_format']['kind']=='task|fixed_commitment'
            assert '填写' in request['entry_intent']
            return json.dumps(obj)
        result=extract_material(draft,caller,image_caller=caller,images_caller=caller,**kw)
        assert len(requests)==1 and result.estimate_fallbacks and not result.items


def test_semantic_completion_enriches_first_valid_task_without_discarding_its_facts():
    draft,_=draft_for('text')
    first=payload(False);first['items'][0].pop('estimate')
    from tests.test_workload_engine import estimate_response
    result=extract_material(draft,lambda *args:json.dumps(first),
        workload_caller=lambda system,user:estimate_response(json.loads(user),20))
    assert result.model_calls==2 and len(result.items)==1
    assert result.items[0].minutes is None and result.items[0].evidence==first['items'][0]['evidence']
    assert result.estimate_fallbacks[0]['estimate']['recommended_minutes']==20
    assert not result.estimate_fallbacks[0]['simple_confirmation_allowed']
    assert result.diagnostics['request_outcome']=='result'


@pytest.mark.parametrize('structured',[False,True])
def test_coverage_is_independent_of_formal_structure(structured):
    obj=payload(not structured)
    obj['coverage']=dict(level='whole',reason='任务范围和全部步骤均已明确',uncovered_content='')
    whole,_=run(obj=obj)
    assert whole.estimate_coverage=='whole' and not whole.coverage_note
    obj['coverage']['level']='partial'
    partial,_=run(obj=obj)
    assert partial.estimate_coverage=='partial' and '可能需要更久' in partial.coverage_note
    assert bool(partial.items or partial.estimate_fallbacks)


def test_unread_content_overrules_model_whole_without_losing_time(tmp_path):
    from src.local_persistence import LocalProfileStore
    text='填写德、智、体、美、劳各项分数'
    source=read_file_material('任意名称.docx',DOCX_MIME,docx_bytes(text,complex_content=True))
    draft=update_file_source(MaterialInbox(),source,'',NOW,'plan','beiyangyuan').draft
    obj=quality_payload();obj['items']=[]
    obj['coverage']=dict(level='whole',reason='模型声称全部读完',uncovered_content='')
    result=extract_material(draft,lambda *a:json.dumps(obj),file_source=source)
    assert result.source_incomplete and result.estimate_coverage=='partial'
    assert result.estimate_fallbacks[0]['estimate']['recommended_minutes']==4
    store=LocalProfileStore(tmp_path/'coverage.sqlite3');store.save(0,material_inbox=MaterialInbox(result))
    assert store.load().material_inbox.draft.estimate_coverage=='partial'


def test_unresolved_work_and_missing_metadata_cannot_claim_whole():
    obj=action_payload([{'kind':'task'},{'kind':'task'},{'kind':'task'}])
    obj['coverage']=dict(level='whole',reason='模型认为完整',uncovered_content='')
    result,_=run(obj=obj)
    assert result.diagnostics['unresolved_count']==2 and result.estimate_coverage=='partial'
    result,_=run(obj=payload(False))
    assert result.items and result.estimate_coverage=='partial'


def test_partial_coverage_survives_repair_reestimate_and_user_confirmation():
    from src.material_estimate_recovery import reestimate_only
    from scripts.offline_product_model import OfflineProductModel
    draft,_=draft_for('text')
    first=payload();first['coverage']=dict(level='partial',reason='只明确部分工作',uncovered_content='还有其他要求')
    repaired=payload();repaired['coverage']=dict(level='whole',reason='格式修复自称完整',uncovered_content='')
    replies=iter([first,repaired])
    result=extract_material(draft,lambda *a:json.dumps(next(replies)))
    assert result.estimate_coverage=='partial'
    entry=result.estimate_fallbacks[0]
    updated=reestimate_only(result,entry['item_id'],OfflineProductModel().agent_caller,'分数已算好')
    assert updated.estimate_coverage=='partial'
    assert confirm_minimal_task(updated,entry['item_id'],'填写分数',4,True).estimate_coverage=='partial'


def test_failed_supplement_reestimate_preserves_visible_result():
    st,session,model,_=live()
    result,_=run()
    save_material_edit(st,MaterialInbox(result),NOW)
    changed=update_source(MaterialInbox(result),FORM,'',NOW,'plan','beiyangyuan','还有自我评价').draft
    model.failure_mode='network'
    assert not handle_material_action(st,session,model,'extract',NOW,source_draft=changed)
    assert st.session_state[MATERIAL_INBOX_KEY].draft==result


@pytest.mark.parametrize('kind',['text','image','docx','pdf_text','pdf_vision'])
def test_complete_coverage_uses_the_same_boundary_for_every_source(kind):
    obj=payload(False)
    obj['coverage']=dict(level='whole',reason='已识别所需工作及全部步骤',uncovered_content='')
    result,calls=run(kind,obj)
    assert len(calls)==1 and result.estimate_coverage=='whole'


def test_unselected_pdf_pages_prevent_whole_material_claim():
    source=read_file_material('test.pdf',PDF_MIME,pdf_bytes(),page_range='1')
    draft=update_file_source(MaterialInbox(),source,'',NOW,'plan','beiyangyuan').draft
    obj=payload(False);obj['items'][0]['evidence']='Experiment one'
    obj['coverage']=dict(level='whole',reason='模型认为全部完成',uncovered_content='')
    result=extract_material(draft,lambda *a:json.dumps(obj),file_source=source)
    assert result.items[0].minutes==20 and result.estimate_coverage=='partial' and result.source_incomplete


def test_empty_supplement_response_cannot_erase_existing_estimate():
    st,session,model,_=live()
    result,_=run();save_material_edit(st,MaterialInbox(result),NOW)
    model.agent_caller=lambda *args:json.dumps(dict(schema_version='campusflow.material-text.v2',
        items=[],reference_date=None,reference_evidence=None))
    assert not handle_material_action(st,session,model,'extract',NOW,source_draft=result)
    assert st.session_state[MATERIAL_INBOX_KEY].draft==result


@pytest.mark.parametrize('kind',['text','image','docx','pdf_text','pdf_vision'])
def test_action_without_formal_items_yields_all_estimate_fields(kind):
    draft,kw=draft_for(kind)
    obj=action_payload()
    if kind=='pdf_text':
        obj['actionability']['evidence']='Experiment one'
    calls=[]
    def caller(system,user,*images):
        calls.append((system,user))
        return json.dumps(obj,ensure_ascii=False)
    result=extract_material(draft,caller,image_caller=caller,images_caller=caller,**kw)
    assert len(calls)==1 and result.model_calls==1
    assert not result.items and result.diagnostics['result_level']=='estimate_fallback'
    entry=result.estimate_fallbacks[0]
    assert set(entry['estimate'])=={'task_name','short_scope','focused_minutes_min',
        'focused_minutes_max','recommended_minutes','rationale','assumptions'}
    assert entry['waiting_note']==obj['actionability']['waiting_note']
    assert '填写' in calls[0][0] and '专注时间不含等待' in calls[0][0]


@pytest.mark.parametrize('invalid_envelope',[{'items':None},{'items':'bad'},
    {'reference_date':'yesterday','reference_evidence':'missing'},{'unexpected':'field'}])
def test_action_survives_invalid_formal_envelope_without_promotion(invalid_envelope):
    obj=action_payload();obj.update(invalid_envelope)
    result,calls=run(obj=obj)
    assert len(calls)==2 and not result.items
    assert result.estimate_fallbacks and not result.estimate_fallbacks[0]['simple_confirmation_allowed']


@pytest.mark.parametrize('minutes',[7,43,95])
def test_action_estimate_uses_workload_response_not_a_form_default(minutes):
    obj=action_payload()
    obj['estimate'].update(min_focus_minutes=minutes-2,max_focus_minutes=minutes+8,recommended_minutes=minutes)
    result,_=run(obj=obj)
    assert result.estimate_fallbacks[0]['estimate']['recommended_minutes']==minutes


def test_evidence_free_action_cannot_yield_time():
    obj=action_payload();obj['actionability']['evidence']='不存在的字段'
    result,_=run(obj=obj)
    assert not result.items and result.estimate_fallbacks[0]['origin']=='local_workload'
    assert '不存在的字段' not in result.estimate_fallbacks[0]['estimate']['rationale']


def test_estimatable_action_with_invalid_minutes_gets_only_one_repair():
    obj=action_payload();obj['estimate']['recommended_minutes']=True
    result,calls=run(obj=obj)
    assert len(calls)==2 and result.estimate_fallbacks[0]['origin']=='local_workload' and not result.items


def test_repaired_formal_task_keeps_first_action_estimate_and_waiting():
    draft,_=draft_for('text')
    first=action_payload();first['items']=None
    repaired=payload(False);repaired['items'][0].pop('estimate')
    replies=iter([first,repaired])
    result=extract_material(draft,lambda *a:json.dumps(next(replies)))
    assert result.model_calls==2 and len(result.items)==1 and not result.estimate_fallbacks
    assert result.items[0].minutes==20 and result.items[0].estimate_waiting_note==first['actionability']['waiting_note']


def test_action_consent_preserves_waiting_and_still_only_prepares_draft():
    result,_=run(obj=action_payload())
    entry=result.estimate_fallbacks[0]
    revised=confirm_minimal_task(result,entry['item_id'],'填写申请表',25,True)
    assert revised.status=='ready' and revised.items[0].estimate_waiting_note==entry['waiting_note']
    assert revised.items[0].duration_source=='user_confirmed'


def test_independent_action_is_not_lost_beside_a_different_valid_item():
    other=payload(False)['items'][0]
    other.update(title='核对证明材料',scope='检查证明是否齐全')
    obj=action_payload([other])
    result,calls=run(obj=obj)
    assert len(calls)==1 and len(result.items)==len(result.estimate_fallbacks)==1
    assert result.items[0].title=='核对证明材料'
    assert not result.estimate_fallbacks[0]['simple_confirmation_allowed']


def test_matching_action_and_full_item_do_not_duplicate_task():
    obj=action_payload(payload(False)['items'])
    result,calls=run(obj=obj)
    assert len(calls)==1 and len(result.items)==1 and not result.estimate_fallbacks
    assert result.items[0].estimate_waiting_note==obj['actionability']['waiting_note']


def run(kind='text',obj=None):
    draft,kw=draft_for(kind)
    obj=payload() if obj is None else obj
    if kind=='pdf_text':
        obj['items'][0]['evidence']='Experiment one'
    calls=[]
    def caller(*args):
        calls.append(1);return json.dumps(obj,ensure_ascii=False)
    result=extract_material(draft,caller,image_caller=caller,images_caller=caller,**kw)
    return result,calls


@pytest.mark.parametrize('kind',['text','image','docx','pdf_text','pdf_vision'])
def test_shared_recovery_uses_existing_responses_only(kind):
    result,calls=run(kind)
    assert calls==[1,1] and result.items==()
    assert result.diagnostics['result_level']=='estimate_fallback'
    assert result.estimate_fallbacks[0]['estimate']['recommended_minutes']==20
    assert 'estimate' not in result.diagnostics and result.original_text in ('',FORM)


def test_full_and_exam_remain_full_and_one_call():
    result,calls=run('docx',payload(False))
    assert len(calls)==1 and result.items[0].minutes==20 and not result.estimate_fallbacks
    obj=payload(False)
    obj['items'][0]['estimate'].update(min_focus_minutes=120,max_focus_minutes=180,recommended_minutes=150)
    result,calls=run('pdf_text',obj)
    assert len(calls)==1 and result.items[0].minutes==150
    assert result.diagnostics['result_level']=='full_structured'


@pytest.mark.parametrize('change',[{'recommended_minutes':300},{'recommended_minutes':True},
    {'min_focus_minutes':0},{'max_focus_minutes':100000},{'basis':''}])
def test_invalid_estimates_never_invent_time(change):
    obj=payload();obj['items'][0]['estimate'].update(change)
    result,calls=run(obj=obj)
    assert not result.items and len(calls)==2
    assert result.estimate_fallbacks[0]['origin']=='local_workload'
    assert result.diagnostics['result_level']=='workload_estimate'


def test_no_task_intent_or_free_text_does_not_yield_minutes():
    obj=payload();obj['items']=[]
    result,calls=run('docx',obj)
    assert not result.items and result.estimate_fallbacks[0]['origin']=='local_workload' and len(calls)==2
    # Empty model output cannot erase a form. A real reference source still
    # cannot borrow time-like numbers from an unstructured model response.
    draft=update_source(MaterialInbox(),'学校简介：校园历史。','',NOW,'plan','beiyangyuan').draft
    result=extract_material(draft,lambda *a:'有15题，明天20:00交')
    assert result.diagnostics['result_level']=='estimate_unavailable'
    assert not result.estimate_fallbacks


@pytest.mark.parametrize('change',[
    {'deadline':timing('下周五',week=1,weekday=5)}, {'location_text':'办公室'},
    {'possible_task_ref':'existing'}, {'uncertainties':[{'field':'identity'}]},
    {'campus_id':'weijinlu'}, {'start':timing('下午')}, {'hidden_deadline':'Friday'}])
def test_constraint_ambiguities_cannot_promote_estimate(change):
    result,_=run(obj=payload(**change))
    entry=result.estimate_fallbacks[0]
    assert not entry['simple_confirmation_allowed']
    with pytest.raises(MaterialError):
        confirm_minimal_task(result,entry['item_id'],'填写申请表',20,True)


def test_user_consent_then_existing_atomic_confirmation_and_stable_receipt():
    st,session,model,_=live()
    result,_=run()
    result=replace(result,plan_fingerprint=plan_fingerprint(session.store,'beiyangyuan'))
    assert save_material_edit(st,MaterialInbox(result),NOW)
    assert not handle_material_action(st,session,model,'confirm',NOW)
    assert load_live_final_turn(st.session_state) is None and not model.calls
    entry=result.estimate_fallbacks[0]
    assert not handle_material_action(st,session,model,'prepare_estimate',NOW,item_id=entry['item_id'],
        confirmed_title='填写申请表',confirmed_minutes=25)
    assert handle_material_action(st,session,model,'prepare_estimate',NOW,item_id=entry['item_id'],
        confirmed_title='填写申请表',confirmed_minutes=25,simple_confirmed=True)
    assert load_live_final_turn(st.session_state) is None and not model.calls
    assert handle_material_action(st,session,model,'confirm',NOW)
    bundle=load_live_final_turn(st.session_state)
    assert bundle.state.tasks[0].total_minutes==25
    task_ref=bundle.state.tasks[0].task_ref
    calls=len(model.calls)
    assert not handle_material_action(st,session,model,'confirm',NOW)
    assert load_live_final_turn(st.session_state).state.tasks[0].task_ref==task_ref
    assert len(model.calls)==calls


def test_partial_batch_keeps_valid_and_estimate_separate():
    obj=payload(False);obj['items'] += payload()['items']+payload(invalid=True)['items']
    result,calls=run(obj=obj)
    assert len(result.items)==len(result.estimate_fallbacks)==1
    assert len(calls)==2 and '1项' in result.message
    assert not result.estimate_fallbacks[0]['simple_confirmation_allowed']


def test_first_estimate_survives_failed_repair_and_old_profile_roundtrip(tmp_path):
    from src.local_persistence import LocalProfileStore,_encode_value,_decode_value
    draft,_=draft_for('docx');kw=draft_for('docx')[1]
    calls=[]
    def caller(*args):
        calls.append(1)
        if len(calls)>1:raise RuntimeError('private server details')
        return json.dumps(payload())
    result=extract_material(draft,caller,**kw)
    assert result.diagnostics['safe_error_category']=='repair_request_failed'
    a=LocalProfileStore(tmp_path/'profiles.sqlite3','user-a');b=LocalProfileStore(tmp_path/'profiles.sqlite3','user-b')
    inbox=MaterialInbox(result);a.save(0,material_inbox=inbox)
    assert a.load().material_inbox==inbox and b.load().material_inbox is None
    encoded=json.dumps(_encode_value(inbox),ensure_ascii=False)
    assert FORM not in encoded and 'base64' not in encoded and 'private server details' not in encoded
    assert _decode_value(json.loads(encoded))==inbox
    stale=update_source(inbox,'新的任务','',NOW,'plan','beiyangyuan')
    assert not stale.draft.estimate_fallbacks


def test_status_copy_is_exact_and_settings_spinner_stays_local():
    root=Path(__file__).resolve().parents[1]
    for name in ('material_ui.py','p2_live_main.py'):
        text=(root/'src'/name).read_text(encoding='utf-8')
        assert 'spinner(\'thinking\')' not in text and 'spinner("thinking")' not in text
        assert 'THINKING.......' in text
    bridge=(root/'src/campusflow_ui.py').read_text(encoding='utf-8')
    assert 'stStatusWidget' in bridge and "node.nodeValue = 'THINKING'" in bridge


def test_explicit_reestimate_uses_existing_estimator_and_never_removes_constraints():
    from src.material_estimate_recovery import reestimate_only
    from scripts.offline_product_model import OfflineProductModel
    result,_=run(obj=payload(location_text='办公室'))
    model=OfflineProductModel(); prompts=[]
    def caller(system,user):
        prompts.append(user)
        return model.agent_caller(system,user)
    updated=reestimate_only(result,result.estimate_fallbacks[0]['item_id'],caller,
        '材料已经备齐','平时检查比较慢')
    assert model.calls==['estimate']
    assert '材料已经备齐' in prompts[0] and '平时检查比较慢' in prompts[0]
    assert updated.estimate_fallbacks[0]['estimate']['recommended_minutes']==35
    assert not updated.estimate_fallbacks[0]['simple_confirmation_allowed']
    assert not updated.items


def test_failed_minimal_confirmation_does_not_replace_reliable_state(monkeypatch,tmp_path):
    from src import p2_live_main as main
    from src.local_persistence import LocalPersistenceError,LocalProfileStore
    st,session,model,_=live(store={main.LOCAL_PROFILE_STORE_KEY:LocalProfileStore(tmp_path/'profile.sqlite3'),
        main.LOCAL_PROFILE_REVISION_KEY:0})
    result,_=run()
    result=replace(result,plan_fingerprint=plan_fingerprint(session.store,'beiyangyuan'))
    assert save_material_edit(st,MaterialInbox(result),NOW)
    before=dict(st.session_state)
    def fail(*a,**kw):raise LocalPersistenceError('synthetic write failure')
    monkeypatch.setattr(main,'_persist_local_profile',fail)
    entry=result.estimate_fallbacks[0]
    assert not handle_material_action(st,session,model,'prepare_estimate',NOW,item_id=entry['item_id'],
        confirmed_title='填写申请表',confirmed_minutes=20,simple_confirmed=True)
    assert st.session_state[MATERIAL_INBOX_KEY]==before[MATERIAL_INBOX_KEY]
    assert load_live_final_turn(st.session_state) is None


def test_recovered_result_renders_native_edit_button_and_clears_on_profile_switch():
    from streamlit.testing.v1 import AppTest
    from src.p2_live_main import _clear_profile_session
    result,_=run()
    app=AppTest.from_file('src/p2_live_main.py').run(timeout=20)
    app.session_state['p2_live_reference_hour']=12
    app.session_state['p2_live_reference_minute']=30
    app.session_state[MATERIAL_INBOX_KEY]=MaterialInbox(result)
    app.session_state['cf_material_text']=FORM
    app.run(timeout=20)
    assert not app.exception
    assert not app.error
    assert any(b.label=='加入计划' for b in app.button)
    assert not any(v.key=='cf_material_supplement' for v in app.text_area)
    button=next(b for b in app.button if b.label=='加入计划')
    button.click().run(timeout=20)
    assert any(v.label=='要做什么' for v in app.text_input)
    assert any(v.label=='采用分钟' for v in app.number_input)
    assert not app.exception and not app.error
    store={'cf_material_estimate_only_x_title':'用户A的任务',MATERIAL_INBOX_KEY:MaterialInbox(result)}
    _clear_profile_session(store)
    assert 'cf_material_estimate_only_x_title' not in store and MATERIAL_INBOX_KEY not in store
