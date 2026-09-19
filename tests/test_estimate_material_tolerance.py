"""Coarse estimates allow uncertainty while rejecting material contradictions."""
import json

import pytest

from src.estimate_arithmetic import check_estimate_arithmetic
from src.material_estimate_diagnostics import capture_estimate_diagnostics
from src.material_workload import estimate_workloads,source_workload,rough_estimate
from tests.test_workload_engine import estimate_response


@pytest.mark.parametrize('subtotal,suggested,low,high,valid,tolerance',[
    (130,150,120,180,True,30),
    (45,135,105,180,False,27),
    (55,55,45,75,True,15),
    (2,17,1,30,False,2),
    (17,2,1,30,False,2),
    (2,3,1,5,True,2),
    (15,30,10,45,False,7.5),
    (420,500,350,600,True,100),
    (300,500,250,700,False,100),
    (45,135,1,10080,False,27),
])
def test_absolute_relative_tolerance_and_short_task_protection(subtotal,suggested,low,high,valid,tolerance):
    check=check_estimate_arithmetic(suggested,low,high,'核心工作({}分钟)'.format(subtotal))
    assert check.applicable and check.valid is valid
    assert check.tolerance==pytest.approx(tolerance)
    assert check.explained_min==check.explained_max==subtotal


@pytest.mark.parametrize('basis',['计算(60分钟)；写作(40分钟)','任务需要计算、讨论和复核。'])
def test_model_recommendation_outside_its_own_range_is_always_invalid(basis):
    check=check_estimate_arithmetic(100,110,150,basis)
    assert not check.valid and check.code=='invalid_duration_range'
    check=check_estimate_arithmetic(100,70,90,basis)
    assert not check.valid and check.code=='invalid_duration_range'


@pytest.mark.parametrize('invalid',[None,True,0,-1,1.5,10**400])
def test_malformed_minutes_cannot_enter_tolerance_math(invalid):
    check=check_estimate_arithmetic(invalid,1,10080,'计算(20分钟)；复核(5分钟)')
    assert not check.valid and check.code=='invalid_duration_range'


def test_breakdown_range_uses_nearest_boundary_not_exact_midpoint():
    check=check_estimate_arithmetic(150,120,180,'计算(60–90分钟)；写作(30–50分钟)')
    assert check.valid and check.explained_min==90 and check.explained_max==140
    assert check.feedback(150,120,180)['unexplained_gap_minutes']==10


def test_explicit_buffer_is_included_once_and_existing_included_steps_stay_excluded():
    check=check_estimate_arithmetic(150,120,180,
        '计算(70分钟)；写作(50分钟)；复核(10分钟，已计入计算)', ['另预留10分钟缓冲'])
    assert check.valid and check.explained_min==check.explained_max==130
    assert len(check.parts)==2 and len(check.extras)==1


def test_no_minute_breakdown_does_not_invent_an_arithmetic_comparison():
    check=check_estimate_arithmetic(150,120,180,'需要完成计算、分析和讨论，有一定不确定性。')
    assert check.valid and not check.applicable and check.code=='no_explicit_breakdown'


@pytest.mark.parametrize('subtotal,suggested,repair_good,expected_calls,expected_origin',[
    (130,150,False,1,'model_workload'),
    (55,55,False,1,'model_workload'),
    (45,135,True,2,'model_workload'),
    (45,135,False,2,'local_workload'),
])
def test_only_material_gaps_use_the_existing_single_repair_slot(subtotal,suggested,repair_good,expected_calls,expected_origin):
    workload=source_workload('登记表\n姓名 | （空白）')[0]
    calls=[]
    def caller(system,user):
        payload=json.loads(user);calls.append(payload)
        obj=json.loads(estimate_response(payload,suggested))
        value=obj['estimates'][0]
        value['basis']='核心工作({}分钟)'.format(subtotal)
        if len(calls)==2:
            assert payload['validation_feedback'][0]['unexplained_gap_minutes']==90
            assert payload['validation_feedback'][0]['tolerance_minutes']==27
            if repair_good:
                value.update(recommended_minutes=45,min_focus_minutes=35,max_focus_minutes=60)
        return json.dumps(obj)
    with capture_estimate_diagnostics() as events:
        result,_=estimate_workloads((workload,),caller)
    assert len(calls)==expected_calls and result[0][2]==expected_origin
    assert sum(e['code']=='repair_requested' for e in events)==expected_calls-1
    if expected_origin=='local_workload':assert result[0][1]==rough_estimate(workload)


def test_moderate_material_estimate_is_accepted_without_arithmetic_repair():
    from src.material_inbox import MaterialInbox,update_source,extract_material
    from tests.test_material_estimate_recovery import FORM,NOW,action_payload
    draft=update_source(MaterialInbox(),FORM,'',NOW,'plan','beiyangyuan').draft
    response=action_payload()
    response['estimate'].update(min_focus_minutes=120,max_focus_minutes=180,recommended_minutes=150,
        basis='计算(70分钟)；写作(50分钟)；缓冲(10分钟)',assumptions=[])
    calls=[]
    def caller(*args):calls.append(args);return json.dumps(response)
    with capture_estimate_diagnostics() as events:
        result=extract_material(draft,caller)
    assert len(calls)==1 and result.model_calls==1
    assert result.estimate_fallbacks[0]['estimate']['recommended_minutes']==150
    assert not any(e['code']=='repair_requested' or e['fallback'] for e in events)
