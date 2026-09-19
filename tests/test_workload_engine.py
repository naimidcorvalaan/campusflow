"""Hard guarantees after evidence recognition, with deliberately useless models."""
import json
from dataclasses import replace

import pytest

from src.material_workload import (source_workload, model_workloads, rough_estimate,
    estimate_workloads, WORKLOAD_SCHEMA)
from src.material_inbox import MaterialInbox, update_file_source, extract_material
from src.file_material import read_file_material, DOCX_MIME
from tests.document_fixtures import assessment_docx_bytes, reference_docx_bytes
from tests.test_material_estimate_recovery import NOW, draft_for


def estimate_response(request, minutes=20):
    return json.dumps(dict(estimates=[dict(work_id=w['work_id'],min_focus_minutes=max(1,minutes-5),
        max_focus_minutes=minutes+10,recommended_minutes=minutes,
        basis='按识别出的录入、写作与核对工作量估算',assumptions=['只处理已识别的范围'])
        for w in request['workloads']]),ensure_ascii=False)


@pytest.mark.parametrize('supplement',['','填表'])
@pytest.mark.parametrize('bad',['{}','null','not JSON','{"items":null}',
    '{"actionability":{"is_estimatable":false,"evidence":"测评考核表"}}'])
def test_readable_form_has_time_even_when_every_model_response_is_useless(supplement,bad):
    source=read_file_material('本科生综合素质测评考核表【主观评价部分】.docx',
        DOCX_MIME,assessment_docx_bytes(True))
    draft=update_file_source(MaterialInbox(),source,'',NOW,'plan','beiyangyuan',supplement).draft
    requests=[]
    def main(system,user):
        request=json.loads(user);requests.append(request)
        assert request['supplemental_context']==(supplement or None)
        assert request['recognized_workload']
        return bad
    def effort(system,user):
        request=json.loads(user);requests.append(request)
        assert WORKLOAD_SCHEMA in system
        assert request['supplemental_context']==(supplement or None)
        assert request['workloads']
        assert not {'material','items','formal_item_format','previous_response','existing_tasks'} & set(request)
        return bad
    result=extract_material(draft,main,file_source=source,workload_caller=effort)
    assert len(requests)==result.model_calls==2
    assert result.diagnostics['result_level']=='workload_estimate'
    assert result.diagnostics['request_outcome']=='result'
    assert result.workload_summary and not result.items
    entry=result.estimate_fallbacks[0];v=entry['estimate']
    assert entry['origin']=='local_workload' and not entry['simple_confirmation_allowed']
    assert 0<v['focused_minutes_min']<=v['recommended_minutes']<=v['focused_minutes_max']
    assert result.estimate_coverage=='partial' and '可能需要更久' in result.coverage_note
    assert '无法' not in result.message and '未能生成' not in result.message


def test_network_failure_cannot_erase_locally_readable_form_work():
    source=read_file_material('任意名字.docx',DOCX_MIME,assessment_docx_bytes())
    draft=update_file_source(MaterialInbox(),source,'',NOW,'plan','beiyangyuan','填表').draft
    calls=[]
    def offline(*args):calls.append(1);raise OSError('synthetic network failure')
    result=extract_material(draft,offline,file_source=source,workload_caller=offline)
    assert result.estimate_fallbacks and result.model_calls==len(calls)==2
    assert result.diagnostics['estimate_origins']==['local_workload']


def test_short_model_estimate_wins_without_requiring_a_formal_task():
    source=read_file_material('表.docx',DOCX_MIME,assessment_docx_bytes())
    draft=update_file_source(MaterialInbox(),source,'',NOW,'plan','beiyangyuan','填表').draft
    result=extract_material(draft,lambda *a:'{"items":null}',file_source=source,
        workload_caller=lambda system,user:estimate_response(json.loads(user),43))
    assert result.estimate_fallbacks[0]['estimate']['recommended_minutes']==43
    assert result.estimate_fallbacks[0]['origin']=='model_workload' and not result.items


