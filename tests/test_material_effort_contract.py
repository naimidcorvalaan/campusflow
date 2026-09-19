"""One ledger contract, bounded copy repair, and safe invalid numeric evidence."""
import json
from copy import deepcopy
import pytest
from src.material_effort import (example,contract,check_effort,check_copy,assert_copy_only,
    validate_ledger,CORE_FIELDS,ADJUSTMENT_FIELDS)
from src.material_formal_validation import FormalValidationError
from src.material_estimate_diagnostics import capture_estimate_diagnostics,EstimateTrace
from src.material_output_schema import output_instructions,estimate_example
from src.material_estimate_recovery import _action_estimate,_estimate
from src.material_inbox import MaterialInbox,update_source,extract_material
from src.material_workload import estimate_workloads
from src.material_estimate_contract import validate_final_estimate
from tests.test_material_effort import ledger
from tests.test_material_estimate_recovery import FORM,NOW,action_payload,payload
from tests.test_estimate_protocol_diagnostics import work,row


def draft():return update_source(MaterialInbox(),FORM,'',NOW,'plan','beiyangyuan','还需查资料').draft


def response(assumption='已计入40分钟查资料，未再增加其他缓冲。'):
    obj=action_payload()
    obj['estimate'].update(recommended_minutes=150,min_focus_minutes=120,max_focus_minutes=180,
        effort_ledger=ledger(110,40),basis='填写并复核材料',assumptions=[assumption])
    return obj


def test_contract_template_validator_and_all_prompt_paths_agree():
    value=example()
    assert set(value['core'][0])==CORE_FIELDS=={'category','minutes'}
    assert set(value['adjustments'][0])==ADJUSTMENT_FIELDS
    assert 'reason' not in contract()['core_item']
    for workload in (False,True):
        assert estimate_example(workload)['effort_ledger']==value
        prompt=output_instructions(workload)
        assert '禁止reason/label/description/work_id' in prompt
        assert json.dumps(contract(),ensure_ascii=False) in prompt


@pytest.mark.parametrize('path',['action','formal','workload'])
def test_illegal_core_reason_rejected_on_every_estimate_path(path):
    obj=response();obj['estimate']['effort_ledger']['core'][0]['reason']='private reasoning'
    if path=='workload':
        target=lambda:validate_ledger(obj['estimate']['effort_ledger'])
    elif path=='action':target=lambda:_action_estimate(obj,draft())
    else:
        item=payload(False)['items'][0];item['estimate']=obj['estimate']
        target=lambda:_estimate(item,draft(),0,True)
    with pytest.raises(FormalValidationError) as exc:target()
    assert exc.value.feedback['code']=='unknown_field'
    assert exc.value.feedback['field_path']=='$.effort_ledger.core[0].reason'
    assert 'private reasoning' not in json.dumps(exc.value.feedback)


@pytest.mark.parametrize('text',['未计入额外缓冲','未留缓冲','没有余量','no buffer'])
def test_positive_adjustment_cannot_be_denied(text):
    value=ledger(115,10)
    with pytest.raises(FormalValidationError) as exc:check_copy(value,'工作量已明确',[text])
    assert exc.value.feedback['code']=='effort_copy_conflict' and exc.value.feedback['copy_only']


@pytest.mark.parametrize('text',['已计入10分钟，未增加其他缓冲','建议包含10分钟余量','已计入10分钟，no further buffer'])
def test_other_unmodeled_buffer_denial_is_not_denial_of_included_adjustments(text):
    assert check_copy(ledger(115,10),'工作量已明确',[text]) is None


@pytest.mark.parametrize('text',['已预留30分钟缓冲','建议包含30分钟休息余量','reserved 30 minutes buffer'])
def test_empty_adjustments_cannot_claim_explicit_buffer(text):
    with pytest.raises(FormalValidationError):check_copy(ledger(115),'工作量已明确',[text])
    assert check_copy(ledger(115),'工作量已明确',['未预留缓冲']) is None
    assert check_copy(ledger(115),'工作量已明确',['额外缓冲0分钟']) is None


@pytest.mark.parametrize('changed_minutes',[False,True])
def test_recognition_copy_repair_is_bounded_and_never_reestimates(changed_minutes):
    first=response('未计入额外缓冲');calls=[]
    def caller(system,user):
        calls.append(json.loads(user));obj=deepcopy(first)
        if len(calls)>1:
            assert calls[-1]['request_stage']=='estimate_copy_repair'
            assert calls[-1]['validation_feedback'][0]['code']=='effort_copy_conflict'
            from src.material_actionability_sources import canonical_response
            assert calls[-1]['previous_response']==json.loads(canonical_response(json.dumps(first),draft()))
            assert '分钟区间、建议、effort_ledger、任务身份和scope必须完全不变' in system
            obj['estimate']['assumptions']=['已包含40分钟查资料，未增加其他缓冲。']
            if changed_minutes:obj['estimate']['recommended_minutes']=155
        return json.dumps(obj)
    with capture_estimate_diagnostics() as events:result=extract_material(draft(),caller)
    assert len(calls)==2
    entry=result.estimate_fallbacks[0]
    if changed_minutes:
        assert any(e['code']=='copy_repair_changed_estimate' for e in events)
        assert entry['origin']=='local_workload'
    else:
        assert entry['origin']!='local_workload'
        assert entry['estimate']['recommended_minutes']==150
        expected=deepcopy(first['estimate']['effort_ledger']);validate_ledger(expected,{'supplement':'还需查资料'})
        assert entry['effort_ledger']==expected
        assert not any(e.get('fallback') for e in events)
    assert validate_final_estimate(entry)==()


