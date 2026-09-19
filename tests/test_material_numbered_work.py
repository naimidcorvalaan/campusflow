"""Coverage, workload and rejection diagnostics use the production path."""
import json

import pytest

from src.material_estimate_diagnostics import capture_estimate_diagnostics
from src.material_inbox import MaterialInbox, MaterialError, extract_material, update_source
from src.material_scope import numbered_scope
from src.material_workload import Feature, Workload, model_workloads, source_workload, rough_estimate, estimate_workloads
from tests.test_material_estimate_recovery import NOW, action_payload
from tests.test_workload_engine import estimate_response


MATERIAL = '''课程综合作业
各部分为同一份报告的工作步骤，需要完成所选部分。
第一部分核对记录。检查原始数据并写出说明。
第二部分计算基础指标。制作对照表。
第三部分推导公式。计算两个不同条件的结果，解释差异。
第四部分证明结论。写出证明过程，编写验证代码。
第五部分撰写二百字讨论。整理分析报告。
第六部分复核计算。提交前逐项检查报告与附件。上传文件。'''


@pytest.mark.parametrize('supplement,expected', [
    ('只做第3至第5部分',(3,4,5)),
    ('只做第三至第五部分',(3,4,5)),
    ('只做第3、4、5部分',(3,4,5)),
    ('只做第3,4,5部分',(3,4,5)),
    ('第三至第五部分及提交检查',(3,4,5)),
    ('已完成前两部分，只剩3–5',(3,4,5)),
    ('前两部分已经做完了，只需要完成第三至第五部分',(3,4,5)),
    ('只做第1、3、5部分',(1,3,5)),
    ('只做第1至第2部分和第5部分',(1,2,5)),
    ('前两部分已经完成',(3,4,5,6)),
    ('计算比较慢',(1,2,3,4,5,6)),
])
def test_selection_preserves_ranges_and_non_contiguous_lists(supplement,expected):
    boundary=numbered_scope(MATERIAL,supplement)
    assert tuple(n for n,_ in boundary.sections)==expected


@pytest.mark.parametrize('extra', ['提交检查','上传','提交','复核'])
def test_non_numbered_delivery_steps_remain_in_both_scope_and_work(extra):
    work=source_workload(MATERIAL,'只做第3至第5部分，及'+extra)[0]
    assert all('第'+n+'部分' in work.scope for n in ('三','四','五'))
    assert ('检查' if extra=='复核' else extra) in work.scope
    assert any(f.kind==('review' if extra in ('提交检查','复核') else 'submit') for f in work.features)


def test_completed_parts_cannot_be_reintroduced_by_model_work():
    from src.material_scope import scope_workload
    boundary=numbered_scope(MATERIAL,'只做第3至第5部分及提交检查')
    old=Workload('先前任务','第一部分', (Feature('review',1,'检查原始数据'),))
    result=scope_workload(boundary,(old,))
    assert '第一部分' not in result.scope and '第二部分' not in result.scope
    assert not any('原始数据' in f.evidence for f in result.features)


def test_valid_model_unit_counts_enrich_a_source_action_without_double_counting():
    from src.material_scope import scope_workload
    text='任务说明\n第一部分计算三种条件。'
    raw=json.dumps(dict(workload=[dict(task_name='计算',scope='三个条件',
        features=[dict(kind='calculation',units=3,evidence='计算三种条件')])]))
    work=scope_workload(numbered_scope(text),model_workloads(raw,text))
    assert sum(f.units for f in work.features if f.kind=='calculation')==3
    duplicate=scope_workload(numbered_scope(text),model_workloads(raw,text)*2)
    assert sum(f.units for f in duplicate.features if f.kind=='calculation')==3


def test_unknown_selected_part_is_not_silently_dropped():
    with pytest.raises(MaterialError):
        source_workload(MATERIAL,'第3至第8部分')


def test_word_counts_in_chinese_and_invalid_neighbor_feature():
    raw=dict(workload=[dict(task_name='写讨论',scope='两个题目及讨论',features=[
        dict(kind='problem',units=2,evidence='两个题目'),
        dict(kind='unknown',units=1,evidence='两个题目'),
        dict(kind='long_text',units=1,evidence='二百字讨论',words=200)])])
    with capture_estimate_diagnostics() as events:
        works=model_workloads(json.dumps(raw),'两个题目，二百字讨论')
    assert len(works)==1 and len(works[0].features)==2
    assert sum(f.units for f in works[0].features)==3
    assert any(e['code']=='invalid_feature' for e in events)


def test_simple_form_estimate_is_not_inflated_by_complex_work_priors():
    short=source_workload('登记表\n姓名 | （空白）')[0]
    assert rough_estimate(short)['recommended_minutes']==2
    complex_work=source_workload(MATERIAL,'第3至第5部分及提交检查')[0]
    assert {'calculation','proof','code','long_text','review'} <= {f.kind for f in complex_work.features}
    assert rough_estimate(complex_work)['min_focus_minutes']>10
    assert any(f.words==200 for f in complex_work.features)


def test_reference_to_names_and_grading_does_not_create_a_form():
    text='报告说明\n匿名数据不含姓名、学号。\n评价主要看计算方法、解释是否清楚、图表是否可读。'
    assert not source_workload(text)


