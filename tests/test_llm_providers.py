"""All provider tests terminate at a fake HTTP boundary; no real secrets."""
import json
import socket
from types import SimpleNamespace
import pytest
import requests

from src.llm_provider import call_llm, call_llm_messages, get_provider, provider_name
from src.llm_errors import LLMClientError, LLMConfigurationError, VisionUnavailable
from src.p1_tju_llm_adapter import load_project_configuration, missing_configuration
from src.p2_tju_live_adapter import TJUP2CallAdapter

KEY = 'synthetic-private-key'
BODY = 'synthetic-private-material'


@pytest.fixture
def deepseek(monkeypatch):
    monkeypatch.setenv('CAMPUSFLOW_LLM_PROVIDER', 'deepseek')
    monkeypatch.setenv('DEEPSEEK_API_KEY', KEY)
    monkeypatch.setenv('DEEPSEEK_MODEL', 'configured-text')
    monkeypatch.setenv('DEEPSEEK_VISION_MODEL', 'configured-vision')


def response(content='{}', status=200, data=None):
    return SimpleNamespace(status_code=status, json=lambda: data if data is not None else {
        'choices': [{'message': {'content': content, 'reasoning_content': BODY}}]})


def fake_http(monkeypatch, result=None, error=None):
    calls = []
    def post(url, **kw):
        calls.append((url, kw))
        if error is not None:
            raise error
        return response() if result is None else result
    monkeypatch.setattr(requests, 'post', post)
    return calls


@pytest.mark.parametrize('selected,expected', [(None,'TJUProvider'),('tju','TJUProvider'),('deepseek','DeepSeekProvider')])
def test_explicit_routing_without_environment_guess(monkeypatch, selected, expected):
    if selected is not None:
        monkeypatch.setenv('CAMPUSFLOW_LLM_PROVIDER', selected)
    assert type(get_provider()).__name__ == expected


def test_invalid_provider_is_safe_configuration_error(monkeypatch, caplog):
    monkeypatch.setenv('CAMPUSFLOW_LLM_PROVIDER', KEY)
    with pytest.raises(LLMConfigurationError) as caught:
        call_llm(BODY)
    assert KEY not in str(caught.value) + caplog.text
    assert 'tju 或 deepseek' in str(caught.value)


def test_deepseek_configuration_does_not_require_tju(deepseek):
    assert missing_configuration() == ()
    assert load_project_configuration(loader=lambda *a, **k: pytest.fail('dotenv not needed')) == ()
    assert missing_configuration({'CAMPUSFLOW_LLM_PROVIDER':'deepseek'}) == ('DEEPSEEK_API_KEY',)


def test_dotenv_can_select_provider_without_overriding_process():
    env = {}
    def loader(path, override):
        assert override is False
        env.update(CAMPUSFLOW_LLM_PROVIDER='deepseek', DEEPSEEK_API_KEY=KEY)
    assert load_project_configuration(env, loader=loader) == ()
    assert provider_name(env) == 'deepseek'


def test_streamlit_root_secrets_route_server_side(monkeypatch, tmp_path):
    from streamlit.runtime.secrets import Secrets
    for name in ('CAMPUSFLOW_LLM_PROVIDER','DEEPSEEK_API_KEY'):
        monkeypatch.setenv(name,'')
    path=tmp_path/'secrets.toml'
    path.write_text('CAMPUSFLOW_LLM_PROVIDER="deepseek"\nDEEPSEEK_API_KEY="synthetic"',encoding='utf-8')
    secrets=Secrets([str(path)])
    monkeypatch.setattr(secrets,'_maybe_install_file_watchers',lambda:None)
    assert secrets.load_if_toml_exists()
    assert load_project_configuration(loader=lambda *a,**k:pytest.fail('local dotenv')) == ()
    calls=fake_http(monkeypatch)
    assert call_llm('test')=='{}'
    assert calls[0][1]['headers']['Authorization']=='Bearer synthetic'


@pytest.mark.parametrize('content', ['answer', '{"ok":true}', '```json\n{"ok":true}\n```', 'prefix {"ok":true}'])
def test_final_text_only_and_original_budget(deepseek,monkeypatch,caplog,content):
    calls=fake_http(monkeypatch,response(content))
    assert TJUP2CallAdapter().agent_caller('system',BODY)==content
    assert len(calls)==1
    url,kw=calls[0]
    assert url=='https://api.deepseek.com/chat/completions'
    assert kw['timeout']==120 and kw['allow_redirects'] is False
    assert kw['json']==dict(model='configured-text',max_tokens=2048,temperature=0,
        thinking={'type':'disabled'},stream=False,messages=[
            {'role':'system','content':'system'},{'role':'user','content':BODY}])
    assert KEY not in caplog.text and BODY not in caplog.text


