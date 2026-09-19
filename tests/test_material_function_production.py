"""Production routing and bounded recovery keep the original business guards."""
import json
from copy import deepcopy
import pytest
from src.p2_tju_live_adapter import TJUP2CallAdapter
from src.material_function_transport import ToolArguments,function_definition
from src.material_inbox import extract_material,MaterialError
from tests.test_fc_original_ten import candidate
from tests.test_workload_engine import estimate_response


def test_only_verified_provider_defaults_to_functions(monkeypatch):
    monkeypatch.setenv('CAMPUSFLOW_LLM_PROVIDER','tju')
    assert TJUP2CallAdapter().recognition_tools
    assert not TJUP2CallAdapter(recognition_tools=False).recognition_tools
    assert not TJUP2CallAdapter(call_function=lambda *a:None).recognition_tools
    monkeypatch.setenv('CAMPUSFLOW_LLM_PROVIDER','deepseek')
    assert not TJUP2CallAdapter().recognition_tools


def test_missing_target_call_is_one_safe_failure_not_content_retry(monkeypatch):
    from src.material_function_transport import extract_arguments
    calls=[]
    def send(*a,**kw):
        calls.append(kw)
        return extract_arguments({'choices':[{'message':{'content':'{}'}}]})
    adapter=TJUP2CallAdapter(message_call_function=send,recognition_tools=True)
    with pytest.raises(ValueError):adapter.agent_caller('campusflow.material-text.v2','{}')
    assert len(calls)==1


def test_formal_failure_repairs_with_same_function_before_independent_estimate():
    draft,_,valid=candidate()
    invalid=deepcopy(valid);invalid['actionability']='actionable'
    messages=[]
    def send(msgs,**kw):
        messages.append((msgs,kw))
        return ToolArguments(json.dumps(invalid if len(messages)==1 else valid))
    adapter=TJUP2CallAdapter(message_call_function=send,recognition_tools=True)
    result=extract_material(draft,adapter.agent_caller,workload_caller=lambda s,u:estimate_response(json.loads(u)))
    assert len(messages)==2 and result.status=='ready'
    assert all(kw['recognition_function']==function_definition() for _,kw in messages)
    assert json.loads(messages[1][0][1]['content'])['request_stage']=='recognition_structure_repair'
    assert result.estimate_fallbacks[0]['origin']=='model_workload'


def test_two_invalid_functions_cannot_recover_into_published_estimate():
    draft,_,invalid=candidate();invalid['actionability']='actionable';calls=[]
    def recognize(*a):calls.append(1);return ToolArguments(json.dumps(invalid))
    with pytest.raises(MaterialError):
        extract_material(draft,recognize,workload_caller=lambda *a:pytest.fail('invalid recognition must not be estimated'))
    assert len(calls)==2


def test_serialization_insurance_keeps_function_channel_and_no_source_context():
    from src.material_serialization_repair import SYSTEM
    messages=[]
    adapter=TJUP2CallAdapter(message_call_function=lambda m,**k:messages.append((m,k)) or ToolArguments('{}'),recognition_tools=True)
    adapter.agent_caller(SYSTEM,json.dumps(dict(request_stage='recognition_serialization_repair',
        previous_response='{"a":',parser_error={'error_offset':5},recognition_contract={})))
    msg,kwargs=messages[0];payload=json.loads(msg[1]['content'])
    assert kwargs['recognition_function']==function_definition()
    assert 'estimate' not in payload['recognition_contract']['properties']
    assert 'material' not in payload and payload['previous_response']=='{"a":'


@pytest.mark.parametrize('succeeds',[True,False])
def test_missing_tool_uses_single_recognition_slot_not_content_fallback(succeeds):
    from src.material_function_transport import extract_arguments
    from src.tju_llm_client import TJUClientError
    draft,_,obj=candidate();calls=[]
    def recognize(s,u):
        calls.append(json.loads(u))
        if len(calls)==2 and succeeds:return ToolArguments(json.dumps(obj))
        try:extract_arguments({'choices':[{'message':{'content':'do not trust this content'}}]})
        except ValueError as exc:raise TJUClientError('safe transport error') from exc
    def run():return extract_material(draft,recognize,workload_caller=lambda s,u:estimate_response(json.loads(u)))
    if succeeds:assert run().estimate_fallbacks[0]['origin']=='model_workload'
    else:
        from src.material_estimate_diagnostics import capture_estimate_diagnostics
        with capture_estimate_diagnostics() as events:
            with pytest.raises(MaterialError):run()
        failures=[e['formal_validation'] for e in events if 'formal_validation' in e]
        assert [v['code'] for v in failures]==['missing_tool_call','missing_tool_call']
        assert failures[-1]['field_path']=='$.message.tool_calls'
    assert len(calls)==2
    assert calls[1]['request_stage']=='recognition_transport_repair'
    assert calls[1]['validation_feedback']['code']=='missing_tool_call'
    assert calls[1]['material']==calls[0]['material']