@pytest.mark.parametrize('mutation',['none','minutes','unknown_field','still_denied'])
def test_workload_copy_repair_rejects_new_fields_or_numeric_changes(mutation):
    calls=[]
    def caller(system,user):
        req=json.loads(user);calls.append(req)
        value=row(req,150)
        value.update(min_focus_minutes=120,max_focus_minutes=180,basis='完整工作量',
            assumptions=['未计入额外缓冲'],effort_ledger=ledger(110,40))
        if len(calls)==2:
            assert req['validation_feedback'][0]['copy_only']
            assert 'core每项严格只有category和minutes' in system
            if mutation!='still_denied':value['assumptions']=['已计入40分钟查资料，未再增加其他缓冲。']
            if mutation=='minutes':value['recommended_minutes']=155
            if mutation=='unknown_field':value['effort_ledger']['core'][0]['reason']='not allowed'
        return json.dumps(dict(estimates=[value]))
    with capture_estimate_diagnostics() as events:
        result,_=estimate_workloads([work()],caller,supplement='还需查资料')
    assert len(calls)==2
    assert result[0][2]==('model_workload' if mutation=='none' else 'local_workload')
    if mutation=='none':assert result[0][1]['recommended_minutes']==150
    if mutation=='minutes':assert any(e['code']=='copy_repair_changed_estimate' for e in events)
    if mutation=='unknown_field':assert any(e.get('formal_validation',{}).get('field_path')=='$.effort_ledger.core[0].reason' for e in events)


def test_invalid_ledger_snapshot_retains_numbers_not_private_prose():
    value=ledger(110,40)
    value['core'][0]['reason']='private PDF body API Key Authorization hidden reasoning'
    stable='abc123'*4
    raw=dict(work_id=stable,recommended_minutes=150,min_focus_minutes=120,max_focus_minutes=180,
        basis='private body',assumptions=[],effort_ledger=value)
    with capture_estimate_diagnostics() as events:EstimateTrace('initial',{stable}).response(json.dumps(raw))
    snapshot=events[0]['snapshot']['estimates'][0]['effort_ledger']
    assert snapshot['valid'] is False
    assert snapshot['ledger_stage']=='initial'
    assert snapshot['core_item_count']==snapshot['adjustment_item_count']==1
    assert snapshot['core'][0]['work_id']==stable
    assert snapshot['core'][0]['present_fields']==['category','minutes','reason']
    assert snapshot['core'][0]['minutes']==110 and snapshot['adjustments'][0]['minutes']==40
    assert snapshot['core_subtotal']==110 and snapshot['explicit_adjustments']==40
    assert snapshot['total']==snapshot['suggested_minutes']==150 and snapshot['gap']==0
    assert snapshot['unknown_fields']==['$.effort_ledger.core[0].reason']
    assert snapshot['validation_error']['field_path']=='$.effort_ledger.core[0].reason'
    assert snapshot['validation_error']['code']=='unknown_field'
    safe=json.dumps(events,ensure_ascii=False)
    for secret in ('private','Authorization','API Key','hidden reasoning','还需查资料'):
        assert secret not in safe


def test_initial_invalid_action_preserves_exact_error_instead_of_generic_root():
    obj=response();obj['estimate']['effort_ledger']['core'][0]['reason']='private'
    with capture_estimate_diagnostics() as events:
        extract_material(draft(),lambda *a:json.dumps(obj),workload_caller=lambda *a:'{}')
    initial=[e['formal_validation'] for e in events if e.get('formal_validation',{}).get('attempt')=='initial']
    assert any(e['field_path']=='$.effort_ledger.core[0].reason' for e in initial)


@pytest.mark.parametrize('mode',['normal','repair','recovery'])
def test_final_paths_use_the_same_valid_ledger_contract(mode):
    from src.material_estimate_recovery import recover_result
    from src.material_estimate_contract import complete_final_estimate
    from tests.test_material_owner_qualifier import FACTS,REQ,CLAIMS
    obj=response()
    if mode!='normal':obj['source_title']='known historical context echo'
    raw=json.dumps(obj)
    if mode=='repair':
        corrected=deepcopy(obj);corrected.pop('source_title')
        replies=[raw,json.dumps(corrected)]
    else:replies=[raw]
    result=recover_result(replies,draft(),set(),mode!='normal')
    entry=complete_final_estimate(result.estimate_fallbacks[0],draft(),set(),replies,FACTS,REQ)
    assert validate_final_estimate(entry)==()
    expected=deepcopy(obj['estimate']['effort_ledger']);validate_ledger(expected,{'supplement':'还需查资料'})
    assert entry['effort_ledger']==expected
    assert entry['estimate']['recommended_minutes']==150
    assert entry['quantity_claims']==CLAIMS
