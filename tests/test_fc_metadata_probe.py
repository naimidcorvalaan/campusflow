import json
from copy import deepcopy
import pytest
from jsonschema import Draft7Validator
from scripts.fc_metadata_probe import strip_annotations,metadata_inventory,inspect_response
from scripts.fc_nullable_adapter_probe import formal_minimal_schema,NAME,CASES
from src.material_tool_schema import tool_schema,recognition_tool_schema
from src.material_recognition_schema import recognition_json_schema
from src.material_inbox import MaterialInbox,update_source
from src.material_actionability_sources import source_spans
from datetime import datetime


def test_annotations_only_schema_positions_not_property_names():
    formal=dict(type='object',title='T',description='D',default={},examples=[{}],
        properties={'description':dict(type='string',description='business field'),
            'nested':dict(type=['object','null'],properties={'enum':dict(type='string',enum=['description','default'])},
                required=['enum'],additionalProperties=False)},required=['description'],additionalProperties=False)
    before=deepcopy(formal);converted=tool_schema(formal);stripped=strip_annotations(converted)
    assert formal==before and converted['title']=='T'
    assert stripped['required']==['description'] and stripped['additionalProperties'] is False
    assert stripped['properties']['description']=={'type':'string'}
    assert stripped['properties']['nested']['anyOf'][0]['properties']['enum']['enum']==['description','default']
    assert not metadata_inventory(stripped)
    assert strip_annotations(stripped)==stripped
    for value in [{},{'description':'ok'},{'description':'ok','nested':None},
        {'description':'ok','nested':{'enum':'description'}},{'description':'ok','nested':{'enum':'illegal'}},
        {'description':'ok','other':True},None]:
        assert Draft7Validator(formal).is_valid(value)==Draft7Validator(stripped).is_valid(value)


def test_defs_and_refs_are_validation_not_annotations():
    schema=dict(definitions={'obj':dict(type='object',description='desc',properties={'x':{'type':'integer'}},required=['x'])},
        anyOf=[{'$ref':'#/definitions/obj'},{'type':'null'}],title='root')
    out=strip_annotations(schema)
    assert out['anyOf']==schema['anyOf'] and 'definitions' in out
    for value in [None,{}, {'x':1},{'x':'no'}]:
        assert Draft7Validator(schema).is_valid(value)==Draft7Validator(out).is_valid(value)


def test_minimal_object_and_null_equivalent_and_formal_unchanged():
    formal=recognition_json_schema();before=deepcopy(formal)
    adapted=strip_annotations(tool_schema(formal));assert formal==before
    Draft7Validator.check_schema(adapted)
    small=formal_minimal_schema();out=strip_annotations(tool_schema(small))
    for value in [None,{'is_estimatable':False,'reason':'没有动作'},'not an object']:
        obj={'actionability':value}
        assert Draft7Validator(small).is_valid(obj)==Draft7Validator(out).is_valid(obj)


def test_integrated_adapter_matches_tested_b_and_remains_idempotent():
    formal=formal_minimal_schema();before=deepcopy(formal)
    result=recognition_tool_schema(formal)
    assert result==strip_annotations(tool_schema(formal))
    assert formal==before
    assert recognition_tool_schema(result)==result
    assert not metadata_inventory(result)
    assert result['properties']['actionability']['anyOf'][1]=={'type':'null'}


@pytest.mark.parametrize('extra',[False,True])
def test_full_safe_diagnostics_and_unchanged_formal_parser(extra):
    draft=update_source(MaterialInbox(),CASES['object'],'',datetime(2026,9,16,14),'plan','beiyangyuan').draft
    action=dict(is_estimatable=True,reason='PRIVATE_REASON_SENTINEL',evidence_refs=[source_spans(draft)[0]['ref']])
    if extra:action['description']='PRIVATE_VALUE_SENTINEL'
    data=dict(choices=[dict(finish_reason='tool_calls',message=dict(tool_calls=[dict(type='function',function=dict(
        name=NAME,arguments=json.dumps(dict(actionability=action))))]))])
    result=inspect_response(data,draft)
    assert result['formal_schema_valid']==result['formal_parser_valid']==(not extra)
    assert result['unknown_fields']==(['description'] if extra else [])
    assert result['missing_required']==[] and result['wrong_types']==[] and result['enum_failures']==[]
    if extra:
        assert result['formal_parser_failure']['code']=='unknown_field'
        assert result['formal_parser_failure']['field_path']=='$.actionability.description'
    assert 'PRIVATE_' not in json.dumps(result)
