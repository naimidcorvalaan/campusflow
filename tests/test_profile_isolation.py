import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from src.local_persistence import (
    LocalPersistenceConflict,
    LocalPersistenceError,
    LocalProfileStore,
    ProfileDirectory,
    default_data_directory,
)
from src.p2_live_main import (
    LOCAL_PROFILE_STORE_KEY,
    _persist_local_profile,
    _restore_local_profile_once,
    _run_estimator_action,
    _switch_to_anonymous_profile,
    _switch_to_student_profile,
)
from src.personal_settings import PersonalSettings, load_personal_settings, save_personal_settings
from src.profile_identity import (
    AUTHENTICATION_CONTEXT_KEY,
    CURRENT_USER_CONTEXT_KEY,
    ProfileIdentity,
    authenticated_context,
    current_identity,
    new_anonymous_identity,
    normalize_student_identifier,
)
from src.task_estimation import make_material


def now():
    return datetime(2026, 9, 6, 14, 0)


def estimate_payload():
    return json.dumps({
        "schema_version": "campusflow.task-estimate.v1",
        "understood": True,
        "task_name": "整理笔记",
        "scope_summary": "整理第三章笔记",
        "completion_criteria": "完成整理并检查",
        "min_focus_minutes": 20,
        "max_focus_minutes": 40,
        "recommended_minutes": 30,
        "basis": "根据任务范围估算",
        "assumptions": [],
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


class Caller:
    def __init__(self):
        self.calls = []

    def __call__(self, system, user):
        self.calls.append((system, user))
        return estimate_payload()


def factory(path):
    return lambda: LocalProfileStore(path)


def test_student_identifier_is_only_a_profile_selector_not_identity_claim():
    assert normalize_student_identifier(" 3026200001 ") == "3026200001"
    with pytest.raises(ValueError, match="不是登录或身份认证|不验证身份"):
        normalize_student_identifier("student name")
    identity = ProfileIdentity("user-opaque", True, "student")
    assert not hasattr(identity, "student_identifier")


def test_data_directory_is_explicit_or_platform_appropriate(tmp_path):
    configured = tmp_path / "configured"
    assert default_data_directory(
        {"CAMPUSFLOW_DATA_DIR": str(configured)}, "linux", tmp_path / "home"
    ) == configured
    assert default_data_directory(
        {"LOCALAPPDATA": str(tmp_path / "local")}, "win32", tmp_path / "home"
    ) == tmp_path / "local" / "CampusFlow"
    assert default_data_directory({}, "linux", tmp_path / "home") == (
        tmp_path / "home" / ".local" / "share" / "campusflow"
    )


def test_anonymous_restore_never_opens_persistent_store(tmp_path):
    calls = []
    page = SimpleNamespace(session_state={CURRENT_USER_CONTEXT_KEY: new_anonymous_identity()})

    _restore_local_profile_once(
        page, now(), lambda: calls.append(True) or LocalProfileStore(tmp_path / "data.sqlite3")
    )

    assert calls == []
    assert LOCAL_PROFILE_STORE_KEY not in page.session_state
    assert "临时使用" in page.session_state["p2_local_profile_status"]


def test_two_student_profiles_are_isolated_and_revision_conflicts_are_scoped(tmp_path):
    path = tmp_path / "profiles.sqlite3"
    directory = ProfileDirectory(path)
    alice = directory.resolve_student("3026200001")
    bob = directory.resolve_student("3026200002")
    assert alice.user_id != bob.user_id

    alice_store = directory.store_for(alice)
    bob_store = directory.store_for(bob)
    alice_settings = PersonalSettings(default_note="A档案", revision=1)
    bob_settings = PersonalSettings(default_note="B档案", revision=1)
    assert alice_store.save(0, personal_settings=alice_settings) == 1
    assert bob_store.load().personal_settings is None
    assert bob_store.save(0, personal_settings=bob_settings) == 1
    with pytest.raises(LocalPersistenceConflict):
        alice_store.save(0, personal_settings=alice_settings)
    assert directory.store_for(alice).load().personal_settings.default_note == "A档案"
    assert directory.store_for(bob).load().personal_settings.default_note == "B档案"


def test_profile_switch_clears_user_facts_and_round_trips_a_b(tmp_path):
    path = tmp_path / "profiles.sqlite3"
    page = {CURRENT_USER_CONTEXT_KEY: new_anonymous_identity()}
    _switch_to_student_profile(page, factory(path), "3026200001", now())
    save_personal_settings(page, PersonalSettings(default_note="A档案", revision=1))
    page["p2_live_current_location"] = "31斋"
    _persist_local_profile(page, reference=now(), now=now())

    _switch_to_student_profile(page, factory(path), "3026200002", now())
    assert load_personal_settings(page).default_note == ""
    assert "p2_live_current_location" not in page
    assert "p5_what_if_preview" not in page
    save_personal_settings(page, PersonalSettings(default_note="B档案", revision=1))
    _persist_local_profile(page, reference=now(), now=now())

    _switch_to_student_profile(page, factory(path), "3026200001", now())
    assert load_personal_settings(page).default_note == "A档案"
    assert "p2_live_current_location" not in page
    _switch_to_anonymous_profile(page, now())
    assert current_identity(page).kind == "anonymous"
    assert load_personal_settings(page).default_note == ""
    assert LOCAL_PROFILE_STORE_KEY not in page


def test_local_profile_switch_drops_previous_authenticated_session_context(tmp_path):
    path = tmp_path / "profiles.sqlite3"
    page = {
        AUTHENTICATION_CONTEXT_KEY: authenticated_context(
            "user-authenticated", "test-oidc", "identity-authenticated"
        ),
        CURRENT_USER_CONTEXT_KEY: ProfileIdentity("user-authenticated", True, "student"),
    }

    _switch_to_student_profile(page, factory(path), "3026200002", now())

    assert AUTHENTICATION_CONTEXT_KEY not in page
    assert current_identity(page).user_id != "user-authenticated"


def test_student_identifier_and_user_id_never_enter_estimation_prompt(tmp_path):
    path = tmp_path / "profiles.sqlite3"
    directory = ProfileDirectory(path)
    identity = directory.resolve_student("3026200001")
    page = {
        CURRENT_USER_CONTEXT_KEY: identity,
        LOCAL_PROFILE_STORE_KEY: directory.store_for(identity),
        "p2_local_profile_revision": 0,
    }
    caller = Caller()
    session = SimpleNamespace(caller=caller, repair_caller=caller)
    adapter = SimpleNamespace(task_estimation_caller=None, supports_image_inputs=False)

    _run_estimator_action(page, session, adapter, make_material(text="整理第三章笔记"), "")

    prompt = "\n".join(value for call in caller.calls for value in call)
    assert "3026200001" not in prompt
    assert identity.user_id not in prompt


def test_explicit_legacy_import_copies_only_into_empty_target(tmp_path):
    path = tmp_path / "profiles.sqlite3"
    legacy = LocalProfileStore(path)
    legacy.save(0, personal_settings=PersonalSettings(default_note="旧档案", revision=1))
    directory = ProfileDirectory(path)
    target = directory.resolve_student("3026200001")

    directory.import_legacy_into(target)
    assert directory.store_for(target).load().personal_settings.default_note == "旧档案"
    assert LocalProfileStore(path).load().personal_settings.default_note == "旧档案"
    with pytest.raises(LocalPersistenceConflict):
        directory.import_legacy_into(target)


def test_profile_switch_rejects_two_migration_sources_before_changing_session(tmp_path):
    path = tmp_path / "profiles.sqlite3"
    page = {CURRENT_USER_CONTEXT_KEY: new_anonymous_identity()}
    original = current_identity(page)

    with pytest.raises(LocalPersistenceError, match="请选择一种迁入来源"):
        _switch_to_student_profile(
            page,
            factory(path),
            "3026200001",
            now(),
            import_legacy=True,
            keep_current_session=True,
        )

    assert current_identity(page) == original
    assert not path.exists()


def test_failed_profile_switch_keeps_current_identity_and_session(tmp_path):
    path = tmp_path / "profiles.sqlite3"
    directory = ProfileDirectory(path)
    identity = directory.resolve_student("3026200001")
    profile_store = directory.store_for(identity)
    profile_store.save(0, personal_settings=PersonalSettings(default_note="可靠档案"))
    page = {
        CURRENT_USER_CONTEXT_KEY: identity,
        LOCAL_PROFILE_STORE_KEY: profile_store,
        "p2_local_profile_revision": 0,  # stale on purpose
    }
    save_personal_settings(page, PersonalSettings(default_note="可靠档案"))

    with pytest.raises(LocalPersistenceConflict):
        _switch_to_student_profile(page, factory(path), "3026200002", now())

    assert current_identity(page) == identity
    assert load_personal_settings(page).default_note == "可靠档案"
