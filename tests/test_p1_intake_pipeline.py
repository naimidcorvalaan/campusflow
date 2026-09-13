import json
import threading
from datetime import datetime

from src.p1_intake_pipeline import run_p1_intake


TEXT = "我现在在31楼，11:30去46楼上课"
NOW = datetime(2026, 5, 1, 10, 5)


def task_json(tasks=None):
    return json.dumps({"schema_version": "p1.task-understanding.v1", "task_interpretations": [] if tasks is None else tasks, "clarification_questions": []}, ensure_ascii=False)


def window_json():
    return json.dumps({"schema_version": "p1.window-context.v1", "current_context": {"current_location_text": "31楼", "current_location_fragment": "31楼", "assumed_current_datetime": None, "assumed_current_datetime_fragment": None, "field_evidence": {"current_location_text": {"source": "ai_extracted_from_user_text", "explanation": "用户说明当前位置"}}, "needs_confirmation": []}, "commitments": [{"commitment_ref": "class-1", "title": "上课", "original_text": "11:30去46楼上课", "starts_at": "2026-05-01T11:30:00", "ends_at": None, "location_text": "46楼", "availability_during": None, "field_evidence": {"starts_at": {"source": "ai_extracted_from_user_text", "explanation": "用户说明开始时间"}, "location_text": {"source": "ai_extracted_from_user_text", "explanation": "用户说明安排地点"}}, "needs_confirmation": []}], "window_constraints": {"free_duration_minutes": None, "ends_at": None, "user_buffer_minutes": None, "field_evidence": {}, "needs_confirmation": []}, "clarification_questions": []}, ensure_ascii=False)


def compact_task_json():
    return json.dumps({"schema_version": "p1.task-extraction.v2", "tasks": []}, ensure_ascii=False)


def compact_window_json():
    return json.dumps({
        "schema_version": "p1.window-extraction.v2",
        "current_location": {"text": "31楼", "fragment": "31楼"},
        "commitments": [{"title": "上课", "original_text": "11:30去46楼上课",
                         "starts_at": "2026-05-01T11:30:00", "ends_at": None,
                         "location_text": "46楼", "availability_during": None}],
        "free_duration_minutes": None, "ends_at": None, "user_buffer_minutes": None,
    }, ensure_ascii=False)


class Replies:
    def __init__(self, values): self.values, self.calls = list(values), 0
    def __call__(self, system, user):
        self.calls += 1
        return self.values.pop(0)


def test_two_first_calls_succeed_and_empty_tasks_are_successful():
    task, window = Replies([task_json()]), Replies([window_json()])
    result = run_p1_intake(TEXT, NOW, task, window)
    assert result.task_attempts == result.window_attempts == 1
    assert result.bundle.task_result.document is not None and result.bundle.tasks == ()
    assert result.bundle.primary_question.field_name == "task_input"


def test_first_calls_can_start_in_parallel():
    barrier = threading.Barrier(2)
    def caller(reply):
        def run(system, user):
            barrier.wait(timeout=2)
            return reply
        return run
    result = run_p1_intake(TEXT, NOW, caller(task_json()), caller(window_json()))
    assert result.bundle.extraction_status.value == "complete"


def test_only_failed_task_branch_retries_once_and_success_branch_is_not_recalled():
    task, window = Replies(["bad", task_json()]), Replies([window_json()])
    result = run_p1_intake(TEXT, NOW, task, window)
    assert result.task_attempts == 2 and result.window_attempts == 1
    assert task.calls == 2 and window.calls == 1


def test_only_failed_window_branch_retries_once():
    task, window = Replies([task_json()]), Replies(["bad", window_json()])
    result = run_p1_intake(TEXT, NOW, task, window)
    assert result.task_attempts == 1 and result.window_attempts == 2
    assert task.calls == 1 and window.calls == 2


def test_both_fail_only_twice_and_partial_result_is_preserved():
    task, window = Replies([task_json()]), Replies(["bad", "still bad"])
    result = run_p1_intake(TEXT, NOW, task, window)
    assert task.calls == 1 and window.calls == 2
    assert result.bundle.tasks == () and result.bundle.window_document is None
    assert "bad" not in " ".join(result.bundle.safe_error_summaries)


def test_both_first_attempts_fail_and_each_retries_only_once():
    task, window = Replies(["bad", task_json()]), Replies(["bad", window_json()])
    result = run_p1_intake(TEXT, NOW, task, window)
    assert result.task_attempts == result.window_attempts == 2
    assert task.calls == window.calls == 2
    assert result.bundle.extraction_status.value == "complete"


def test_exceptions_are_safe_and_other_branch_survives():
    def broken(system, user): raise RuntimeError("Authorization Bearer secret https://x")
    result = run_p1_intake(TEXT, NOW, broken, Replies([window_json()]))
    assert result.bundle.window_document is not None
    assert "Authorization" not in " ".join(result.bundle.safe_error_summaries)


def test_compact_v2_branches_normalize_before_existing_strict_parsers():
    result = run_p1_intake(TEXT, NOW, Replies([compact_task_json()]), Replies([compact_window_json()]))
    assert result.bundle.extraction_status.value == "complete"
    assert result.bundle.window_document.commitments[0].commitment_ref == "input-commitment-1"


def test_retry_prompt_contains_safe_failure_category_not_first_raw_output():
    marker = "UNIQUE_RAW_OUTPUT Authorization Bearer https://invalid"
    task = Replies([marker, compact_task_json()])
    window = Replies([compact_window_json()])
    result = run_p1_intake(TEXT, NOW, task, window)
    assert result.task_attempts == 2
    # Replies only records counts; use a recording callable for prompt assertions.
    prompts = []
    outputs = iter([marker, compact_task_json()])

    def recording_task(system, user):
        prompts.append(user)
        return next(outputs)

    run_p1_intake(TEXT, NOW, recording_task, Replies([compact_window_json()]))
    assert "失败类别：JSON 格式错误" in prompts[1]
    assert marker not in prompts[1]
    assert "Authorization" not in prompts[1] and "Bearer" not in prompts[1]
