import json

import pytest

from src.estimate_arithmetic import check_estimate_arithmetic
from src.material_estimate_diagnostics import capture_estimate_diagnostics
from src.material_workload import estimate_workloads, source_workload, rough_estimate
from tests.test_workload_engine import estimate_response


@pytest.mark.parametrize('basis,assumptions,recommend,low,high', [
    ('做题(20分钟)；订正(15分钟)；提交(5分钟)',[],40,35,50),
    ('做题20分钟；撰写讨论15分钟；检查5分钟',[],40,30,50),
    ('做题(20分钟)；写报告(25分钟)',['另预留30分钟查笔记和复核'],75,60,90),
    ('核心作业45分钟',['另预留30分钟查笔记和复核'],75,60,90),
    ('计算(20分钟)；推导(20分钟)',['用户计算较慢，核心用时按1.5倍估计'],60,50,75),
    ('计算(20分钟)；推导(20分钟)',['用户计算较慢，时间多预留50%'],60,50,75),
    ('计算(20分钟)；写报告(20分钟)',['用户熟练，用时减少25%'],30,25,45),
    ('第一部分15分钟；第二部分20分钟；第三部分20分钟；第四部分20分钟；第五部分20分钟；提交检查10分钟；返工缓冲20分钟',[],135,120,135),
    ('计算(13分钟)；订正(14分钟)',[],30,25,40),
    ('计算(15–20分钟)；讨论(20-30分钟)；提交检查(5分钟)',[],45,35,65),
    ('计算(15-20分)；讨论(20-30分)',[],45,30,60),
    ('计算(20分钟)；复核(10分钟)',['已计入复核10分钟，无需另加'],30,20,40),
    ('置信区间(15-20分钟)；稳健性检验(10-15分钟)；讨论(15-20分钟)；提交检查(10-15分钟)',[],60,45,75),
    ('计算(40分钟)',[],45,35,55),
    ('计算(20分钟)；写作(20分钟)',['用户计算较慢，计算用时按1.5倍估计'],50,40,65),
])
def test_explained_ranges_buffers_speed_and_rounding_are_valid(basis,assumptions,recommend,low,high):
    result=check_estimate_arithmetic(recommend,low,high,basis,assumptions)
    assert result.applicable and result.valid


@pytest.mark.parametrize('basis', [
    '需要完成计算、讨论并复核，有一定不确定性。',
    '含95%置信区间和300字讨论，使用两个区域的32条记录。',
    '观察时长(45分钟)，课程时长(90分钟)是数据，不是工作时间。',
    '成绩(80分)；得分(90分)，请整理评分说明。',
    '每题10分钟，每道题5分钟仅为速度示例。',
])
def test_no_additive_minute_breakdown_is_not_invented(basis):
    result=check_estimate_arithmetic(135,100,170,basis)
    assert not result.applicable and result.valid


def test_unexplained_large_gap_is_rejected_even_if_total_range_is_wide():
    result=check_estimate_arithmetic(135,30,180,'计算(20分钟)；讨论(20分钟)；提交(5分钟)',
        ['有一定不确定性，需要检查'])
    assert result.applicable and not result.valid
    assert result.explained_min==result.explained_max==45
    assert result.code=='unexplained_time_gap'


def test_original_seven_part_ledger_is_counted_in_full():
    basis='数据整理与清洗(8min)：核对。数据表构建(2min)：录入。统计计算(9min)：计算。描述性分析(4min)：分析。置信区间(8min)：计算。比较分析(11min)：检验。复核与提交(3min)：检查。'
    result=check_estimate_arithmetic(135,105,165,basis)
    assert len(result.parts)==7 and result.explained_min==45
    assert not result.valid


def test_step_specific_speed_does_not_multiply_unrelated_steps():
    result=check_estimate_arithmetic(75,50,90,'计算(20分钟)；写作(20分钟)',
        ['用户计算较慢，计算用时按1.5倍估计'])
    assert result.explained_min==50 and result.adjusted_part_count==1
    assert not result.valid


