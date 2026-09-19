import json
from copy import deepcopy
import pytest

from src.material_recognition_semantics import field_states,enum_glossary,policy_guide,policy_repair_context
from src.material_recognition_schema import recognition_json_schema,validate_parameters
from src.material_output_schema import recognition_semantic_guide,TIME_REQUIRED
from src.material_formal_validation import FormalValidationError
from src.material_function_transport import ToolArguments,function_definition
from src.material_workload import RATES,LABELS,FEATURE_SEMANTICS
from src.material_effort_provenance import POLICIES,validate_row
from tests.test_fc_original_ten import candidate
from scripts.fc_original_ten import audit_candidate


def test_enum_repair_feedback_includes_only_declared_choices():
    schema=dict(type='string',enum=['scope','identity'])
    with pytest.raises(FormalValidationError) as failure:
        validate_parameters('private invented reason',schema,'$.items[0].uncertainties[0].field')
    assert failure.value.feedback['allowed_values']==['scope','identity']
    assert 'private invented reason' not in str(failure.value.feedback)


def test_time_semantics_project_shared_declarations_without_changing_schema(monkeypatch):
    from src.material_output_schema import ITEM_FIELD_SEMANTICS
    before=recognition_json_schema()
    monkeypatch.setitem(ITEM_FIELD_SEMANTICS,'start','test time ownership')
    assert 'items[*].start：test time ownership' in recognition_semantic_guide()
    assert recognition_json_schema()==before


def test_missing_time_is_whole_null_not_null_string_placeholder():
    time_schema=recognition_json_schema()['properties']['items']['items']['properties']['start']
    validate_parameters(None,time_schema)
    invalid={key:None for key in TIME_REQUIRED}
    with pytest.raises(FormalValidationError):validate_parameters(invalid,time_schema)


def test_glossary_projects_formal_categories_and_metadata(monkeypatch):
    assert set(RATES)==set(LABELS)==set(FEATURE_SEMANTICS)
    for kind in RATES:assert kind+'：'+LABELS[kind] in enum_glossary()
    monkeypatch.setitem(LABELS,'submit','changed label')
    monkeypatch.setitem(FEATURE_SEMANTICS,'submit','changed boundary')
    assert 'submit：changed label；changed boundary' in recognition_semantic_guide()
    assert 'writing' not in RATES


def test_glossary_is_not_response_enum_mapping():
    draft,gold,obj=candidate();obj['workload'][0]['features'][0]['kind']='writing'
    row=audit_candidate(ToolArguments(json.dumps(obj)),draft,gold)
    assert not row['schema_valid'] and row['formal_failure']['code']=='invalid_enum'


def test_scope_summary_can_rely_on_grounded_action_features():
    draft,gold,obj=candidate()
    obj['actionability']['short_scope']='实验表格中的缺失项'
    obj['workload'][0]['scope']='实验表格中的缺失项'
    row=audit_candidate(ToolArguments(json.dumps(obj)),draft,gold)
    assert row['scope_correct'] and row['business_pass']
    obj['workload'][0]['features']=[]
    assert not audit_candidate(ToolArguments(json.dumps(obj)),draft,gold)['scope_correct']


def test_unverified_or_wrong_feature_cannot_rescue_scope():
    draft,gold,obj=candidate()
    obj['actionability']['short_scope']=obj['workload'][0]['scope']='实验表格中的缺失项'
    obj['workload'][0]['features'][1]['evidence']='not in source'
    assert not audit_candidate(ToolArguments(json.dumps(obj)),draft,gold)['scope_correct']


def test_save_is_not_submit_and_gold_stays_strict():
    draft,gold,obj=candidate(6)
    obj['workload'][0]['features'].append(dict(kind='submit',units=1,evidence=gold['text']))
    row=audit_candidate(ToolArguments(json.dumps(obj)),draft,gold)
    assert row['schema_valid'] and not row['workload_semantic_correct']
    assert '本地保存' in FEATURE_SEMANTICS['submit']


def test_optional_and_nullable_are_independent_and_generated(monkeypatch):
    schema=dict(type='object',properties=dict(
        optional=dict(type='string'),nullable=dict(type=['string','null']),required=dict(type='string')),
        required=['required'],additionalProperties=False)
    states={r['path']:r for r in field_states(schema)}
    assert not states['$.optional']['required'] and not states['$.optional']['nullable']
    assert states['$.nullable']['nullable']
    validate_parameters({'required':'value','nullable':None},schema)
    for obj in ({'required':'value','optional':None},{'optional':'value'}):
        with pytest.raises(FormalValidationError):validate_parameters(obj,schema)
    before=deepcopy(schema);guide=recognition_semantic_guide(schema)
    assert '$.optional' in guide and schema==before
    schema['properties']['added']=dict(type='string')
    assert '$.added' in recognition_semantic_guide(schema)


def test_actual_time_contract_requires_text_or_null_parent():
    schema=recognition_json_schema()['properties']['items']['items']['properties']['deadline']
    assert 'text' in TIME_REQUIRED and 'text' in schema['required']
    validate_parameters(None,schema)
    obj={k:None for k in TIME_REQUIRED}
    with pytest.raises(FormalValidationError) as exc:validate_parameters(obj,schema)
    assert exc.value.feedback['field_path']=='$.text'
    obj.pop('text')
    with pytest.raises(FormalValidationError):validate_parameters(obj,schema)
    assert '$.items[*].deadline' in recognition_semantic_guide()


def policy_object(core=8,minutes=1):
    return {'estimate':{'effort_ledger':dict(core=[dict(category='review',minutes=core)],adjustments=[dict(
        category='context_switching',minutes=minutes,reason='切换任务',provenance_type='estimation_policy',
        provenance_ref='bounded_context_switching_v1',included_in=None)])}}


def test_policy_context_exact_limit_and_strict_guard():
    obj=policy_object();observation=policy_repair_context(obj)[0]
    assert observation['observed_minutes']==1 and observation['allowed_limit']==0
    assert observation['path']=='$.estimate.effort_ledger.adjustments[0].minutes'
    row=obj['estimate']['effort_ledger']['adjustments'][0]
    with pytest.raises(FormalValidationError) as exc:validate_row(row,0,8)
    assert exc.value.feedback['failure_reason']=='policy_limit_exceeded'
    validate_row(row,0,20)
    assert policy_repair_context(policy_object(20))[0]['allowed_limit']==2


def test_registry_change_updates_guide_and_repair_context(monkeypatch):
    ref='bounded_context_switching_v1'
    monkeypatch.setitem(POLICIES,ref,dict(category='context_switching',ratio=.07,maximum_minutes=3))
    assert '比例=0.07，绝对上限=3分钟' in policy_guide()
    assert policy_repair_context(policy_object(100))[0]['allowed_limit']==3


def test_tool_schema_frozen_annotations_not_reintroduced():
    formal=recognition_json_schema();before=deepcopy(formal)
    definition=function_definition()
    recognition_semantic_guide(formal)
    assert formal==before
    from scripts.fc_metadata_probe import metadata_inventory
    assert not metadata_inventory(definition['parameters'])
    assert definition['parameters']['properties']['actionability']['anyOf'][1]=={'type':'null'}


def test_full_ten_gate_denies_before_six_pass(tmp_path,monkeypatch):
    from scripts import fc_semantic_business_probe as probe
    monkeypatch.setattr(probe,'DEST',tmp_path)
    (tmp_path/'six').mkdir();(tmp_path/'six/calls.json').write_text(json.dumps([
        dict(fixture=i,final_pass=i!=3) for i in probe.FAILED]))
    with pytest.raises(RuntimeError,match='gate'):probe.run('ten')
