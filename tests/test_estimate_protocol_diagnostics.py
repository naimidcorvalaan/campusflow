"""Actual estimate parser/repair boundaries, safe evidence, unchanged adoption."""
import json

import pytest

from src.estimate_arithmetic import check_estimate_arithmetic
from src.material_estimate_diagnostics import capture_estimate_diagnostics,EstimateTrace
from src.material_workload import estimate_workloads,source_workload,rough_estimate
from tests.test_workload_engine import estimate_response


def work():
    return source_workload('申请表\n姓名 | （空白）\n自我评价（300字） | （空白）')[0]


def row(request,minutes=45):
    value=json.loads(estimate_response(request,minutes))['estimates'][0]
    value['basis']='填写(20分钟)；写作(20分钟)；提交(5分钟)'
    return value


def snapshots(events):return [e['snapshot'] for e in events if 'snapshot' in e]


@pytest.mark.parametrize('identical',[True,False])
def test_duplicate_top_level_work_id_is_observed_and_repaired_without_merging(identical):
    workload=work();calls=[]
    def caller(system,user):
        request=json.loads(user);calls.append(request)
        value=row(request)
        if len(calls)==1:
            other=dict(value) if identical else dict(value,recommended_minutes=70,min_focus_minutes=60,
                max_focus_minutes=80,basis='填写(30分钟)；写作(35分钟)；提交(5分钟)')
            return json.dumps(dict(estimates=[value,other]))
        assert request['request_stage']=='estimate_protocol_repair'
        assert request['validation_feedback'][0]['occurrences']==2
        assert request['validation_feedback'][0]['code']=='duplicate_work_id'
        assert len(request['previous_estimates'])==2
        assert request['workloads']==calls[0]['workloads']
        assert '恰好对应一个' in system and 'basis' in system
        return json.dumps(dict(estimates=[value]))
    with capture_estimate_diagnostics() as events:
        result,_=estimate_workloads((workload,),caller)
    assert len(calls)==2 and result[0][2]=='model_workload'
    assert result[0][1]['recommended_minutes']==45
    first,second=snapshots(events)
    assert first['work_id_counts']=={workload.work_id:2}
    assert second['work_id_counts']=={workload.work_id:1}
    assert second['repair_diff']['object_count_before']==2
    assert second['repair_diff']['object_count_after']==1
    assert second['repair_diff']['error_codes_before']==['duplicate_work_id']
    assert second['repair_diff']['error_codes_after']==[]
    assert not second['fallback']


def test_one_work_object_contains_several_basis_breakdown_items_and_buffer():
    calls=[]
    def caller(system,user):
        request=json.loads(user);calls.append(request)
        assert 'breakdown' not in request['output']['estimates'][0]
        value=row(request,75);value['assumptions']=['另预留30分钟查资料和复核']
        return json.dumps(dict(estimates=[value]))
    with capture_estimate_diagnostics() as events:
        result,_=estimate_workloads((work(),),caller)
    assert len(calls)==1 and result[0][2]=='model_workload'
    arithmetic=snapshots(events)[0]['estimates'][0]['arithmetic']
    assert len(arithmetic['breakdown'])==4 and arithmetic['valid']
    assert arithmetic['breakdown_sum_min']==arithmetic['explained_max']==75
    assert arithmetic['unexplained_gap_minutes']==0


@pytest.mark.parametrize('repair_good',[True,False])
def test_exact_arithmetic_gap_is_diagnosable_and_gets_only_one_repair(repair_good):
    calls=[];workload=work()
    def caller(system,user):
        request=json.loads(user);calls.append(request)
        value=row(request,135)
        if len(calls)==2:
            feedback=request['validation_feedback'][0]
            assert feedback['recommended_minutes']==135
            assert feedback['explained_min']==feedback['explained_max']==45
            assert feedback['unexplained_gap_minutes']==90
            assert feedback['field']=='basis'
            assert request['workloads']==calls[0]['workloads']
            if repair_good:value['assumptions']=['另预留90分钟查资料和返工']
        return json.dumps(dict(estimates=[value]))
    with capture_estimate_diagnostics() as events:
        result,_=estimate_workloads((workload,),caller)
    assert len(calls)==2
    first,second=snapshots(events)
    bad=first['estimates'][0]
    assert bad['suggested_minutes']==135 and bad['duration_min']==130 and bad['duration_max']==145
    assert bad['arithmetic']['breakdown_sum_min']==45
    assert [p['min_minutes'] for p in bad['arithmetic']['breakdown']]==[20,20,5]
    assert first['validator_errors']==[dict(code='unexplained_time_gap',field='basis',index=0)]
    assert second['attempt_kind']=='repair'
    assert second['fallback'] is not repair_good
    if repair_good:
        assert second['estimates'][0]['arithmetic']['explained_max']==135
        assert 'arithmetic' in second['repair_diff']['changed_fields']
        assert result[0][2]=='model_workload'
    else:
        assert result[0][1]==rough_estimate(workload) and result[0][2]=='local_workload'
        assert events[-1]['fallback_reason']=='unexplained_time_gap'


def test_invalid_duplicate_row_cannot_make_first_row_look_unique():
    calls=[];workload=work()
    def caller(system,user):
        request=json.loads(user);calls.append(request)
        value=row(request)
        return json.dumps(dict(estimates=[value,dict(value,basis=None)]))
    with capture_estimate_diagnostics() as events:
        result,_=estimate_workloads((workload,),caller)
    assert len(calls)==2 and result[0][2]=='local_workload'
    assert all(s['work_id_counts']=={workload.work_id:2} for s in snapshots(events))
    assert events[-1]['fallback_reason']=='duplicate_work_id'


