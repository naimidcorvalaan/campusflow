"""Regressions for readable work being lost before the estimate reaches UI."""
import json

import pytest

from tests.document_fixtures import assessment_docx_bytes
from tests.test_material_estimate_recovery import action_payload, payload, draft_for, NOW
from src.file_material import read_file_material, DOCX_MIME
from src.material_inbox import MaterialInbox, update_file_source, extract_material


def test_word_content_controls_and_nested_blank_fields_reach_the_model():
    source = read_file_material('任意文件名.docx', DOCX_MIME, assessment_docx_bytes())
    for field in ('姓名', '学号', '德', '智', '体', '美', '劳', '自我评价（约300字', '支撑材料名称', '辅导员签字'):
        assert source.text.count(field) == 1
    assert '（空白）' in source.text
    assert not source.warnings  # Content controls with plain text are readable.


@pytest.mark.parametrize('supplement', ['', '填表'])
def test_blank_assessment_form_keeps_evidenced_partial_time_despite_question(supplement):
    source = read_file_material('本科生综合素质测评考核表【主观评价部分】.docx',
        DOCX_MIME, assessment_docx_bytes(partial=True))
    draft = update_file_source(MaterialInbox(), source, '', NOW, 'plan', 'beiyangyuan', supplement).draft
    calls = []
    def caller(system, user):
        request = json.loads(user.split('\n上次输出')[0])
        calls.append(request)
        assert '自我评价（约300字' in request['material']
        assert request['supplemental_context'] == (supplement or None)
        obj = action_payload()
        obj['actionability'].update(task_name='准备并填写综合素质测评表',
            short_scope='填写个人信息和五项评分，回顾经历写约300字自我评价，整理证明并复核。',
            evidence='自我评价（约300字，结合本学年经历）', reason='已读到基本信息、评分、主观文字与证明要求')
        obj['estimate'].update(min_focus_minutes=35, max_focus_minutes=65, recommended_minutes=50,
            basis='个人信息与评分录入、回忆经历及约300字写作、整理已有证明并复核。',
            assumptions=['评分标准已知，证明可在已有记录中找到'],
            clarification_question='未读部分是否还有其他评价要求？')
        obj['coverage'] = dict(level='partial', reason='另有未读取的图片内容', uncovered_content='图片中的其他要求')
        return json.dumps(obj, ensure_ascii=False)
    result = extract_material(draft, caller, file_source=source)
    assert result.estimate_fallbacks[0]['estimate']['recommended_minutes'] == 50
    assert result.estimate_coverage == 'partial' and not result.items
    assert not result.estimate_fallbacks[0]['simple_confirmation_allowed']
    assert '你准备怎么处理' not in result.message
    assert len(calls) <= 2


def test_second_response_time_is_not_outvoted_by_more_untimed_items():
    draft, _ = draft_for('text')
    first = payload(False)
    first['items'][0].pop('estimate')
    first['items'].append(dict(first['items'][0], title='核对证明', scope='检查证明'))
    second = action_payload()
    second['actionability'].update(task_name='填写申请信息', short_scope='先完成可确认的填写部分')
    replies = iter([first, second])
    result = extract_material(draft, lambda *args: json.dumps(next(replies)))
    assert result.estimate_fallbacks and result.diagnostics['estimate_available']
    assert len(result.items) == 2  # Already validated facts survive the completion call.
    assert not result.estimate_fallbacks[0]['simple_confirmation_allowed']


@pytest.mark.parametrize('kind', ['text', 'image', 'docx', 'pdf_text', 'pdf_vision'])
def test_partial_question_cannot_erase_a_bounded_estimate(kind):
    draft, kw = draft_for(kind)
    obj = payload()
    obj['items'][0]['estimate']['clarification_question'] = '是否还需要准备其他证明？'
    if kind == 'pdf_text':
        obj['items'][0]['evidence'] = 'Experiment one'
    def caller(*args):
        return json.dumps(obj)
    result = extract_material(draft, caller, image_caller=caller, images_caller=caller, **kw)
    assert result.estimate_fallbacks and not result.items
    assert result.estimate_coverage == 'partial'
    assert not result.estimate_fallbacks[0]['simple_confirmation_allowed']


def test_estimate_evidence_may_normalize_layout_whitespace_but_not_invent_words():
    from src.material_inbox import update_source
    draft = update_source(MaterialInbox(), '姓名\n学号\n自我评价', '', NOW, 'plan', 'beiyangyuan').draft
    obj = action_payload()
    obj['actionability']['evidence'] = '姓名 学号'
    result = extract_material(draft, lambda *a: json.dumps(obj))
    assert result.estimate_fallbacks
    obj['actionability']['evidence'] = '姓名 学院'
    result = extract_material(draft, lambda *a: json.dumps(obj))
    assert not result.estimate_fallbacks


def test_document_user_note_is_explicit_intent_too():
    source = read_file_material('任意文件名.docx', DOCX_MIME, assessment_docx_bytes())
    draft = update_file_source(MaterialInbox(), source, '', NOW, 'plan', 'beiyangyuan', source_note='填表').draft
    obj = dict(schema_version='campusflow.material-text.v2', reference_date=None,
        reference_evidence=None, items=[], actionability=dict(is_estimatable=False,
            reason='没有说明要做什么', evidence='本科生综合素质测评考核表'))
    result = extract_material(draft, lambda *args: json.dumps(obj), file_source=source)
    assert '你准备怎么处理' not in result.message
    assert result.diagnostics['request_outcome'] == 'result'
    assert result.estimate_fallbacks[0]['origin'] == 'local_workload'


def test_matching_action_enriches_only_its_own_row_in_a_batch():
    draft, _ = draft_for('text')
    item = payload(False)['items'][0]
    item.pop('estimate')
    obj = action_payload([item, dict(item, title='核对证明', scope='检查证明')])
    result = extract_material(draft, lambda *args: json.dumps(obj))
    assert result.items[0].minutes == 20 and result.items[1].minutes is None
    assert not result.estimate_fallbacks
    assert result.model_calls == 1
