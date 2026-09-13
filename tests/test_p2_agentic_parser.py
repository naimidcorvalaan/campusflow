import pytest

from src.p2_agentic_models import LifecycleAction, TotalSourceChoice
from src.p2_agentic_parser import (
    AgenticParseError,
    extract_json_object,
    parse_day_plan_intent,
    parse_reconciliation,
    parse_review,
)

RECONCILIATION_PROGRESS = (
    '{"schema_version": "p2.task-reconciliation.v1", "updates": ['
    '{"target_task_ref": "day_task_001", "new_task_title": null, '
    '"progress_delta_minutes": 30, "set_total_minutes": null, '
    '"set_total_source": null, "lifecycle_action": "none", '
    '"is_splittable": null, "minimum_slice_minutes": null}], "questions": []}'
)

RECONCILIATION_NEW_TASK = (
    '{"schema_version": "p2.task-reconciliation.v1", "updates": ['
    '{"target_task_ref": null, "new_task_title": "整理操作系统实验", '
    '"progress_delta_minutes": null, "set_total_minutes": 120, '
    '"set_total_source": "user_text", "lifecycle_action": "none", '
    '"is_splittable": true, "minimum_slice_minutes": 30}], "questions": []}'
)

DAY_PLAN = (
    '{"schema_version": "p2.day-plan-intent.v1", '
    '"task_order": ["day_task_002", "day_task_001"], '
    '"include_low_attention": false, "task_estimates": ['
    '{"task_ref": "day_task_003", "estimated_total_minutes": 100, '
    '"is_splittable": true, "minimum_slice_minutes": 30}], '
    '"rationale": "先做 B"}'
)

REVIEW_ACCEPT = (
    '{"schema_version": "p2.day-review.v1", "decision": "accept", '
    '"reason": "符合用户意图", "suggested_task_order": null, '
    '"include_low_attention": null}'
)


def test_parse_reconciliation_progress():
    result = parse_reconciliation(RECONCILIATION_PROGRESS)
    assert result.schema_version == "p2.task-reconciliation.v1"
    update = result.updates[0]
    assert update.target_task_ref == "day_task_001"
    assert update.progress_delta_minutes == 30
    assert update.lifecycle_action == LifecycleAction.NONE


def test_parse_reconciliation_new_task():
    result = parse_reconciliation(RECONCILIATION_NEW_TASK)
    update = result.updates[0]
    assert update.target_task_ref is None
    assert update.new_task_title == "整理操作系统实验"
    assert update.set_total_minutes == 120
    assert update.set_total_source == TotalSourceChoice.USER_TEXT
    assert update.is_splittable is True


def test_parse_reconciliation_lifecycle_actions():
    for action in ("skip_today", "abandon", "complete", "resume_today"):
        text = (
            '{"schema_version": "p2.task-reconciliation.v1", "updates": ['
            '{"target_task_ref": "day_task_001", "new_task_title": null, '
            '"progress_delta_minutes": null, "set_total_minutes": null, '
            '"set_total_source": null, "lifecycle_action": "' + action + '", '
            '"is_splittable": null, "minimum_slice_minutes": null}], "questions": []}'
        )
        result = parse_reconciliation(text)
        assert result.updates[0].lifecycle_action.value == action


def test_parse_reconciliation_questions():
    text = (
        '{"schema_version": "p2.task-reconciliation.v1", "updates": [], '
        '"questions": ["你说的是哪个实验？"]}'
    )
    result = parse_reconciliation(text)
    assert result.questions == ("你说的是哪个实验？",)


def test_extract_json_object_with_fence_and_trailing_comma():
    fence = chr(96) * 3
    text = "好的，结果如下：\n" + fence + "\n" + (
        '{"schema_version": "p2.task-reconciliation.v1", "updates": [], '
        '"questions": [],}'
    ) + "\n" + fence
    payload = extract_json_object(text)
    assert payload["schema_version"] == "p2.task-reconciliation.v1"


def test_extract_json_object_rejects_empty():
    with pytest.raises(AgenticParseError):
        extract_json_object("   ")


def test_missing_schema_version_rejected():
    with pytest.raises(AgenticParseError):
        parse_reconciliation('{"updates": [], "questions": []}')


def test_wrong_schema_version_rejected():
    with pytest.raises(AgenticParseError):
        parse_reconciliation(
            '{"schema_version": "p2.wrong.v1", "updates": [], "questions": []}'
        )


def test_progress_bool_rejected():
    text = (
        '{"schema_version": "p2.task-reconciliation.v1", "updates": ['
        '{"target_task_ref": "day_task_001", "new_task_title": null, '
        '"progress_delta_minutes": true, "set_total_minutes": null, '
        '"set_total_source": null, "lifecycle_action": "none", '
        '"is_splittable": null, "minimum_slice_minutes": null}], "questions": []}'
    )
    with pytest.raises(AgenticParseError):
        parse_reconciliation(text)


def test_negative_minutes_rejected():
    text = (
        '{"schema_version": "p2.task-reconciliation.v1", "updates": ['
        '{"target_task_ref": "day_task_001", "new_task_title": null, '
        '"progress_delta_minutes": -5, "set_total_minutes": null, '
        '"set_total_source": null, "lifecycle_action": "none", '
        '"is_splittable": null, "minimum_slice_minutes": null}], "questions": []}'
    )
    with pytest.raises(AgenticParseError):
        parse_reconciliation(text)


