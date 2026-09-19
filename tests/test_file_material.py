import io
import json
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace

import pytest
from PIL import Image

from tests.document_fixtures import docx_bytes, pdf_bytes, TASK_TEXT, TASK_TWO
from src.file_material import (read_file_material, parse_page_range, FileMaterialError,
    DOCX_MIME, PDF_MIME, MAX_RENDER_EDGE, MAX_DOCX_BYTES, MAX_TEXT_CHARS)
from src.material_inbox import (MaterialInbox, update_file_source, extract_material,
    MaterialError, edited, apply_notice_date, resolved_time, MATERIAL_INBOX_KEY)
from src.material_ui import handle_material_action, save_material_edit
from src.material_planning import plan_fingerprint
from src.p2_live_main import make_live_session
from src.p2_session import load_live_final_turn
from src.p2_tju_live_adapter import TJUP2CallAdapter
from src.p3_campus_registry import DEFAULT_CAMPUS_REGISTRY
from src.local_persistence import _encode_value, _decode_value
from scripts.low_input_model import LowInputModel
from scripts.material_demo_model import row, timing

NOW = datetime(2026, 9, 7, 14)


def source(kind='docx', **kwargs):
    if kind == 'docx':
        return read_file_material('tasks.docx', DOCX_MIME, docx_bytes(**kwargs))
    return read_file_material('tasks.pdf', PDF_MIME, pdf_bytes(kwargs.pop('pages', (TASK_TEXT,TASK_TWO))), **kwargs)


def inbox_for(material, **kwargs):
    return update_file_source(MaterialInbox(),material,'',NOW,'plan','beiyangyuan',**kwargs)


def response(relative=False):
    item = row('实验报告', 'Experiment one', scope='完成步骤1–3并写报告', completion='提交报告')
    item['estimate'] = dict(min_focus_minutes=60,max_focus_minutes=90,recommended_minutes=75,
        basis='实验步骤和报告要求', assumptions=['仅完成指定步骤'], clarification_question=None)
    if relative:
        item['deadline'] = timing('next Friday',clock='22:00',week=1,weekday=5)
    return json.dumps(dict(schema_version='campusflow.material-text.v2',reference_date=None,
        reference_evidence=None,items=[item]),ensure_ascii=False)


def test_docx_order_lists_tables_and_complex_notice():
    material = source(complex_content=True)
    assert material.text.index('# Course tasks') < material.text.index(TASK_TEXT)
    assert '• Check calculations' in material.text and 'Task | Deadline' in material.text
    assert len(material.warnings) == 1
    assert TASK_TEXT not in repr(material)


@pytest.mark.parametrize('data,mime,name', [
    (b'bad',DOCX_MIME,'x.docx'), (b'PK\x03\x04broken',DOCX_MIME,'x.docx'),
    (docx_bytes(),PDF_MIME,'x.docx'), (docx_bytes(''),DOCX_MIME,'x.docx'),
    (docx_bytes('',True),DOCX_MIME,'x.docx'), (b'old',DOCX_MIME,'x.doc'),
    (b'bad',PDF_MIME,'x.pdf'), (b'%PDF-broken',PDF_MIME,'x.pdf'),
    (pdf_bytes(('',)),PDF_MIME,'x.pdf'), (pdf_bytes(tuple('' for _ in range(21))),PDF_MIME,'x.pdf'),
    (b'x' * (MAX_DOCX_BYTES+1),DOCX_MIME,'x.docx'),
    (docx_bytes(extra={'../escape':'bad'}),DOCX_MIME,'x.docx'),
    (docx_bytes(extra={'word/bomb':'0' * 100000}),DOCX_MIME,'x.docx'),
], ids=lambda value: 'fixture')
def test_invalid_or_excessive_containers_fail_safely(data,mime,name):
    with pytest.raises(FileMaterialError) as error:
        read_file_material(name,mime,data)
    assert 'Traceback' not in str(error.value)


@pytest.mark.parametrize('pages,kind,vision', [
    ((TASK_TEXT,TASK_TWO),'pdf_text',()),
    ((None,None),'pdf_vision',(1,2)),
    ((TASK_TEXT,None),'pdf_vision',(2,)),
    (('1',),'pdf_vision',(1,)),
])
def test_pdf_per_page_classification_render_and_order(pages,kind,vision):
    material = source('pdf',pages=pages,render=True)
    assert material.source_type == kind and material.vision_pages == vision
    assert len(material.images) == len(vision)
    if TASK_TEXT in pages:
        assert '[第1页]' in material.text and 'Experiment one' in material.text
    for mime, data in material.images:
        image = Image.open(io.BytesIO(data))
        assert mime == 'image/png' and max(image.size) <= MAX_RENDER_EDGE
        assert len(data) > 100


