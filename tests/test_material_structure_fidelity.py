"""Source structure and requirement ownership survive formal estimation."""
import json
import pytest

from src.material_scope import numbered_scope
from src.material_structure import source_title, display_name
from src.material_requirements import source_requirements, requirement_issues, evaluate_estimate_facts
from src.material_workload import source_workload, estimate_workloads
from src.material_estimate_diagnostics import capture_estimate_diagnostics
from tests.test_workload_engine import estimate_response


@pytest.mark.parametrize('body',[
    '第一部分数据检查。\n第二部分计算误差。',
    '实验包括以下内容：第一部分数据检查；第二部分计算误差。',
    '数据包已提供。第一部分数据检查。第二部分计算误差。',
    '1. 数据检查\n2. 计算误差',
    '需要完成：1. 数据检查；2. 计算误差',
    '（一）数据检查\n（二）计算误差',
    '需要完成：（一）数据检查；（二）计算误差',
    '第\n一部\n分数据检查。\n第二部分计算误差。',
    '第一部分\n数据检查。\n第二部分\n计算误差。',
    '第一部分至少报告误差。\n第二部分至多撰写两种方法的讨论。',
])
def test_task_section_markers_are_not_limited_to_line_start(body):
    boundary=numbered_scope('实验任务说明\n'+body)
    assert boundary and [n for n,_ in boundary.sections]==[1,2]
    if '数据检查' in body:assert '数据检查' in boundary.scope and '计算误差' in boundary.scope


@pytest.mark.parametrize('body',[
    '第一，实验结果有误差；第二，图表用于说明。',
    '第一名完成了实验，第二名尚未完成。',
    '请阅读第一部分的数据说明和第二部分的参考报告。',
    '第一部分的说明仅供参考；第二部分中讨论了背景。',
    '数据系数为1.25，误差系数为2.75。',
])
def test_ordinary_ordinals_references_and_decimals_are_not_task_headings(body):
    assert numbered_scope('参考说明\n'+body) is None


@pytest.mark.parametrize('marker',['[第1页]','[第2页]','page 1','Page 2 of 12'])
def test_page_locators_are_not_titles_or_scope(marker):
    text=marker+'\n[实验A]任务说明\n第一部分计算误差。\n'+marker+'\n第二部分撰写讨论。'
    boundary=numbered_scope(text)
    assert boundary.title=='[实验A]任务说明'
    assert marker not in boundary.scope
    assert display_name(marker,text)=='[实验A]任务说明'


def test_page_break_inside_a_document_heading_preserves_title():
    assert source_title('[第1页]\n数字处理实验\n[第2页]\n指导书\n第一部分计算误差')=='数字处理实验指导书'
    assert source_title('[真实标题]\n任务说明')=='[真实标题]'


SOURCE='实验任务说明\n报告包含六至八页正文。\n第一部分数据检查。\n第二部分撰写讨论。结果讨论约四百字。提交前检查报告。'
REQUIREMENTS=source_requirements(SOURCE)
FACTS={'page_count':dict(value=12,unit='page_count',source='file_metadata')}


def test_explicit_requirements_keep_owner_scope_unit_and_range():
    assert {(r['subject'],r['scope'],r['minimum'],r['maximum'],r['unit']) for r in REQUIREMENTS}=={
        ('report_body','overall_deliverable',6,8,'pages'),('discussion','subtask',400,400,'words')}
    assert source_requirements('任务要求\n约400字。')==[]
    assert source_requirements('例如报告需要800字。')==[]
    assert source_requirements('结论\n4 幅值保留：检查主要成分。')==[]


@pytest.mark.parametrize('text',[
    '包含约400字讨论，并需完成6～8页正文。',
    '报告包含约400字讨论，正文要求6至8页。',
    '撰写讨论约400字；报告正文6—8页。',
    '报告（含约400字讨论）需要6至8页正文。',
])
def test_correct_subtask_and_deliverable_lengths_do_not_conflict_with_input_pages(text):
    errors,_,rendered,_=evaluate_estimate_facts(text,[],FACTS,requirements=REQUIREMENTS)
    assert not errors and rendered==text


@pytest.mark.parametrize('text',['完成400字长文本报告','整个报告约400字','撰写400字报告'])
def test_local_word_length_cannot_be_promoted_to_report(text):
    issues=requirement_issues(text,[],REQUIREMENTS)
    assert issues[0]['code']=='requirement_scope_mismatch'
    assert issues[0]['subject']=='report_body'


def test_wrong_page_length_and_input_page_count_are_still_rejected():
    assert requirement_issues('完成2页正文',[],REQUIREMENTS)[0]['code'] in ('requirement_value_mismatch','requirement_qualifier_mismatch')
    errors=evaluate_estimate_facts('材料总共1页，完成6至8页正文',[],FACTS,requirements=REQUIREMENTS)[0]
    assert any(e['code']=='material_quantity_mismatch' for e in errors)


