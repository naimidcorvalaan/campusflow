"""Owners and qualifier strength must survive source -> model -> final copy."""
import json
import pytest

from src.material_quantity_facts import source_quantity_facts,result_quantity_issues
from src.material_requirements import source_requirements,evaluate_estimate_facts,requirement_issues
from src.material_quantity_semantics import semantic_value,valid_claim
from src.material_estimate_diagnostics import capture_estimate_diagnostics
from src.material_workload import source_workload,estimate_workloads
from tests.test_workload_engine import estimate_response

SOURCE='实验作业\n报告包含六至八页正文。第一部分检查数据。第二部分撰写讨论。结果讨论约四百字。提交检查。'
FACTS=source_quantity_facts('',12)
REQ=source_requirements(SOURCE)
CLAIMS=[semantic_value('source_document','page_count','exact',12,12),
    semantic_value('deliverable_body','page_count','range',6,8),
    semantic_value('subtask_discussion','word_count','approximately',400,400)]
GOOD='材料本身共12页；需完成6～8页正文，其中讨论部分约400字。'


def test_three_domains_coexist_in_structured_and_visible_copy():
    assert FACTS['page_count']['owner']=='source_document'
    assert {r['qualifier'] for r in REQ}=={'range','approximately'}
    errors,_,text,_=evaluate_estimate_facts(GOOD,[],FACTS,CLAIMS,REQ)
    assert not errors and text==GOOD
    text='正文页数为6～8页，讨论字数约400字，材料共12页。'
    assert not evaluate_estimate_facts(text,[],FACTS,CLAIMS,REQ)[0]


@pytest.mark.parametrize('wording',['约400字','大约400字','400字左右','400字上下'])
def test_approximate_equivalents_are_preserved(wording):
    text=wording+'讨论'
    assert source_requirements(text)[0]['qualifier']=='approximately'
    assert not requirement_issues(text,[],REQ)


PAIRS=[('400字','exact'),('300～500字','range'),('约400字','approximately'),
       ('至少400字','minimum'),('不超过400字','maximum')]
@pytest.mark.parametrize('source_text,source_q',PAIRS)
@pytest.mark.parametrize('copy_text,copy_q',PAIRS)
def test_value_semantics_cannot_silently_change(source_text,source_q,copy_text,copy_q):
    req=source_requirements(source_text+'讨论')
    assert req[0]['qualifier']==source_q
    errors=requirement_issues(copy_text+'讨论',[],req)
    assert bool(errors)==(copy_q!=source_q)


@pytest.mark.parametrize('wording',['至少400字','不少于400字','必须达到400字','必须400字','不超过400字'])
def test_approximately_is_not_a_minimum_maximum_or_exact_obligation(wording):
    assert requirement_issues('讨论部分'+wording,[],REQ)


def test_derived_midpoint_can_be_internal_but_never_replaces_original_range():
    derived=semantic_value('deliverable_body','page_count','exact',7,7,'derived_estimate')
    assert not evaluate_estimate_facts(GOOD,[],FACTS,CLAIMS+[derived],REQ)[0]
    text='按7页正文估算工作量，原要求正文6～8页，讨论约400字。'
    assert not evaluate_estimate_facts(text,[],FACTS,CLAIMS+[derived],REQ)[0]
    for bad in ['正文7页','完成共7页正文','正文约7页','正文页数为7页']:
        assert evaluate_estimate_facts(bad,[],FACTS,CLAIMS+[derived],REQ)[0]
    bad=dict(derived,source_type='original_fact')
    assert evaluate_estimate_facts(GOOD,[],FACTS,CLAIMS+[bad],REQ)[0]


@pytest.mark.parametrize('bad',['材料共7页','约7页材料','源文件7页','材料共12页；材料共7页'])
def test_true_source_conflict_is_not_hidden_by_deliverable_claims(bad):
    assert evaluate_estimate_facts(bad,[],FACTS,CLAIMS,REQ)[0]
    claims=[dict(CLAIMS[0],minimum=7,maximum=7),*CLAIMS[1:]]
    assert any(e['code']=='material_quantity_mismatch' for e in evaluate_estimate_facts(GOOD,[],FACTS,claims,REQ)[0])


def test_source_derived_and_action_counts_cannot_masquerade_as_original_fact():
    for source_type in ('derived_estimate','workload_feature'):
        bad=dict(CLAIMS[0],source_type=source_type)
        assert evaluate_estimate_facts(GOOD,[],FACTS,[bad],REQ)[0]
    wrong_owner=semantic_value('material_record','page_count','exact',12,12)
    assert not valid_claim(wrong_owner)