def test_invalid_lifecycle_rejected():
    text = (
        '{"schema_version": "p2.task-reconciliation.v1", "updates": ['
        '{"target_task_ref": "day_task_001", "new_task_title": null, '
        '"progress_delta_minutes": null, "set_total_minutes": null, '
        '"set_total_source": null, "lifecycle_action": "pending", '
        '"is_splittable": null, "minimum_slice_minutes": null}], "questions": []}'
    )
    with pytest.raises(AgenticParseError):
        parse_reconciliation(text)


def test_set_total_without_source_rejected():
    text = (
        '{"schema_version": "p2.task-reconciliation.v1", "updates": ['
        '{"target_task_ref": "day_task_001", "new_task_title": null, '
        '"progress_delta_minutes": null, "set_total_minutes": 150, '
        '"set_total_source": null, "lifecycle_action": "none", '
        '"is_splittable": null, "minimum_slice_minutes": null}], "questions": []}'
    )
    with pytest.raises(AgenticParseError):
        parse_reconciliation(text)


def test_both_target_and_new_title_rejected():
    text = (
        '{"schema_version": "p2.task-reconciliation.v1", "updates": ['
        '{"target_task_ref": "day_task_001", "new_task_title": "改名", '
        '"progress_delta_minutes": null, "set_total_minutes": null, '
        '"set_total_source": null, "lifecycle_action": "none", '
        '"is_splittable": null, "minimum_slice_minutes": null}], "questions": []}'
    )
    with pytest.raises(AgenticParseError):
        parse_reconciliation(text)


def test_parse_day_plan_intent():
    intent = parse_day_plan_intent(DAY_PLAN)
    assert intent.task_order == ("day_task_002", "day_task_001")
    assert intent.include_low_attention is False
    estimate = intent.task_estimates[0]
    assert estimate.task_ref == "day_task_003"
    assert estimate.estimated_total_minutes == 100
    assert estimate.minimum_slice_minutes == 30
    assert intent.rationale == "先做 B"


def test_parse_day_plan_empty_rationale_is_none():
    text = (
        '{"schema_version": "p2.day-plan-intent.v1", "task_order": [], '
        '"include_low_attention": false, "task_estimates": [], "rationale": ""}'
    )
    intent = parse_day_plan_intent(text)
    assert intent.rationale is None


def test_parse_day_plan_estimate_total_required():
    text = (
        '{"schema_version": "p2.day-plan-intent.v1", "task_order": [], '
        '"include_low_attention": false, "task_estimates": ['
        '{"task_ref": "day_task_003", "estimated_total_minutes": null, '
        '"is_splittable": true, "minimum_slice_minutes": 30}], "rationale": null}'
    )
    with pytest.raises(AgenticParseError):
        parse_day_plan_intent(text)


def test_parse_review_accept():
    result = parse_review(REVIEW_ACCEPT)
    assert result.decision == "accept"
    assert result.reason == "符合用户意图"
    assert result.suggested_task_order is None


def test_parse_review_revise():
    text = (
        '{"schema_version": "p2.day-review.v1", "decision": "revise", '
        '"reason": "应优先 B", "suggested_task_order": ["day_task_002"], '
        '"include_low_attention": false}'
    )
    result = parse_review(text)
    assert result.decision == "revise"
    assert result.suggested_task_order == ("day_task_002",)
    assert result.include_low_attention is False


def test_parse_review_invalid_decision():
    text = (
        '{"schema_version": "p2.day-review.v1", "decision": "maybe", '
        '"reason": null, "suggested_task_order": null, '
        '"include_low_attention": null}'
    )
    with pytest.raises(AgenticParseError):
        parse_review(text)


def test_sensitive_content_filtered():
    text = (
        '{"schema_version": "p2.day-review.v1", "decision": "revise", '
        '"reason": "API Key: sk-abc 有问题", "suggested_task_order": null, '
        '"include_low_attention": null}'
    )
    result = parse_review(text)
    assert result.reason == "[已过滤敏感内容]"


def test_reason_truncated():
    long_reason = "x" * 1000
    text = (
        '{"schema_version": "p2.day-review.v1", "decision": "accept", '
        '"reason": "' + long_reason + '", "suggested_task_order": null, '
        '"include_low_attention": null}'
    )
    result = parse_review(text)
    assert len(result.reason) <= 400

def test_parse_reconciliation_duplicate_target_rejected():
    text = (
        '{"schema_version": "p2.task-reconciliation.v1", "updates": ['
        '{"target_task_ref": "day_task_001", "new_task_title": null, '
        '"progress_delta_minutes": 30, "set_total_minutes": null, '
        '"set_total_source": null, "lifecycle_action": "none", '
        '"is_splittable": null, "minimum_slice_minutes": null},'
        '{"target_task_ref": "day_task_001", "new_task_title": null, '
        '"progress_delta_minutes": 30, "set_total_minutes": null, '
        '"set_total_source": null, "lifecycle_action": "none", '
        '"is_splittable": null, "minimum_slice_minutes": null}], "questions": []}'
    )
    with pytest.raises(AgenticParseError):
        parse_reconciliation(text)


def test_parse_reconciliation_multiple_new_tasks_allowed():
    text = (
        '{"schema_version": "p2.task-reconciliation.v1", "updates": ['
        '{"target_task_ref": null, "new_task_title": "新任务A", '
        '"progress_delta_minutes": null, "set_total_minutes": null, '
        '"set_total_source": null, "lifecycle_action": "none", '
        '"is_splittable": null, "minimum_slice_minutes": null},'
        '{"target_task_ref": null, "new_task_title": "新任务B", '
        '"progress_delta_minutes": null, "set_total_minutes": null, '
        '"set_total_source": null, "lifecycle_action": "none", '
        '"is_splittable": null, "minimum_slice_minutes": null}], "questions": []}'
    )
    result = parse_reconciliation(text)
    assert len(result.updates) == 2
