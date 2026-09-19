"""Material entity totals cannot be invented from estimation action units."""
import json

import pytest

from src.material_quantity_facts import source_quantity_facts,quantity_issues
from src.material_workload import Workload,Feature,estimate_workloads,rough_estimate
from src.material_estimate_diagnostics import capture_estimate_diagnostics,record_quantities
from tests.test_workload_engine import estimate_response


SOURCE='比较两个区域的使用时长，各有十六条记录，转换后总行数为三十二。'
FACTS=source_quantity_facts(SOURCE)


def work():
    return Workload('完成数据作业','计算、讨论及提交检查',(
        Feature('review',8,'检查数据和计算结果'),Feature('calculation',7,'计算统计量'),
        Feature('long_text',3,'撰写讨论'),Feature('chart',1,'绘制图表')))


def test_facts_are_distinct_from_action_counts_and_keep_their_source():
    context=work().estimation_context()
    assert FACTS['region_count']['value']==2 and FACTS['record_count']['value']==32
    assert FACTS['records_per_region']['value']==16
    assert context['workload_action_counts']==dict(review_actions=8,calculation_actions=7,long_text_actions=3,chart_actions=1)
    assert all('units' not in f and 'action_count' in f for f in context['features'])
    assert FACTS['record_count']['source']=='explicit_source_total'


@pytest.mark.parametrize('text,key,value',[
    ('材料有四个区域，共128条记录。','record_count',128),
    ('比较三个区域。','region_count',3),
    ('共三十二条记录。','record_count',32),
    ('包含6个任务部分与3张表格。','section_count',6),
    ('包含6个任务部分与3张表格。','table_count',3),
    ('比较3组数据。','group_count',3),
])
def test_general_explicit_source_counts(text,key,value):
    assert source_quantity_facts(text)[key]['value']==value


def test_per_group_counts_and_hypotheses_do_not_become_totals():
    facts=source_quantity_facts('每个区域16条记录。假设有7个区域。建议增加8个区域。')
    assert 'record_count' not in facts and 'region_count' not in facts
    assert facts['records_per_region']['value']==16
    assert 'record_count' not in source_quantity_facts('共32条记录。总计40条记录。')


@pytest.mark.parametrize('basis',[
    '处理32条记录，比较2个区域。','处理三十二条记录，比较两个区域。',
    '多个区域的多条记录需要复核。','进行8个复核动作与7个计算动作。',
    '每个区域16条记录，共32条记录。','抽查8条记录，再核对全部32条记录。',
])
def test_grounded_quantities_approximation_actions_and_explicit_subsets_pass(basis):
    assert quantity_issues(basis,[],FACTS)==[]


@pytest.mark.parametrize('basis,key,claimed,expected',[
    ('共8条记录','record_count',8,32),('共7个区域','region_count',7,2),
    ('共八条记录','record_count',8,32),('共七个区域','region_count',7,2),
    ('每个区域32条记录','records_per_region',32,16),
    ('材料有3页','page_count',3,4),
])
def test_known_quantity_conflicts_are_exact_and_safe(basis,key,claimed,expected):
    facts=dict(FACTS,**source_quantity_facts('',4))
    issue=quantity_issues(basis,[],facts)[0]
    assert {k:issue[k] for k in ('code','field','entity','claimed','expected')}==dict(
        code='material_quantity_mismatch',field='basis',entity=key,claimed=claimed,expected=expected)


def test_unknown_count_allows_nonprecise_but_not_action_derived_entity_count():
    assert quantity_issues('处理多条记录和多个区域',[],{})==[]
    assert quantity_issues('处理8条记录',[],{})==[]
    assert quantity_issues('计算', ['需要处理7个区域'],{})==[]


@pytest.mark.parametrize('bad',['共8条记录','共7个区域','共8条记录，共7个区域'])
def test_shared_bounded_repair_changes_only_fact_basis_without_forcing_minutes(bad):
    calls=[]
    def caller(system,user):
        payload=json.loads(user);calls.append(payload)
        value=json.loads(estimate_response(payload,150))
        row=value['estimates'][0]
        row.update(min_focus_minutes=120,max_focus_minutes=180,
            basis='核心工作(130分钟)：'+(bad if len(calls)==1 else '处理32条记录，比较2个区域'))
        assert payload['material_facts']==FACTS
        assert payload['workloads'][0]['workload_action_counts']['review_actions']==8
        if len(calls)==2:
            assert '保持原值' in system
            assert payload['request_stage']=='estimate_protocol_repair'
            assert payload['validation_feedback'][0]['quantity_feedback']
        return json.dumps(value)
    with capture_estimate_diagnostics() as events:
        results,_=estimate_workloads((work(),),caller,material_facts=FACTS)
    assert len(calls)==2 and results[0][2]=='model_workload'
    assert results[0][1]['recommended_minutes']==150
    assert results[0][1]['min_focus_minutes']==120 and results[0][1]['max_focus_minutes']==180
    assert sum(e['code']=='repair_requested' for e in events)==1
    assert not any(e['fallback'] for e in events)


