import json
import sqlite3
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace

from src.local_persistence import LocalProfileStore
from src.p2_live_main import (
    _initialize_settings_draft,
    _restore_local_profile_once,
    _run_estimator_action,
    _settings_draft_from_widgets,
    _settings_widget_key,
)
from src.p2_session import P2SessionController
from src.p2_window_derivation import derive_day_state
from src.p3_campus_registry import DEFAULT_CAMPUS_REGISTRY
from src.p4_execution_context import (
    CurrentLocationSource,
    ExecutableTaskBinding,
    ExecutionPlanContext,
)
from src.p5_agent_context import build_agent_decision_context
from src.p5_what_if import fingerprint_context
from src.personal_settings import (
    CourseOccurrenceOverride,
    CourseTemplate,
    PersonalSettings,
    SavedCampusPlace,
    apply_personal_defaults_to_context,
    capture_occurrence_overrides,
    expand_timetable,
    inject_timetable,
    load_personal_settings,
    occurrence_ref,
    personal_location_alias,
    save_personal_settings,
    saved_place_options,
)
from src.profile_identity import CURRENT_USER_CONTEXT_KEY, legacy_identity
from src.task_estimation import load_task_estimate_draft, make_material


def dt(hour=9, minute=0, day=7):
    return datetime(2026, 9, day, hour, minute)


def course(**overrides):
    values = dict(
        template_ref="course_math", title="高等数学", weekday=1,
        start_time="10:00", end_time="11:30", start_week=1, end_week=16,
        week_pattern="every", campus_id="weijinlu", location_text="第九教学楼",
        location_node_id="weijinlu_teaching_9", location_display_name="第九教学楼",
    )
    values.update(overrides)
    return CourseTemplate(**values)


def settings(**overrides):
    values = dict(
        revision=1, travel_mode="bike", learning_rhythm="rest_breaks",
        lunch_start="11:30", lunch_end="12:50",
        dinner_start="17:30", dinner_end="18:50",
        default_note="我做数学通常比较慢，可能需要查笔记",
        semester_first_monday="2026-09-07", semester_end_date="2026-12-31",
        courses=(course(),),
    )
    values.update(overrides)
    return PersonalSettings(**values)


def empty_state(day=7):
    return derive_day_state(dt(9, day=day), dt(23, day=day), (), (), reference_datetime=dt(9, day=day))


def estimate_payload():
    return json.dumps({
        "schema_version": "campusflow.task-estimate.v1", "understood": True,
        "task_name": "高数作业", "scope_summary": "完成第三章", "completion_criteria": "完成并检查",
        "min_focus_minutes": 40, "max_focus_minutes": 70, "recommended_minutes": 60,
        "basis": "按范围估算", "assumptions": [], "clarification_needed": False,
        "clarification_question": None, "adjustment_basis": None, "location_text": None,
        "deadline_time": None, "is_splittable": True, "minimum_chunk_minutes": 15,
        "preferred_chunk_minutes": 30, "requires_single_session": False,
    }, ensure_ascii=False)


class EstimateCaller:
    def __init__(self):
        self.calls = []

    def __call__(self, system, user):
        self.calls.append((system, user))
        return estimate_payload()


def test_settings_round_trip_and_additive_fields_preserve_existing_rows(tmp_path):
    path = tmp_path / "campusflow.sqlite3"
    store = LocalProfileStore(path)
    assert store.save(0, estimate_draft=None, added_estimate_drafts={}, personal_settings=settings()) == 1
    reopened = LocalProfileStore(path).load()
    assert reopened.personal_settings == settings()

    connection = sqlite3.connect(str(path))
    from src.local_persistence import PROFILE_SCHEMA_VERSION
    assert connection.execute("SELECT schema_version FROM profile_meta").fetchone()[0] == PROFILE_SCHEMA_VERSION
    assert connection.execute("SELECT COUNT(*) FROM estimate_snapshot").fetchone()[0] == 1
    connection.close()


