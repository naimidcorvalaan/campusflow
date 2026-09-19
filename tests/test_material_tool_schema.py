from copy import deepcopy
import json
import pytest
from jsonschema import Draft7Validator
from src.material_tool_schema import tool_schema,recognition_tool_schema
from src.material_recognition_schema import recognition_json_schema
from src.material_function_transport import ToolArguments,function_definition
from src.material_json import parse
from src.material_formal_validation import FormalValidationError


def nullable():
    return dict(type=['object','null'],title='value',description='说明',properties={'x':{'type':'string'}},
        required=['x'],additionalProperties=False)


def test_only_nullable_object_changes_and_preserves_structure():
    original=nullable();before=deepcopy(original);adapted=tool_schema(original)
    assert original==before
    assert adapted==dict(title='value',description='说明',anyOf=[dict(type='object',properties={'x':{'type':'string'}},
        required=['x'],additionalProperties=False),dict(type='null')])
    assert tool_schema(adapted)==adapted


@pytest.mark.parametrize('schema',[{'type':'object','properties':{}},{'type':'string'}, {'type':'number'},
    {'type':'boolean'},{'type':['string','null']},{'type':['object','null','string']},
    {'type':'string','enum':['review','code']},{'default':{'type':['object','null']}}])
def test_other_types_enum_and_metadata_unchanged(schema):assert tool_schema(schema)==schema


@pytest.mark.parametrize('extra',[{}, {'enum':[{'x':'a'}]}, {'enum':[None,{'x':'a'}]},
    {'anyOf':[{'type':'object'}]}, {'allOf':[{'not':{'type':'null'}}]}])
def test_equivalence_including_constraints_on_null(extra):
    schema=dict(nullable(),**extra);adapted=tool_schema(schema)
    for candidate in [None,{}, {'x':'a'},{'x':'b'}, {'x':1}, {'x':'a','unknown':True},'value',3,True,[]]:
        assert Draft7Validator(schema).is_valid(candidate)==Draft7Validator(adapted).is_valid(candidate)


def test_nested_optional_not_made_required():
    schema=dict(type='object',properties={'optional':nullable(),'required':nullable()},required=['required'],additionalProperties=False)
    out=tool_schema(schema)
    assert out['required']==['required'] and out['additionalProperties'] is False
    for candidate in [{},{'required':None},{'required':{'x':'ok'}},{'required':None,'optional':None}]:
        assert Draft7Validator(schema).is_valid(candidate)==Draft7Validator(out).is_valid(candidate)


def test_formal_schema_unchanged_and_response_not_canonicalized():
    formal=recognition_json_schema();before=deepcopy(formal)
    from src.material_stage_projection import recognition_stage_schema
    assert function_definition()['parameters']==recognition_tool_schema(recognition_stage_schema())
    assert 'estimate' in formal['properties'] and 'estimate' not in function_definition()['parameters']['properties']
    assert formal==before and formal['properties']['actionability']['type']==['object','null']
    bad=dict(schema_version='campusflow.material-text.v2',reference_date=None,reference_evidence=None,items=[],actionability='{"is_estimatable":true}')
    with pytest.raises(FormalValidationError) as exc:parse(ToolArguments(json.dumps(bad)))
    assert exc.value.feedback['field_path']=='$.actionability'


def test_full_schema_is_idempotent_and_standard_valid():
    adapted=tool_schema(recognition_json_schema());Draft7Validator.check_schema(adapted)
    assert tool_schema(adapted)==adapted


def test_schema_description_cannot_be_returned_as_business_field():
    obj=dict(schema_version='campusflow.material-text.v2',reference_date=None,reference_evidence=None,items=[],
        actionability=dict(is_estimatable=True,reason='存在检查任务',evidence_refs=[],description='额外字段'))
    assert not Draft7Validator(function_definition()['parameters']).is_valid(obj)
    with pytest.raises(FormalValidationError) as exc:parse(ToolArguments(json.dumps(obj)))
    assert exc.value.feedback['code']=='unknown_field'
    assert exc.value.feedback['field_path']=='$.actionability.description'


@pytest.mark.parametrize('case',['object','null'])
def test_probe_uses_formal_parser_without_response_conversion(case):
    from scripts.fc_nullable_adapter_probe import CASES,NAME,inspect_result
    from src.material_inbox import MaterialInbox,update_source
    from src.material_actionability_sources import source_spans
    from datetime import datetime
    draft=update_source(MaterialInbox(),CASES[case],'',datetime(2026,9,16),'plan','beiyangyuan').draft
    action=None if case=='null' else dict(is_estimatable=True,reason='存在检查任务',evidence_refs=[source_spans(draft)[0]['ref']])
    def response(value):return dict(choices=[dict(message=dict(tool_calls=[dict(type='function',function=dict(
        name=NAME,arguments=json.dumps({'actionability':value})))]),finish_reason='tool_calls')])
    result=inspect_result(response(action),case,draft)
    assert result['case_pass'] and result['formal_parser_valid'] and result['source_valid']
    wrong=inspect_result(response(json.dumps(action)),case,draft)
    assert not wrong['case_pass'] and wrong['actionability_type']=='string'
