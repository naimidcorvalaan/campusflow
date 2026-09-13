from datetime import datetime
from types import SimpleNamespace

import pytest

from scripts.low_input_model import LowInputModel, STORIES
from src.p2_live_main import _handle_intake_submit, make_live_session
from src.p2_session import load_live_final_turn
from src.p2_main import _pending_questions, render_page_text
from src.p2_models import SourceKind
from src.p3_campus_registry import DEFAULT_CAMPUS_REGISTRY
from src.personal_settings import PersonalSettings, CourseTemplate, save_personal_settings


def journey(story, estimate=True, settings=None):
    store = {}
    if settings:
        save_personal_settings(store, settings)
    model = LowInputModel(story, estimate)
    campus = "beiyangyuan"
    map_data = DEFAULT_CAMPUS_REGISTRY.get_campus_map(campus)
    session = make_live_session(store, model, map_data=map_data, campus_id=campus,
                               companion_enabled=True, agent_intelligence_enabled=True)
    messages = []
    st = SimpleNamespace(session_state=store, error=messages.append,
                         warning=messages.append, write=messages.append)
    assert _handle_intake_submit(st, session, (), datetime(2026, 9, 7, 14), STORIES[story],
        map_data, campus, current_location_text="", rerun_after_commit=False), messages
    return store, model, load_live_final_turn(store)


@pytest.mark.parametrize("story", ("a", "b", "c"))
def test_one_text_field_produces_plan_without_location_or_duration_form(story):
    settings = None
    if story == "c":
        settings = PersonalSettings(default_note="我做数学通常比较慢，可能查笔记",
            semester_first_monday="2026-09-07", semester_end_date="2026-12-31",
            courses=(CourseTemplate("saved_class", "高等数学", 1, "19:00", "20:30", 1, 16,
                "every", "beiyangyuan", "第31教学楼"),))
    store, model, bundle = journey(story, settings=settings)
    work = bundle.state.tasks[0]
    assert work.total_minutes == 45
    assert work.total_source is SourceKind.AI_ESTIMATED
    assert work.completed_minutes == 0
    assert any(a.task_ref == work.task_ref for a in bundle.result.allocation_plan.allocations)
    assert len(_pending_questions(bundle.turn, bundle.extra_questions)) <= 1
    plan_prompts = [user for system, user in model.prompts if "day-plan-intent" in system]
    assert STORIES[story] in plan_prompts[0]
    if story == "b":
        assert "AI暂估" in render_page_text(bundle.turn, bundle.extra_questions)
        assert not _pending_questions(bundle.turn, bundle.extra_questions)
        assert bundle.execution_context.current_location.location is None
    if story == "a":
        course = bundle.state.commitments[0]
        assert course.starts_at.hour == 19 and course.ends_at is None
        windows = {w.window_ref: w for w in bundle.state.windows}
        assert all(windows[a.window_ref].ends_at <= course.starts_at
                   for a in bundle.result.allocation_plan.allocations)
    if story == "c":
        assert len(bundle.state.commitments) == 1
        assert bundle.state.commitments[0].ends_at.hour == 20
        assert "我做数学通常比较慢" in plan_prompts[0]
        assert "高等数学" in model.prompts[0][1]


def test_unestimable_work_stays_unknown_and_explains_estimator_next_step():
    _, _, bundle = journey("b", estimate=False)
    assert bundle.state.tasks[0].total_minutes is None
    assert not bundle.result.allocation_plan.allocations
    text = render_page_text(bundle.turn, bundle.extra_questions)
    assert "任务时间不确定" in text
    assert "60分钟" not in text


def test_automatic_reference_advances_but_manual_and_restored_manual_do_not():
    from src.p2_live_main import (_sync_reference_clock, LIVE_REFERENCE_HOUR_KEY,
        LIVE_REFERENCE_MINUTE_KEY, LIVE_REFERENCE_SOURCE_KEY, _clear_profile_session)
    store = {}
    _sync_reference_clock(store, datetime(2026, 9, 7, 14))
    _sync_reference_clock(store, datetime(2026, 9, 7, 14, 20))
    assert store[LIVE_REFERENCE_MINUTE_KEY] == 20
    store[LIVE_REFERENCE_HOUR_KEY] = 16
    _sync_reference_clock(store, datetime(2026, 9, 7, 14, 21))
    assert store[LIVE_REFERENCE_HOUR_KEY] == 16
    assert store[LIVE_REFERENCE_SOURCE_KEY] == "manual"
    store["p2_reference_reset_requested"] = True
    _sync_reference_clock(store, datetime(2026, 9, 7, 14, 22))
    assert store[LIVE_REFERENCE_HOUR_KEY] == 14
    _clear_profile_session(store)
    assert "p2_reference_last_auto" not in store
    store.update({LIVE_REFERENCE_HOUR_KEY: 13, LIVE_REFERENCE_MINUTE_KEY: 10,
                  LIVE_REFERENCE_SOURCE_KEY: "restored_manual"})
    _sync_reference_clock(store, datetime(2026, 9, 7, 14, 25))
    assert store[LIVE_REFERENCE_HOUR_KEY] == 13


def test_estimator_confirms_unknown_existing_task_without_duplicating_or_reestimating():
    from src.task_estimation import run_task_estimation, make_material, confirm_estimated_task
    store, model, before = journey("b", estimate=False)
    task_ref = before.state.tasks[0].task_ref
    material = make_material("整理今天的笔记，把三处标记的内容补全")
    result = run_task_estimation("low-input-estimate", material, "", model.agent_caller)
    draft = result.draft
    confirmed = confirm_estimated_task(draft, material, "", draft.result.task_name,
        draft.result.scope_summary, draft.result.completion_criteria, 30)
    session = make_live_session(store, model, map_data=DEFAULT_CAMPUS_REGISTRY.get_campus_map("beiyangyuan"),
        campus_id="beiyangyuan", companion_enabled=True, agent_intelligence_enabled=True)
    estimate_calls = model.calls.count("estimate")
    added = session.add_confirmed_estimated_task_atomic(confirmed, existing_task_ref=task_ref)
    assert len(added.bundle.state.tasks) == 1
    task = added.bundle.state.tasks[0]
    assert task.task_ref == task_ref and task.title == before.state.tasks[0].title
    assert task.total_minutes == 30 and task.completed_minutes == 0
    assert task.total_source is SourceKind.USER_CONFIRMED
    assert model.calls.count("estimate") == estimate_calls
    calls = len(model.calls)
    assert session.add_confirmed_estimated_task_atomic(confirmed, existing_task_ref=task_ref).duplicate
    assert len(model.calls) == calls