def test_product_defaults_and_walk_hint_survive_reopen(tmp_path):
    defaults = PersonalSettings()
    assert defaults.travel_mode == "walk"
    assert defaults.learning_rhythm == "rest_breaks"
    saved = replace(defaults, default_walk_hint_seen=True)
    path = tmp_path / "defaults.sqlite3"
    LocalProfileStore(path).save(
        0, estimate_draft=None, added_estimate_drafts={}, personal_settings=saved
    )
    assert LocalProfileStore(path).load().personal_settings.default_walk_hint_seen is True


def test_legacy_unspecified_preferences_use_new_product_defaults():
    settings = PersonalSettings(travel_mode=None, learning_rhythm=None)

    assert ("default_transport_mode", "walk") in settings.planning_context_items()
    assert ("default_learning_rhythm", "rest_breaks") in settings.planning_context_items()
    assert "希望适当穿插休息" in settings.estimation_context()


def test_additive_personal_setting_field_loads_from_older_snapshot(tmp_path):
    path = tmp_path / "older-settings.sqlite3"
    LocalProfileStore(path).save(
        0, estimate_draft=None, added_estimate_drafts={},
        personal_settings=PersonalSettings(),
    )
    connection = sqlite3.connect(str(path))
    payload = json.loads(connection.execute(
        "SELECT payload_json FROM personal_settings"
    ).fetchone()[0])
    payload["fields"].pop("default_walk_hint_seen")
    connection.execute(
        "UPDATE personal_settings SET payload_json=?",
        (json.dumps(payload, ensure_ascii=False),),
    )
    connection.commit()
    connection.close()
    assert LocalProfileStore(path).load().personal_settings.default_walk_hint_seen is False


def test_saved_place_candidates_use_map_categories_and_stay_campus_scoped():
    for campus_id in ("beiyangyuan", "weijinlu"):
        map_data = DEFAULT_CAMPUS_REGISTRY.get_campus_map(campus_id)
        dorms = saved_place_options(map_data, "dormitory")
        study = saved_place_options(map_data, "study")
        assert dorms and study
        assert all(item.category == "dormitory" for item in dorms)
        assert all(item.category in ("teaching", "teaching_building", "library") for item in study)
        map_ids = {item.id for item in map_data.nodes}
        assert all(item.id in map_ids for item in dorms + study)


def test_new_session_restores_settings_without_model_or_plan_action(tmp_path):
    path = tmp_path / "settings.sqlite3"
    LocalProfileStore(path).save(
        0, estimate_draft=None, added_estimate_drafts={}, personal_settings=settings()
    )
    fresh = SimpleNamespace(session_state={})
    fresh.session_state[CURRENT_USER_CONTEXT_KEY] = legacy_identity()
    _restore_local_profile_once(fresh, dt(), lambda: LocalProfileStore(path))
    assert load_personal_settings(fresh.session_state) == settings()
    assert "p2_state" not in fresh.session_state
    assert "p4_live_final_turn" not in fresh.session_state


def test_long_term_default_enters_new_estimate_but_current_supplement_stays_separate():
    caller = EstimateCaller()
    store = {}
    save_personal_settings(store, settings())
    _run_estimator_action(
        store,
        SimpleNamespace(caller=caller, repair_caller=caller),
        SimpleNamespace(task_estimation_caller=None, supports_image_inputs=False),
        make_material(text="完成高数第三章"),
        "这次只做奇数题",
    )
    payload = json.loads(caller.calls[0][1])
    assert "数学通常比较慢" in payload["saved_default_context"]
    assert payload["supplemental_context"] == "这次只做奇数题"
    assert payload["context_priority"].startswith("current supplemental_context")


