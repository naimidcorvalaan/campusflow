import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from src.local_persistence import (
    LocalPersistenceConflict,
    LocalPersistenceError,
    LocalPersistenceIncompatible,
    LocalPersistenceUnreadable,
    LocalProfileStore,
    ProfileDirectory,
    SavedPlanRecord,
)
from src.p1_models import SourceKind
from src.p2_live_main import (
    LOCAL_PROFILE_REVISION_KEY,
    LOCAL_PROFILE_STALE_BUNDLE_KEY,
    LOCAL_PROFILE_STALE_KEY,
    LOCAL_PROFILE_STATUS_KEY,
    LOCAL_PROFILE_STORE_KEY,
    LIVE_CAMPUS_SELECT_KEY,
    LIVE_ESTIMATE_SUPPLEMENT_KEY,
    LIVE_ESTIMATE_TEXT_KEY,
    _persist_local_profile,
    _business_snapshot,
    _persist_after_mutation,
    _prepare_restored_replan,
    _restore_local_profile_once,
)
from src.p2_main import render_page_streamlit
from src.p2_session import P2SessionController, load_live_final_turn
from src.p3_campus_registry import DEFAULT_CAMPUS_REGISTRY
from src.p3_campus_session import SELECTED_CAMPUS_ID_KEY
from src.profile_identity import CURRENT_USER_CONTEXT_KEY, legacy_identity
from src.personal_settings import PERSONAL_TRANSPORT_EXPLICIT_KEY, PersonalSettings
from src.p4_execution_context import (
    CurrentLocationContext, CurrentLocationSource, ExecutionLocation,
    ExecutionLocationSource,
)
from src.task_estimation import (
    TASK_ESTIMATE_ADDED_DRAFTS_KEY,
    TASK_ESTIMATE_DRAFT_KEY,
    confirm_estimated_task,
    make_material,
    mark_draft_added,
    run_task_estimation,
)


def dt(hour, minute=0, day=5):
    return datetime(2026, 9, day, hour, minute)


class QueueCaller:
    def __init__(self, *values):
        self.values = list(values)
        self.calls = []

    def __call__(self, system, user):
        self.calls.append((system, user))
        if self.values:
            return self.values.pop(0)
        return json.dumps({
            "schema_version": "p2.day-review.v1",
            "decision": "accept",
            "reason": None,
            "suggested_task_order": None,
            "include_low_attention": None,
        })


def estimate_payload(minutes=30):
    return json.dumps({
        "schema_version": "campusflow.task-estimate.v1",
        "understood": True,
        "task_name": "高数作业第3—6题",
        "scope_summary": "完成第3—6题并检查答案",
        "completion_criteria": "四道题均完成并检查",
        "min_focus_minutes": 25,
        "max_focus_minutes": 40,
        "recommended_minutes": minutes,
        "basis": "根据确认的四道题范围估算",
        "assumptions": ["题目材料完整"],
        "clarification_needed": False,
        "clarification_question": None,
        "adjustment_basis": None,
        "location_text": None,
        "deadline_time": None,
        "is_splittable": True,
        "minimum_chunk_minutes": 15,
        "preferred_chunk_minutes": 30,
        "requires_single_session": False,
    }, ensure_ascii=False)


def build_saved_estimate_plan(path):
    material = make_material(text="完成高数作业第3—6题")
    estimate_caller = QueueCaller(estimate_payload())
    draft = run_task_estimation(
        "estimate_001", material, "我做数学比较慢", estimate_caller
    ).draft
    confirmed = confirm_estimated_task(
        draft, material, draft.supplemental_context,
        draft.result.task_name, draft.result.scope_summary,
        draft.result.completion_criteria, 35,
    )
    formal_store = {}
    planning_caller = QueueCaller()
    controller = P2SessionController(
        formal_store,
        planning_caller,
        map_data=DEFAULT_CAMPUS_REGISTRY.get_campus_map("weijinlu"),
        campus_id="weijinlu",
    )
    outcome = controller.add_confirmed_estimated_task_atomic(
        confirmed, reference_datetime=dt(9)
    )
    added_draft = mark_draft_added(draft, outcome.task_ref)
    formal_store[TASK_ESTIMATE_DRAFT_KEY] = added_draft
    formal_store[SELECTED_CAMPUS_ID_KEY] = "weijinlu"
    formal_store[LOCAL_PROFILE_STORE_KEY] = LocalProfileStore(path)
    formal_store[LOCAL_PROFILE_REVISION_KEY] = 0
    _persist_local_profile(
        formal_store, reference=dt(9), campus_id="weijinlu", now=dt(9, 1)
    )
    return formal_store, planning_caller, outcome.task_ref, confirmed