@pytest.mark.parametrize('data', [None, {}, {'choices':[]}, {'choices':[{'message':{'reasoning_content':BODY}}]},
    {'choices':[{'message':{'content':None}}]}, {'choices':[{'message':{'content':[]}}]}])
def test_invalid_or_empty_final_content(deepseek,monkeypatch,caplog,data):
    calls=fake_http(monkeypatch,response('  ',data=data))
    with pytest.raises(LLMClientError):call_llm(BODY)
    assert len(calls)==1 and 'stage=response_structure' in caplog.text
    assert BODY not in caplog.text


def test_non_json_envelope(deepseek,monkeypatch,caplog):
    def invalid():raise ValueError(BODY+' '+KEY)
    calls=fake_http(monkeypatch,SimpleNamespace(status_code=200,json=invalid))
    with pytest.raises(LLMClientError):call_llm(BODY)
    assert len(calls)==1 and 'stage=response_json' in caplog.text
    assert KEY not in caplog.text and BODY not in caplog.text


@pytest.mark.parametrize('status',[301,400,401,402,403,404,429,500,503])
def test_http_failure_metadata_only(deepseek,monkeypatch,caplog,status):
    class Failure:
        status_code=status
        @property
        def text(self):pytest.fail('body read')
        @property
        def headers(self):pytest.fail('headers read')
        def json(self):pytest.fail('error body read')
    calls=fake_http(monkeypatch,Failure())
    with pytest.raises(LLMClientError) as caught:call_llm(BODY)
    category='http_5xx' if status>=500 else 'http_'+str(status)
    assert 'category='+category in caplog.text
    assert 'status='+str(status) in caplog.text and 'provider=deepseek' in caplog.text
    assert 'response_received=True' in caplog.text and len(calls)==1
    assert KEY not in caplog.text+str(caught.value)
    assert BODY not in caplog.text and 'Authorization' not in caplog.text
    assert all(r.exc_info is None and r.stack_info is None for r in caplog.records)


@pytest.mark.parametrize('error,category',[(requests.ConnectTimeout(BODY),'connect_timeout'),
    (requests.ReadTimeout(BODY),'read_timeout'),(requests.Timeout(BODY),'timeout'),
    (requests.exceptions.SSLError(BODY),'tls_error'),(requests.ConnectionError(BODY),'connection_error'),
    (requests.RequestException(BODY),'requests_exception'),(RuntimeError(BODY),'unexpected_exception')])
def test_transport_failures_no_retry(deepseek,monkeypatch,caplog,error,category):
    calls=fake_http(monkeypatch,error=error)
    with pytest.raises(LLMClientError) as caught:call_llm(BODY)
    assert len(calls)==1 and 'category='+category in caplog.text
    assert 'response_received=False' in caplog.text
    assert BODY not in caplog.text+str(caught.value)


def test_dns_and_httpx_safe_categories(deepseek,monkeypatch,caplog):
    error=requests.ConnectionError(KEY)
    error.__cause__=socket.gaierror(BODY)
    fake_http(monkeypatch,error=error)
    with pytest.raises(LLMClientError):call_llm(BODY)
    assert 'dns_error' in caplog.text
    Error=type('ReadError',(Exception,),{'__module__':'httpx'})
    fake_http(monkeypatch,error=Error(KEY))
    with pytest.raises(LLMClientError):call_llm(BODY)
    assert 'httpx_exception' in caplog.text
    assert KEY not in caplog.text and BODY not in caplog.text


@pytest.mark.parametrize('base',['https://user:synthetic-private-key@api.deepseek.com',
    'https://api.deepseek.com?key=synthetic-private-key','https://[invalid',
    'http://api.deepseek.com'])
def test_bad_endpoint_never_sends_or_logs_credentials(deepseek,monkeypatch,caplog,base):
    monkeypatch.setenv('DEEPSEEK_BASE_URL',base)
    calls=fake_http(monkeypatch)
    with pytest.raises(LLMConfigurationError) as caught:call_llm(BODY)
    assert not calls and KEY not in caplog.text+str(caught.value)
    assert base not in str(caught.value)


def test_endpoint_path_and_exception_class_are_redacted(deepseek,monkeypatch,caplog):
    monkeypatch.setenv('DEEPSEEK_BASE_URL','https://'+KEY+'.invalid/'+KEY)
    fake_http(monkeypatch,error=type(KEY,(Exception,),{})(BODY))
    with pytest.raises(LLMClientError):call_llm(BODY)
    assert KEY not in caplog.text and BODY not in caplog.text
    assert 'endpoint_host=redacted' in caplog.text and 'exception_type=redacted' in caplog.text