def test_saving_defaults_does_not_rewrite_an_existing_estimate_result():
    caller = EstimateCaller()
    store = {}
    _run_estimator_action(
        store,
        SimpleNamespace(caller=caller, repair_caller=caller),
        SimpleNamespace(task_estimation_caller=None, supports_image_inputs=False),
        make_material(text="完成高数第三章"),
        "这次只做奇数题",
    )
    before = load_task_estimate_draft(store)
    save_personal_settings(store, settings())
    assert load_task_estimate_draft(store) is before
    assert before.result.recommended_minutes == 60


def test_unsaved_settings_widgets_remain_an_isolated_draft():
    store = {}
    original = settings()
    save_personal_settings(store, original)
    _initialize_settings_draft(store)
    store[_settings_widget_key("default_note")] = "这只是尚未保存的编辑"
    candidate = _settings_draft_from_widgets(store)
    assert candidate.default_note == "这只是尚未保存的编辑"
    assert candidate.revision == original.revision + 1
    assert load_personal_settings(store) is original


def test_legacy_unmatched_place_is_not_rebound_outside_role_choices():
    store = {}
    original = PersonalSettings(saved_places=(
        SavedCampusPlace("weijinlu", "study", "31斋"),
    ))
    save_personal_settings(store, original)
    _initialize_settings_draft(store)

    candidate = _settings_draft_from_widgets(store)

    retained = candidate.place_for("weijinlu", "study")
    assert retained.raw_text == "31斋"
    assert retained.node_id is None


def test_defaults_apply_to_execution_facts_without_overriding_explicit_transport():
    context = ExecutionPlanContext(bindings=(
        ExecutableTaskBinding(
            task_ref="meal", activity_kind="meal", effective_duration_minutes=40,
            duration_source="meal_default", meal_period="dinner",
            meal_temporal_source="inferred_meal_window",
            meal_window_start_minutes=17 * 60, meal_window_end_minutes=19 * 60,
        ),
    ))
    defaulted = apply_personal_defaults_to_context(context, settings())
    assert defaulted.transport_mode == "bike"
    meal = defaulted.binding_for("meal")
    assert (meal.meal_window_start_minutes, meal.meal_window_end_minutes) == (17 * 60 + 30, 18 * 60 + 50)

    explicit = context.with_transport_mode("walk")
    assert apply_personal_defaults_to_context(explicit, settings(), True).transport_mode == "walk"


def test_saved_dormitory_is_a_campus_scoped_destination_not_current_location():
    saved = SavedCampusPlace(
        "weijinlu", "dormitory", "31斋", "weijinlu_dorm_31", "31斋"
    )
    profile = settings(saved_places=(saved,))
    assert personal_location_alias("回宿舍", "weijinlu", profile) == saved
    assert personal_location_alias("回宿舍", "beiyangyuan", profile) is None
    assert ExecutionPlanContext().current_location.source is CurrentLocationSource.UNKNOWN


def test_timetable_expands_by_planning_date_week_pattern_and_selected_campus_only():
    profile = settings(courses=(
        course(),
        course(template_ref="course_other", campus_id="beiyangyuan", title="跨校区课程", start_time="16:00", end_time="17:00"),
        course(template_ref="course_even", week_pattern="even", title="双周课程", start_time="14:00", end_time="15:00"),
    ))
    selected, other = expand_timetable(profile, dt(day=7).date(), "weijinlu")
    assert [item.commitment_ref for item in selected] == ["schedule_course_math_20260907"]
    assert [item.commitment_ref for item in other] == ["schedule_course_other_20260907"]
    assert selected[0].starts_at == dt(10)
    assert selected[0].ends_at == dt(11, 30)
    assert selected[0].commitment_kind == "class"


def test_timetable_injection_is_idempotent_and_dedupes_same_occurrence_not_name():
    state = empty_state()
    first = inject_timetable(state, settings(), "weijinlu")
    second = inject_timetable(first.state, settings(), "weijinlu")
    assert len(first.state.commitments) == len(second.state.commitments) == 1
    assert first.added_commitment_refs == ("schedule_course_math_20260907",)
    assert second.added_commitment_refs == ()


