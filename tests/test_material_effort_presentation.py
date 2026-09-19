from copy import deepcopy
from types import SimpleNamespace
import pytest
from src.material_effort_presentation import present_effort,numeric_copy_issues,explanation
from src.material_ui import _render_estimate_card


def ledger(core=175):
    return dict(completeness='complete',core=[dict(category='review',minutes=core)],
        adjustments=[dict(category='rest_buffer',minutes=15,reason='合理休息余量',
            provenance_type='estimation_policy',provenance_ref='bounded_rest_buffer_v1',included_in=None)])


def value(text='核对结果，核心合计205分钟。',assumptions=None):
    return dict(task_name='实验任务',short_scope='检查、分析和提交',focused_minutes_min=180,
        focused_minutes_max=270,recommended_minutes=225,rationale=text,assumptions=assumptions or [])


@pytest.mark.parametrize('total,conflict',[(175,False),(205,True)])
def test_core_total_detected_without_changing_formal_values(total,conflict):
    v=value('核对结果，核心合计{}分钟。'.format(total));l=ledger();before=deepcopy((v,l))
    issues=numeric_copy_issues(v,l)
    assert bool(issues)==conflict
    copy,note=present_effort(v,l)
    assert '核对结果' in copy['rationale']
    assert '分钟' not in copy['rationale']
    assert '小计175分钟' in note
    assert (v,l)==before
    assert copy['recommended_minutes']==225


@pytest.mark.parametrize('text,metric',[
    ('调整合计50分钟','adjustments'),('缓冲共50分钟','adjustments'),
    ('建议总专注时间250分钟','suggested'),('建议预留250分钟','suggested'),
    ('预计180～300分钟','range'),('时长区间180–300分钟','range')])
def test_explicit_repeated_totals_cannot_drift(text,metric):
    v=value(text)
    assert any(e['metric']==metric for e in numeric_copy_issues(v,ledger()))
    assert '分钟' not in present_effort(v,ledger())[0]['rationale']


def test_adjustments_already_in_core_not_added_twice():
    l=ledger();l['adjustments'][0]['included_in']=0
    assert not numeric_copy_issues(value('调整合计0分钟'),l)
    assert '可解释合计175分钟' in present_effort(value(),l)[1]


def test_repair_replaces_structure_but_old_prose_cannot_survive_render():
    v=value();old=ledger(205);new=ledger(175)
    assert '小计205分钟' in present_effort(v,old)[1]
    copy,note=present_effort(v,new)
    assert '205' not in copy['rationale']+note
    assert '小计175分钟' in note
    assert v['rationale']=='核对结果，核心合计205分钟。'


def test_explanation_keeps_task_quantities_and_qualifiers():
    text='正文6～8页，讨论约400字；比较两种方法（约30分钟），再复核20分钟。'
    result=explanation(text)
    assert '正文6～8页' in result and '讨论约400字' in result
    assert '比较两种方法' in result and '再复核' in result
    assert '30' not in result and '20' not in result


def test_numberless_explanation_and_assumptions_unchanged():
    v=value('需要核对结果并说明原因。',['已有原始数据。'])
    projected,_=present_effort(v,ledger())
    assert projected==v


@pytest.mark.parametrize('text',['阅读（约十五分钟），再检查。','阅读约0.5小时，再检查。',
    'Reading (20 minutes), then review.'])
def test_other_effort_annotations_do_not_form_second_numeric_source(text):
    rendered=explanation(text)
    assert '分钟' not in rendered and '小时' not in rendered and 'minutes' not in rendered
    assert '检查' in rendered or 'review' in rendered


def test_all_assumptions_and_adjustment_reasons_share_projection():
    v=value(assumptions=['核心合计205分钟','建议总专注时间999分钟'])
    l=ledger();l['adjustments'][0]['reason']='休息缓冲，预计20分钟'
    projected,note=present_effort(v,l)
    assert all('分钟' not in a for a in projected['assumptions'])
    assert '20分钟' not in note and '休息余量15分钟' in note
    assert l['adjustments'][0]['minutes']==15


def test_removed_minute_annotation_does_not_leave_dangling_limit_label():
    assert explanation('合理休息，按核心比例估算，上限15分钟')=='合理休息，按核心比例估算'


def test_no_ledger_keeps_existing_fallback_presentation():
    v=value();copy,note=present_effort(v,None)
    assert copy is v and note==''


def test_formal_renderer_shows_only_structure_numbers_and_manual_value_untouched():
    class Screen:
        def markdown(self,text,**kwargs):self.text=text
    screen=Screen();v=value();l=ledger();v['adopted_minutes']=232
    _render_estimate_card(screen,SimpleNamespace(estimate_coverage='whole'),v,
        effort_ledger=l,facts_note='源文件共12页；正文6～8页；讨论约400字')
    assert '205分钟' not in screen.text and '小计175分钟' in screen.text
    assert '180–270' in screen.text and '225' in screen.text
    assert '正文6～8页' in screen.text and '讨论约400字' in screen.text
    assert v['adopted_minutes']==232 and l==ledger()