def test_task_image_and_scanned_pdf_use_vision_only(deepseek,monkeypatch):
    import base64
    image=b'\x89PNG\r\n\x1a\nsynthetic-image'
    adapter=TJUP2CallAdapter()
    calls=fake_http(monkeypatch)
    adapter.task_estimation_caller('system','task',image_mime='image/png',image_bytes=image)
    adapter.material_images_caller('system','pdf',[('image/png',image),('image/png',image)])
    assert len(calls)==2
    for _,kw in calls:
        assert kw['json']['model']=='configured-vision'
        content=kw['json']['messages'][1]['content']
        assert content[1]['image_url']['url']=='data:image/png;base64,'+base64.b64encode(image).decode()
    assert len(calls[1][1]['json']['messages'][1]['content'])==3


def test_vision_rejects_non_user_images_and_never_retries_text(deepseek,monkeypatch):
    calls=fake_http(monkeypatch,response(status=503))
    parts=[{'type':'image_url','image_url':{'url':'data:image/png;base64,AAAA'}}]
    with pytest.raises(LLMClientError):call_llm_messages([{'role':'system','content':parts}])
    assert not calls
    with pytest.raises(VisionUnavailable):call_llm_messages([{'role':'user','content':parts}],model='text-override')
    assert len(calls)==1 and calls[0][1]['json']['model']=='configured-vision'


@pytest.mark.parametrize('provider',['tju','deepseek'])
def test_text_and_image_preserve_existing_protocol(monkeypatch,provider):
    monkeypatch.setenv('CAMPUSFLOW_LLM_PROVIDER',provider)
    for name,value in {'TJU_LLM_BASE_URL':'https://model.invalid/v1','TJU_LLM_API_KEY':KEY,
        'TJU_LLM_MODEL':'original-qwen','DEEPSEEK_API_KEY':KEY}.items():monkeypatch.setenv(name,value)
    calls=fake_http(monkeypatch,response('answer'))
    adapter=TJUP2CallAdapter()
    assert adapter.agent_caller('system','user')=='answer'
    assert adapter.task_estimation_caller('system','image','image/png',b'\x89PNG\r\n\x1a\nfixture')=='answer'
    if provider=='tju':
        assert all(x[1]['json']['model']=='original-qwen' for x in calls)
        assert 'thinking' not in calls[0][1]['json']
    assert all(x[1]['timeout']==120 and x[1]['json']['max_tokens']==2048 for x in calls)


@pytest.mark.parametrize('kind',['text','docx','pdf_text','image','pdf_vision'])
def test_real_material_entry_routes_by_content(deepseek,monkeypatch,kind):
    from tests.test_material_estimate_recovery import draft_for,action_payload
    from src.material_inbox import extract_material
    draft,kw=draft_for(kind)
    raw=json.dumps(action_payload())
    if kind=='pdf_text':
        from tests.test_file_material import response as material_response
        raw=material_response()
    calls=fake_http(monkeypatch,response(raw))
    adapter=TJUP2CallAdapter()
    result=extract_material(draft,adapter.agent_caller,image_caller=adapter.task_estimation_caller,
        images_caller=adapter.material_images_caller,workload_caller=adapter.workload_estimation_caller,**kw)
    assert result.diagnostics['estimate_available']
    assert result.model_calls==len(calls)<=3
    assert calls[0][1]['json']['model']==('configured-vision' if kind in ('image','pdf_vision') else 'configured-text')


def test_mixed_pdf_vision_failure_reuses_repair_slot_and_marks_partial(deepseek,monkeypatch):
    from tests.test_file_material import source,inbox_for,response as material_response
    from tests.document_fixtures import TASK_TEXT
    from src.material_inbox import extract_material
    material=source('pdf',pages=(TASK_TEXT,None),render=True)
    calls=[]
    def post(url,**kw):
        calls.append(kw['json'])
        if kw['json']['model']=='configured-vision':return response(status=503)
        raw=json.loads(material_response())
        raw['coverage']=dict(level='whole',reason='all text read',uncovered_content='')
        return response(json.dumps(raw))
    monkeypatch.setattr(requests,'post',post)
    adapter=TJUP2CallAdapter()
    result=extract_material(inbox_for(material).draft,adapter.agent_caller,file_source=material,
        image_supported=True,images_caller=adapter.material_images_caller)
    assert len(calls)==result.model_calls==2
    assert result.diagnostics['estimate_available'] and result.source_incomplete
    assert result.estimate_coverage=='partial' and '可能需要更久' in result.coverage_note
    assert result.diagnostics['repair_kind']=='readable_text'
    text=json.loads(calls[1]['messages'][1]['content'])
    assert 'Experiment one' in text['material']
    assert text['attached_image_pages_in_order']==[] and text['source_limits']
    assert isinstance(calls[1]['messages'][1]['content'],str)