def test_duplicate_repair_and_arithmetic_repair_share_one_slot():
    calls=[]
    def caller(system,user):
        request=json.loads(user);calls.append(request)
        value=row(request,45 if len(calls)==1 else 135)
        return json.dumps(dict(estimates=[value,value] if len(calls)==1 else [value]))
    with capture_estimate_diagnostics() as events:
        result,_=estimate_workloads((work(),),caller)
    assert len(calls)==2 and result[0][2]=='local_workload'
    assert sum(e['code']=='repair_requested' for e in events)==1
    assert snapshots(events)[-1]['validator_errors'][0]['code']=='unexplained_time_gap'


@pytest.mark.parametrize('basis,assumptions,expected',[
    ('区间估计20分钟，比较检验20分钟，讨论15分钟，提交5分钟',[],60),
    ('计算(40分钟)；复核(10分钟，已计入计算)；提交(5分钟)',[],45),
    ('计算(40分钟)；其中复核10分钟；提交5分钟',[],45),
    ('计算(20–30分钟)；撰写(15–20分钟)',['另预留10分钟检查'],50),
])
def test_equivalent_minute_notations_are_not_false_gaps(basis,assumptions,expected):
    check=check_estimate_arithmetic(expected,expected-10,expected+15,basis,assumptions)
    assert check.valid and check.applicable


def test_snapshot_shows_included_minutes_without_adding_them_twice():
    value=dict(recommended_minutes=45,min_focus_minutes=40,max_focus_minutes=60,
        basis='计算(40分钟)；复核(10分钟，已计入计算)；提交(5分钟)',assumptions=[])
    with capture_estimate_diagnostics() as events:
        EstimateTrace('workload_estimate').response(json.dumps(dict(estimates=[value])))
    arithmetic=snapshots(events)[0]['estimates'][0]['arithmetic']
    assert arithmetic['explained_max']==45
    mention=next(m for m in arithmetic['minute_mentions'] if m['min_minutes']==10)
    assert mention['explicitly_included'] and not mention['selected_by_parser']


@pytest.mark.parametrize('mode',['bad_json','unknown_id','unknown_field','free_text','wrong_type','unexpected_breakdown'])
def test_rejected_snapshots_never_contain_credentials_or_sensitive_text(mode):
    secrets=['PRIVATE_API_KEY_SENTINEL','Authorization: Bearer PRIVATE_AUTH_SENTINEL',
        'PRIVATE_WORD_BODY_SENTINEL'*50,'PRIVATE_REASONING_SENTINEL']
    def caller(system,user):
        value=row(json.loads(user))
        if mode=='bad_json':return '\n'.join(secrets)
        if mode=='unknown_id':value['work_id']='\n'.join(secrets)
        if mode=='unknown_field':value['unexpected_metadata']=secrets
        if mode=='free_text':
            value['basis']=secrets[0]+'(20分钟)；复核(5分钟)'
            value['assumptions']=secrets[1:]
        if mode=='wrong_type':value['recommended_minutes']=secrets[0]
        if mode=='unexpected_breakdown':
            value['breakdown']=[dict(label=secrets[0],min_minutes=10,max_minutes=20,suggested_minutes=15)]
            value['setup_minutes']=5
        return json.dumps(dict(estimates=[value]))
    with capture_estimate_diagnostics() as events:
        result,_=estimate_workloads((work(),),caller)
    serialized=json.dumps(events,ensure_ascii=False)
    assert all(secret not in serialized for secret in secrets)
    assert result[0][2]=='local_workload'
    snapshot=snapshots(events)[0]
    assert snapshot['validator_errors']
    if mode=='unknown_id':assert snapshot['work_id_counts']=={'unknown_1':1}
    if mode=='wrong_type':
        assert snapshot['estimates'][0]['field_types']['recommended_minutes']=='string'
        assert snapshot['estimates'][0]['suggested_minutes'] is None
    if mode=='unexpected_breakdown':
        observed=snapshot['estimates'][0]
        assert observed['unexpected_breakdown'][0]['suggested_minutes']==15
        assert observed['explicit_extra_minutes']['setup_minutes']==5


def test_snapshots_are_opt_in_and_not_part_of_estimate_or_user_draft():
    trace=EstimateTrace('workload_estimate')
    trace.response('PRIVATE_SENTINEL')
    assert trace.previous is None
    with capture_estimate_diagnostics() as events:
        result,_=estimate_workloads((work(),),lambda s,u:json.dumps(dict(estimates=[row(json.loads(u))])))
    assert snapshots(events)
    assert set(result[0][1])=={'work_id','min_focus_minutes','max_focus_minutes','recommended_minutes','basis','assumptions','quantity_claims'}
    with capture_estimate_diagnostics() as fresh:pass
    assert fresh==[]


def test_formal_material_pipeline_counts_protocol_repair_and_keeps_scope():
    from src.material_inbox import MaterialInbox,update_source,extract_material
    from tests.test_material_numbered_work import MATERIAL,NOW
    draft=update_source(MaterialInbox(),MATERIAL,'',NOW,'plan','beiyangyuan','第3至第5部分及提交检查').draft
    calls=[]
    def effort(system,user):
        request=json.loads(user);calls.append(request)
        value=row(request)
        return json.dumps(dict(estimates=[value,value] if len(calls)==1 else [value]))
    with capture_estimate_diagnostics() as events:
        result=extract_material(draft,lambda *a:'{}',workload_caller=effort)
    assert result.model_calls==3 and result.diagnostics['estimate_repair_calls']==1
    value=result.estimate_fallbacks[0]
    assert value['origin']=='model_workload'
    assert all('第'+n+'部分' in value['estimate']['short_scope'] for n in ('三','四','五'))
    assert '提交检查' in value['estimate']['short_scope']
    assert 'snapshot' not in json.dumps(result.diagnostics)
