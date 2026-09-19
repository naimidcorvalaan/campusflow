from src.material_scope import section_markers,numbered_scope


def test_explicit_review_headings_keep_first_and_last_parts():
    text='本次作业包括：第一部分 需求梳理。阅读资料并画流程图。\n第二部分 测试设计。设计测试。\n第三部分 评审交付。撰写结论并提交。'
    assert [m[2] for m in section_markers(text)]==[1,2,3]
    scope=numbered_scope(text)
    assert [n for n,_ in scope.sections]==[1,2,3]


def test_prose_reference_does_not_become_heading():
    assert not section_markers('请参考第一部分的评审结论。')