@pytest.mark.parametrize('bad', ['{}', 'not JSON', '{"estimates":null}'])
def test_failed_models_still_cover_every_requested_part_and_delivery(bad):
    supplement='前两部分已经做完，只估第三至第五部分及提交检查'
    draft=update_source(MaterialInbox(),MATERIAL,'',NOW,'plan','beiyangyuan',supplement).draft
    seen=[]
    def recognize(system,user):
        payload=json.loads(user);seen.append(payload)
        assert payload['material']==MATERIAL
        assert payload['required_scope']['section_numbers']==[3,4,5]
        return bad
    with capture_estimate_diagnostics() as events:
        result=extract_material(draft,recognize,workload_caller=lambda *a:bad)
    value=result.estimate_fallbacks[0]['estimate']
    assert result.model_calls==2 and not result.items
    assert all('第'+n+'部分' in value['short_scope'] for n in ('三','四','五'))
    assert '提交检查' in value['short_scope']
    assert value['recommended_minutes']>10
    assert any(e['fallback'] for e in events)
    assert 'validation' not in result.message and 'events' not in result.diagnostics


def test_valid_json_for_a_smaller_scope_cannot_override_user_selection():
    supplement='第3至第5部分及提交检查'
    draft=update_source(MaterialInbox(),MATERIAL,'',NOW,'plan','beiyangyuan',supplement).draft
    response=action_payload()
    response['actionability'].update(task_name='完成推导',short_scope='第三部分推导公式',
        evidence='第三部分推导公式',reason='有推导工作')
    requests=[]
    def effort(system,user):
        request=json.loads(user);requests.append(request)
        assert all('第'+n+'部分' in request['workloads'][0]['scope'] for n in ('三','四','五'))
        assert '提交检查' in request['workloads'][0]['scope']
        return estimate_response(request,95)
    with capture_estimate_diagnostics() as events:
        result=extract_material(draft,lambda *a:json.dumps(response),workload_caller=effort)
    assert result.estimate_fallbacks[0]['estimate']['recommended_minutes']==95
    assert result.estimate_fallbacks[0]['origin']=='model_workload'
    assert len(requests)==1
    assert any(e['code']=='coverage_mismatch' for e in events)


@pytest.mark.parametrize('mutation,code', [
    ('unknown_field','unknown_field'),('missing_field','missing_field'),
    ('unknown_work_id','unknown_work_id'),('invalid_duration_range','invalid_duration_range'),
    ('duplicate_work_id','duplicate_work_id'),('model_parse_failed','model_parse_failed'),
])
def test_safe_rejection_diagnostics_name_the_rule_without_payload(mutation,code):
    work=source_workload('登记表\n姓名 | （空白）')[0]
    secret='PRIVATE_SENTINEL_DO_NOT_LOG'
    def caller(system,user):
        if mutation=='model_parse_failed':return secret
        obj=json.loads(estimate_response(json.loads(user)))
        row=obj['estimates'][0]
        if mutation=='unknown_field':row['unexpected_estimate_metadata']=secret
        elif mutation=='missing_field':del row['min_focus_minutes']
        elif mutation=='unknown_work_id':row['work_id']=secret
        elif mutation=='invalid_duration_range':row['recommended_minutes']=True
        elif mutation=='duplicate_work_id':obj['estimates'].append(dict(row))
        return json.dumps(obj)
    with capture_estimate_diagnostics() as events:
        results,_=estimate_workloads((work,),caller)
    assert results[0][2]=='local_workload'
    assert code in {e['code'] for e in events}
    assert events[-1]['fallback_reason']==code
    assert secret not in json.dumps(events)


def test_accepted_suggestion_has_a_positive_diagnostic():
    work=source_workload('登记表\n姓名 | （空白）')[0]
    with capture_estimate_diagnostics() as events:
        result,_=estimate_workloads((work,),lambda s,u:estimate_response(json.loads(u),8))
    assert result[0][2]=='model_workload'
    assert events[-1]['schema_valid'] and not events[-1]['fallback']


def test_full_scope_reaches_estimator_without_losing_tail_at_160_characters():
    scope='计算各项结果；'*25+'核对并上传附件'
    work=Workload('作业',scope,(Feature('calculation',1,'计算结果'),))
    seen=[]
    estimate_workloads((work,),lambda s,u:seen.append(json.loads(u)) or '{}')
    assert seen[0]['workloads'][0]['scope']==scope


def test_scope_failure_does_not_rebind_formal_task_identity_or_timing():
    from scripts.material_demo_model import row
    from src.material_inbox import SCHEMA
    task=row('独立证明任务','第四部分证明结论',scope='第四部分证明结论',completion='写出证明',
        minutes=30,duration_evidence='30分钟')
    text=MATERIAL+'\n独立证明任务明确时长30分钟。'
    draft=update_source(MaterialInbox(),text,'',NOW,'plan','beiyangyuan','第3至第5部分').draft
    obj=dict(schema_version=SCHEMA,items=[task],reference_date=None,reference_evidence=None)
    with pytest.raises(MaterialError,match='完整对应'):
        extract_material(draft,lambda *a:json.dumps(obj))
    assert draft.items==()  # Immutable source draft and no published task mutation.
