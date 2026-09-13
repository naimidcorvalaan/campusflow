import json
import sqlite3
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.external_auth import (
    VerifiedExternalIdentity,
    streamlit_oidc_identity,
    verified_external_identity,
)
from src.local_persistence import (
    LocalPersistenceConflict,
    LocalProfileStore,
    ProfileDirectory,
)
from src.p2_live_main import (
    EXTERNAL_AUTH_MODE_KEY,
    LIVE_CURRENT_LOCATION_KEY,
    LOCAL_PROFILE_STORE_KEY,
    _persist_local_profile,
    _run_estimator_action,
    _synchronize_external_identity,
)
from src.p5_what_if import WHAT_IF_PREVIEW_KEY
from src.personal_settings import PersonalSettings, load_personal_settings, save_personal_settings
from src.profile_identity import (
    AUTHENTICATION_CONTEXT_KEY,
    CURRENT_USER_CONTEXT_KEY,
    current_authentication_context,
    current_identity,
)
from src.task_estimation import make_material


class Caller:
    def __init__(self):
        self.calls = []

    def __call__(self, system, user):
        self.calls.append((system, user))
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


def now():
    return datetime(2026, 9, 6, 14, 0)


def factory(path):
    return lambda: LocalProfileStore(path)


def assertion(subject, provider="mock-oidc"):
    return verified_external_identity(provider, subject)


class FakeOIDCUser(dict):
    @property
    def is_logged_in(self):
        return self["is_logged_in"]


def test_streamlit_oidc_adapter_uses_only_authenticated_subject():
    assert streamlit_oidc_identity(
        FakeOIDCUser(is_logged_in=False, sub="ignored"), "test-oidc"
    ) is None
    resolved = streamlit_oidc_identity(
        FakeOIDCUser(
            is_logged_in=True,
            sub="stable-alice-subject",
            access_token="must-not-cross-boundary",
        ),
        "test-oidc",
    )
    assert resolved == assertion("stable-alice-subject", "test-oidc")
    assert not hasattr(resolved, "access_token")
    with pytest.raises(ValueError, match="stable subject"):
        streamlit_oidc_identity(FakeOIDCUser(is_logged_in=True), "test-oidc")


def test_external_subject_maps_stably_and_provider_can_be_linked(tmp_path):
    path = tmp_path / "profiles.sqlite3"
    directory = ProfileDirectory(path)

    first = directory.resolve_external_identity(assertion("alice-subject"))
    again = directory.resolve_external_identity(assertion("alice-subject"))
    bob = directory.resolve_external_identity(assertion("bob-subject"))
    second_link = directory.link_external_identity(
        first.identity, assertion("alice-at-new-provider", "future-tju-sso")
    )
    migrated_provider = directory.resolve_external_identity(
        assertion("alice-at-new-provider", "future-tju-sso")
    )

    assert again == first
    assert bob.identity.user_id != first.identity.user_id
    assert migrated_provider.identity.user_id == first.identity.user_id
    assert migrated_provider.identity_link_id == second_link


def test_authenticated_alice_bob_alice_round_trip_clears_session_facts(tmp_path):
    path = tmp_path / "profiles.sqlite3"
    page = {}

    alice = _synchronize_external_identity(page, factory(path), lambda: assertion("alice"), now())
    save_personal_settings(page, PersonalSettings(default_note="Alice设置"))
    page[LIVE_CURRENT_LOCATION_KEY] = "Alice位置"
    page[WHAT_IF_PREVIEW_KEY] = "Alice未采用预演"
    _persist_local_profile(page, reference=now(), now=now())

    bob = _synchronize_external_identity(page, factory(path), lambda: assertion("bob"), now())
    assert bob.user_id != alice.user_id
    assert load_personal_settings(page).default_note == ""
    assert LIVE_CURRENT_LOCATION_KEY not in page
    assert WHAT_IF_PREVIEW_KEY not in page
    save_personal_settings(page, PersonalSettings(default_note="Bob设置"))
    _persist_local_profile(page, reference=now(), now=now())

    restored = _synchronize_external_identity(
        page, factory(path), lambda: assertion("alice"), now()
    )
    assert restored.user_id == alice.user_id
    assert load_personal_settings(page).default_note == "Alice设置"
    assert LIVE_CURRENT_LOCATION_KEY not in page
    assert WHAT_IF_PREVIEW_KEY not in page


def test_logout_clears_authenticated_scope_and_returns_anonymous(tmp_path):
    path = tmp_path / "profiles.sqlite3"
    page = {}
    _synchronize_external_identity(page, factory(path), lambda: assertion("alice"), now())
    page[LIVE_CURRENT_LOCATION_KEY] = "Alice位置"
    page[WHAT_IF_PREVIEW_KEY] = "Alice预演"

    identity = _synchronize_external_identity(page, factory(path), lambda: None, now())

    assert identity.kind == "anonymous"
    assert current_authentication_context(page).authenticated is False
    assert LIVE_CURRENT_LOCATION_KEY not in page
    assert WHAT_IF_PREVIEW_KEY not in page
    assert LOCAL_PROFILE_STORE_KEY not in page


