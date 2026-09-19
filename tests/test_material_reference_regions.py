from datetime import datetime
from src.material_inbox import MaterialInbox,update_source
from src.material_scope import numbered_scope,scope_workload,section_markers
from src.material_actionability_sources import source_spans


TASKS='本次作业：\n第一部分 数据核对。检查记录。\n第二部分 报告交付。撰写结论并提交。\n'
REFERENCE='以下为课程参考背景。仅供参考，不构成额外开发任务。\n'
BACKGROUND='第三部分 编写代码。运行脚本并测试数据库。\n请计算其他项目的指标。'


def test_explicit_reference_region_does_not_extend_last_task():
    text=TASKS+REFERENCE+BACKGROUND
    scope=numbered_scope(text)
    assert [n for n,_ in scope.sections]==[1,2]
    assert all('数据库' not in body for _,body in scope.sections)
    assert scope_workload(scope).features==scope_workload(numbered_scope(TASKS)).features


def test_reference_spans_remain_locatable_but_do_not_support_actions():
    text=TASKS+REFERENCE+BACKGROUND
    draft=update_source(MaterialInbox(),text,'',datetime(2026,9,17),'plan','beiyangyuan').draft
    spans=source_spans(draft)
    reference=[s for s in spans if '数据库' in s['text'] or '其他项目' in s['text']]
    assert reference and all(s['support_state']=='non_action' for s in reference)
    assert all(s['support_origin']=='explicit_reference_region' for s in reference)
    assert draft.original_text==text
    assert any(s['support_state']=='action_supporting' for s in spans)


def test_explicit_task_resumption_restores_subsequent_instructions():
    text=TASKS+REFERENCE+BACKGROUND+'\n以下为本次作业要求：\n第四部分 计算误差。计算误差并核对。'
    assert [m[2] for m in section_markers(text)]==[1,2,4]
    assert [n for n,_ in numbered_scope(text).sections]==[1,2,4]


def test_background_word_alone_does_not_remove_required_work():
    text=TASKS+'第三部分 背景分析。阅读背景并计算误差。'
    assert [n for n,_ in numbered_scope(text).sections]==[1,2,3]


def test_pdf_wrapped_reference_declaration_has_same_scope():
    text=TASKS+'以下为课程设计背景。它只介绍其他项目的实现，不构成额外的\n独立开发任务。\n'+BACKGROUND
    assert [n for n,_ in numbered_scope(text).sections]==[1,2]
