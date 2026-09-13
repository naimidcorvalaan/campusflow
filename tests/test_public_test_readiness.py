from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts import deployment_check
from scripts.server_readiness import ReadinessError, check_server, health_url
from scripts.sqlite_profile_backup import (
    BackupError,
    backup_database,
    restore_dry_run,
    verify_database,
)
from src.local_persistence import LocalProfileStore, ProfileDirectory, PROFILE_SCHEMA_VERSION
from src.personal_settings import PersonalSettings
from src.profile_identity import (
    AUTHENTICATION_CONTEXT_KEY,
    CURRENT_USER_CONTEXT_KEY,
    activate_authentication_context,
    authenticated_context,
    authentication_context_for_profile,
    current_authentication_context,
    current_identity,
    new_anonymous_identity,
)


def test_authentication_boundary_maps_only_verified_internal_identity():
    context = authenticated_context("user-opaque-a", "test-oidc", "identity-a")
    page = {}

    identity = activate_authentication_context(page, context)

    assert identity.user_id == "user-opaque-a"
    assert identity.persistent is True
    assert current_identity(page) == identity
    assert current_authentication_context(page) == context
    assert not hasattr(context, "student_identifier")
    assert not hasattr(context, "credential")


def test_anonymous_authentication_context_remains_ephemeral():
    identity = new_anonymous_identity()
    context = authentication_context_for_profile(identity)

    assert context.authenticated is False
    assert context.identity_provider is None
    assert context.persistent is False
    assert context.internal_user_id == identity.user_id


def test_authenticated_sessions_keep_profile_data_and_revisions_isolated(tmp_path):
    path = tmp_path / "profiles.sqlite3"
    directory = ProfileDirectory(path)
    profile_a = directory.resolve_student("3026200001")
    profile_b = directory.resolve_student("3026200002")
    context_a = authenticated_context(profile_a.user_id, "test-oidc", "identity-a")
    context_b = authenticated_context(profile_b.user_id, "test-oidc", "identity-b")
    page_a = {AUTHENTICATION_CONTEXT_KEY: context_a}
    page_b = {AUTHENTICATION_CONTEXT_KEY: context_b}

    store_a = directory.store_for(current_identity(page_a))
    store_b = directory.store_for(current_identity(page_b))
    assert store_a.save(0, personal_settings=PersonalSettings(default_note="A档案")) == 1
    assert store_b.load().personal_settings is None
    assert store_b.save(0, personal_settings=PersonalSettings(default_note="B档案")) == 1

    assert store_a.load().revision == 1
    assert store_b.load().revision == 1
    assert store_a.load().personal_settings.default_note == "A档案"
    assert store_b.load().personal_settings.default_note == "B档案"


def test_online_backup_preserves_two_profile_scopes_and_is_a_stable_snapshot(tmp_path):
    source = tmp_path / "campusflow.sqlite3"
    directory = ProfileDirectory(source)
    profile_a = directory.resolve_student("3026200001")
    profile_b = directory.resolve_student("3026200002")
    directory.store_for(profile_a).save(
        0, personal_settings=PersonalSettings(default_note="A档案")
    )
    directory.store_for(profile_b).save(
        0, personal_settings=PersonalSettings(default_note="B档案")
    )

    backup = backup_database(
        source,
        tmp_path / "backups",
        now=datetime(2026, 9, 6, 6, 7, 8, tzinfo=timezone.utc),
    )
    directory.store_for(profile_a).save(
        1, personal_settings=PersonalSettings(default_note="A档案新版本")
    )

    result = verify_database(backup)
    assert result.schema_versions == (PROFILE_SCHEMA_VERSION,)
    assert result.profile_count == 2
    assert LocalProfileStore(backup, profile_a.user_id, "student").load().personal_settings.default_note == "A档案"
    assert LocalProfileStore(backup, profile_b.user_id, "student").load().personal_settings.default_note == "B档案"