def test_material_aggregation_uses_same_formal_validator_with_typed_owners():
    facts=source_quantity_facts('材料有2个区域，共32条记录。')
    claims=[semantic_value('material_region','region_count','exact',2,2),
        dict(semantic_value('material_record','record_count','exact',16,16),
             aggregation='per_region',scope_entity='region',scope_count=2)]
    assert not evaluate_estimate_facts('2个区域各16条记录',[],facts,claims)[0]
    bad=[claims[0],dict(claims[1],minimum=7,maximum=7)]
    assert evaluate_estimate_facts('2个区域各7条记录',[],facts,bad)[0]


def test_safe_snapshot_includes_owner_metric_qualifier_not_source_prose():
    secret='Bearer SECRET_PDF_TEXT_DO_NOT_LOG'
    with capture_estimate_diagnostics() as events:
        evaluate_estimate_facts(GOOD+secret,[],FACTS,CLAIMS,REQ)
    event=next(e['semantic_quantities'] for e in events if 'semantic_quantities' in e)
    assert {(r['owner'],r['qualifier']) for r in event['source_facts']}=={
        ('source_document','exact'),('deliverable_body','range'),('subtask_discussion','approximately')}
    assert all(c['decision']=='accepted' for c in event['claims'])
    assert secret not in json.dumps(events) and SOURCE not in json.dumps(events,ensure_ascii=False)


@pytest.mark.parametrize('success',[True,False])
def test_one_fact_repair_keeps_minutes_and_still_checks_final_copy(success):
    work=source_workload(SOURCE)
    calls=[]
    def caller(system,user):
        payload=json.loads(user);calls.append(payload)
        obj=json.loads(estimate_response(payload,120))
        basis='处理(120分钟)：材料8页；正文6～8页；讨论约400字'
        if len(calls)==2:
            assert 'material_quantity_mismatch' in json.dumps(payload['validation_feedback'])
            if success:basis='处理(120分钟)：'+GOOD
        obj['estimates'][0].update(basis=basis,assumptions=[],quantity_claims=CLAIMS)
        return json.dumps(obj,ensure_ascii=False)
    result,_=estimate_workloads(work,caller,material_facts=FACTS,material_requirements=REQ)
    assert len(calls)==2
    if success:
        assert result[0][2]=='model_workload' and result[0][1]['recommended_minutes']==120
        assert not evaluate_estimate_facts(result[0][1]['basis'],[],FACTS,CLAIMS,REQ)[0]
    else:assert result[0][2]=='local_workload'


@pytest.mark.parametrize('field,bad',[('owner',{}),('metric',[]),('qualifier',[]),('source_type',{}),('minimum',True)])
def test_bad_typed_schema_is_rejected_safely(field,bad):
    row=dict(CLAIMS[0],**{field:bad})
    assert not valid_claim(row)
    assert evaluate_estimate_facts('',[],FACTS,[row],REQ)[0]


@pytest.mark.parametrize('supplement',['','第一部分已完成，只做第二部分及提交检查'])
def test_typed_claims_survive_formal_file_final_check_and_manual_minutes(supplement):
    from dataclasses import replace
    from src.file_material import read_file_material,DOCX_MIME
    from tests.document_fixtures import docx_bytes
    from src.material_inbox import MaterialInbox,update_file_source,extract_material,item_values
    from src.material_estimate_recovery import confirm_minimal_task
    from tests.test_material_estimate_recovery import NOW
    from src.material_scope import numbered_scope
    source=replace(read_file_material('test.docx',DOCX_MIME,docx_bytes(SOURCE)),text=SOURCE,page_count=12)
    draft=update_file_source(MaterialInbox(),source,'',NOW,'plan','beiyangyuan',supplemental_context=supplement).draft
    calls=[]
    def main(system,user):
        calls.append('recognition');p=json.loads(user)
        assert p['material']==SOURCE and p['material_requirements']==REQ
        return '{}'
    def effort(system,user):
        calls.append('estimate');p=json.loads(user)
        assert p['workloads'][0]['scope']==numbered_scope(SOURCE,supplement).scope
        obj=json.loads(estimate_response(p,120))
        obj['estimates'][0].update(basis='处理(120分钟)：'+GOOD,assumptions=[],quantity_claims=CLAIMS)
        return json.dumps(obj,ensure_ascii=False)
    result=extract_material(draft,main,file_source=source,workload_caller=effort)
    entry=result.estimate_fallbacks[0]
    assert entry['origin']=='model_workload' and entry['quantity_claims']==CLAIMS
    assert calls==['recognition','estimate']
    assert not result_quantity_issues(result,FACTS,REQ)
    bad=dict(entry,estimate=dict(entry['estimate'],rationale='正文7页，讨论至少400字'))
    assert result_quantity_issues(replace(result,estimate_fallbacks=(bad,)),FACTS,REQ)
    prepared=confirm_minimal_task(result,entry['item_id'],'实验任务',127,True,entry['estimate']['short_scope'])
    assert item_values(prepared.items[0],prepared)['minutes']=='127'