def test_empty_plan_estimate_add_is_saved_and_restored_through_new_sqlite_connection(tmp_path):
    path = tmp_path / "profile" / "campusflow.sqlite3"
    original, planning_caller, task_ref, confirmed = build_saved_estimate_plan(path)
    original[PERSONAL_TRANSPORT_EXPLICIT_KEY] = True
    _persist_local_profile(
        original, reference=dt(9), campus_id="weijinlu", now=dt(9, 2)
    )
    assert len(planning_caller.calls) == 1

    # This is a genuinely new connection and a fresh Streamlit-like session.
    recovered = LocalProfileStore(path).load()
    assert recovered.revision == 2
    assert recovered.latest_plan.final_turn is not load_live_final_turn(original)
    task = next(item for item in recovered.latest_plan.final_turn.state.tasks if item.task_ref == task_ref)
    binding = recovered.latest_plan.final_turn.execution_context.binding_for(task_ref)
    assert task.total_minutes == 35
    assert task.completed_minutes == 0
    assert task.total_source is SourceKind.USER_CONFIRMED
    assert binding.scope_summary == "完成第3—6题并检查答案"
    assert binding.duration_source == "user_explicit"
    assert recovered.estimate_draft.added_task_ref == task_ref
    assert recovered.added_estimate_drafts == {"estimate_001": task_ref}

    fresh = SimpleNamespace(session_state={})
    fresh.session_state[CURRENT_USER_CONTEXT_KEY] = legacy_identity()
    _restore_local_profile_once(fresh, dt(9, 1), lambda: LocalProfileStore(path))
    assert fresh.session_state[SELECTED_CAMPUS_ID_KEY] == "weijinlu"
    assert fresh.session_state[LIVE_CAMPUS_SELECT_KEY] == "卫津路校区"
    assert load_live_final_turn(fresh.session_state).state.tasks[0].task_ref == task_ref
    assert fresh.session_state[LIVE_ESTIMATE_TEXT_KEY] == "完成高数作业第3—6题"
    assert fresh.session_state[LIVE_ESTIMATE_SUPPLEMENT_KEY] == "我做数学比较慢"
    assert fresh.session_state[PERSONAL_TRANSPORT_EXPLICIT_KEY] is True
    assert "已恢复" in fresh.session_state[LOCAL_PROFILE_STATUS_KEY]

    no_model_calls = QueueCaller()
    restored_controller = P2SessionController(
        fresh.session_state,
        no_model_calls,
        map_data=DEFAULT_CAMPUS_REGISTRY.get_campus_map("weijinlu"),
        campus_id="weijinlu",
    )
    repeated = restored_controller.add_confirmed_estimated_task_atomic(
        confirmed, reference_datetime=dt(9)
    )
    assert repeated.duplicate is True
    assert len(fresh.session_state["p2_state"].tasks) == 1
    assert no_model_calls.calls == []


