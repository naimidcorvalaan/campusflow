import pytest
from src.material_workload import evidence_matches
from src.material_workload_semantics import validate_workload_semantics


@pytest.mark.parametrize('source',['检查临\n时数据的边界。','检查临\r\n时数据的边界。','检查临 \n 时数据的边界。'])
def test_chinese_physical_wrap_preserves_exact_quote(source):
    quote='检查临时数据的边界。'
    assert evidence_matches(quote,source)
    obj={'workload':[{'features':[{'kind':'review','evidence':quote}]}]}
    validate_workload_semantics(obj,source)
    assert obj['workload'][0]['features'][0]['evidence']==quote


@pytest.mark.parametrize('quote,source',[
    ('readable','read\nable'),('检查临时数据','检查临\n\n时数据'),
    ('检查临时数据','检查临 时数据'),('检查正式数据','检查临\n时数据'),
    ('检查数据提交结果','检查数据；提交结果'),('提交结果检查数据','检查数据；提交结果'),
])
def test_layout_normalization_cannot_join_words_paragraphs_or_rewrite(quote,source):
    assert not evidence_matches(quote,source)


def test_existing_space_separated_field_quotes_remain_valid():
    assert evidence_matches('姓名 学号','姓名\n学号\n自我评价')
    assert not evidence_matches('姓名 学院','姓名\n学号\n自我评价')


def test_chinese_punctuation_preserved_across_physical_wrap():
    assert evidence_matches('核对时间、地点和课程。完成检查。','核对时间、\n地点和课程。\n完成检查。')
    assert not evidence_matches('核对时间地点和课程','核对时间、\n地点和课程')