def _workload_run(repair_mode):
    work=source_workload('申请表\n姓名 | （空白）\n自我评价（300字） | （空白）')[0]
    calls=[]
    def caller(system,user):
        payload=json.loads(user);calls.append(payload)
        obj=json.loads(estimate_response(payload,135))
        for row in obj['estimates']:
            row['basis']='填写(20分钟)；写作(20分钟)；提交(5分钟)'
            if len(calls)==2:
                assert payload['request_stage']=='estimate_arithmetic_repair'
                assert payload['validation_feedback'][0]['explained_max']==45
                assert '未解释差额' in system
                if repair_mode=='explain':
                    row['assumptions']=['另预留90分钟查资料和返工']
                elif repair_mode=='correct':
                    row.update(min_focus_minutes=40,max_focus_minutes=55,recommended_minutes=45)
                elif repair_mode=='erase':
                    row['basis']='综合考虑任务难度估计'
        return json.dumps(obj)
    with capture_estimate_diagnostics() as events:
        result,_=estimate_workloads((work,),caller)
    return work,result,calls,events


@pytest.mark.parametrize('mode,minutes',[('explain',135),('correct',45)])
def test_one_bounded_repair_can_explain_difference_or_correct_model_minutes(mode,minutes):
    work,result,calls,events=_workload_run(mode)
    assert len(calls)==2
    assert result[0][2]=='model_workload' and result[0][1]['recommended_minutes']==minutes
    assert sum(e['code']=='repair_requested' for e in events)==1
    assert calls[0]['workloads']==calls[1]['workloads']


@pytest.mark.parametrize('mode',['fail','erase'])
def test_failed_repair_or_removed_breakdown_uses_existing_safe_fallback(mode):
    work,result,calls,events=_workload_run(mode)
    assert len(calls)==2
    assert result[0][2]=='local_workload' and result[0][1]==rough_estimate(work)
    assert events[-1]['fallback']


def test_material_model_arithmetic_failure_gets_one_repair_not_two():
    from src.material_inbox import MaterialInbox,update_source,extract_material
    from tests.test_material_estimate_recovery import action_payload,NOW
    source='申请表\n姓名 | （空白）\n自我评价（300字） | （空白）'
    draft=update_source(MaterialInbox(),source,'',NOW,'plan','beiyangyuan').draft
    response=action_payload()
    response['actionability'].update(task_name='填写申请',short_scope='填写姓名和评价',evidence='自我评价（300字）')
    response['estimate'].update(min_focus_minutes=110,max_focus_minutes=160,recommended_minutes=135,
        basis='填写(20分钟)；写作(20分钟)；提交(5分钟)')
    calls=[]
    def effort(system,user):
        payload=json.loads(user);calls.append(payload)
        assert payload['request_stage']=='estimate_arithmetic_repair'
        obj=json.loads(estimate_response(payload,135))
        obj['estimates'][0]['basis']=response['estimate']['basis']
        return json.dumps(obj)
    result=extract_material(draft,lambda *a:json.dumps(response),workload_caller=effort)
    assert len(calls)==1 and result.model_calls==2
    assert result.diagnostics['estimate_repair_calls']==1
    assert result.estimate_fallbacks[0]['origin']=='local_workload'


def test_user_adopted_minutes_are_not_reconciled_to_model_breakdown():
    from src.task_estimation import make_material,run_task_estimation,confirm_estimated_task
    from tests.test_task_estimation import estimate_json
    payload=json.loads(estimate_json(45))
    payload['basis']='做题(20分钟)；订正(20分钟)；检查(5分钟)'
    material=make_material('完成练习并订正')
    draft=run_task_estimation('adopt_custom',material,'',lambda *a:json.dumps(payload)).draft
    confirmed=confirm_estimated_task(draft,material,'',draft.result.task_name,draft.result.scope_summary,
        draft.result.completion_criteria,19)
    assert confirmed.total_minutes==19 and confirmed.duration_source=='user_modified'


def test_standalone_estimator_reuses_its_one_repair_slot_for_arithmetic():
    from src.task_estimation import make_material,run_task_estimation
    from tests.test_task_estimation import estimate_json
    bad=json.loads(estimate_json(135));bad['basis']='计算(20分钟)；讨论(20分钟)；检查(5分钟)'
    good=dict(bad,min_focus_minutes=40,max_focus_minutes=60,recommended_minutes=45)
    calls=[]
    def caller(system,user):
        calls.append(json.loads(user))
        if len(calls)==2:assert 'validation_feedback' in calls[-1]
        return json.dumps(bad if len(calls)==1 else good)
    run=run_task_estimation('bounded',make_material('完成练习'),'计算较慢',caller)
    assert run.repaired and run.call_count==2
    assert run.draft.result.recommended_minutes==45