def test_unverified_identity_input_fails_closed_and_query_cannot_select_profile(tmp_path):
    path = tmp_path / "profiles.sqlite3"
    page = {"query_subject": "bob"}
    identity = _synchronize_external_identity(page, factory(path), lambda: None, now())
    assert identity.kind == "anonymous"

    _synchronize_external_identity(page, factory(path), lambda: assertion("alice"), now())
    page[LIVE_CURRENT_LOCATION_KEY] = "Alice位置"
    with pytest.raises(TypeError, match="invalid assertion"):
        _synchronize_external_identity(
            page,
            factory(path),
            lambda: {"provider": "mock-oidc", "subject": "bob"},
            now(),
        )

    assert current_identity(page).kind == "anonymous"
    assert LIVE_CURRENT_LOCATION_KEY not in page


def test_configured_auth_mode_never_falls_back_to_local_profile_selector(tmp_path):
    path = tmp_path / "profiles.sqlite3"
    directory = ProfileDirectory(path)
    local_profile = directory.resolve_student("3026200001")
    page = {
        CURRENT_USER_CONTEXT_KEY: local_profile,
        LOCAL_PROFILE_STORE_KEY: directory.store_for(local_profile),
        "p2_local_profile_revision": 0,
        LIVE_CURRENT_LOCATION_KEY: "旧档案位置",
    }

    identity = _synchronize_external_identity(page, factory(path), lambda: None, now())

    assert page[EXTERNAL_AUTH_MODE_KEY] is True
    assert identity.kind == "anonymous"
    assert LOCAL_PROFILE_STORE_KEY not in page
    assert LIVE_CURRENT_LOCATION_KEY not in page


def test_student_identifier_is_optional_metadata_not_authentication(tmp_path):
    path = tmp_path / "profiles.sqlite3"
    directory = ProfileDirectory(path)
    alice = directory.resolve_external_identity(assertion("alice"))
    bob = directory.resolve_external_identity(assertion("bob"))

    directory.associate_student_identifier(alice.identity, "3026200001")

    assert directory.has_student_identifier(alice.identity) is True
    assert directory.resolve_external_identity(assertion("alice")).identity.user_id == alice.identity.user_id
    assert directory.resolve_external_identity(assertion("bob")).identity.user_id == bob.identity.user_id
    assert bob.identity.user_id != alice.identity.user_id
    with pytest.raises(LocalPersistenceConflict, match="已关联其他档案"):
        directory.associate_student_identifier(bob.identity, "3026200001")


def test_provider_tokens_are_not_part_of_schema_context_or_database(tmp_path):
    path = tmp_path / "profiles.sqlite3"
    directory = ProfileDirectory(path)
    resolution = directory.resolve_external_identity(assertion("opaque-subject"))
    context_page = {}
    _synchronize_external_identity(
        context_page, factory(path), lambda: assertion("opaque-subject"), now()
    )
    context = context_page[AUTHENTICATION_CONTEXT_KEY]

    with sqlite3.connect(str(path)) as connection:
        columns = tuple(
            row[1] for row in connection.execute("PRAGMA table_info(external_identity_links)")
        )
    assert "token" not in " ".join(columns).lower()
    assert not hasattr(context, "token")
    assert not hasattr(context, "provider_subject")
    assert context.internal_user_id == resolution.identity.user_id
    assert b"provider-token-must-never-be-stored" not in path.read_bytes()


def test_schema_v3_migrates_additively_without_losing_profile(tmp_path):
    path = tmp_path / "profiles.sqlite3"
    directory = ProfileDirectory(path)
    original = directory.resolve_student("3026200001")
    directory.store_for(original).save(
        0, personal_settings=PersonalSettings(default_note="v3保留内容")
    )
    with sqlite3.connect(str(path)) as connection:
        connection.execute("DROP TABLE external_identity_links")
        connection.execute("UPDATE profile_meta SET schema_version=3")
        connection.commit()

    reopened = ProfileDirectory(path)
    restored = reopened.resolve_student("3026200001", create=False)
    snapshot = reopened.store_for(restored).load()

    assert restored.user_id == original.user_id
    assert snapshot.personal_settings.default_note == "v3保留内容"
    with sqlite3.connect(str(path)) as connection:
        from src.local_persistence import PROFILE_SCHEMA_VERSION
        assert connection.execute("SELECT schema_version FROM profile_meta").fetchone()[0] == PROFILE_SCHEMA_VERSION
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='external_identity_links'"
        ).fetchone() == (1,)


def test_authenticated_context_and_subject_do_not_enter_estimation_prompt(tmp_path):
    path = tmp_path / "profiles.sqlite3"
    page = {}
    identity = _synchronize_external_identity(
        page, factory(path), lambda: assertion("private-provider-subject"), now()
    )
    caller = Caller()
    session = SimpleNamespace(caller=caller, repair_caller=caller)
    adapter = SimpleNamespace(task_estimation_caller=None, supports_image_inputs=False)

    _run_estimator_action(page, session, adapter, make_material(text="整理第三章笔记"), "")

    prompt = "\n".join(value for call in caller.calls for value in call)
    assert "private-provider-subject" not in prompt
    assert "mock-oidc" not in prompt
    assert identity.user_id not in prompt


def test_private_streamlit_header_probe_is_not_imported_by_production_source():
    source_root = Path(__file__).resolve().parents[1] / "src"
    contents = "\n".join(
        path.read_text(encoding="utf-8") for path in source_root.glob("*.py")
    )
    assert "_get_websocket_headers" not in contents
