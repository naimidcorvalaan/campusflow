import pytest

import src.p1_prompt_builder as prompt_builder
from src.p1_models import MAX_USER_TEXT_LENGTH
from src.p1_extraction_normalizer import normalize_task_extraction
from src.p1_prompt_builder import build_p1_prompt, build_p1_system_prompt, build_p1_user_prompt
from src.p1_parser import parse_task_understanding


def test_system_prompt_defines_schema_features_and_source_contract():
    prompt = build_p1_system_prompt()
    for text in (
        "p1.task-extraction.v2", "location_requirement",
        "estimated_total_minutes", "minimum_slice_minutes",
        "extracted_fields", "estimated_fields",
        "confirmation_fields", "task_ref", "程序生成",
    ):
        assert text in prompt


def test_system_prompt_distinguishes_fixed_commitments_from_todos():
    prompt = build_p1_system_prompt()
    assert "固定安排不是待办" in prompt
    assert "11:30去46楼上课" in prompt


def test_fixed_json_structure_is_parser_compatible():
    prompt = build_p1_system_prompt()
    user_text = "背30分钟单词"
    canonical = normalize_task_extraction(prompt[prompt.index("{"):], user_text)
    result = parse_task_understanding(canonical, user_text)
    assert result.status == "ok"


def test_prompt_requires_sources_and_unsplittable_duration_rule():
    prompt = build_p1_system_prompt()
    assert "三个数组都可以省略" in prompt
    assert "不得重复、冲突或引用空字段" in prompt
    assert "minimum_slice_minutes 必须等于它" in prompt


def test_prompt_allows_optional_source_arrays_and_teaches_generic_split_location_semantics():
    prompt = build_p1_system_prompt()
    assert "三个数组都可以省略" in prompt
    assert "无法证明来自用户原话时，宁可作为 AI 暂估" in prompt
    assert "no_specific_location" in prompt and "不要仅因此要求确认" in prompt
    assert "完成计组实验3" in prompt and "30～90 分钟片段" in prompt
    assert "不要因为“完成”二字就默认不可拆分" in prompt


def test_prompt_forbids_route_and_state_outputs():
    prompt = build_p1_system_prompt()
    for text in ("路线", "ETA", "时间窗口", "首选方案", "任务进度", "生命周期"):
        assert text in prompt


def test_system_and_user_prompts_are_separate():
    user_text = "给导师发送修改后的独特表格"
    system_prompt, user_prompt = build_p1_prompt(user_text)
    assert user_text in user_prompt
    assert user_text not in system_prompt


@pytest.mark.parametrize("value", [None, 1, [], {}, "", "  "])
def test_user_prompt_rejects_invalid_input(value):
    expected = TypeError if not isinstance(value, str) else ValueError
    with pytest.raises(expected):
        build_p1_user_prompt(value)


def test_user_prompt_rejects_overlong_input_and_module_has_no_api_client():
    with pytest.raises(ValueError):
        build_p1_user_prompt("字" * (MAX_USER_TEXT_LENGTH + 1))
    assert not hasattr(prompt_builder, "call_tju_llm")
