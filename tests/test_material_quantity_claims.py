"""Aggregation, not proximity to a number, determines quantity validation."""
import json

import pytest

from src.material_quantity_claims import evaluate_quantities
from src.material_quantity_facts import quantity_issues
from src.material_estimate_diagnostics import capture_estimate_diagnostics,record_quantities
from src.material_workload import estimate_workloads
from tests.test_material_quantity_facts import FACTS,work
from tests.test_workload_engine import estimate_response


def claim(entity,count,aggregation='total',parent=None,parents=None):
    return dict(entity=entity,count=count,aggregation=aggregation,scope_entity=parent,scope_count=parents)


@pytest.mark.parametrize('basis',[
    '2个区域各16条记录，共32条记录',
    '2个区域，每组16条记录，共32条记录',
    '每个区域16条记录，共32条记录',
    '每区16条记录，共32条记录',
    '2个区域各16条，共32条',
    '其中16条需要复核，共32条',
    '16条需要复核，共32条',
    '第三部分包含8条记录，共32条记录',
    '每个部分8条记录，共32条记录',
])
def test_local_or_per_group_counts_are_not_global_totals(basis):
    errors,observed,_,_=evaluate_quantities(basis,[],FACTS)
    assert not errors
    assert all(o['decision']=='accepted' for o in observed)


@pytest.mark.parametrize('basis',['共16条记录','总计16条','一共16条','全部16条记录'])
def test_explicit_incorrect_totals_are_rejected(basis):
    errors,observed,_,_=evaluate_quantities(basis,[],FACTS)
    assert errors[0]['code']=='material_quantity_mismatch'
    assert observed[0]['aggregation']=='total' and observed[0]['expected']==32


def test_unknown_scope_is_clarification_not_an_invented_total_mismatch():
    errors,observed,_,_=evaluate_quantities('处理16条记录',[],FACTS)
    assert not errors and not observed


def test_structured_attribution_preserves_minutes_and_makes_copy_explicit():
    claims=[claim('region',2),claim('record',16,'per_region','region',2),claim('record',32)]
    errors,observed,basis,_=evaluate_quantities('计算(120分钟)：处理16条记录，覆盖2个区域，共32条记录',[],FACTS,claims)
    assert not errors and '每区域16条记录' in basis
    assert '120分钟' in basis and not quantity_issues(basis,[],FACTS)
    per=next(c for c in observed if c['aggregation']=='per_region')
    assert per['scope_count']==2 and per['mismatch_reason']=='per_scope_product_matches_total'


def test_per_region_product_can_be_verified_without_independent_per_region_fact():
    facts={k:v for k,v in FACTS.items() if k!='records_per_region'}
    assert not evaluate_quantities('每区16条记录',[],facts,[claim('record',16,'per_region','region',2)])[0]
    errors=evaluate_quantities('每区8条记录',[],facts,[claim('record',8,'per_region','region',2)])[0]
    assert any(e['code']=='per_scope_total_mismatch' for e in errors)


def test_model_cannot_change_parent_count_to_make_its_product_fit():
    errors=evaluate_quantities('',[],FACTS,[claim('record',8,'per_region','region',4)])[0]
    assert errors[0]['code']=='scope_count_mismatch'


def test_subset_is_bounded_but_does_not_have_to_equal_total():
    assert not evaluate_quantities('其中8条记录',[],FACTS,[claim('record',8,'scoped_subset')])[0]
    assert evaluate_quantities('其中33条记录',[],FACTS,[claim('record',33,'scoped_subset')])[0][0]['code']=='subset_exceeds_total'


def test_per_section_is_not_compared_directly_to_record_total():
    errors,observed,_,_=evaluate_quantities('每部分8条记录',[],FACTS,[claim('record',8,'per_section','section')])
    assert not errors
    assert all(o['aggregation']=='per_section' and o['decision']=='accepted' for o in observed)


def test_structured_subset_cannot_override_an_explicit_false_total_in_basis():
    errors=evaluate_quantities('共16条记录',[],FACTS,[claim('record',16,'scoped_subset')])[0]
    assert any(e['code']=='quantity_scope_conflict' for e in errors)
    assert any(e['code']=='material_quantity_mismatch' for e in errors)


@pytest.mark.parametrize('entity,count',[('record',8),('region',7)])
def test_action_counts_cannot_become_structured_material_totals(entity,count):
    assert evaluate_quantities('',[],FACTS,[claim(entity,count)])[0][0]['code']=='material_quantity_mismatch'


@pytest.mark.parametrize('bad',[
    {'entity':'secret'},claim('record',True),claim('record',-1),claim('record',16,'made_up'),
    claim('record',16,'total','region',2),claim('record',16,'per_region','section',2),
    claim('secret-material-body',8),claim('record',16,'per_region','region','secret'),
])
def test_malformed_claims_get_fixed_safe_feedback(bad):
    errors,observed,_,_=evaluate_quantities('文字依据',[],FACTS,[bad])
    assert errors==[dict(code='invalid_quantity_claim',field='quantity_claims')]
    assert not observed


def test_safe_diagnostics_include_count_scope_decision_and_expected_fact():
    secret='Bearer PRIVATE-KEY material body'
    errors,observed,_,_=evaluate_quantities('每区16条记录，共32条记录。'+secret,[],FACTS)
    with capture_estimate_diagnostics() as events:
        record_quantities('workload_estimate',FACTS,issues=errors,claims=observed)
    encoded=json.dumps(events,ensure_ascii=False)
    assert secret not in encoded and '每区' not in encoded
    row=events[0]['quantities']['claims'][0]
    assert row['entity']=='record' and row['count']==16 and row['aggregation']=='per_region'
    assert row['scope_entity']=='region' and row['scope_count']==2 and row['decision']=='accepted'
    assert row['expected_material_fact']=='records_per_region' and row['expected']==16


def test_unattributed_prose_does_not_create_a_material_claim_or_repair():
    calls=[]
    def caller(system,user):
        p=json.loads(user);calls.append(p)
        obj=json.loads(estimate_response(p,120))
        obj['estimates'][0].update(min_focus_minutes=105,max_focus_minutes=145,
            basis='计算(110分钟)：处理16条记录')
        return json.dumps(obj)
    with capture_estimate_diagnostics() as events:
        results,_=estimate_workloads((work(),),caller,material_facts=FACTS)
    assert len(calls)==1 and results[0][2]=='model_workload'
    assert results[0][1]['recommended_minutes']==120
    assert not any(e['code']=='repair_requested' for e in events)


def test_typed_normal_response_uses_one_call_with_unchanged_work_id():
    calls=[]
    def caller(system,user):
        p=json.loads(user);calls.append(p)
        assert 'quantity_claims' not in p['output']['estimates'][0]
        obj=json.loads(estimate_response(p,120))
        obj['estimates'][0].update(basis='计算(110分钟)：2个区域各16条记录，共32条记录',
            quantity_claims=[claim('region',2),claim('record',16,'per_region','region',2),claim('record',32)])
        return json.dumps(obj)
    result,_=estimate_workloads((work(),),caller,material_facts=FACTS)
    assert len(calls)==1 and result[0][2]=='model_workload' and result[0][1]['work_id']==work().work_id
