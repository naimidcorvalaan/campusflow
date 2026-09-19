import json
from copy import deepcopy
import pytest
from src.material_function_transport import (NAME,ToolArguments,function_definition,
    extract_arguments,validate_parameters)
from src.material_output_schema import MATERIAL_REQUIRED,MATERIAL_OPTIONAL,recognition_template
from src.material_formal_validation import FormalValidationError
from src.material_json import parse
from src.material_recognition_boundary import canonical_recognition
from src.p2_tju_live_adapter import TJUP2CallAdapter


def obj():return dict(schema_version='campusflow.material-text.v2',reference_date=None,reference_evidence=None,items=[])
def response(args=None,calls=None):
    calls=calls if calls is not None else [dict(type='function',function=dict(name=NAME,arguments=args or json.dumps(obj())))]
    return dict(choices=[dict(finish_reason='tool_calls',message=dict(content=None,tool_calls=calls))])


def test_schema_generated_from_one_shared_template():
    schema=function_definition()['parameters']
    from src.material_output_schema import RECOGNITION_ROOT_OWNERS
    assert set(recognition_template())==MATERIAL_REQUIRED|MATERIAL_OPTIONAL
    assert set(schema['properties'])=={k for k,v in RECOGNITION_ROOT_OWNERS.items() if v=='recognition'}
    assert set(schema['required'])==MATERIAL_REQUIRED
    assert schema['additionalProperties'] is False
    from src.material_recognition_schema import recognition_json_schema
    assert 'estimate' not in schema['properties']['items']['items']['properties']
    estimate=recognition_json_schema()['properties']['items']['items']['properties']['estimate']['properties']
    core=estimate['effort_ledger']['properties']['core']['items']['properties']
    assert set(core)=={'category','minutes'}
    assert core['minutes']['type']=='integer'
    assert schema['properties']['items']['items']['properties']['minutes']['type']==['integer','null']


@pytest.mark.parametrize('mutation,code',[
    (lambda d:d['choices'][0]['message'].update(tool_calls=[]),'missing_tool_call'),
    (lambda d:d['choices'][0]['message']['tool_calls'].append(deepcopy(d['choices'][0]['message']['tool_calls'][0])),'multiple_tool_calls'),
    (lambda d:d['choices'][0]['message']['tool_calls'][0]['function'].update(name='other'),'wrong_tool_name'),
    (lambda d:d['choices'][0]['message']['tool_calls'][0]['function'].update(arguments={}), 'wrong_arguments_type'),
])
def test_target_tool_boundary_strict(mutation,code):
    d=response();mutation(d)
    with pytest.raises(FormalValidationError) as exc:extract_arguments(d)
    assert exc.value.feedback['code']==code


@pytest.mark.parametrize('payload',[None,[],{'choices':[None]},
    {'choices':[{'message':'bad'}]},
    {'choices':[{'message':{'tool_calls':[None]}}]},
    {'choices':[{'message':{'tool_calls':[{'type':'function','function':None}]}}]}])
def test_malformed_envelope_has_safe_formal_failure(payload):
    with pytest.raises(FormalValidationError) as exc:extract_arguments(payload)
    assert exc.value.feedback['code'] in ('missing_tool_call','wrong_tool_name')
    assert exc.value.feedback['field_path']=='$.message.tool_calls'


def test_function_result_is_strict_and_preserves_business_values():
    raw=extract_arguments(response())
    assert isinstance(raw,ToolArguments) and parse(raw)==obj()


@pytest.mark.parametrize('suffix',['\n explanation','}',',"second":{}','{}'])
def test_arguments_do_not_use_content_salvage(suffix):
    raw=ToolArguments(json.dumps(obj())+suffix)
    assert canonical_recognition(raw,lambda _:None) is raw
    with pytest.raises(FormalValidationError):parse(raw)


@pytest.mark.parametrize('raw',['{"schema_version":"campusflow.material-text.v2",}',
    '```json\n{}\n```','{"a":1,"a":2}','{"a":NaN}','{"a":1'])
def test_malformed_tool_arguments_never_locally_repaired(raw):
    with pytest.raises(FormalValidationError):parse(ToolArguments(raw))


def test_unknown_fields_cannot_bypass_generated_schema():
    d=obj();d['material_facts']={}
    with pytest.raises(FormalValidationError) as exc:parse(ToolArguments(json.dumps(d)))
    assert exc.value.feedback['code']=='unknown_field'
    assert exc.value.feedback['field_path']=='$.material_facts'


def test_actionability_types_and_refs_remain_formal():
    d=obj();d['actionability']=dict(is_estimatable='true',task_name='任务',short_scope='提交',reason='材料要求',
        evidence=None,evidence_refs=['fake'],confirmation_required=[],waiting_note=None)
    with pytest.raises(FormalValidationError) as exc:validate_parameters(d)
    assert exc.value.feedback['field_path']=='$.actionability.is_estimatable'
    # Valid JSON/schema does not turn a nonexistent source ref into proof.
    d['actionability']['is_estimatable']=True
    validate_parameters(d)
    from src.material_inbox import MaterialInbox,update_source
    from src.material_actionability_sources import validate_repaired_action
    from datetime import datetime
    draft=update_source(MaterialInbox(),'请完成数据检查并提交结果。','',datetime(2026,9,16),'plan','beiyangyuan').draft
    with pytest.raises(FormalValidationError):validate_repaired_action(json.dumps(d),draft)


def test_normal_semantic_repair_use_same_function_and_budget():
    calls=[]
    def message(messages,**kw):calls.append((messages,kw));return ToolArguments(json.dumps(obj()))
    adapter=TJUP2CallAdapter(message_call_function=message,recognition_tools=True)
    for prompt in ['normal','semantic repair']:
        adapter.agent_caller('campusflow.material-text.v2 '+prompt,'complete material context')
    assert calls[0][1]==calls[1][1]
    assert calls[0][1]['recognition_function']==function_definition()
    assert calls[0][1]['max_tokens']==2048 and calls[0][1]['timeout']==120
    assert calls[0][0][1]['content']=='complete material context'


def test_other_agents_keep_existing_transport():
    calls=[]
    adapter=TJUP2CallAdapter(call_function=lambda prompt,**kw:calls.append(kw) or 'legacy',recognition_tools=True)
    assert adapter.agent_caller('campusflow.workload-estimate.v1','estimate request')=='legacy'
    assert 'recognition_function' not in calls[0]


def test_client_sends_tools_auto_without_unverified_response_format(monkeypatch):
    from src.tju_llm_client import call_tju_llm_messages
    monkeypatch.setattr('src.tju_llm_client._require_all_envs',lambda:('https://example.invalid/api','fake','tju-llm'))
    captured=[]
    class Reply:
        status_code=200
        def json(self):return response()
    def post(url,**kwargs):captured.append(kwargs);return Reply()
    monkeypatch.setattr('src.tju_llm_client.requests.post',post)
    result=call_tju_llm_messages([{'role':'user','content':'hello'}],recognition_function=function_definition())
    assert isinstance(result,ToolArguments)
    payload=captured[0]['json']
    assert payload['tool_choice']=='auto' and len(payload['tools'])==1
    assert 'response_format' not in payload and 'json_schema' not in payload
