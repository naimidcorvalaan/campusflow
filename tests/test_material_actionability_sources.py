"""Recognition anchors bind source provenance, never estimate semantics."""
import json

import pytest

from src.material_actionability_sources import (source_spans,resolve,canonical_response,
    needs_repair,validate_repaired_action,assert_binding_only,REPAIR)
from src.material_inbox import MaterialInbox,update_source,extract_material,parse_extraction
from src.material_estimate_recovery import _action_estimate
from src.material_formal_validation import FormalValidationError
from tests.test_material_estimate_recovery import FORM,NOW,action_payload


def draft(text=FORM, supplement=''):
    return update_source(MaterialInbox(),text,'',NOW,'plan','beiyangyuan',supplement).draft


def with_refs(current, refs=None):
    obj=action_payload()
    obj['actionability'].update(evidence=None,evidence_refs=refs if refs is not None else [source_spans(current)[0]['ref']],
        reason='根据原始填写和复核要求判断工作量，不逐字复述')
    return obj


def test_valid_source_ref_and_free_reason():
    current=draft();obj=with_refs(current)
    assert resolve(obj['actionability'],current)[1]['verified_evidence_required']
    assert _action_estimate(obj,current)['estimate']['recommended_minutes']==20


@pytest.mark.parametrize('kind',['unknown','foreign','duplicate','wrong_type','empty','scalar'])
def test_bad_refs_cannot_be_rescued_by_valid_quote(kind):
    current=draft();ref=source_spans(current)[0]['ref']
    refs={'unknown':['material_span_unknown'],'foreign':[source_spans(draft(FORM+'另一份材料'))[0]['ref']],
        'duplicate':[ref,ref],'wrong_type':[42],'empty':[],'scalar':42}[kind]
    obj=with_refs(current,refs);obj['actionability']['evidence']=FORM
    with pytest.raises(FormalValidationError):_action_estimate(obj,current)
    assert needs_repair(json.dumps(obj),current)


def test_multi_refs_are_verified_separately():
    current=draft('填写申请表。\n核对证明材料。')
    obj=with_refs(current,[s['ref'] for s in source_spans(current)])
    refs,audit=resolve(obj['actionability'],current)
    assert audit['evidence_format']=='multi_ref' and audit['evidence_ref_valid_count']==2
    assert len(refs)==2 and audit['verified_evidence_required']


def test_known_work_cannot_be_proved_by_unrelated_background_block():
    current=draft('校园创办于近代。\n'+FORM)
    obj=with_refs(current,[source_spans(current)[0]['ref']])
    assert resolve(obj['actionability'],current)[1]['provenance_failure_reason']=='insufficient_support_mapping'
    with pytest.raises(FormalValidationError):_action_estimate(obj,current)


@pytest.mark.parametrize('evidence',['填写  姓名','填写 姓名'])
def test_unique_exact_or_whitespace_quote_migrates(evidence):
    current=draft('申请表\n填写  姓名，核对材料。')
    obj=action_payload();obj['actionability']['evidence']=evidence
    result=json.loads(canonical_response(json.dumps(obj),current))
    assert result['actionability']['evidence_refs']==[source_spans(current)[1]['ref']]
    assert result['estimate']==obj['estimate']


@pytest.mark.parametrize('evidence',['填写姓名','改写的填写要求','填写姓名 + 核对材料'])
def test_ambiguous_missing_or_stitched_quote_requires_repair(evidence):
    current=draft('填写姓名\n填写姓名\n核对材料')
    obj=action_payload();obj['actionability']['evidence']=evidence
    raw=json.dumps(obj)
    assert canonical_response(raw,current)==raw
    assert needs_repair(raw,current)


def test_ref_in_completed_task_cannot_support_remaining_scope():
    current=draft('作业要求：\n第一部分 计算数据。\n第二部分 核对结果。','第一部分已完成，只需要完成第二部分')
    spans=source_spans(current)
    assert not spans[1]['in_current_scope'] and spans[2]['in_current_scope']
    assert resolve(with_refs(current,[spans[1]['ref']])['actionability'],current)[1]['provenance_failure_reason']=='outside_current_scope'


def test_refs_stable_and_page_markers_not_evidence_blocks():
    current=draft('[第1页]\n填写材料\n[第2页]\n核对材料')
    spans=source_spans(current)
    assert [s['page'] for s in spans]==[1,2]
    assert spans==source_spans(current)
    for s in spans:assert current.original_text[s['start']:s['end']]==s['text']


def test_bound_repair_cannot_change_estimate_and_uses_only_given_refs():
    current=draft();obj=action_payload();obj['actionability']['evidence']='无法定位的改写'
    fixed=with_refs(current)
    validate_repaired_action(json.dumps(fixed),current)
    assert_binding_only(json.dumps(obj),json.dumps(fixed))
    assert fixed['estimate']==obj['estimate'] and fixed['items']==obj['items']
    assert _action_estimate(fixed,current)
    fixed['actionability']['evidence_refs']=['invented']
    with pytest.raises(FormalValidationError):validate_repaired_action(json.dumps(fixed),current)


def test_repair_cannot_add_estimate_fields_or_prose():
    current=draft();obj=action_payload()
    patch=with_refs(current);patch['unknown']=1
    with pytest.raises(FormalValidationError) as exc:parse_extraction(json.dumps(patch),current,set())
    assert exc.value.feedback['code']=='unknown_field'
    with pytest.raises(ValueError):validate_repaired_action('解释'+json.dumps(patch),current)


def test_diagnostics_do_not_contain_source_or_evidence_text():
    current=draft(FORM+' PRIVATE_SOURCE')
    obj=with_refs(current,['private-unknown-ref']);obj['actionability']['evidence']='PRIVATE_EVIDENCE'
    with pytest.raises(FormalValidationError) as exc:_action_estimate(obj,current)
    safe=json.dumps(exc.value.feedback)
    for secret in ['PRIVATE_SOURCE','PRIVATE_EVIDENCE','private-unknown-ref',FORM]:assert secret not in safe
    audit=exc.value.feedback['observed']
    assert audit['candidate_source_span_count']==1 and audit['evidence_length']==16
    assert audit['exact_match'] is False and audit['normalized_match'] is False


def test_production_ref_repair_is_bounded_and_preserves_minutes():
    current=draft();obj=action_payload();obj['actionability']['evidence']='先处理字段，再核对申请资料'
    requests=[]
    def caller(system,user):
        data=json.loads(user);requests.append(data)
        assert data['material']==FORM
        if len(requests)==1:
            assert 'actionability.evidence_refs' in system
            return json.dumps(obj)
        assert data['request_stage']=='recognition_evidence_repair'
        assert '一个完整JSON object' in system and REPAIR in system
        fixed=with_refs(current,[data['verified_action_source_refs'][0]['ref']])
        assert data['output']==requests[0]['output']
        return json.dumps(fixed)
    result=extract_material(current,caller)
    assert result.model_calls==len(requests)==2
    assert result.estimate_fallbacks[0]['estimate']['recommended_minutes']==20
    assert result.diagnostics['repair_kind']=='actionability_source_binding'
    assert not result.estimate_fallbacks[0]['confirmation_fields']


def test_unsupported_action_cannot_use_reason_as_proof():
    current=draft();obj=with_refs(current,[])
    obj['actionability']['reason']=FORM
    with pytest.raises(FormalValidationError) as exc:_action_estimate(obj,current)
    assert 'verified_evidence_required' in exc.value.feedback['observed']['failed_conditions']
