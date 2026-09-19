import json
from copy import deepcopy
import pytest
from src.material_workload_semantics import validate_workload_semantics
from src.material_formal_validation import FormalValidationError
from src.material_function_transport import ToolArguments
from src.material_inbox import extract_material
from tests.test_fc_original_ten import candidate
from tests.test_workload_engine import estimate_response


@pytest.mark.parametrize('text',['运行程序后导出结果到磁盘','执行脚本并写入文件','run code and save output locally'])
@pytest.mark.parametrize('kind',['submit','attachment'])
def test_grounded_local_output_is_not_delivery_or_attachment(text,kind):
    obj={'workload':[{'features':[{'kind':'code'},{'kind':kind,'evidence':text}]}]}
    before=deepcopy(obj)
    with pytest.raises(FormalValidationError) as exc:validate_workload_semantics(obj,text)
    assert exc.value.feedback['category']==kind and obj==before
    assert 'source_support' in exc.value.feedback
    assert text not in json.dumps(exc.value.feedback)


@pytest.mark.parametrize('text',['运行程序导出结果作为附件提交','run code, save output, upload to instructor','执行代码、保存文件并发给老师'])
def test_real_delivery_is_not_blocked(text):
    validate_workload_semantics({'workload':[{'features':[{'kind':'code'},{'kind':'submit','evidence':text}]}]},text)


def test_unknown_semantics_not_inferred_and_no_enum_alias_mapping():
    validate_workload_semantics({'workload':[{'features':[{'kind':'code'},{'kind':'submit','evidence':'操作结果'}]}]},'操作结果')


@pytest.mark.parametrize('kind,text',[
    ('short_text','阅读章节，撰写批注并保存文件到本地。'),
    ('review','核对统计数据，把复核结果写入文件。'),
    ('code','运行单元测试，检查失败记录，将结果保存到本地文件。'),
])
def test_local_output_boundary_applies_without_code_word_matching(kind,text):
    obj={'workload':[{'features':[{'kind':kind,'evidence':text},{'kind':'submit','evidence':text}]}]}
    with pytest.raises(FormalValidationError):validate_workload_semantics(obj,text)
    validate_workload_semantics(obj,text+'最后上传给助教。')


@pytest.mark.parametrize('text',['编写清洗脚本并检查结果','调试程序并核对数值','运行代码并检查日志'])
def test_explicit_code_without_delivery_is_not_submit(text):
    obj={'workload':[{'features':[{'kind':'code'},{'kind':'submit','evidence':text}]}]}
    with pytest.raises(FormalValidationError) as exc:validate_workload_semantics(obj,text)
    assert exc.value.feedback['invariant_id']=='code_is_not_external_delivery'


def test_short_quote_of_code_result_review_is_not_external_delivery():
    source='编写数据分析程序，运行后核对结果。'
    obj={'workload':[{'features':[{'kind':'code'},{'kind':'submit','evidence':'核对结果'}]}]}
    with pytest.raises(FormalValidationError):validate_workload_semantics(obj,source)
    validate_workload_semantics(obj,source+'最后上传课程系统。')


def test_rewritten_workload_evidence_cannot_silently_drop_actual_work():
    value={'workload':[{'features':[{'kind':'short_text','evidence':'重新写摘要'}]}]}
    with pytest.raises(FormalValidationError) as exc:
        validate_workload_semantics(value,'请修订摘要并提交。')
    assert exc.value.feedback['code']=='workload_evidence_mismatch'
    assert exc.value.feedback['field_path']=='$.workload[0].features[0].evidence'
    assert '重新写摘要' not in json.dumps(exc.value.feedback,ensure_ascii=False)


@pytest.mark.parametrize('text',['比较给定算法的结果并说明差异','对比两个表格并写出结论','compare the provided results'])
def test_comparison_is_not_evidence_of_external_lookup(text):
    with pytest.raises(FormalValidationError) as exc:
        validate_workload_semantics({'workload':[{'features':[{'kind':'research','evidence':text}]}]},text)
    assert exc.value.feedback['invariant_id']=='comparison_is_not_source_lookup'


def test_comparison_with_explicit_lookup_stays_research():
    text='查阅相关文献并比较理论依据'
    validate_workload_semantics({'workload':[{'features':[{'kind':'research','evidence':text}]}]},text)


def test_production_uses_one_semantic_repair_for_grounded_conflict():
    draft,gold,valid=candidate(6);invalid=deepcopy(valid)
    invalid['workload'][0]['features'].append(dict(kind='submit',units=1,evidence=gold['text']))
    calls=[]
    def recognize(s,u):
        calls.append(json.loads(u));return ToolArguments(json.dumps(invalid if len(calls)==1 else valid))
    result=extract_material(draft,recognize,workload_caller=lambda s,u:estimate_response(json.loads(u)))
    assert len(calls)==2 and result.status=='ready'
    assert calls[1]['validation_feedback'][0]['code']=='workload_semantic_mismatch'
    assert result.estimate_fallbacks[0]['origin']=='model_workload'