def test_text_draft_manual_minutes_and_stale_state_round_trip_but_image_is_rejected(tmp_path):
    material = make_material(text="整理课程笔记")
    draft = run_task_estimation(
        "estimate_002", material, "只整理第三章", QueueCaller(estimate_payload())
    ).draft
    draft = replace(
        draft, adopted_minutes=45, duration_source="user_modified",
        status="stale", error_message="范围已变化，请重新估算。",
    )
    path = tmp_path / "draft.sqlite3"
    persistence = LocalProfileStore(path)
    revision = persistence.save(0, estimate_draft=draft, added_estimate_drafts={})
    restored = LocalProfileStore(path).load()
    assert revision == 1
    assert restored.estimate_draft.status == "stale"
    assert restored.estimate_draft.adopted_minutes == 45
    assert restored.estimate_draft.duration_source == "user_modified"
    assert restored.estimate_draft.supplemental_context == "只整理第三章"

    image = make_material(
        image_name="task.png", image_mime="image/png",
        image_bytes=b"\x89PNG\r\n\x1a\ncontent",
    )
    with pytest.raises(LocalPersistenceError, match="本地保存失败"):
        persistence.save(
            revision,
            estimate_draft=replace(draft, material=image),
            added_estimate_drafts={},
        )


def test_saving_other_facts_while_image_draft_is_open_preserves_last_text_estimate(tmp_path):
    material = make_material(text="整理课程笔记")
    text_draft = run_task_estimation(
        "estimate_text", material, "", QueueCaller(estimate_payload())
    ).draft
    path = tmp_path / "profile.sqlite3"
    persistence = LocalProfileStore(path)
    revision = persistence.save(
        0, estimate_draft=text_draft, added_estimate_drafts={}
    )
    revision = persistence.save(
        revision, estimate_draft=None, added_estimate_drafts={},
        preserve_estimate_snapshot=True,
    )
    restored = LocalProfileStore(path).load()
    assert restored.revision == revision
    assert restored.estimate_draft == text_draft


def test_cross_day_plan_is_view_only_and_never_activates_old_location_or_tasks(tmp_path):
    path = tmp_path / "profile.sqlite3"
    original, _, _, _ = build_saved_estimate_plan(path)
    original_bundle = load_live_final_turn(original)
    fresh = SimpleNamespace(session_state={})
    fresh.session_state[CURRENT_USER_CONTEXT_KEY] = legacy_identity()
    _restore_local_profile_once(fresh, dt(8, day=6), lambda: LocalProfileStore(path))
    assert load_live_final_turn(fresh.session_state) is None
    assert "p2_state" not in fresh.session_state
    assert "p4_execution_plan_context" not in fresh.session_state
    assert fresh.session_state[LOCAL_PROFILE_STALE_BUNDLE_KEY].version == original_bundle.version
    assert fresh.session_state[LOCAL_PROFILE_STALE_KEY] is True
    assert "未自动复制" in fresh.session_state[LOCAL_PROFILE_STATUS_KEY]


def test_revision_conflict_corrupt_and_incompatible_files_preserve_reliable_record(tmp_path):
    path = tmp_path / "profile.sqlite3"
    store = LocalProfileStore(path)
    assert store.save(0, estimate_draft=None, added_estimate_drafts={}) == 1
    with pytest.raises(LocalPersistenceConflict):
        store.save(0, estimate_draft=None, added_estimate_drafts={})
    assert LocalProfileStore(path).load().revision == 1

    corrupt = tmp_path / "corrupt.sqlite3"
    corrupt.write_bytes(b"not a sqlite database")
    before = corrupt.read_bytes()
    with pytest.raises(LocalPersistenceUnreadable):
        LocalProfileStore(corrupt).load()
    assert corrupt.read_bytes() == before

    incompatible = tmp_path / "old.sqlite3"
    LocalProfileStore(incompatible).save(0, estimate_draft=None, added_estimate_drafts={})
    connection = sqlite3.connect(str(incompatible))
    connection.execute("UPDATE profile_meta SET schema_version=999")
    connection.commit()
    connection.close()
    with pytest.raises(LocalPersistenceIncompatible):
        LocalProfileStore(incompatible).load()


