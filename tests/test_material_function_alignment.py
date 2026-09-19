import json
from dataclasses import fields,MISSING
import pytest
from datetime import datetime
from src.material_recognition_schema import recognition_json_schema,feature_schema,validate_parameters
from src.material_function_transport import ToolArguments,function_definition
from src.material_output_schema import MATERIAL_REQUIRED,ITEM_REQUIRED,TIME_REQUIRED
from src.material_workload import Feature,RATES,Workload
from src.material_inbox import MaterialInbox,update_source,parse_extraction
from src.material_actionability_sources import source_spans,resolve,validate_repaired_action
from src.material_formal_validation import FormalValidationError


def draft(text='请检查表格，核对后提交。'):
    return update_source(MaterialInbox(),text,'',datetime(2026,9,16,14),'plan','beiyangyuan').draft

def basic():return dict(schema_version='campusflow.material-text.v2',reference_date=None,reference_evidence=None,items=[])


def test_dataclass_defaults_define_optional_feature_words():
    schema=feature_schema()
    assert schema['required']==[f.name for f in fields(Feature) if f.default is MISSING]
    assert 'words' not in schema['required']
    assert schema['properties']['words']=={'type':'integer','default':0}
    validate_parameters(dict(kind='review',units=1,evidence='检查'),schema)
    with pytest.raises(FormalValidationError):validate_parameters(dict(kind='review',units=1,evidence='检查',words=None),schema)


def test_formal_required_sets_are_not_prompt_key_inference():
    schema=recognition_json_schema();props=schema['properties']
    assert set(schema['required'])==MATERIAL_REQUIRED
    assert set(props['items']['items']['required'])==ITEM_REQUIRED
    assert set(props['items']['items']['properties']['deadline']['required'])==TIME_REQUIRED
    assert props['workload']['items']['required']==[f.name for f in fields(Workload) if f.default is MISSING]


def test_nullable_and_optional_are_independent_at_every_boundary():
    schema=recognition_json_schema();props=schema['properties']
    assert 'reference_date' in schema['required'] and 'null' in props['reference_date']['type']
    assert 'actionability' not in schema['required']
    action=props['actionability'];assert 'reason' in action['required']
    assert action['properties']['reason']['type']=='string'
    assert 'evidence' not in action['required'] and 'null' in action['properties']['evidence']['type']
    assert 'evidence_refs' not in action['required'] and action['properties']['evidence_refs']['type']=='array'
    ledger=props['estimate']['properties']['effort_ledger'];adjust=ledger['properties']['adjustments']['items']
    assert 'included_in' in adjust['required'] and adjust['properties']['included_in']['type']==['integer','null']


def test_input_requirements_are_not_model_owned_output_fields():
    schema=recognition_json_schema()
    assert not {'requirements','material_requirements','material_facts','quantity_claims'} & set(schema['properties'])
    nested=schema['properties']['estimate']
    assert 'quantity_claims' in nested['properties'] and 'quantity_claims' not in nested['required']
    assert nested['properties']['quantity_claims']['items']['anyOf']


@pytest.mark.parametrize('kind',list(RATES))
def test_workload_enums_use_formal_model_not_ledger_categories(kind):
    s=feature_schema();assert set(s['properties']['kind']['enum'])==set(RATES)
    validate_parameters(dict(kind=kind,units=1,evidence='检查'),s)
    assert Feature(kind,1,'检查').kind==kind


@pytest.mark.parametrize('kind',['writing','submission','reading'])
def test_ledger_category_cannot_be_accepted_as_feature_kind(kind):
    with pytest.raises(FormalValidationError):validate_parameters(dict(kind=kind,units=1,evidence='检查'),feature_schema())
    with pytest.raises(ValueError):Feature(kind,1,'检查')


def test_shared_shape_does_not_replace_formal_reference_validator():
    d=draft();obj=basic();obj.update(reference_date='2026-09-15',reference_evidence='并不存在的日期')
    validate_parameters(obj)
    with pytest.raises(FormalValidationError):parse_extraction(ToolArguments(json.dumps(obj)),d,set())


def test_missing_formal_root_still_rejected_for_both_channels():
    obj=basic();obj.pop('items')
    for raw in [json.dumps(obj),ToolArguments(json.dumps(obj))]:
        with pytest.raises(FormalValidationError) as exc:parse_extraction(raw,draft(),set())
        assert exc.value.feedback['field_path']=='$.items'


def test_compound_directive_reuses_known_action_provenance():
    d=draft('请运行程序，保存输出并核对结果。')
    span=source_spans(d)[0]
    assert span['support_state']=='action_supporting'
    assert span['support_origin']=='explicit_source_directive'
    action=dict(is_estimatable=True,reason='需要核对结果',evidence_refs=[span['ref']])
    assert resolve(action,d)[1]['verified_evidence_required']
    obj=basic();obj['actionability']=action
    validate_repaired_action(json.dumps(obj),d)


@pytest.mark.parametrize('text',['信号与系统基础知识。','请勿运行程序，也不要核对结果。','背景：请运行程序并核对结果。'])
def test_unverified_or_negative_sources_do_not_become_automatic_positive(text):
    d=draft(text);span=source_spans(d)[0]
    assert span['support_state']!='action_supporting'
    result=resolve(dict(is_estimatable=True,reason='解释',evidence_refs=[span['ref']]),d)[1]
    assert not result['verified_evidence_required']
    expected='insufficient_support_mapping' if span['support_state']=='unknown' else 'source_does_not_support_action'
    assert result['provenance_failure_reason']==expected


def test_evidence_optional_supports_existing_unique_quote_compatibility():
    d=draft();obj=basic();obj['actionability']=dict(is_estimatable=True,reason='明确要求检查',evidence=d.original_text)
    validate_parameters(obj);validate_repaired_action(json.dumps(obj),d)


def test_formal_reason_is_not_made_optional_to_get_fixture_through():
    obj=basic();obj['actionability']=dict(is_estimatable=False,evidence_refs=[])
    with pytest.raises(FormalValidationError):validate_parameters(obj)
    with pytest.raises(FormalValidationError):validate_repaired_action(json.dumps(obj),draft())


def test_every_function_export_is_the_same_formal_schema():
    from src.material_tool_schema import recognition_tool_schema
    from src.material_stage_projection import recognition_stage_schema
    assert function_definition()['parameters']==recognition_tool_schema(recognition_stage_schema())
    assert function_definition()==function_definition()
