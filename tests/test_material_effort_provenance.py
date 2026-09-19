"""Verified fact IDs and bounded policies, not quote similarity."""
from copy import deepcopy
import json
import pytest
from src.material_effort import validate_ledger,check_effort
from src.material_effort_provenance import (catalog_for,add,receipt,source_context,
    assert_provenance_repair,POLICIES)
from src.material_formal_validation import FormalValidationError
from src.material_workload import Workload,Feature,estimate_workloads
from src.material_estimate_diagnostics import capture_estimate_diagnostics
from tests.test_estimate_protocol_diagnostics import row


def work():return Workload('检查实验结果','检查结果并提交',(Feature('review',1,'需要检查结果'),))


def test_workload_policy_repair_receives_computed_limits_from_registry():
    calls=[]
    def caller(system,user):
        request=json.loads(user);calls.append(request)
        value=row(request,110)
        value.update(min_focus_minutes=90,max_focus_minutes=130,basis='按已识别工作及切换余量估算',assumptions=[])
        value['effort_ledger']=ledger(minutes=12)
        if len(calls)==2:
            context=request['policy_validation_context'][0]
            assert context['core_subtotal']==100
            assert context['observed_minutes']==12 and context['allowed_limit']==10
            assert context['policy_name']=='bounded_context_switching_v1'
            assert context['policy_total_limit']==25
            assert 'policy_validation_context' in system and '不得提高上限' in system
            value['effort_ledger']['adjustments'][0]['minutes']=context['allowed_limit']
        return json.dumps(dict(estimates=[value]))
    result,error=estimate_workloads([work()],caller)
    assert len(calls)==2 and result[0][2]=='model_workload'
    assert result[0][1]['recommended_minutes']==110
    assert result[0][1]['effort_ledger']['adjustments'][0]['minutes']==10


def ledger(category='context_switching',minutes=10,kind='estimation_policy',ref='bounded_context_switching_v1'):
    return dict(completeness='complete',core=[dict(category='calculation',minutes=100)],
        adjustments=[dict(category=category,minutes=minutes,reason='对应工作与处理节奏的余量',
            provenance_type=kind,provenance_ref=ref,included_in=None)])


@pytest.mark.parametrize('source',['material','user'])
def test_valid_facts_referenced_without_restating_source(source):
    catalog=catalog_for([work()],supplement='我计算比较慢')
    kind='material_fact' if source=='material' else 'user_fact'
    ref=next(k for k,v in catalog.items() if v['provenance_type']==kind)
    value=ledger('review' if source=='material' else 'user_pace',10,kind,ref)
    value['adjustments'][0]['reason']='预留结果复核时间' if source=='material' else '按较慢的计算速度调整'
    assert validate_ledger(value,catalog)==(100,10)
    assert 'evidence' not in value['adjustments'][0]
    assert validate_ledger(value,receipt(catalog,value))==(100,10)


@pytest.mark.parametrize('ref',list(POLICIES))
def test_allowed_policy_is_bounded_and_not_a_source_claim(ref):
    value=ledger(POLICIES[ref]['category'],10,'estimation_policy',ref)
    assert validate_ledger(value,{})==(100,10)
    assert check_effort(130,110,150,'按既有工作量和策略估算',ledger=value,sources={}).valid


@pytest.mark.parametrize('mutation,reason',[
    (lambda r:r.pop('provenance_type'),'missing_provenance'),
    (lambda r:r.update(provenance_type='heuristic'),'wrong_source_type'),
    (lambda r:r.update(provenance_ref='invented_policy'),'unsupported_policy'),
    (lambda r:r.update(minutes=100),'policy_limit_exceeded'),
    (lambda r:r.update(provenance_type='material_fact',provenance_ref='material_fact_'+'a'*24),'unknown_ref'),
])
def test_unsupported_provenance_is_precise_safe_and_rejected(mutation,reason):
    value=ledger();mutation(value['adjustments'][0])
    value['adjustments'][0]['reason']='PRIVATE_BODY Authorization API Key'
    with pytest.raises(FormalValidationError) as exc:validate_ledger(value,{})
    feedback=exc.value.feedback
    assert feedback['code']=='unverified_adjustment'
    assert feedback['failure_reason']==reason
    assert feedback['adjustment_index']==0 and feedback['field_path'].startswith('$.effort_ledger.adjustments[0].')
    assert 'PRIVATE_BODY' not in json.dumps(feedback)