def test_v2_single_profile_database_migrates_to_legacy_scope_without_data_loss(tmp_path):
    source_path = tmp_path / "source.sqlite3"
    original, _, task_ref, _ = build_saved_estimate_plan(source_path)
    expected = LocalProfileStore(source_path).load()
    connection = sqlite3.connect(str(source_path))
    day_row = connection.execute(
        "SELECT plan_date,campus_id,reference_datetime,reference_source,saved_at,payload_json "
        "FROM day_snapshots"
    ).fetchone()
    estimate_row = connection.execute(
        "SELECT payload_json,added_json FROM estimate_snapshot"
    ).fetchone()
    revision = connection.execute("SELECT revision FROM profile_meta").fetchone()[0]
    connection.close()

    old_path = tmp_path / "v2.sqlite3"
    connection = sqlite3.connect(str(old_path))
    connection.execute(
        "CREATE TABLE profile_meta(profile_id INTEGER PRIMARY KEY CHECK(profile_id=1),"
        "schema_version INTEGER NOT NULL,revision INTEGER NOT NULL,updated_at TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE day_snapshots(plan_date TEXT PRIMARY KEY,campus_id TEXT NOT NULL,"
        "reference_datetime TEXT NOT NULL,reference_source TEXT NOT NULL,saved_at TEXT NOT NULL,"
        "payload_json TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE estimate_snapshot(profile_id INTEGER PRIMARY KEY CHECK(profile_id=1),"
        "payload_json TEXT,added_json TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE personal_settings(profile_id INTEGER PRIMARY KEY CHECK(profile_id=1),"
        "payload_json TEXT NOT NULL,updated_at TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO profile_meta VALUES(1,2,?,?)", (revision, dt(9).isoformat())
    )
    connection.execute("INSERT INTO day_snapshots VALUES(?,?,?,?,?,?)", day_row)
    connection.execute("INSERT INTO estimate_snapshot VALUES(1,?,?)", estimate_row)
    connection.commit()
    connection.close()

    migrated = LocalProfileStore(old_path).load()
    assert migrated.user_id == "legacy-local"
    assert migrated.revision == expected.revision
    assert migrated.latest_plan.final_turn.state.tasks[0].task_ref == task_ref
    assert migrated.estimate_draft.added_task_ref == task_ref
    connection = sqlite3.connect(str(old_path))
    assert {row[1] for row in connection.execute("PRAGMA table_info(profile_meta)")} == {
        "user_id", "schema_version", "revision", "updated_at"
    }
    assert connection.execute(
        "SELECT student_identifier FROM profiles WHERE user_id='legacy-local'"
    ).fetchone()[0] is None
    connection.close()


def test_plan_progress_estimate_and_settings_are_partitioned_by_user_id(tmp_path):
    path = tmp_path / "profiles.sqlite3"
    legacy_session, _, task_ref, _ = build_saved_estimate_plan(path)
    legacy = LocalProfileStore(path).load()
    directory = ProfileDirectory(path)
    profile_a = directory.resolve_student("3026200001")
    profile_b = directory.resolve_student("3026200002")
    store_a = directory.store_for(profile_a)
    store_b = directory.store_for(profile_b)

    store_a.save(
        0,
        plan_record=legacy.latest_plan,
        estimate_draft=legacy.estimate_draft,
        added_estimate_drafts=legacy.added_estimate_drafts,
        personal_settings=PersonalSettings(default_note="A档案"),
    )

    snapshot_a = LocalProfileStore(
        path, profile_a.user_id, "student"
    ).load()
    snapshot_b = LocalProfileStore(
        path, profile_b.user_id, "student"
    ).load()
    task_a = next(
        task for task in snapshot_a.latest_plan.final_turn.state.tasks
        if task.task_ref == task_ref
    )
    assert task_a.completed_minutes == 0
    assert snapshot_a.estimate_draft.added_task_ref == task_ref
    assert snapshot_a.added_estimate_drafts
    assert snapshot_a.personal_settings.default_note == "A档案"
    assert snapshot_b.latest_plan is None
    assert snapshot_b.estimate_draft is None
    assert snapshot_b.added_estimate_drafts == {}
    assert snapshot_b.personal_settings is None
    legacy_task = next(
        task for task in load_live_final_turn(legacy_session).state.tasks
        if task.task_ref == task_ref
    )
    assert legacy_task.completed_minutes == 0

