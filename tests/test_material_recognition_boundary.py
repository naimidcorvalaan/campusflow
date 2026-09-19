import json
import pytest
from src.material_recognition_boundary import canonical_recognition
from src.material_inbox import parse_extraction,extract_material
from src.material_formal_validation import extract_material_object,FormalValidationError
from src.material_estimate_diagnostics import capture_estimate_diagnostics
from tests.test_material_effort_contract import draft,response


def run(raw):
    with capture_estimate_diagnostics() as events:
        corrected=canonical_recognition(raw,lambda s:parse_extraction(s,draft(),set()))
    return corrected,next(e['recognition_boundary'] for e in events if 'recognition_boundary' in e)


@pytest.mark.parametrize('tail',['','  ','\n以上是整理结果。','}\n}',' 完成整理。'])
def test_unique_formal_object_boundary(tail):
    raw=json.dumps(response(),ensure_ascii=False)
    corrected,info=run(raw+tail)
    assert extract_material_object(corrected)==json.loads(raw)
    assert info['trailing_content_discarded']==bool(tail.strip())
    assert info['first_complete_object_end_offset']==len(raw)
    assert info['trailing_content_length']==len(tail)
    if tail.strip():assert corrected==raw and info['decision']=='canonicalized'


@pytest.mark.parametrize('tail',['{}',' []',' 修正版：{}',' "estimate":null',' null',' true',
    '这是修订结果',' 改为其他任务',' 总共120分钟',' {"broken":','\n```json\n{}\n```'])
def test_ambiguous_or_structured_suffix_is_never_discarded(tail):
    raw=json.dumps(response())+tail
    corrected,info=run(raw)
    assert corrected==raw and not info['trailing_content_discarded']
    assert info['error_code']=='ambiguous_multiple_structures'
    with pytest.raises(FormalValidationError):extract_material_object(corrected)


@pytest.mark.parametrize('raw',['{"broken":','{"a":oops}','{"a":1}}}', 'prefix {}'])
def test_no_bracket_completion_or_schema_bypass(raw):
    corrected,info=run(raw)
    assert corrected==raw and not info['trailing_content_discarded']
    with pytest.raises((ValueError,TypeError)):parse_extraction(corrected,draft(),set())


def test_extra_closer_is_counted_without_rstrip_repair():
    raw=json.dumps(response())
    corrected,info=run(raw+'}}')
    assert corrected==raw
    assert info['response_shape']=='extra_closing_braces'
    assert info['unmatched_close_braces']==2
    assert info['top_level_object_count_candidate']==1


def test_second_full_object_is_counted():
    raw=json.dumps(response());corrected,info=run(raw+raw)
    assert corrected==raw+raw and info['top_level_object_count_candidate']==2
    assert info['trailing_content_contains_json_opener']


def test_invalid_first_schema_never_canonicalized():
    obj=response();obj['unknown_field']='private-body'
    raw=json.dumps(obj)+' 以上是整理结果。'
    corrected,info=run(raw)
    assert corrected==raw and info['decision']=='schema_rejected'
    assert 'private-body' not in json.dumps(info)


@pytest.mark.parametrize('tail',[' 以上是整理结果。','}'])
def test_formal_pipeline_executes_actionability_after_boundary(tail):
    with capture_estimate_diagnostics() as events:
        result=extract_material(draft(),lambda s,u:json.dumps(response())+tail)
    assert result.estimate_fallbacks
    checks=[e['actionability_conditions'] for e in events if 'actionability_conditions' in e]
    assert checks and all(c['combined_decision'] for c in checks)
    assert all(c['reason_present'] and c['verified_evidence_required'] for c in checks)
    assert any(e.get('recognition_boundary',{}).get('trailing_content_discarded') for e in events)


def test_estimate_parser_is_still_strict_and_diagnostics_do_not_leak():
    raw=json.dumps(response())+' private-tail-body'
    with pytest.raises(FormalValidationError):extract_material_object(raw)
    corrected,info=run(raw)
    assert 'private-tail-body' not in json.dumps(info)
    assert info['decision']=='canonicalized'