def test_ref_type_and_category_must_match_verified_source():
    sources=catalog_for([work()],supplement='我计算比较慢')
    ref=next(k for k,v in sources.items() if v['provenance_type']=='user_fact')
    for category,kind in [('review','user_fact'),('user_pace','material_fact')]:
        with pytest.raises(FormalValidationError) as exc:validate_ledger(ledger(category,10,kind,ref),sources)
        assert exc.value.feedback['failure_reason']=='wrong_source_type'


def test_policy_splitting_cannot_bypass_combined_limit():
    value=ledger('uncertainty',20,'estimation_policy','bounded_uncertainty_v1')
    value['adjustments'].append(deepcopy(value['adjustments'][0]))
    with pytest.raises(FormalValidationError) as exc:validate_ledger(value,{})
    assert exc.value.feedback['failure_reason']=='policy_limit_exceeded'


@pytest.mark.parametrize('mode',['exact','paraphrase','ambiguous'])
def test_legacy_quote_migrates_only_when_exact_and_unambiguous(mode):
    catalog=catalog_for(supplement='我计算比较慢')
    value=ledger('user_pace')
    value['adjustments'][0]=dict(category='user_pace',minutes=10,reason='速度调整',
        evidence_ref='supplement',evidence='我计算比较慢' if mode!='paraphrase' else '计算不太快',included_in=None)
    if mode=='ambiguous':add(catalog,'user_fact',{'another':True},{'user_pace'},'我计算比较慢',('supplement',))
    if mode=='exact':
        validate_ledger(value,catalog)
        assert value['adjustments'][0]['provenance_ref'] in catalog
        assert 'evidence' not in value['adjustments'][0]
    else:
        with pytest.raises(FormalValidationError) as exc:validate_ledger(value,catalog)
        assert exc.value.feedback['failure_reason']=='legacy_free_text_evidence'


@pytest.mark.parametrize('delete',[False,True])
def test_repair_uses_allowed_catalog_or_removes_adjustment(delete):
    calls=[]
    def caller(system,user):
        p=json.loads(user);calls.append(p)
        value=row(p,130)
        value.update(min_focus_minutes=110,max_focus_minutes=150,basis='计算及必要工作',assumptions=[],
            effort_ledger=ledger('user_pace',10,'user_fact','user_fact_'+'0'*24))
        assert p['verified_evidence_refs'] and p['allowed_estimation_policies']
        if len(calls)==2:
            assert p['validation_feedback'][0]['failure_reason']=='unknown_ref'
            assert '不复述evidence' in system
            if delete:
                value['effort_ledger']['adjustments']=[]
                value.update(recommended_minutes=100,min_focus_minutes=90,max_focus_minutes=120)
            else:
                ref=next(v['provenance_ref'] for v in p['verified_evidence_refs'] if v['provenance_type']=='user_fact')
                value['effort_ledger']['adjustments'][0]['provenance_ref']=ref
        return json.dumps(dict(estimates=[value]))
    with capture_estimate_diagnostics() as events:
        results,_=estimate_workloads([work()],caller,supplement='我计算比较慢')
    assert len(calls)==2 and results[0][2]=='model_workload'
    assert results[0][1]['recommended_minutes']==(100 if delete else 130)
    assert not any(e.get('fallback') for e in events)
    assert '我计算比较慢' not in json.dumps(events,ensure_ascii=False)


def test_ref_rebinding_does_not_silently_reestimate_minutes():
    before=dict(recommended_minutes=130,min_focus_minutes=110,max_focus_minutes=150,basis='计算',assumptions=[],effort_ledger=ledger())
    after=deepcopy(before);after['recommended_minutes']=135
    with pytest.raises(FormalValidationError):assert_provenance_repair(before,after)
    after=deepcopy(before);after['effort_ledger']['adjustments']=[];after['recommended_minutes']=100
    assert assert_provenance_repair(before,after) is None


def total_exceeded_estimate():
    value=dict(recommended_minutes=150,min_focus_minutes=130,max_focus_minutes=175,
               basis='按工作和已列策略估算',assumptions=[],effort_ledger=ledger())
    value['effort_ledger']['core'][0]['minutes']=120
    value['effort_ledger']['adjustments']=[
        ledger(category,minutes,ref=ref)['adjustments'][0]
        for category,minutes,ref in [('rest_buffer',12,'bounded_rest_buffer_v1'),
            ('context_switching',10,'bounded_context_switching_v1'),
            ('uncertainty',10,'bounded_uncertainty_v1')]]
    return value


