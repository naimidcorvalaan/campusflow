from tests.test_p1_context_bundle import task_result, window_result
from src.p1_context_bundle import build_p1_context_bundle
from src.p1_candidate_prompt_builder import build_p1_candidate_prompt


def test_candidate_prompts_are_separate_and_safe():
    bundle = build_p1_context_bundle(task_result(), window_result())
    system, user = build_p1_candidate_prompt(bundle)
    assert "路径" in system and "ETA" in system
    assert "完成实验" in user
    assert "Authorization" not in user
    assert '"schema_version":"p1.candidate.v1"' in system
    assert "missing_information" in system and "no_executable_tasks" in system


def test_task_document_user_text_is_used_when_window_missing():
    from src.p1_context_bundle import build_p1_context_bundle
    from tests.test_p1_context_bundle import task_result, window_result
    user = build_p1_candidate_prompt(build_p1_context_bundle(task_result(), window_result(False)))[1]
    assert '"user_text":"完成实验"' in user


def test_candidate_prompt_prioritizes_partial_progress_for_long_splittable_tasks():
    system = build_p1_candidate_prompt(
        build_p1_context_bundle(task_result(), window_result()))[0]
    assert "is_splittable=true" in system
    assert "minimum_slice_minutes" in system
    assert "先推进一部分" in system
    assert "预计 120 分钟" in system and "30～90 分钟" in system
