"""Complete source provenance and bounded, full-contract recognition repair."""
import json
import pytest

from src.material_actionability_sources import source_spans,source_context,resolve,validate_repaired_action,assert_binding_only
from src.material_estimate_diagnostics import capture_estimate_diagnostics
from src.material_formal_validation import FormalValidationError
from src.material_inbox import extract_material,MaterialError
from tests.test_material_actionability_sources import draft,with_refs
from tests.test_material_estimate_recovery import action_payload


def test_every_source_has_explicit_support_state_and_provenance():
    current=draft('未经分类的内容。\n仅供参考：历史沿革。\n作业要求：\n第一部分 计算数据。\n使用附件中的测量值。\n第二部分 核对结果。')
    spans=source_spans(current)
    assert len(spans)==6
    assert spans[0]['support_state']=='unknown'
    assert spans[1]['support_state']=='non_action'
    assert all(s['support_state']=='action_supporting' for s in spans[3:])
    assert spans[4]['support_origin']=='numbered_scope_source_region'
    assert spans[4]['section_numbers']==[1]
    assert sum(s['support_state'] in ('unknown','non_action','action_supporting') for s in spans)==len(spans)


@pytest.mark.parametrize('source,reason',[
    ('未分类的信息。','insufficient_support_mapping'),
    ('仅供参考：历史沿革。','source_does_not_support_action'),
])
def test_unknown_and_negative_are_different_and_neither_passes(source,reason):
    current=draft(source)
    _,audit=resolve(with_refs(current)['actionability'],current)
    assert not audit['verified_evidence_required']
    assert audit['provenance_failure_reason']==reason
    assert audit['evidence_ref_valid_count']==1
    assert audit['referenced_sources'][0]['ref_valid']


def test_scope_provenance_covers_repeated_action_categories_not_just_first_quote():
    current=draft('作业要求：\n第一部分 计算均值。\n第二部分 计算方差。\n第三部分 计算误差。')
    spans=source_spans(current)
    assert all(s['support_state']=='action_supporting' for s in spans[1:])
    for s in spans[1:]:
        assert resolve(with_refs(current,[s['ref']])['actionability'],current)[1]['verified_evidence_required']


def test_task_provenance_refs_are_given_to_normal_and_repair_without_source_text_in_diagnostics():
    current=draft('任务要求：\n第一部分 计算PRIVATE_DATA。\n第二部分 核对PRIVATE_DATA。')
    context=source_context(current)
    assert len(context['verified_action_source_refs'])==2
    assert 'PRIVATE_DATA' not in json.dumps(context['verified_action_source_refs'])
    assert 'PRIVATE_DATA' in json.dumps(context['actionability_source_spans'])
    obj=with_refs(current,[context['verified_action_source_refs'][0]['ref']])
    audit=resolve(obj['actionability'],current)[1]
    assert 'PRIVATE_DATA' not in json.dumps(audit)
    assert audit['referenced_sources'][0]['support_origin']=='numbered_scope_source_region'


def test_sparse_workload_positives_cannot_make_unclassified_blocks_negative():
    current=draft('尚未分类的材料。\n申请表：填写姓名，核对证明材料。')
    spans=source_spans(current)
    assert spans[0]['support_state']=='unknown'
    assert spans[1]['support_state']=='action_supporting'
    assert resolve(with_refs(current,[spans[0]['ref']])['actionability'],current)[1]['provenance_failure_reason']=='insufficient_support_mapping'


def test_false_decision_with_no_refs_is_allowed_but_invented_refs_are_not():
    current=draft('只有未分类内容。');obj=with_refs(current,[])
    obj['actionability']['is_estimatable']=False
    validate_repaired_action(json.dumps(obj),current)
    obj['actionability']['evidence_refs']=['invented']
    with pytest.raises(FormalValidationError):validate_repaired_action(json.dumps(obj),current)


def test_binding_repair_cannot_reestimate():
    current=draft();obj=with_refs(current)
    fixed=json.loads(json.dumps(obj));fixed['estimate']['recommended_minutes']+=5
    with pytest.raises(FormalValidationError) as exc:assert_binding_only(json.dumps(obj),json.dumps(fixed))
    assert exc.value.feedback['code']=='source_repair_changed_protected_fields'


@pytest.mark.parametrize('tail',['{}',' []',',"revision":{"fixed":true}}'])
def test_ambiguous_recognition_gets_one_full_contract_repair(tail):
    current=draft();obj=with_refs(current);raw=json.dumps(obj)
    requests=[]
    def caller(system,user):
        data=json.loads(user);requests.append(data)
        if len(requests)==1:return raw+tail
        assert data['request_stage']=='recognition_structure_repair'
        assert data['output']==requests[0]['output']
        assert data['validation_feedback']['error_code']=='ambiguous_multiple_structures'
        assert data['validation_feedback']['second_structure_type'] in ('object','array','structured_fragment')
        assert data['validation_feedback']['candidate_structure_count']>=2
        assert '唯一最终JSON object' in system
        return raw
    with capture_estimate_diagnostics() as events:result=extract_material(current,caller)
    assert result.model_calls==len(requests)==2
    assert result.estimate_fallbacks[0]['estimate']['recommended_minutes']==20
    assert all(e['actionability_conditions']['combined_decision'] for e in events if 'actionability_conditions' in e)
    repair=next(e['recognition_repair'] for e in events if 'recognition_repair' in e)
    assert repair['repair_triggered'] and repair['accepted'] and repair['repair_final_object_count']==1


@pytest.mark.parametrize('second_kind',['ambiguous','unknown_field','unknown_ref','unsupported_ref'])
def test_recognition_repair_never_bypasses_json_schema_or_provenance(second_kind):
    current=draft();obj=with_refs(current);raw=json.dumps(obj)
    calls=[]
    def caller(system,user):
        data=json.loads(user);calls.append(data)
        if len(calls)==1:return raw+'{}'
        if data.get('request_stage')=='recognition_structure_repair':
            fixed=json.loads(raw)
            if second_kind=='ambiguous':return raw+'[]'
            if second_kind=='unknown_field':fixed['unknown_field']=1
            if second_kind in ('unknown_ref','unsupported_ref'):fixed['actionability']['evidence_refs']=['unknown'] if second_kind=='unknown_ref' else []
            return json.dumps(fixed)
        return '{}'  # Independent estimator cannot rescue the invalid envelope.
    with capture_estimate_diagnostics() as events:
        result=extract_material(current,caller)
    from src.material_estimate_contract import validate_final_estimate
    for entry in result.estimate_fallbacks:
        with pytest.raises(ValueError):validate_final_estimate(entry)
    assert sum(c.get('request_stage')=='recognition_structure_repair' for c in calls)==1
    repair=next(e['recognition_repair'] for e in events if 'recognition_repair' in e)
    assert repair['decision']=='rejected' and not repair['accepted']
    if second_kind=='ambiguous':assert repair['repair_final_object_count']>=1