@pytest.mark.parametrize('value', ['0','3','2-1','1,1','1-2,2','1;2','1-2,','-1','1-999','1.5'])
def test_page_range_rejects_invalid(value):
    with pytest.raises(FileMaterialError):
        parse_page_range(value,2)


def test_limits_require_selection_not_silent_truncation():
    with pytest.raises(FileMaterialError,match='最多5'):
        source('pdf',pages=(None,)*6)
    selected = source('pdf',pages=(None,)*6,page_range='1-3, 5',render=True)
    assert selected.selected_pages == (1,2,3,5) and len(selected.images)==4
    with pytest.raises(FileMaterialError):
        source(text=' '.join('unique{}'.format(i) for i in range(2400)))
    with pytest.raises(FileMaterialError):
        source('pdf',pages=('A task requirement ' * (MAX_TEXT_CHARS // 19 + 1),))


def test_text_document_model_once_draft_no_fulltext_and_metadata_not_reference():
    material = source(text=TASK_TEXT+' next Friday')
    inbox = inbox_for(material)
    calls = []
    def caller(system,user):
        calls.append(json.loads(user))
        assert '文件创建/修改时间绝不作为通知日期' in system
        return response(True)
    draft = extract_material(inbox.draft,caller,file_source=material)
    assert len(calls)==1 and calls[0]['material'].startswith('# Course tasks')
    assert draft.original_text=='' and draft.reference_date is None
    assert resolved_time(draft.items[0].deadline,None) is None
    encoded = json.dumps(_encode_value(MaterialInbox(draft)),ensure_ascii=False)
    assert TASK_TEXT not in encoded and 'Check calculations' not in encoded
    assert _decode_value(json.loads(encoded)).draft == draft
    revised = apply_notice_date(MaterialInbox(draft),'2026-09-07')
    assert resolved_time(revised.draft.items[0].deadline,revised.draft.reference_date)==datetime(2026,9,18,22)
    assert update_file_source(revised,material,'2026-09-07',NOW,'plan','beiyangyuan') is revised


def test_pdf_multiple_images_use_one_formal_client_request_and_no_text_loss():
    material = source('pdf',pages=(TASK_TEXT,None,None),render=True)
    requests = []
    adapter = TJUP2CallAdapter(message_call_function=lambda messages,**kw: requests.append((messages,kw)) or response())
    draft = extract_material(inbox_for(material).draft,adapter.agent_caller,file_source=material,
        image_supported=True,images_caller=adapter.material_images_caller)
    assert draft.items[0].minutes == 75 and len(requests)==1
    content = requests[0][0][1]['content']
    assert len(content)==3 and content[1]['image_url']['url'].startswith('data:image/png;base64,')
    prompt = json.loads(content[0]['text'])
    assert 'Experiment one' in prompt['material'] and prompt['attached_image_pages_in_order']==[2,3]
    assert requests[0][1]['timeout']==120
    encoded=json.dumps(_encode_value(MaterialInbox(draft)))
    assert 'base64' not in encoded and TASK_TEXT not in encoded


def test_changed_file_or_pages_invalidates_and_missing_file_cannot_reextract():
    material = source('pdf',pages=(TASK_TEXT,TASK_TWO))
    inbox = inbox_for(material)
    ready = replace(inbox,draft=extract_material(inbox.draft,lambda *a:response(),file_source=material))
    changed = source('pdf',pages=(TASK_TEXT,TASK_TWO),page_range='1')
    stale = update_file_source(ready,changed,'',NOW,'plan','beiyangyuan')
    assert stale.draft.status=='stale' and not stale.draft.items
    with pytest.raises(MaterialError):
        extract_material(ready.draft,lambda *a:pytest.fail('no call'),file_source=changed)
    with pytest.raises(MaterialError):
        extract_material(ready.draft,lambda *a:pytest.fail('no call'))


def test_live_file_extract_edit_confirm_uses_existing_atomic_path():
    store={}
    model=LowInputModel('b')
    model.agent_caller_original=model.agent_caller
    model.agent_caller=lambda system,user: response() if 'campusflow.material-text.v2' in system else model.agent_caller_original(system,user)
    session=make_live_session(store,model,map_data=DEFAULT_CAMPUS_REGISTRY.get_campus_map('beiyangyuan'),
        campus_id='beiyangyuan',companion_enabled=True,agent_intelligence_enabled=True)
    messages=[]
    st=SimpleNamespace(session_state=store,warning=messages.append,error=messages.append,write=messages.append)
    material=source()
    inbox=update_file_source(MaterialInbox(),material,'',NOW,plan_fingerprint(store,'beiyangyuan'),'beiyangyuan')
    assert save_material_edit(st,inbox,NOW)
    assert handle_material_action(st,session,model,'extract',NOW,file_source=material),messages
    assert load_live_final_turn(store) is None
    draft=store[MATERIAL_INBOX_KEY].draft
    updated=replace(draft,items=(edited(draft.items[0],minutes='80'),))
    assert save_material_edit(st,replace(store[MATERIAL_INBOX_KEY],draft=updated),NOW)
    assert handle_material_action(st,session,model,'confirm',NOW),messages
    bundle=load_live_final_turn(store)
    assert bundle.state.tasks[0].total_minutes==80 and bundle.state.tasks[0].completed_minutes==0
    assert not handle_material_action(st,session,model,'confirm',NOW)
    assert load_live_final_turn(store) is bundle


def test_file_draft_profiles_and_failed_save_keep_isolation(tmp_path):
    from src.local_persistence import LocalProfileStore,LocalPersistenceConflict
    path=tmp_path/'profiles.sqlite3'
    a,b=LocalProfileStore(path,'file-user-a'),LocalProfileStore(path,'file-user-b')
    material=source()
    draft=extract_material(inbox_for(material).draft,lambda *a:response(),file_source=material)
    inbox=MaterialInbox(draft)
    revision=a.save(0,material_inbox=inbox)
    assert b.load().material_inbox is None and a.load().material_inbox==inbox
    with pytest.raises(LocalPersistenceConflict):
        a.save(0,material_inbox=replace(inbox,draft=replace(draft,status='discarded')))
    assert a.load().revision==revision and a.load().material_inbox==inbox
    # Physical SQLite contains only the structured result, never extracted prose.
    import sqlite3
    with sqlite3.connect(str(path)) as db:
        payload=db.execute('SELECT payload_json FROM material_snapshot').fetchone()[0]
    assert TASK_TEXT not in payload and 'base64' not in payload


def test_file_multi_items_repair_bounded_and_atomic_failure():
    material=source('pdf')
    payload=json.loads(response())
    payload['items'].append(dict(payload['items'][0],title='实验二报告',evidence='Experiment two'))
    raw=json.dumps(payload)
    calls=[]
    def caller(*args):
        calls.append(1)
        return 'not json' if len(calls)==1 else raw
    draft=extract_material(inbox_for(material).draft,caller,file_source=material)
    assert len(draft.items)==2 and len(calls)==2 and draft.original_text==''
    st,session,model = _file_live_state()
    draft=replace(draft,plan_fingerprint=plan_fingerprint(session.store,'beiyangyuan'))
    assert save_material_edit(st,MaterialInbox(draft),NOW)
    invalid=replace(draft,items=(draft.items[0],edited(draft.items[1],minutes='-1')))
    assert save_material_edit(st,MaterialInbox(invalid),NOW)
    assert not handle_material_action(st,session,model,'confirm',NOW)
    assert load_live_final_turn(st.session_state) is None
    assert save_material_edit(st,MaterialInbox(replace(draft,
        items=(draft.items[0],edited(draft.items[1],selected=False)))),NOW)
    assert handle_material_action(st,session,model,'confirm',NOW)
    assert len(load_live_final_turn(st.session_state).state.tasks)==1


def _file_live_state():
    model=LowInputModel('b')
    store={}
    session=make_live_session(store,model,map_data=DEFAULT_CAMPUS_REGISTRY.get_campus_map('beiyangyuan'),
        campus_id='beiyangyuan',companion_enabled=True,agent_intelligence_enabled=True)
    messages=[]
    return SimpleNamespace(session_state=store,warning=messages.append,error=messages.append,write=messages.append),session,model


def test_docx_external_entities_never_resolved():
    import zipfile
    original=docx_bytes()
    output=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original)) as src, zipfile.ZipFile(output,'w') as dst:
        for name in src.namelist():
            data=src.read(name)
            if name=='word/document.xml':
                data=b'<!DOCTYPE document [<!ENTITY secret SYSTEM "file:///private">]>' + data.replace(b'Course tasks',b'&secret;')
            dst.writestr(name,data)
    with pytest.raises(FileMaterialError):
        read_file_material('x.docx',DOCX_MIME,output.getvalue())
