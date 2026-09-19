import json
from dataclasses import replace
import pytest
from src.material_evidence import canonical_source_quote


@pytest.mark.parametrize('source,quote',[
    ('核对数据并\n提交报告。','核对数据并提交报告。'),
    ('完成第一步。\n复核第二步。','完成第一步。 复核第二步。'),
    ('Read the\nreport and submit.','Read the report and submit.'),
])
def test_layout_equivalence_restores_an_exact_source_slice(source,quote):
    value=canonical_source_quote(quote,source)
    assert value==source and value in source


@pytest.mark.parametrize('source,quote',[
    ('完成分析。还需补数据。然后提交。','完成分析。然后提交。'),
    ('核对数据后提交','检查数据后提交'),
    ('完成\n\n分析','完成分析'),
    ('foo\nbar','foobar'),
    ('核对、提交','核对提交'),
    ('核对\n结果；核对 结果','核对\t结果'),
])
def test_no_semantic_guess_or_ambiguous_anchor(source,quote):
    assert canonical_source_quote(quote,source) is None


def test_original_field_limit_still_applies_after_canonicalization():
    assert canonical_source_quote('核对结果','核对'+' '*601+'结果') is None


def test_formal_file_parser_stores_restored_source_not_model_paraphrase():
    from tests.test_file_material import source,inbox_for,response
    from src.material_inbox import parse_extraction
    material=source(text='Experiment one\nsubmission')
    draft=replace(inbox_for(material).draft,original_text=material.text)
    obj=json.loads(response());obj['items'][0]['evidence']='Experiment one submission'
    result=parse_extraction(json.dumps(obj),draft,())
    assert result.items[0].evidence=='Experiment one\nsubmission'
    obj['items'][0]['evidence']='Experiment one delivery'
    with pytest.raises(ValueError):parse_extraction(json.dumps(obj),draft,())