def test_incomplete_stable_refs_are_rejected_without_deleting_snapshot(tmp_path):
    path = tmp_path / "profile.sqlite3"
    build_saved_estimate_plan(path)
    connection = sqlite3.connect(str(path))
    raw = connection.execute(
        "SELECT payload_json FROM day_snapshots"
    ).fetchone()[0]
    payload = json.loads(raw)
    bindings = payload["final_turn"]["execution_context"]["fields"]["bindings"]["$tuple"]
    bindings[0]["fields"]["task_ref"] = "missing_task_ref"
    changed = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    connection.execute("UPDATE day_snapshots SET payload_json=?", (changed,))
    connection.commit()
    connection.close()
    with pytest.raises(LocalPersistenceUnreadable):
        LocalProfileStore(path).load()
    connection = sqlite3.connect(str(path))
    assert connection.execute("SELECT payload_json FROM day_snapshots").fetchone()[0] == changed
    connection.close()


def test_failed_save_rolls_back_new_business_state_and_keeps_disk_revision(tmp_path):
    path = tmp_path / "profile.sqlite3"
    original, _, _, _ = build_saved_estimate_plan(path)
    reliable = load_live_final_turn(original)
    snapshot = _business_snapshot(original)
    original["p4_live_final_turn"] = replace(reliable, version=reliable.version + 1)
    original[LOCAL_PROFILE_REVISION_KEY] = 0  # stale page revision

    class PageStub:
        session_state = original

        def __init__(self):
            self.warnings = []

        def warning(self, value):
            self.warnings.append(value)

    page = PageStub()
    assert not _persist_after_mutation(
        page, snapshot, reference=dt(9), campus_id="weijinlu", now=dt(9, 2)
    )
    assert load_live_final_turn(original) is reliable
    assert LocalProfileStore(path).load().revision == 1
    assert any("另一个页面" in item for item in page.warnings)


def test_stale_refresh_rebases_time_without_completing_work_or_trusting_old_location(tmp_path):
    path = tmp_path / "profile.sqlite3"
    store, _, task_ref, _ = build_saved_estimate_plan(path)
    original_task = next(item for item in store["p2_state"].tasks if item.task_ref == task_ref)
    context = store["p4_execution_plan_context"].with_current_location(
        CurrentLocationContext(
            ExecutionLocation(
                "weijinlu", "weijinlu_dorm_31", "31斋",
                ExecutionLocationSource.USER_CURRENT_LOCATION,
            ),
            CurrentLocationSource.USER,
        )
    )
    store["p4_execution_plan_context"] = context
    store[LOCAL_PROFILE_STALE_KEY] = True
    _prepare_restored_replan(store, dt(10), SimpleNamespace(
        default_safety_buffer_minutes=0, travel_minutes_by_commitment={}
    ))
    rebased_task = next(item for item in store["p2_state"].tasks if item.task_ref == task_ref)
    assert store["p2_state"].now == dt(10)
    assert rebased_task.completed_minutes == original_task.completed_minutes == 0
    assert store["p4_execution_plan_context"].current_location.source is CurrentLocationSource.UNKNOWN
    assert context.current_location.source is CurrentLocationSource.USER


class MarkdownStub:
    def __init__(self):
        self.values = []

    def markdown(self, value, **kwargs):
        self.values.append(value)


def test_saved_snapshot_renderer_does_not_claim_old_action_is_current(tmp_path):
    path = tmp_path / "profile.sqlite3"
    original, _, _, _ = build_saved_estimate_plan(path)
    bundle = load_live_final_turn(original)
    stub = MarkdownStub()
    render_page_streamlit(stub, bundle.turn, saved_snapshot=True)
    rendered = "\n".join(stub.values)
    assert "上次保存的方案" in rendered
    assert "保存时" in rendered
    assert "cf-current\"" not in rendered