@pytest.mark.parametrize('kind',['image','scan'])
def test_unreadable_visual_source_fails_without_guess(deepseek,monkeypatch,kind):
    from tests.test_material_estimate_recovery import draft_for
    from tests.test_file_material import source,inbox_for
    from src.material_inbox import extract_material,MaterialError
    draft,kw=draft_for('image')
    if kind=='scan':
        material=source('pdf',pages=(None,),render=True)
        draft=inbox_for(material).draft
        kw=dict(file_source=material,image_supported=True)
    calls=fake_http(monkeypatch,response(status=503))
    adapter=TJUP2CallAdapter()
    with pytest.raises(MaterialError,match='图片理解服务暂不可用'):
        extract_material(draft,adapter.agent_caller,image_caller=adapter.task_estimation_caller,
            images_caller=adapter.material_images_caller,**kw)
    assert len(calls)==1


def test_timetable_vision_and_failure_preserve_saved_settings(deepseek,monkeypatch):
    from tests.test_timetable_text_import import settings,payload,course
    from src.timetable_text_import import run_timetable_image_import,TimetableImportError
    saved=settings()
    adapter=TJUP2CallAdapter()
    calls=fake_http(monkeypatch,response(payload([course()])))
    draft=run_timetable_image_import('timetable.png','image/png',b'\x89PNG\r\n\x1a\nfixture',
        saved,adapter.task_estimation_caller,adapter.agent_caller)
    assert draft.rows and saved.courses==()
    assert calls[0][1]['json']['model']=='configured-vision'
    calls=fake_http(monkeypatch,response(status=503))
    with pytest.raises(TimetableImportError,match='原课表未改变'):
        run_timetable_image_import('timetable.png','image/png',b'\x89PNG\r\n\x1a\nfixture',
            saved,adapter.task_estimation_caller,adapter.agent_caller)
    assert len(calls)==1 and saved.courses==()


def test_planning_through_provider_then_failed_feedback_preserves_published_bundle(deepseek,monkeypatch):
    from tests.test_p2_live_main import (CountingCaller,_StubSt,_run_main,dt,
        load_live_final_turn,SAFE_ERROR_TEXT)
    model=CountingCaller()
    calls=[]
    def post(url,**kw):
        calls.append(kw)
        messages=kw['json']['messages']
        return response(model(messages[0]['content'],messages[1]['content']))
    monkeypatch.setattr(requests,'post',post)
    adapter=TJUP2CallAdapter()
    stub=_StubSt().set_inputs(reference_hour=9,reference_minute=0,
        intake='我现在在宿舍，10点到11点半上课。今天要做计组实验。',intake_submitted=True)
    _run_main(stub,adapter.agent_caller,now=dt(9))
    original=load_live_final_turn(stub.session_state)
    assert original is not None and not stub.errors
    assert len(calls)==len(model.calls) and all(k['json']['model']=='configured-text' for k in calls)
    count=len(calls)
    stub.set_inputs(reference_hour=9,reference_minute=0)
    _run_main(stub,adapter.agent_caller,now=dt(9))
    assert len(calls)==count
    failures=fake_http(monkeypatch,response(status=402))
    stub.set_inputs(reference_hour=9,reference_minute=0,feedback='新增一项任务',feedback_submitted=True)
    _run_main(stub,adapter.agent_caller,now=dt(9))
    assert failures and load_live_final_turn(stub.session_state) is original
    assert stub.errors[-1]==(SAFE_ERROR_TEXT,)


def test_visual_unavailable_notice_survives_material_action_boundary(deepseek,monkeypatch):
    from tests.test_material_estimate_recovery import draft_for,NOW
    from tests.test_file_material import _file_live_state
    from src.material_inbox import MaterialInbox,MATERIAL_INBOX_KEY
    from src.material_ui import handle_material_action
    draft,kw=draft_for('image')
    st,session,_=_file_live_state()
    st.session_state[MATERIAL_INBOX_KEY]=MaterialInbox(draft)
    st.session_state['cf_material_flow']={'phase':'processing','message':'','supplement':'填表'}
    calls=fake_http(monkeypatch,response(status=503))
    adapter=TJUP2CallAdapter()
    assert not handle_material_action(st,session,adapter,'extract',NOW,
        source_draft=draft,image_mime=kw['image_mime'],image_bytes=kw['image_bytes'])
    flow=st.session_state['cf_material_flow']
    assert '图片识别暂不可用' in flow['message'] and flow['supplement']=='填表'
    assert st.session_state[MATERIAL_INBOX_KEY].draft is draft
    assert len(calls)==1