@pytest.mark.parametrize('kind',['text','docx','pdf_text','pdf_vision','image'])
def test_workload_survives_bad_formal_data_through_all_source_adapters(kind):
    draft,kw=draft_for(kind)
    quote='Experiment one' if kind=='pdf_text' else '人文学术讲座学分申请表'
    obj=dict(workload=[dict(task_name='填写申请',scope='填写已识别字段',features=[
        dict(kind='basic_field',units=1,evidence=quote,words=0)])],items=None)
    image_calls=[];effort_calls=[]
    def main(*args):image_calls.append(args);return json.dumps(obj)
    def effort(system,user):effort_calls.append(json.loads(user));return '{}'
    result=extract_material(draft,main,image_caller=main,images_caller=main,workload_caller=effort,**kw)
    assert len(image_calls)==len(effort_calls)==1
    assert result.estimate_fallbacks and result.model_calls==2
    assert 'material' not in effort_calls[0]


def test_late_visual_recognition_uses_at_most_three_calls_and_never_reasks_afterwards():
    draft,kw=draft_for('image')
    responses=iter(['{}',json.dumps(dict(workload=[dict(task_name='写自我评价',scope='主观评价文字',
        features=[dict(kind='long_text',units=1,evidence='自我评价（300字）',words=300)])],items=None))])
    result=extract_material(draft,lambda *a:'{}',image_caller=lambda *a:next(responses),**kw)
    assert result.model_calls==3 and result.estimate_fallbacks


def test_local_ranges_change_with_fields_writing_recall_and_material_preparation():
    base='申请表\n表格：\n姓名 | （空白）'
    added=base+'\n学号 | （空白）\n学院 | （空白）'
    writing=added+'\n自我评价（300字，回顾经历） | （空白）'
    longer=writing.replace('300字','900字')
    proof=longer+'\n支撑材料名称 | （空白）\n复核信息 | （空白）'
    ranges=[rough_estimate(source_workload(text)[0]) for text in (base,added,writing,longer,proof)]
    assert all(a['min_focus_minutes']<=b['min_focus_minutes'] and a['max_focus_minutes']<b['max_focus_minutes']
        for a,b in zip(ranges,ranges[1:]))
    with_wait=proof+'\n辅导员签字 | （空白）\n学院审批 | （空白）'
    assert rough_estimate(source_workload(with_wait)[0])==ranges[-1]
    assert source_workload(with_wait)[0].waiting_note


def test_only_identified_score_slots_count_not_arbitrary_table_rows():
    text='测评表\n表格：\n德 | （空白）\n智 | （空白）\n体 | （空白）\n美 | （空白）\n劳 | （空白）'
    work=source_workload(text)[0]
    assert sum(f.units for f in work.features if f.kind=='score')==5
    assert not source_workload('统计数据\n项目 | 金额\n甲 | 100\n乙 | 200')
    assert not source_workload('表格：\n辅导员签字 | （空白）\n学院审批 | （空白）')
    assert not source_workload('这是一段学校历史介绍','填表')


def test_pure_reference_stays_a_question_until_an_actual_action_is_given():
    source=read_file_material('材料.docx',DOCX_MIME,reference_docx_bytes())
    draft=update_file_source(MaterialInbox(),source,'',NOW,'plan','beiyangyuan').draft
    obj=dict(schema_version='campusflow.material-text.v2',items=[],reference_date=None,reference_evidence=None,
        actionability=dict(is_estimatable=False,reason='学校背景介绍',evidence='学校简介'))
    result=extract_material(draft,lambda *a:json.dumps(obj),file_source=source)
    assert not result.estimate_fallbacks and '你准备怎么处理' in result.message
    draft=update_file_source(MaterialInbox(),source,'',NOW,'plan','beiyangyuan','阅读').draft
    result=extract_material(draft,lambda *a:'{}',file_source=source)
    assert result.estimate_fallbacks and '你准备怎么处理' not in result.message