def test_restore_dry_run_never_changes_existing_target(tmp_path):
    source = tmp_path / "source.sqlite3"
    target = tmp_path / "target.sqlite3"
    source_profile = ProfileDirectory(source).resolve_student("3026200001")
    target_profile = ProfileDirectory(target).resolve_student("3026200002")
    LocalProfileStore(source, source_profile.user_id, "student").save(
        0, personal_settings=PersonalSettings(default_note="来源")
    )
    LocalProfileStore(target, target_profile.user_id, "student").save(
        0, personal_settings=PersonalSettings(default_note="目标")
    )
    backup = backup_database(source, tmp_path / "backups")
    target_before = target.read_bytes()

    plan = restore_dry_run(backup, target)

    assert plan["target_exists"] is True
    assert plan["target_profiles"] == 1
    assert target.read_bytes() == target_before
    assert LocalProfileStore(target, target_profile.user_id, "student").load().personal_settings.default_note == "目标"


def test_backup_verification_rejects_corrupt_input_without_deleting_it(tmp_path):
    corrupt = tmp_path / "corrupt.sqlite3"
    corrupt.write_bytes(b"not a sqlite database")

    with pytest.raises(BackupError, match="无法验证"):
        verify_database(corrupt)

    assert corrupt.read_bytes() == b"not a sqlite database"


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, limit):
        assert limit == 64
        return b"ok"


def test_readiness_probe_uses_streamlit_health_endpoint_without_credentials():
    seen = []

    def opener(url, timeout):
        seen.append((url, timeout))
        return _Response()

    target = check_server("https://campusflow.test.example/base/", opener=opener)

    assert target == "https://campusflow.test.example/base/_stcore/health"
    assert seen == [(target, 5.0)]
    with pytest.raises(ReadinessError):
        health_url("file:///tmp/campusflow")
    with pytest.raises(ReadinessError, match="不能包含凭据"):
        health_url("https://user:secret@campusflow.test.example")


def test_deployment_diagnostics_report_names_not_secret_values(tmp_path, monkeypatch, capsys):
    data_dir = tmp_path / "outside-source"
    monkeypatch.setenv("CAMPUSFLOW_DATA_DIR", str(data_dir))
    monkeypatch.setenv("TJU_LLM_API_KEY", "never-print-this-secret")
    monkeypatch.setenv("TJU_LLM_BASE_URL", "https://secret-endpoint.invalid/v1")
    monkeypatch.setenv("TJU_LLM_MODEL", "private-model-label")

    assert deployment_check.main() == 0
    output = capsys.readouterr().out
    assert "model_configuration=present" in output
    assert "never-print-this-secret" not in output
    assert "secret-endpoint" not in output
    assert "private-model-label" not in output


def test_linux_deployment_templates_do_not_depend_on_windows_paths():
    root = Path(__file__).resolve().parents[1]
    service = (root / "deploy" / "campusflow.service.example").read_text(encoding="utf-8")
    environment = (root / "deploy" / "campusflow.env.example").read_text(encoding="utf-8")
    nginx = (root / "deploy" / "nginx-campusflow.conf.example").read_text(encoding="utf-8")
    oidc = (root / "deploy" / "streamlit-oidc.secrets.toml.example").read_text(
        encoding="utf-8"
    )

    assert "LOCALAPPDATA" not in service + environment + nginx + oidc
    assert "\\\\" not in service
    assert "--server.address=127.0.0.1" in service
    assert "--server.port=8501" in service
    assert "CAMPUSFLOW_DATA_DIR=/var/lib/campusflow" in environment
    assert "replace_on_server" in environment
    assert "auth_request_set $campusflow_auth_subject" in nginx
    assert "proxy_set_header X-CampusFlow-Auth-Subject $campusflow_auth_subject" in nginx
    assert 'proxy_set_header Authorization ""' in nginx
    assert "client_secret = \"replace_" in oidc
    assert "oauth2callback" in oidc