def test_requirement_routing_does_not_expand_copy_or_confuse_file_pages():
    text='完成6至8页正文。'+('检查。'*127)
    assert len(text)<=400
    errors,_,rendered,_=evaluate_estimate_facts(text,[],FACTS,requirements=REQUIREMENTS)
    assert not errors and rendered==text
    text='\ue000包含400字讨论，完成6至8页正文。'
    assert evaluate_estimate_facts(text,[],FACTS,requirements=REQUIREMENTS)[2]==text


def test_word_requirements_for_different_writing_subjects_are_not_interchangeable():
    req=source_requirements('申请书800字；摘要200字。')
    assert not requirement_issues('完成800字申请书，包含200字摘要',[],req)
    assert requirement_issues('完成200字申请书',[],req)[0]['code']=='requirement_value_mismatch'
    req=source_requirements('论文1200至1500字；摘要300字。')
    assert req[0]['minimum']==1200 and req[0]['maximum']==1500
    assert not requirement_issues('论文1200至1500字，摘要300字',[],req)
    assert requirement_issues('论文1500字，摘要300字',[],req)[0]['code']=='requirement_qualifier_mismatch'


@pytest.mark.parametrize('repair_ok',[True,False])
def test_wrong_requirement_gets_one_bounded_repair_without_changing_minutes(repair_ok):
    works=source_workload(SOURCE)
    calls=[]
    def caller(system,user):
        payload=json.loads(user);calls.append(payload)
        assert payload['material_requirements']==REQUIREMENTS
        obj=json.loads(estimate_response(payload,120))
        row=obj['estimates'][0]
        row.update(min_focus_minutes=105,max_focus_minutes=145,
            basis='分析(100分钟)；写作(20分钟)：完成400字报告',assumptions=[])
        if len(calls)==2:
            assert 'requirement_scope_mismatch' in json.dumps(payload['validation_feedback'])
            assert '保持不变' in system
            if repair_ok:row['basis']='分析(100分钟)；写作(20分钟)：包含约400字讨论，并完成6至8页正文'
        return json.dumps(obj,ensure_ascii=False)
    with capture_estimate_diagnostics() as events:
        results,_=estimate_workloads(works,caller,material_facts=FACTS,material_requirements=REQUIREMENTS)
    assert len(calls)==2
    if repair_ok:
        assert results[0][2]=='model_workload' and results[0][1]['recommended_minutes']==120
    else:assert results[0][2]=='local_workload'
    assert 'requirement_scope_mismatch' in {e['code'] for e in events}
    assert SOURCE not in json.dumps(events,ensure_ascii=False)


@pytest.mark.parametrize('supplement,expected',[('',[1,2]),('第一部分已完成，只需要第二部分及提交检查',[2])])
def test_formal_file_flow_keeps_requirements_title_scope_and_manual_adoption(supplement,expected):
    from src.material_inbox import MaterialInbox,update_file_source,extract_material,item_values
    from dataclasses import replace
    from src.material_estimate_recovery import confirm_minimal_task
    from tests.test_material_estimate_recovery import NOW
    from tests.document_fixtures import docx_bytes
    from src.file_material import read_file_material,DOCX_MIME
    source=read_file_material('generic.docx',DOCX_MIME,docx_bytes(SOURCE))
    source=replace(source,text='[第1页]\n'+SOURCE,page_count=12)
    draft=update_file_source(MaterialInbox(),source,'',NOW,'plan','beiyangyuan',supplemental_context=supplement).draft
    def recognize(system,user):
        payload=json.loads(user)
        assert payload['source_title']=='实验任务说明'
        assert payload['material_requirements']==REQUIREMENTS
        assert payload['material']==source.text # Preserve the complete product input.
        return '{}'
    def estimate(system,user):
        payload=json.loads(user)
        row=json.loads(estimate_response(payload,120))
        row['estimates'][0].update(basis='处理(120分钟)：包含约400字讨论，完成6至8页正文',assumptions=[])
        return json.dumps(row,ensure_ascii=False)
    result=extract_material(draft,recognize,file_source=source,workload_caller=estimate)
    entry=result.estimate_fallbacks[0]
    assert entry['origin']=='model_workload'
    assert entry['estimate']['task_name']=='实验任务说明'
    assert [n for n,_ in numbered_scope(source.text,supplement).sections]==expected
    assert '[第1页]' not in entry['estimate']['short_scope']
    prepared=confirm_minimal_task(result,entry['item_id'],'实验任务',127,True,entry['estimate']['short_scope'])
    assert item_values(prepared.items[0],prepared)['minutes']=='127'
