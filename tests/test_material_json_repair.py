import json
from copy import deepcopy
import pytest
from src.material_formal_validation import extract_material_object,FormalValidationError
from src.material_effort import validate_ledger
from src.material_effort_provenance import assert_provenance_repair
from src.material_estimate_recovery import _action_estimate
from tests.test_material_effort_provenance import ledger
from tests.test_material_effort_contract import draft,response


@pytest.mark.parametrize('raw',[ '{"a":1}', '```json\n{"a":1}\n```','{"a":1,}'])
def test_only_narrow_existing_json_compatibility(raw):
    assert extract_material_object(raw)=={'a':1}


@pytest.mark.parametrize('raw,kind',[
    ('Explanation {"a":1}','leading_prose_or_non_json'),
    ('{"a":1} Thanks','trailing_prose'),
    ('{"a":1}{"b":2}','multiple_json_objects'),
    ("{'a':1}",'invalid_property_quotes_or_comma'),
    ('{"a":1','missing_comma_or_closing_delimiter'),
    ('{"a":"secret\nbody"}','control_character'),
    ('{"a":"private-body}','unterminated_string'),
    ('{"a" 1}','missing_colon'),
])
def test_malformed_response_diagnostic_has_positions_without_body(raw,kind):
    with pytest.raises(FormalValidationError) as exc:extract_material_object(raw)
    safe=exc.value.feedback
    assert safe['invariant_id']=='material_response_json_object'
    v=safe['observed'];assert v['parse_error_type']==kind
    assert v['response_length']==len(raw)
    assert all(k in v for k in ('error_offset','error_line','error_column','first_token_type',
        'markdown_fence_exists','leading_prose','trailing_prose','appears_truncated','opens_object','closes_object'))
    assert 'private-body' not in json.dumps(safe) and 'secret' not in json.dumps(safe)


def test_trailing_comma_inside_text_not_changed():
    assert extract_material_object('{"a":"private,}",}')=={'a':'private,}'}


@pytest.mark.parametrize('minutes',[16,31,100])
def test_rest_policy_limit_not_relaxed(minutes):
    with pytest.raises(FormalValidationError) as exc:validate_ledger(ledger('rest_buffer',minutes,ref='bounded_rest_buffer_v1'),{})
    assert exc.value.feedback['failure_reason']=='policy_limit_exceeded'


def test_policy_repair_can_reduce_excess_but_not_legal_minutes():
    before=dict(recommended_minutes=131,min_focus_minutes=110,max_focus_minutes=150,
        effort_ledger=ledger('rest_buffer',31,ref='bounded_rest_buffer_v1'),basis='工作',assumptions=[])
    after=deepcopy(before);after['effort_ledger']['adjustments'][0]['minutes']=15;after['recommended_minutes']=115
    assert_provenance_repair(before,after)
    assert validate_ledger(after['effort_ledger'],{})==(100,15)
    illegal=deepcopy(after);illegal['effort_ledger']['adjustments'][0]['minutes']=10
    with pytest.raises(FormalValidationError):assert_provenance_repair(after,illegal)


@pytest.mark.parametrize('bad_repair',[False,True])
def test_policy_repair_is_single_json_contract_and_fallback_on_syntax_error(bad_repair):
    from src.material_inbox import extract_material
    from src.material_estimate_contract import validate_final_estimate
    calls=[]
    def caller(system,user):
        p=json.loads(user);calls.append(p)
        if p.get('request_stage')=='recognition_serialization_repair':
            assert '只修上一份响应的JSON序列化' in system
            return '{"broken":'
        assert '只能返回一个完整JSON object' in system and '不得添加未知字段' in system
        if len(calls)==2 and bad_repair:return '{"broken":'
        obj=response('建议时间包含休息余量。')
        obj['estimate'].update(recommended_minutes=131 if len(calls)==1 else 115,
            min_focus_minutes=100,max_focus_minutes=150,
            effort_ledger=ledger('rest_buffer',31 if len(calls)==1 else 15,ref='bounded_rest_buffer_v1'))
        return json.dumps(obj)
    result=extract_material(draft(),caller)
    assert len(calls)==(3 if bad_repair else 2)
    entry=result.estimate_fallbacks[0]
    assert (entry['origin']=='local_workload')==bad_repair
    assert validate_final_estimate(entry)==()
    if not bad_repair:assert entry['estimate']['recommended_minutes']==115


@pytest.mark.parametrize('change,condition',[
    ({'evidence':None},'evidence_present'),({'evidence':12},'evidence_string'),
    ({'evidence':''},'evidence_nonempty'),({'evidence':'x'*601},'evidence_length_valid'),
    ({'evidence':'private-body-not-in-source'},'verified_evidence_required'),
    ({'reason':None},'reason_present'),({'reason':5},'reason_string'),
    ({'reason':' '},'reason_nonempty'),({'reason':'x'*401},'reason_length_valid'),
])
def test_actionability_failed_subconditions_are_observable_and_private(change,condition):
    obj=response();obj['actionability'].update(change)
    with pytest.raises(FormalValidationError) as exc:_action_estimate(obj,draft())
    safe=exc.value.feedback;obs=safe['observed']
    assert safe['invariant_id']=='action_estimate_grounded_evidence_and_reason'
    assert condition in obs['failed_conditions'] and obs['combined_decision'] is False
    assert obs['evidence_ref_count'] is None and obs['reason_shape']
    assert '$.actionability.reason' in safe['involved_field_paths']
    assert 'private-body' not in json.dumps(safe)


def test_fenced_json_never_bypasses_formal_schema():
    from src.material_inbox import parse_extraction
    from tests.test_material_estimate_recovery import action_payload
    obj=action_payload();obj['unknown_field']=7
    with pytest.raises(FormalValidationError) as exc:parse_extraction('```json\n'+json.dumps(obj)+'\n```',draft(),set())
    assert exc.value.feedback['code']=='unknown_field'