def test_timetable_reconciles_changed_or_removed_templates_on_explicit_update():
    first = inject_timetable(empty_state(), settings(), "weijinlu")
    moved = settings(courses=(course(start_time="12:00", end_time="13:30"),))
    second = inject_timetable(
        first.state, moved, "weijinlu", default_safety_buffer_minutes=7
    )
    assert len(second.state.commitments) == 1
    assert second.state.commitments[0].starts_at == dt(12)
    assert second.state.windows[0].safety_buffer_minutes == 7

    removed = inject_timetable(second.state, settings(courses=()), "weijinlu")
    assert removed.state.commitments == ()


def test_timetable_dedupes_same_named_time_slot_but_not_same_name_at_another_time():
    generated = expand_timetable(settings(), dt(day=7).date(), "weijinlu")[0][0]
    user_course = replace(
        generated, commitment_ref="user_course", location_text="9教"
    )
    existing = derive_day_state(
        dt(9), dt(23), (user_course,), (), reference_datetime=dt(9)
    )
    deduped = inject_timetable(existing, settings(), "weijinlu")
    assert [item.commitment_ref for item in deduped.state.commitments] == ["user_course"]

    later = settings(courses=(course(start_time="12:00", end_time="13:30"),))
    separate = inject_timetable(existing, later, "weijinlu")
    assert len(separate.state.commitments) == 2


def test_occurrence_override_changes_only_one_date_and_never_template():
    ref = occurrence_ref("course_math", dt(day=7).date())
    override = CourseOccurrenceOverride(
        ref, "replace", starts_at=dt(14), ends_at=dt(15, 30), location_text="第九教学楼"
    )
    base = settings(occurrence_overrides=(override,))
    selected, _ = expand_timetable(base, dt(day=7).date(), "weijinlu")
    assert selected[0].starts_at == dt(14)
    assert base.courses[0].start_time == "10:00"

    original = inject_timetable(empty_state(), settings(), "weijinlu").state
    cancelled_state = derive_day_state(
        original.now, original.day_end, (), original.tasks,
        reference_datetime=original.reference_datetime,
    )
    captured = capture_occurrence_overrides(settings(), original, cancelled_state)
    assert captured.occurrence_overrides[0].action == "cancel"
    assert captured.courses[0].start_time == "10:00"
    assert expand_timetable(captured, dt(day=7).date(), "weijinlu")[0] == ()


def test_personal_revision_participates_in_what_if_fingerprint():
    state = empty_state()
    context = ExecutionPlanContext()
    one = build_agent_decision_context(state, context, "weijinlu", personal_settings=settings(revision=1))
    two = build_agent_decision_context(state, context, "weijinlu", personal_settings=settings(revision=2))
    assert fingerprint_context(one) != fingerprint_context(two)


def test_controller_uses_saved_timetable_only_when_planning_is_explicitly_started():
    calls = []

    def caller(system, user):
        calls.append((system, user))
        if "Day Plan Agent" in system:
            return json.dumps({
                "schema_version": "p2.day-plan-intent.v1", "task_order": [],
                "include_low_attention": False, "notes": [], "clarification_question": None,
            })
        return json.dumps({
            "schema_version": "p2.day-review.v1", "decision": "accept",
            "reason": None, "suggested_task_order": None, "include_low_attention": None,
        })

    store = {}
    save_personal_settings(store, settings())
    controller = P2SessionController(
        store, caller, map_data=DEFAULT_CAMPUS_REGISTRY.get_campus_map("weijinlu"),
        campus_id="weijinlu",
    )
    assert store.get("p2_state") is None and calls == []
    turn = controller.start_day(empty_state())
    assert [item.commitment_ref for item in turn.result.updated_state.commitments] == [
        "schedule_course_math_20260907"
    ]
    assert calls
