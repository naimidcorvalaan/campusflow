from copy import deepcopy
import json
import pytest
from src.material_stage_projection import project_stage,recognition_stage_schema
from src.material_output_schema import RECOGNITION_ROOT_OWNERS,recognition_template,recognition_semantic_guide
from src.material_recognition_schema import recognition_json_schema,validate_parameters
from src.material_function_transport import ToolArguments,function_definition
from src.material_formal_validation import FormalValidationError
from src.material_json import parse
from tests.test_fc_original_ten import candidate
from scripts.fc_original_ten import audit_candidate
from scripts.fc_action_coverage import coverage,repair_feedback


def test_stage_projection_preserves_full_model_and_exact_other_constraints():
    formal=recognition_json_schema();before=deepcopy(formal);stage=recognition_stage_schema()
    assert formal==before and 'estimate' in formal['properties'] and 'estimate' in recognition_template()
    assert 'estimate' not in formal['required'] and 'estimate' not in stage['properties']
    assert stage['required']==formal['required'] and stage['additionalProperties'] is False
    assert all(v==formal['properties'][k] for k,v in stage['properties'].items() if k!='items')
    original_item=formal['properties']['items']['items']
    projected_item=stage['properties']['items']['items']
    assert 'estimate' in original_item['properties'] and 'estimate' not in projected_item['properties']
    assert projected_item['required']==original_item['required']
    assert all(v==original_item['properties'][k] for k,v in projected_item['properties'].items())
    assert project_stage(stage,{k:v for k,v in RECOGNITION_ROOT_OWNERS.items() if k in stage['properties']},'recognition')==stage


def test_ownership_change_drives_schema_and_guide(monkeypatch):
    monkeypatch.setitem(RECOGNITION_ROOT_OWNERS,'coverage','presentation')
    assert 'coverage' not in recognition_stage_schema()['properties']
    assert 'coverage → presentation' in recognition_semantic_guide()


def test_cannot_remove_required_or_forget_ownership():
    schema=recognition_json_schema();owners=dict(RECOGNITION_ROOT_OWNERS,schema_version='estimate')
    with pytest.raises(ValueError,match='required'):project_stage(schema,owners,'recognition')
    del owners['schema_version']
    with pytest.raises(ValueError,match='ownership'):project_stage(schema,owners,'recognition')


def test_historical_root_estimate_still_formal_but_tool_rejects_no_deletion():
    _,_,obj=candidate();obj['estimate']=None;before=deepcopy(obj)
    validate_parameters(obj)  # Optional historical nullable field still exists.
    assert parse(json.dumps(obj))['estimate'] is None
    with pytest.raises(FormalValidationError) as exc:parse(ToolArguments(json.dumps(obj)))
    assert exc.value.feedback['field_path']=='$.estimate' and obj==before


def test_no_root_estimate_still_runs_complete_formal_estimator():
    from src.material_inbox import extract_material
    from tests.test_workload_engine import estimate_response
    draft,_,obj=candidate();calls=[]
    def recognition(system,user):calls.append('recognition');return ToolArguments(json.dumps(obj))
    def estimator(system,user):calls.append('estimate');return estimate_response(json.loads(user),20)
    result=extract_material(draft,recognition,workload_caller=estimator)
    assert calls==['recognition','estimate']
    assert result.estimate_fallbacks[0]['origin']=='model_workload'
    assert result.estimate_fallbacks[0]['estimate']['recommended_minutes']==20
    assert result.workload_summary and result.status=='ready'


def test_normal_and_repair_share_stage_subset_and_guide():
    from src.material_function_transport import call_recognition
    calls=[]
    def call(messages,**kw):calls.append((messages,kw));return '{}'
    for stage in ('normal','repair'):call_recognition(stage,'{}',call,30,1000)
    assert calls[0][1]['recognition_function']==calls[1][1]['recognition_function']==function_definition()
    assert all(recognition_semantic_guide() in c[0][0]['content'] for c in calls)
    assert 'estimate' not in calls[0][1]['recognition_function']['parameters']['properties']


@pytest.mark.parametrize('index,missing',( (2,'submit'),(7,'short_text') ))
def test_action_can_be_covered_equivalently_without_duplicate_features(index,missing):
    draft,gold,obj=candidate(index)
    obj['workload'][0]['features']=[f for f in obj['workload'][0]['features'] if f['kind']!=missing and not(missing=='short_text' and f['kind']=='long_text')]
    row=audit_candidate(ToolArguments(json.dumps(obj)),draft,gold)
    assert row['business_pass'] and row['missing_feature_groups']
    assert all(r['covered'] for r in row['action_coverage'])


def test_completely_lost_submit_generates_source_anchored_one_repair_feedback():
    draft,gold,obj=candidate(2)
    obj['workload'][0]['features']=[f for f in obj['workload'][0]['features'] if f['kind']!='submit']
    obj['workload'][0]['scope']=obj['actionability']['short_scope']='填写申请表姓名日期并核对'
    row=audit_candidate(ToolArguments(json.dumps(obj)),draft,gold)
    fb=repair_feedback(row)
    assert not row['business_pass'] and fb['missing_action_categories']==[['submit']]
    assert fb['supporting_source_refs'] and fb['current_feature_categories']
    obj['actionability']['short_scope']+='后上传'
    assert audit_candidate(ToolArguments(json.dumps(obj)),draft,gold)['business_pass']


def test_completely_lost_text_action_fails_and_local_save_not_submit():
    draft,gold,obj=candidate(7)
    obj['workload'][0]['features']=[f for f in obj['workload'][0]['features'] if f['kind'] not in ('short_text','long_text')]
    obj['workload'][0]['scope']=obj['actionability']['short_scope']='检查问题顺序后提交'
    row=audit_candidate(ToolArguments(json.dumps(obj)),draft,gold)
    assert row['coverage_feedback']['missing_action_categories']==[['long_text','short_text']]
    draft,gold,obj=candidate(6)
    obj['workload'][0]['features'].append(dict(kind='submit',units=1,evidence=gold['text']))
    row=audit_candidate(ToolArguments(json.dumps(obj)),draft,gold)
    assert row['coverage_feedback']['unsupported_feature_categories']==['submit']
    assert 'submit' not in gold['allowed_feature_kinds']


def test_real_upload_and_hand_in_are_submission_equivalents():
    gold=dict(expected_feature_groups=[['submit']])
    for text in ('上传材料','hand in the assignment','upload the file'):
        assert coverage({'actionability':{'short_scope':text}},[],gold)[0]['covered']
    assert not coverage({'actionability':{'short_scope':'save output locally'}},[],gold)[0]['covered']