def test_fact_repair_still_failing_uses_existing_fallback_once():
    calls=[]
    def caller(system,user):
        payload=json.loads(user);calls.append(payload)
        value=json.loads(estimate_response(payload,150))
        value['estimates'][0]['basis']='核心工作(130分钟)：共8条记录'
        return json.dumps(value)
    result,_=estimate_workloads((work(),),caller,material_facts=FACTS)
    assert len(calls)==2 and result[0][2]=='local_workload'
    assert result[0][1]==rough_estimate(work())


def test_quantity_and_arithmetic_errors_share_one_repair_not_two():
    calls=[]
    def caller(system,user):
        p=json.loads(user);calls.append(p)
        obj=json.loads(estimate_response(p,135));row=obj['estimates'][0]
        row['basis']='核心工作(45分钟)：共8条记录' if len(calls)==1 else '核心工作(130分钟)：处理32条记录'
        return json.dumps(obj)
    result,_=estimate_workloads((work(),),caller,material_facts=FACTS)
    assert len(calls)==2 and result[0][2]=='model_workload'
    feedback=calls[1]['validation_feedback'][0]
    assert feedback['unexplained_gap_minutes']==90 and feedback['quantity_feedback']


def test_opt_in_quantity_diagnostics_never_include_source_text_or_unknown_labels():
    secret='Bearer secret-material-contents'
    with capture_estimate_diagnostics() as events:
        record_quantities('workload_estimate',dict(FACTS,**{secret:dict(value=8,source=secret)}),
            [dict(review_actions=8,**{secret:100})],quantity_issues('共8条记录'+secret,[],FACTS))
    encoded=json.dumps(events,ensure_ascii=False)
    assert secret not in encoded and SOURCE not in encoded
    quantity_event=next(e for e in events if 'quantities' in e)
    assert {k:v['value'] for k,v in quantity_event['quantities']['material_facts'].items()}=={k:v['value'] for k,v in FACTS.items()}
    assert quantity_event['quantities']['validation_errors'][0]['claimed']==8


def test_real_material_path_has_grounded_context_without_changing_scope_or_adoption():
    from src.material_inbox import MaterialInbox,update_source,extract_material,item_values
    from src.material_estimate_recovery import confirm_minimal_task
    from tests.test_material_estimate_recovery import NOW
    text=SOURCE+'\n第一部分计算均值。\n第二部分撰写讨论。\n提交检查。'
    draft=update_source(MaterialInbox(),text,'',NOW,'plan','beiyangyuan').draft
    recognition=[]
    def main(system,user):
        p=json.loads(user);recognition.append(p)
        assert p['material_facts']['record_count']['value']==32
        return '{}'
    def effort(system,user):
        p=json.loads(user)
        assert p['material_facts']['region_count']['value']==2
        obj=json.loads(estimate_response(p,150))
        obj['estimates'][0]['basis']='核心工作(130分钟)：处理32条记录，比较2个区域'
        return json.dumps(obj)
    result=extract_material(draft,main,workload_caller=effort)
    entry=result.estimate_fallbacks[0]
    assert entry['origin']=='model_workload' and len(recognition)==1
    assert '第一部分' in entry['estimate']['short_scope'] and '第二部分' in entry['estimate']['short_scope']
    prepared=confirm_minimal_task(result,entry['item_id'],'完成作业',77,True,entry['estimate']['short_scope'])
    assert item_values(prepared.items[0],prepared)['minutes']=='77'


def test_direct_recognition_repair_does_not_restore_older_bad_basis():
    from src.material_inbox import MaterialInbox,update_source,extract_material
    from tests.test_material_estimate_recovery import NOW,action_payload
    text=SOURCE+'\n分析数据。'
    draft=update_source(MaterialInbox(),text,'',NOW,'plan','beiyangyuan').draft
    calls=[]
    def caller(system,user):
        calls.append(user)
        obj=action_payload()
        obj['actionability'].update(task_name='分析数据',short_scope='分析数据',
            evidence='分析数据',reason='需要分析数据',waiting_note='')
        obj['estimate'].update(basis='共8条记录' if len(calls)==1 else '处理32条记录',
            assumptions=[])
        return json.dumps(obj)
    result=extract_material(draft,caller)
    assert len(calls)==2 and result.model_calls==2
    value=result.estimate_fallbacks[0]['estimate']
    assert value['rationale']=='处理32条记录' and value['recommended_minutes']==20