def test_total_policy_repair_can_reduce_individually_legal_row_without_relaxing_limits():
    before=total_exceeded_estimate()
    with pytest.raises(FormalValidationError):validate_ledger(before['effort_ledger'],{})
    after=deepcopy(before);after['effort_ledger']['adjustments'][2]['minutes']=8
    assert_provenance_repair(before,after)
    assert validate_ledger(after['effort_ledger'],{})==(120,30)
    still_over=deepcopy(before);still_over['effort_ledger']['adjustments'][2]['minutes']=9
    with pytest.raises(FormalValidationError):validate_ledger(still_over['effort_ledger'],{})


def test_total_policy_repair_cannot_increase_other_rows_or_change_core():
    before=total_exceeded_estimate();after=deepcopy(before)
    after['effort_ledger']['adjustments'][2]['minutes']=5
    after['effort_ledger']['adjustments'][1]['minutes']=11
    assert validate_ledger(after['effort_ledger'],{})==(120,28)
    with pytest.raises(FormalValidationError):assert_provenance_repair(before,after)
    after=deepcopy(before);after['effort_ledger']['core'][0]['minutes']=140
    with pytest.raises(FormalValidationError):assert_provenance_repair(before,after)


def test_workload_total_policy_repair_keeps_model_result_after_valid_reduction():
    calls=[]
    def caller(system,user):
        p=json.loads(user);calls.append(p)
        value=row(p,150);value.update(total_exceeded_estimate())
        if len(calls)==2:
            context=p['policy_validation_context'][0]
            assert context['policy_total_observed']==32 and context['policy_total_limit']==30
            value['effort_ledger']['adjustments'][2]['minutes']=8
        return json.dumps(dict(estimates=[value]))
    results,_=estimate_workloads([work()],caller)
    assert len(calls)==2 and results[0][2]=='model_workload'
    assert results[0][1]['recommended_minutes']==150


def test_policy_only_final_contract_has_no_private_source_receipt():
    from src.material_estimate_recovery import _action_estimate
    from src.material_estimate_contract import complete_final_estimate,validate_final_estimate
    from tests.test_material_effort_contract import draft,response
    from tests.test_material_owner_qualifier import FACTS,REQ
    obj=response('已计入10分钟切换余量，未再增加其他缓冲。')
    obj['estimate'].update(effort_ledger=ledger(),recommended_minutes=130,min_focus_minutes=110,max_focus_minutes=150)
    entry=_action_estimate(obj,draft())
    final=complete_final_estimate(entry,draft(),set(),[json.dumps(obj)],FACTS,REQ)
    assert final['effort_provenance']=={}
    assert validate_final_estimate(final)==()


@pytest.mark.parametrize('repair',[False,True])
def test_recognition_receives_same_catalog_used_by_final_validation(repair):
    from src.material_inbox import extract_material
    from src.material_effort_provenance import ACTIVE
    from src.material_estimate_contract import validate_final_estimate
    from tests.test_material_effort_contract import draft,response
    calls=[]
    def caller(system,user):
        p=json.loads(user);calls.append(p)
        ref=next(v['provenance_ref'] for v in p['verified_evidence_refs']
            if v['provenance_type']=='user_fact' and 'user_pace' in v['allowed_categories'])
        if repair and len(calls)==1:ref='user_fact_'+'f'*24
        obj=response('已计入10分钟速度调整，未增加其他缓冲。')
        obj['estimate'].update(recommended_minutes=130,min_focus_minutes=110,max_focus_minutes=150,
            effort_ledger=ledger('user_pace',10,'user_fact',ref))
        if len(calls)>1:assert p['request_stage']=='estimate_provenance_repair'
        return json.dumps(obj)
    result=extract_material(draft(),caller,default_user_context='我计算比较慢')
    assert len(calls)==(2 if repair else 1)
    entry=result.estimate_fallbacks[0]
    assert entry['origin']!='local_workload' and entry['estimate']['recommended_minutes']==130
    assert validate_final_estimate(entry)==()
    assert len(entry['effort_provenance'])==1
    assert '我计算比较慢' not in json.dumps(entry['effort_provenance'],ensure_ascii=False)
    assert ACTIVE.get() is None
