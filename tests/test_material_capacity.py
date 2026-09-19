from dataclasses import replace
from datetime import datetime
import pytest
from src import file_material
from src.material_inbox import MaterialInbox,update_file_source,extract_material,MaterialError
from src.material_workload import source_workload
from tests.document_fixtures import docx_bytes


def test_production_file_limit_remains_explicit_and_rejects_without_truncation():
    assert file_material.MAX_TEXT_CHARS==12000
    with pytest.raises(file_material.FileMaterialError,match='未截断'):
        file_material._text_limit('a'*12001)


def test_file_draft_and_parser_share_capacity_without_persisting_override(monkeypatch):
    text='背景信息。'*3300+'\n第一部分 数据检查。核对记录。\n第二部分 评审交付。撰写结论并提交。'
    with monkeypatch.context() as scoped:
        scoped.setattr(file_material,'MAX_TEXT_CHARS',20000)
        source=file_material.read_file_material('course.docx',file_material.DOCX_MIME,docx_bytes(text))
        draft=update_file_source(MaterialInbox(),source,'',datetime(2026,9,17),'plan','beiyangyuan').draft
        assert replace(draft,original_text=source.text).original_text==source.text
        assert len(source.text)>16000 and text in source.text
    with pytest.raises(ValueError):replace(draft,original_text=source.text)
    assert file_material.MAX_TEXT_CHARS==12000


def test_workload_does_not_silently_drop_admitted_tail_sections():
    text='课程作业要求。\n'+'背景信息。'*2500+'\n第一部分 数据检查。核对记录。\n第二部分 评审交付。撰写结论并提交。'
    work=source_workload(text)
    assert work and '数据检查' in work[0].scope and '评审交付' in work[0].scope


def test_file_plus_note_checks_shared_limit_before_model_call(monkeypatch):
    source=file_material.read_file_material('course.docx',file_material.DOCX_MIME,docx_bytes('完成数据核对并提交'))
    draft=update_file_source(MaterialInbox(),source,'',datetime(2026,9,17),'plan','beiyangyuan',source_note='补充情况').draft
    monkeypatch.setattr(file_material,'MAX_TEXT_CHARS',len(source.text))
    def no_call(*args):raise AssertionError('must reject before model')
    with pytest.raises(MaterialError,match='合计超过'):
        extract_material(draft,no_call,file_source=source)


def test_noun_heading_uses_explicit_following_work_not_random_numbered_prose():
    from src.material_scope import section_markers
    assert [m[2] for m in section_markers('第一部分 资源清单。整理依赖信息。\n第二部分 环境准备。检查软件版本。\n第三部分 验收脚本。设计场景。')]==[1,2,3]
    assert not section_markers('第一部分的资源已经就绪。\n第二部分 旧版概要。这是历史记录。')


def test_experimental_capacity_is_process_scoped_and_restores_defaults():
    from scripts.material_capacity_study import experimental_capacity
    with experimental_capacity(30000,30):
        assert file_material.MAX_TEXT_CHARS==30000 and file_material.MAX_PDF_PAGES==30
    assert file_material.MAX_TEXT_CHARS==12000 and file_material.MAX_PDF_PAGES==20


def test_context_error_diagnostics_keep_limits_not_provider_body():
    from scripts.material_capacity_study import context_error_metadata
    text='Maximum context length is 262144 tokens. You requested 301000 tokens. private user text'
    result=context_error_metadata(text)
    assert result['maximum_context_tokens']==262144 and result['requested_context_tokens']==301000
    assert result['context_limit_signal'] is True
    assert 'private' not in str(result)
