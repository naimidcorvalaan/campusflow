"""Offline tests only: no calls and no production compatibility adapter."""
import json
import inspect
from copy import deepcopy
import pytest
from jsonschema import Draft7Validator
from scripts.fc_schema_probe import schemas,payload,wire_schema,examine,current_schema_preflight,FUNCTION
from src.material_recognition_schema import recognition_json_schema,validate_parameters
from src.material_function_transport import function_definition,ToolArguments
from src.material_json import parse
from src.material_formal_validation import FormalValidationError
from src.material_workload import RATES
from src.p2_tju_live_adapter import TJUP2CallAdapter


def reply(action):
    return {'choices':[{'finish_reason':'tool_calls','message':{'tool_calls':[{'type':'function',
        'function':{'name':FUNCTION,'arguments':json.dumps({'actionability':action})}}]}}]}


def test_matrix_reproducible_and_nullable_semantics_not_optional():
    first=schemas();assert first==schemas()
    for variant in ('A','B','C','D'):
        schema=first[variant];Draft7Validator.check_schema(schema)
        assert not Draft7Validator(schema).is_valid({})
    for variant in ('B','C'):
        assert Draft7Validator(first[variant]).is_valid({'actionability':None})
        assert Draft7Validator(first[variant]).is_valid({'actionability':{'is_estimatable':False,'reason':'没有动作'}})
    assert not Draft7Validator(first['A']).is_valid({'actionability':None})


def test_b_reproduces_current_union_expression_exactly():
    assert schemas()['B']['properties']['actionability']['type']==recognition_json_schema()['properties']['actionability']['type']==['object','null']
    assert 'anyOf' in schemas()['C']['properties']['actionability']


def test_actual_prepared_bytes_match_current_function_contract():
    audit=current_schema_preflight()
    assert audit['parameters']==function_definition()['parameters']
    action=audit['actionability']['anyOf'][0]
    assert action['additionalProperties'] is False
    assert action['required']==['is_estimatable','reason']
    assert not audit['actionability_root_required']


@pytest.mark.parametrize('variant',['A','B','C','D'])
def test_string_actionability_never_automatically_decoded(variant):
    result=examine(reply('{"is_estimatable":true,"reason":"检查"}'),variant)
    assert result['json_valid'] and result['actionability_type']=='string'
    assert not result['schema_valid']


def test_enum_from_formal_values_illegal_values_still_rejected():
    schema=schemas()['E'];assert schema['properties']['feature_type']['enum']==list(RATES)
    assert not Draft7Validator(schema).is_valid({'feature_type':'writing'})
    assert Draft7Validator(schema).is_valid({'feature_type':'review'})


def test_experiment_d_does_not_redefine_business_actionability():
    original=schemas()['A']['properties']['actionability'];always=schemas()['D']['properties']['actionability']
    assert set(always['properties'])-set(original['properties'])=={'present'}
    for key,value in original['properties'].items():assert always['properties'][key]==value
    assert always['properties']['present']['type']=='boolean'


def test_formal_validator_stays_strict_and_adapter_disabled():
    obj=dict(schema_version='campusflow.material-text.v2',reference_date=None,reference_evidence=None,
        items=[],actionability='{"is_estimatable":true}')
    with pytest.raises(FormalValidationError):parse(ToolArguments(json.dumps(obj)))
    assert inspect.signature(TJUP2CallAdapter).parameters['recognition_tools'].default is None
    assert TJUP2CallAdapter(recognition_tools=False).recognition_tools is False


def test_normal_repair_same_declaration_not_experiment_schema():
    calls=[]
    def message(messages,**kwargs):calls.append(kwargs['recognition_function']);return '{}'
    adapter=TJUP2CallAdapter(message_call_function=message,recognition_tools=True)
    for stage in ('normal','semantic repair'):adapter.agent_caller('campusflow.material-text.v2 '+stage,'fixed')
    assert calls==[function_definition(),function_definition()]


def test_prepared_live_probe_contains_only_small_payload():
    import requests
    for variant in schemas():
        prepared=requests.Request('POST','https://example.invalid',json=payload(variant)).prepare()
        assert wire_schema(prepared.body)['parameters']==schemas()[variant]
        assert len(prepared.body)<4000
        assert payload(variant)['tool_choice']=='auto'
