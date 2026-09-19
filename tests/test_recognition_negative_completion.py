import json
from datetime import datetime
import pytest
from src.material_inbox import MaterialInbox,update_source,extract_material
from src.material_function_transport import ToolArguments
from src.material_actionability_sources import source_spans,explicit_directive


@pytest.mark.parametrize('action',[None,dict(is_estimatable=False,reason='没有需要完成的任务',evidence_refs=[])])
def test_validated_nonaction_is_not_repaired_into_new_work(action):
    draft=update_source(MaterialInbox(),'仅供参考：校园天气晴朗。','',datetime(2026,9,17),'plan','beiyangyuan').draft
    calls=[]
    def recognize(*args):
        calls.append(1)
        return ToolArguments(json.dumps(dict(schema_version='campusflow.material-text.v2',
            reference_date=None,reference_evidence=None,items=[],workload=[],actionability=action)))
    result=extract_material(draft,recognize,workload_caller=lambda *args:pytest.fail('background cannot be estimated'))
    assert len(calls)==1 and not result.items and not result.estimate_fallbacks


@pytest.mark.parametrize('text',[
    '请修订项目摘要并校对术语。','请调试应用程序，并运行测试。',
    '任务一：填写记录。步骤二：计算误差。','需要修改研究报告的结构。'])
def test_explicit_task_provenance_is_supported(text):
    draft=update_source(MaterialInbox(),text,'',datetime(2026,9,17),'plan','beiyangyuan').draft
    assert all(s['support_state']=='action_supporting' for s in source_spans(draft))


@pytest.mark.parametrize('text',['背景介绍：修订是一种编辑行为。','尚无任务要求。','请不要调试程序。'])
def test_vocabulary_does_not_make_background_or_negation_directive(text):
    assert not explicit_directive(text)


def test_item_semantics_derived_from_shared_declarations(monkeypatch):
    from src.material_output_schema import recognition_semantic_guide,ITEM_FIELD_SEMANTICS
    monkeypatch.setitem(ITEM_FIELD_SEMANTICS,'uncertainties','test shared meaning')
    assert 'test shared meaning' in recognition_semantic_guide()


def test_optional_absence_is_not_a_mandatory_confirmation():
    from src.material_output_schema import CONFIRMATION_SEMANTICS,recognition_semantic_guide
    from src.material_recognition_prompts import recognition_system_prompt
    assert CONFIRMATION_SEMANTICS in recognition_system_prompt('recognition')
    assert CONFIRMATION_SEMANTICS in recognition_semantic_guide()
    assert '实际存在或不能排除' not in recognition_system_prompt('recognition')
    # Existing data/content compatibility is retained; this corrects the new
    # function-stage instruction, not the formal adoption gate or source data.
    assert '实际存在或不能排除' in recognition_system_prompt()
