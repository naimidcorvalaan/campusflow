from datetime import datetime

import pytest

from src.p2_agentic_models import (
    DAY_PLAN_INTENT_SCHEMA_VERSION,
    MAX_CALLS_PER_ROUND,
    P2AgenticDayResult,
    RECONCILIATION_SCHEMA_VERSION,
    REVIEW_SCHEMA_VERSION,
    DayPlanIntent,
    LifecycleAction,
    ReconciliationResult,
    ReconciliationUpdate,
    ReviewResult,
    TaskEstimate,
    TotalSourceChoice,
)
from src.p2_allocator import allocate_tasks_across_windows
from src.p2_day_plan import summarize_day_plan
from src.p2_models import DayPlanningState


def make_update(**overrides):
    values = dict(
        target_task_ref="day_task_001",
        new_task_title=None,
        progress_delta_minutes=None,
        set_total_minutes=None,
        set_total_source=None,
        lifecycle_action=LifecycleAction.NONE,
        is_splittable=None,
        minimum_slice_minutes=None,
    )
    values.update(overrides)
    return ReconciliationUpdate(**values)


def minimal_state():
    now = datetime(2026, 9, 1, 9, 0)
    return DayPlanningState(now, now, datetime(2026, 9, 1, 22, 0), (), (), (), None, (), ())


def test_schema_version_constants():
    assert RECONCILIATION_SCHEMA_VERSION == "p2.task-reconciliation.v1"
    assert DAY_PLAN_INTENT_SCHEMA_VERSION == "p2.day-plan-intent.v1"
    assert REVIEW_SCHEMA_VERSION == "p2.day-review.v1"
    assert MAX_CALLS_PER_ROUND == 24


def test_reconciliation_update_valid_existing():
    update = make_update(progress_delta_minutes=30)
    assert update.target_task_ref == "day_task_001"
    assert update.progress_delta_minutes == 30
    assert update.lifecycle_action == LifecycleAction.NONE


def test_reconciliation_update_valid_new_task():
    update = make_update(target_task_ref=None, new_task_title="整理操作系统实验")
    assert update.new_task_title == "整理操作系统实验"


def test_update_requires_target_or_new_title():
    with pytest.raises(ValueError):
        make_update(target_task_ref=None, new_task_title=None)


def test_update_rejects_both_target_and_new_title():
    with pytest.raises(ValueError):
        make_update(new_task_title="新标题")


def test_update_rejects_bool_progress():
    with pytest.raises(ValueError):
        make_update(progress_delta_minutes=True)


def test_update_rejects_negative_progress():
    with pytest.raises(ValueError):
        make_update(progress_delta_minutes=0)


def test_update_rejects_invalid_lifecycle():
    with pytest.raises(ValueError):
        make_update(lifecycle_action="pending")  # type: ignore


def test_update_rejects_total_without_source():
    with pytest.raises(ValueError):
        make_update(set_total_minutes=120, set_total_source=None)


def test_update_rejects_source_without_total():
    with pytest.raises(ValueError):
        make_update(set_total_minutes=None, set_total_source=TotalSourceChoice.USER_TEXT)


def test_update_rejects_invalid_source_choice():
    with pytest.raises(ValueError):
        make_update(set_total_minutes=120, set_total_source="user_text")  # type: ignore


def test_reconciliation_result_version_mismatch():
    with pytest.raises(ValueError):
        ReconciliationResult("p2.wrong.v1", (make_update(),), ())


def test_reconciliation_result_valid():
    result = ReconciliationResult(
        RECONCILIATION_SCHEMA_VERSION, (make_update(),), ("哪个实验？",)
    )
    assert result.questions == ("哪个实验？",)


def test_reconciliation_result_questions_validated():
    with pytest.raises(ValueError):
        ReconciliationResult(RECONCILIATION_SCHEMA_VERSION, (), ("",))


def test_task_estimate_validation():
    assert TaskEstimate("day_task_001", 100, True, 30).estimated_total_minutes == 100
    with pytest.raises(ValueError):
        TaskEstimate("day_task_001", 0, True, 30)
    with pytest.raises(ValueError):
        TaskEstimate("day_task_001", 100, True, 0)


def test_day_plan_intent_validation():
    with pytest.raises(ValueError):
        DayPlanIntent(DAY_PLAN_INTENT_SCHEMA_VERSION, ("day_task_001",), "yes", (), None)
    with pytest.raises(ValueError):
        DayPlanIntent(DAY_PLAN_INTENT_SCHEMA_VERSION, ("",), False, (), None)


def test_review_result_validation():
    with pytest.raises(ValueError):
        ReviewResult(REVIEW_SCHEMA_VERSION, "maybe", None, None, None)
    assert ReviewResult(REVIEW_SCHEMA_VERSION, "accept", "ok", None, None).decision == "accept"


def test_p2_agentic_day_result_valid():
    state = minimal_state()
    plan = allocate_tasks_across_windows(state)
    summary = summarize_day_plan(plan, state)
    result = P2AgenticDayResult(
        original_state=state,
        updated_state=state,
        reconciliation_result=None,
        day_plan_intent=None,
        allocation_plan=plan,
        day_summary=summary,
        review_result=None,
        revision_used=False,
        call_count=0,
        warnings=(),
        questions=(),
    )
    assert result.call_count == 0
    assert result.revision_used is False


def test_p2_agentic_day_result_rejects_negative_call_count():
    state = minimal_state()
    plan = allocate_tasks_across_windows(state)
    summary = summarize_day_plan(plan, state)
    with pytest.raises(ValueError):
        P2AgenticDayResult(
            original_state=state,
            updated_state=state,
            reconciliation_result=None,
            day_plan_intent=None,
            allocation_plan=plan,
            day_summary=summary,
            review_result=None,
            revision_used=False,
            call_count=-1,
            warnings=(),
            questions=(),
        )


def test_p2_agentic_day_result_rejects_over_budget_call_count():
    state = minimal_state()
    plan = allocate_tasks_across_windows(state)
    summary = summarize_day_plan(plan, state)
    with pytest.raises(ValueError):
        P2AgenticDayResult(
            original_state=state,
            updated_state=state,
            reconciliation_result=None,
            day_plan_intent=None,
            allocation_plan=plan,
            day_summary=summary,
            review_result=None,
            revision_used=False,
            call_count=MAX_CALLS_PER_ROUND + 1,
            warnings=(),
            questions=(),
        )