def test_hallucinated_features_and_numbers_do_not_become_workload_evidence():
    obj=dict(workload=[dict(task_name='填写',scope='填信息',features=[
        dict(kind='basic_field',units=99,evidence='姓名'),dict(kind='long_text',units=1,words=900,evidence='不存在')])])
    assert not model_workloads(json.dumps(obj),'姓名')


def test_invalid_estimate_numbers_never_cross_the_calculation_boundary():
    work=source_workload('登记表\n姓名 | （空白）')[0]
    for bad in (True,-1,0,10**20,None):
        def caller(system,user):
            obj=json.loads(estimate_response(json.loads(user)))
            obj['estimates'][0]['recommended_minutes']=bad
            return json.dumps(obj)
        result,_=estimate_workloads((work,),caller)
        assert result[0][2]=='local_workload'
        assert result[0][1]==rough_estimate(work)


def test_workload_call_has_capacity_for_complete_ledger_and_bounded_timeout():
    from src.p2_tju_live_adapter import TJUP2CallAdapter
    calls=[]
    adapter=TJUP2CallAdapter(call_function=lambda user,**kw:calls.append((user,kw)) or '{}')
    adapter.workload_estimation_caller('system','{"supplemental_context":"填表"}')
    from src.p2_tju_live_adapter import MAX_WORKLOAD_OUTPUT_TOKENS
    assert len(calls)==1 and calls[0][1]['max_tokens']==MAX_WORKLOAD_OUTPUT_TOKENS==2048 and calls[0][1]['timeout']==30


def test_fixed_arrangement_cannot_cancel_recognized_filling_work():
    from src.material_inbox import update_source
    from scripts.material_demo_model import row, timing
    text='登记表\n姓名 | （空白）\n2026-09-07 16:00–17:00班会'
    draft=update_source(MaterialInbox(),text,'',NOW,'plan','beiyangyuan').draft
    fixed=row('班会','2026-09-07 16:00–17:00班会',kind='fixed_commitment',scope='',completion='',
        start=timing('2026-09-07 16:00','2026-09-07','16:00'),
        end=timing('2026-09-07 16:00–17:00','2026-09-07','17:00'),commitment_kind='meeting')
    response=json.dumps(dict(schema_version='campusflow.material-text.v2',items=[fixed],
        reference_date=None,reference_evidence=None))
    result=extract_material(draft,lambda *a:response,workload_caller=lambda *a:'{}')
    assert len(result.items)==1 and result.items[0].kind=='fixed_commitment'
    assert result.estimate_fallbacks and result.model_calls==2


def test_short_call_compacts_evidence_without_losing_work_counts():
    from src.material_workload import Workload, Feature
    work=Workload('填写','已识别部分',tuple(Feature('long_text',2,str(n)+'字'*500,300) for n in range(32)))
    requests=[]
    result,_=estimate_workloads((work,),lambda system,user:requests.append(user) or '{}','填表')
    payload=json.loads(requests[0]);features=payload['workloads'][0]['features']
    # The existing workload summary remains compact; the newly required
    # verified-reference catalog intentionally has no old whole-prompt cap.
    assert len(features)==1
    assert len(payload['verified_evidence_refs'])==33  # 32 facts plus user supplement
    assert len({r['provenance_ref'] for r in payload['verified_evidence_refs']})==33
    assert features[0]['action_count']==64 and features[0]['total_words']==19200
    assert result[0][1]==rough_estimate(work)


def test_unknown_reference_columns_with_filling_intent_only_estimate_one_record():
    work=source_workload('统计数据\n项目 | 金额\n甲 | 100\n乙 | 200','填表')[0]
    assert '一条记录' in work.scope
    assert sum(f.units for f in work.features)==2
