"""Versioned, profile-scoped local persistence for CampusFlow.

Only explicit business snapshots are written.  This module never serializes
``st.session_state`` wholesale, model clients, prompts, call traces, secrets,
or uploaded image bytes.  SQLite transactions and a revision compare-and-swap
keep an older browser tab from silently replacing a newer saved record.
"""

import json
import os
import re
import sqlite3
import sys
import uuid
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

from src.external_auth import VerifiedExternalIdentity
from src.profile_identity import LEGACY_USER_ID, ProfileIdentity, normalize_student_identifier


PROFILE_SCHEMA_VERSION = 5
DEFAULT_PROFILE_FILENAME = "campusflow.sqlite3"
_ALLOWED_CLASS_REGISTRY = None


class LocalPersistenceError(RuntimeError):
    pass


class LocalPersistenceConflict(LocalPersistenceError):
    pass


class LocalPersistenceUnreadable(LocalPersistenceError):
    pass


class LocalPersistenceIncompatible(LocalPersistenceError):
    pass


@dataclass(frozen=True)
class SavedPlanRecord:
    plan_date: str
    campus_id: str
    reference_datetime: datetime
    reference_source: str
    saved_at: datetime
    final_turn: object
    day_preferences: Optional[object] = None
    latest_user_text: Optional[str] = None
    latest_feedback_text: Optional[str] = None
    transport_was_explicit: bool = False


@dataclass(frozen=True)
class LocalProfileSnapshot:
    revision: int
    latest_plan: Optional[SavedPlanRecord]
    estimate_draft: Optional[object]
    added_estimate_drafts: dict
    personal_settings: Optional[object] = None
    user_id: str = LEGACY_USER_ID
    material_inbox: Optional[object] = None


@dataclass(frozen=True)
class ExternalIdentityResolution:
    identity: ProfileIdentity
    identity_link_id: str


def default_data_directory(environ=None, platform_name=None, home=None):
    """Resolve a stable data directory without depending on the launch cwd."""
    values = os.environ if environ is None else environ
    platform_name = sys.platform if platform_name is None else platform_name
    home = Path.home() if home is None else Path(home)
    configured = values.get("CAMPUSFLOW_DATA_DIR")
    if configured:
        return Path(configured).expanduser()
    if platform_name.startswith("win") and values.get("LOCALAPPDATA"):
        return Path(values["LOCALAPPDATA"]) / "CampusFlow"
    if platform_name == "darwin":
        return home / "Library" / "Application Support" / "CampusFlow"
    return Path(values.get("XDG_DATA_HOME") or (home / ".local" / "share")) / "campusflow"


def default_profile_path():
    return default_data_directory() / DEFAULT_PROFILE_FILENAME


class LocalProfileStore:
    """SQLite-backed view of one opaque user scope."""

    def __init__(self, path=None, user_id=LEGACY_USER_ID, profile_kind="legacy"):
        self.path = Path(path) if path is not None else default_profile_path()
        if not isinstance(user_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{4,80}", user_id):
            raise ValueError("user_id invalid")
        if profile_kind not in ("legacy", "student"):
            raise ValueError("persistent profile kind invalid")
        self.user_id = user_id
        self.profile_kind = profile_kind

    def for_user(self, identity):
        if not isinstance(identity, ProfileIdentity) or not identity.persistent:
            raise ValueError("persistent profile identity required")
        return LocalProfileStore(self.path, identity.user_id, identity.kind)

    def load(self):
        if not self.path.exists():
            return LocalProfileSnapshot(0, None, None, {}, None, self.user_id)
        try:
            # Opening an existing version-1 profile runs the small additive
            # migration before decoding any business snapshot.
            with self._connect(create=True) as connection:
                self._ensure_profile(connection)
                schema, revision = self._read_meta(connection)
                if schema != PROFILE_SCHEMA_VERSION:
                    raise LocalPersistenceIncompatible(
                        "本地记录版本暂不兼容，原文件已保留。"
                    )
                row = connection.execute(
                    "SELECT plan_date, campus_id, reference_datetime, reference_source, "
                    "saved_at, payload_json FROM day_snapshots "
                    "WHERE user_id=? ORDER BY saved_at DESC LIMIT 1",
                    (self.user_id,),
                ).fetchone()
                plan = None
                if row is not None:
                    payload = json.loads(row[5])
                    if not isinstance(payload, dict) or payload.get("record_version") != 1:
                        raise ValueError("saved plan record version invalid")
                    final_turn = decode_live_final_turn(payload["final_turn"])
                    plan = SavedPlanRecord(
                        row[0], row[1], _decode_datetime(row[2]), row[3],
                        _decode_datetime(row[4]), final_turn,
                        _decode_value(payload.get("day_preferences")),
                        payload.get("latest_user_text"), payload.get("latest_feedback_text"),
                        bool(payload.get("transport_was_explicit", False)),
                    )
                draft_row = connection.execute(
                    "SELECT payload_json, added_json FROM estimate_snapshot WHERE user_id = ?",
                    (self.user_id,),
                ).fetchone()
                draft = None
                added = {}
                if draft_row is not None:
                    if draft_row[0]:
                        draft = decode_task_estimate_draft(json.loads(draft_row[0]))
                    added = _decode_added_mapping(draft_row[1])
                settings_row = connection.execute(
                    "SELECT payload_json FROM personal_settings WHERE user_id = ?",
                    (self.user_id,),
                ).fetchone()
                settings = None
                if settings_row is not None and settings_row[0]:
                    settings = _decode_value(json.loads(settings_row[0]))
                    from src.personal_settings import PersonalSettings
                    if not isinstance(settings, PersonalSettings):
                        raise ValueError("personal settings snapshot invalid")
                material_row = connection.execute(
                    "SELECT payload_json FROM material_snapshot WHERE user_id=?", (self.user_id,)
                ).fetchone()
                material = _decode_value(json.loads(material_row[0])) if material_row else None
                if material is not None:
                    from src.material_inbox import MaterialInbox
                    if not isinstance(material, MaterialInbox):
                        raise ValueError("material snapshot invalid")
                return LocalProfileSnapshot(revision, plan, draft, added, settings, self.user_id, material)
        except (LocalPersistenceError,):
            raise
        except (sqlite3.Error, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise LocalPersistenceUnreadable(
                "本地记录无法读取，原文件已保留；请不要覆盖它。"
            ) from exc

    def save(self, expected_revision, plan_record=None, estimate_draft=None,
             added_estimate_drafts=None, personal_settings=None,
             preserve_estimate_snapshot=False, material_inbox=None):
        """Atomically save supplied facts and return the new revision.

        ``estimate_draft=None`` explicitly stores no draft.  Callers must omit
        image drafts rather than converting image bytes into JSON.
        """
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
            raise TypeError("expected_revision must be an integer")
        if expected_revision < 0:
            raise ValueError("expected_revision must be >= 0")
        if not isinstance(preserve_estimate_snapshot, bool):
            raise TypeError("preserve_estimate_snapshot must be bool")
        if plan_record is not None and not isinstance(plan_record, SavedPlanRecord):
            raise TypeError("plan_record must be SavedPlanRecord or None")
        added = dict(added_estimate_drafts or {})
        _validate_added_mapping(added)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._connect(create=True) as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._ensure_profile(connection)
                schema, current_revision = self._read_meta(connection)
                if schema != PROFILE_SCHEMA_VERSION:
                    raise LocalPersistenceIncompatible(
                        "本地记录版本暂不兼容，原文件已保留。"
                    )
                if current_revision != expected_revision:
                    raise LocalPersistenceConflict(
                        "本地记录已由另一个页面更新，本页未覆盖较新的记录。"
                    )
                if plan_record is not None:
                    payload = json.dumps(
                        {
                            "record_version": 1,
                            "final_turn": encode_live_final_turn(plan_record.final_turn),
                            "day_preferences": _encode_value(plan_record.day_preferences),
                            "latest_user_text": plan_record.latest_user_text,
                            "latest_feedback_text": plan_record.latest_feedback_text,
                            "transport_was_explicit": plan_record.transport_was_explicit,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    connection.execute(
                        "INSERT INTO day_snapshots(user_id, plan_date, campus_id, reference_datetime, "
                        "reference_source, saved_at, payload_json) VALUES(?,?,?,?,?,?,?) "
                        "ON CONFLICT(user_id,plan_date) DO UPDATE SET campus_id=excluded.campus_id, "
                        "reference_datetime=excluded.reference_datetime, "
                        "reference_source=excluded.reference_source, saved_at=excluded.saved_at, "
                        "payload_json=excluded.payload_json",
                        (
                            self.user_id, plan_record.plan_date, plan_record.campus_id,
                            plan_record.reference_datetime.isoformat(),
                            plan_record.reference_source, plan_record.saved_at.isoformat(), payload,
                        ),
                    )
                draft_payload = None
                if estimate_draft is not None:
                    draft_payload = json.dumps(
                        encode_task_estimate_draft(estimate_draft),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                added_payload = json.dumps(
                    added, ensure_ascii=False, separators=(",", ":")
                )
                if preserve_estimate_snapshot:
                    connection.execute(
                        "INSERT INTO estimate_snapshot(user_id, payload_json, added_json) "
                        "VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
                        "added_json=excluded.added_json",
                        (self.user_id, None, added_payload),
                    )
                else:
                    connection.execute(
                        "INSERT INTO estimate_snapshot(user_id, payload_json, added_json) "
                        "VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
                        "payload_json=excluded.payload_json, added_json=excluded.added_json",
                        (self.user_id, draft_payload, added_payload),
                    )
                if personal_settings is not None:
                    from src.personal_settings import PersonalSettings
                    if not isinstance(personal_settings, PersonalSettings):
                        raise TypeError("personal_settings must be PersonalSettings")
                    connection.execute(
                        "INSERT INTO personal_settings(user_id, payload_json, updated_at) "
                        "VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
                        "payload_json=excluded.payload_json, updated_at=excluded.updated_at",
                        (
                            self.user_id,
                            json.dumps(_encode_value(personal_settings), ensure_ascii=False, separators=(",", ":")),
                            datetime.now().isoformat(),
                        ),
                    )
                if material_inbox is not None:
                    from src.material_inbox import MaterialInbox
                    if not isinstance(material_inbox, MaterialInbox):
                        raise TypeError("material inbox invalid")
                    connection.execute(
                        "INSERT INTO material_snapshot(user_id,payload_json) VALUES(?,?) "
                        "ON CONFLICT(user_id) DO UPDATE SET payload_json=excluded.payload_json",
                        (self.user_id, json.dumps(_encode_value(material_inbox), ensure_ascii=False)),
                    )
                new_revision = current_revision + 1
                connection.execute(
                    "UPDATE profile_meta SET revision=?, updated_at=? WHERE user_id=?",
                    (new_revision, datetime.now().isoformat(), self.user_id),
                )
                connection.commit()
                return new_revision
        except (LocalPersistenceError,):
            raise
        except (sqlite3.Error, OSError, ValueError, TypeError) as exc:
            raise LocalPersistenceError("本地保存失败，原有可靠记录未被替换。") from exc

    def _connect(self, create):
        if not create and not self.path.exists():
            raise LocalPersistenceUnreadable("本地记录不存在。")
        connection = sqlite3.connect(str(self.path), timeout=5.0)
        connection.execute("PRAGMA foreign_keys = ON")
        if create:
            self._ensure_schema(connection)
        return connection

    @staticmethod
    def _ensure_schema(connection):
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "profile_meta" in tables:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(profile_meta)")
            }
            if "profile_id" in columns:
                LocalProfileStore._migrate_single_profile_schema(connection, tables)
            elif "user_id" not in columns:
                raise LocalPersistenceIncompatible(
                    "本地记录版本暂不兼容，原文件已保留。"
                )
        LocalProfileStore._create_profile_schema(connection)
        versions = {
            int(row[0]) for row in connection.execute(
                "SELECT DISTINCT schema_version FROM profile_meta"
            )
        }
        if versions and versions.issubset({3, 4, PROFILE_SCHEMA_VERSION}):
            connection.execute(
                "UPDATE profile_meta SET schema_version=? WHERE schema_version IN (3,4)",
                (PROFILE_SCHEMA_VERSION,),
            )
            versions = {PROFILE_SCHEMA_VERSION}
        if versions and versions != {PROFILE_SCHEMA_VERSION}:
            raise LocalPersistenceIncompatible("本地记录版本暂不兼容，原文件已保留。")
        connection.commit()

    @staticmethod
    def _create_profile_schema(connection):
        connection.execute(
            "CREATE TABLE IF NOT EXISTS profiles("
            "user_id TEXT PRIMARY KEY, profile_kind TEXT NOT NULL, "
            "student_identifier TEXT UNIQUE, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS profile_meta("
            "user_id TEXT PRIMARY KEY REFERENCES profiles(user_id) ON DELETE CASCADE, "
            "schema_version INTEGER NOT NULL, revision INTEGER NOT NULL, updated_at TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS day_snapshots("
            "user_id TEXT NOT NULL REFERENCES profiles(user_id) ON DELETE CASCADE, "
            "plan_date TEXT NOT NULL, campus_id TEXT NOT NULL, "
            "reference_datetime TEXT NOT NULL, reference_source TEXT NOT NULL, "
            "saved_at TEXT NOT NULL, payload_json TEXT NOT NULL, "
            "PRIMARY KEY(user_id,plan_date))"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS estimate_snapshot("
            "user_id TEXT PRIMARY KEY REFERENCES profiles(user_id) ON DELETE CASCADE, "
            "payload_json TEXT, added_json TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS personal_settings("
            "user_id TEXT PRIMARY KEY REFERENCES profiles(user_id) ON DELETE CASCADE, "
            "payload_json TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS material_snapshot("
            "user_id TEXT PRIMARY KEY REFERENCES profiles(user_id) ON DELETE CASCADE, "
            "payload_json TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS external_identity_links("
            "identity_link_id TEXT NOT NULL UNIQUE, provider TEXT NOT NULL, "
            "provider_subject TEXT NOT NULL, "
            "user_id TEXT NOT NULL REFERENCES profiles(user_id) ON DELETE CASCADE, "
            "created_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, "
            "PRIMARY KEY(provider,provider_subject))"
        )

    @staticmethod
    def _migrate_single_profile_schema(connection, existing_tables):
        """Move v1/v2 rows into the explicit legacy-local profile atomically."""
        row = connection.execute(
            "SELECT schema_version, revision, updated_at FROM profile_meta WHERE profile_id=1"
        ).fetchone()
        if row is None or int(row[0]) not in (1, 2):
            raise LocalPersistenceIncompatible("本地记录版本暂不兼容，原文件已保留。")
        for name in ("profile_meta", "day_snapshots", "estimate_snapshot", "personal_settings"):
            if name in existing_tables:
                connection.execute("ALTER TABLE {} RENAME TO {}_single_v2".format(name, name))
        LocalProfileStore._create_profile_schema(connection)
        now = datetime.now().isoformat()
        connection.execute(
            "INSERT INTO profiles(user_id,profile_kind,student_identifier,created_at,updated_at) "
            "VALUES(?,?,?,?,?)",
            (LEGACY_USER_ID, "legacy", None, row[2] or now, now),
        )
        connection.execute(
            "INSERT INTO profile_meta(user_id,schema_version,revision,updated_at) VALUES(?,?,?,?)",
            (LEGACY_USER_ID, PROFILE_SCHEMA_VERSION, int(row[1]), now),
        )
        if "day_snapshots" in existing_tables:
            connection.execute(
                "INSERT INTO day_snapshots(user_id,plan_date,campus_id,reference_datetime,"
                "reference_source,saved_at,payload_json) "
                "SELECT ?,plan_date,campus_id,reference_datetime,reference_source,saved_at,payload_json "
                "FROM day_snapshots_single_v2",
                (LEGACY_USER_ID,),
            )
        if "estimate_snapshot" in existing_tables:
            connection.execute(
                "INSERT INTO estimate_snapshot(user_id,payload_json,added_json) "
                "SELECT ?,payload_json,added_json FROM estimate_snapshot_single_v2 WHERE profile_id=1",
                (LEGACY_USER_ID,),
            )
        if "personal_settings" in existing_tables:
            connection.execute(
                "INSERT INTO personal_settings(user_id,payload_json,updated_at) "
                "SELECT ?,payload_json,updated_at FROM personal_settings_single_v2 WHERE profile_id=1",
                (LEGACY_USER_ID,),
            )
        for name in ("profile_meta", "day_snapshots", "estimate_snapshot", "personal_settings"):
            if name in existing_tables:
                connection.execute("DROP TABLE {}_single_v2".format(name))

    def _ensure_profile(self, connection):
        now = datetime.now().isoformat()
        connection.execute(
            "INSERT OR IGNORE INTO profiles(user_id,profile_kind,student_identifier,created_at,updated_at) "
            "VALUES(?,?,?,?,?)",
            (self.user_id, self.profile_kind, None, now, now),
        )
        connection.execute(
            "INSERT OR IGNORE INTO profile_meta(user_id,schema_version,revision,updated_at) "
            "VALUES(?,?,?,?)",
            (self.user_id, PROFILE_SCHEMA_VERSION, 0, now),
        )

    def _read_meta(self, connection):
        row = connection.execute(
            "SELECT schema_version, revision FROM profile_meta WHERE user_id=?",
            (self.user_id,),
        ).fetchone()
        if row is None:
            raise LocalPersistenceUnreadable("本地记录缺少版本信息，原文件已保留。")
        return int(row[0]), int(row[1])


class ProfileDirectory:
    """Resolve local student labels to opaque profile identities.

    The student identifier is an index for a trusted local prototype, not an
    authentication secret.  Callers receive only the opaque identity.
    """

    def __init__(self, path=None):
        self.path = Path(path) if path is not None else default_profile_path()

    def resolve_student(self, student_identifier, create=True):
        normalized = normalize_student_identifier(student_identifier)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with sqlite3.connect(str(self.path), timeout=5.0) as connection:
                connection.execute("PRAGMA foreign_keys = ON")
                LocalProfileStore._ensure_schema(connection)
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT user_id FROM profiles WHERE student_identifier=?",
                    (normalized,),
                ).fetchone()
                if row is None:
                    if not create:
                        return None
                    user_id = "user-" + uuid.uuid4().hex
                    now = datetime.now().isoformat()
                    connection.execute(
                        "INSERT INTO profiles(user_id,profile_kind,student_identifier,created_at,updated_at) "
                        "VALUES(?,?,?,?,?)",
                        (user_id, "student", normalized, now, now),
                    )
                    connection.execute(
                        "INSERT INTO profile_meta(user_id,schema_version,revision,updated_at) "
                        "VALUES(?,?,?,?)",
                        (user_id, PROFILE_SCHEMA_VERSION, 0, now),
                    )
                else:
                    user_id = row[0]
                connection.commit()
                return ProfileIdentity(user_id, True, "student")
        except LocalPersistenceError:
            raise
        except (sqlite3.Error, OSError, ValueError) as exc:
            raise LocalPersistenceError("个人档案暂时无法打开，当前可靠内容未改变。") from exc

    def resolve_external_identity(self, assertion, create=True):
        """Map one already-verified provider subject to an opaque user id."""
        if not isinstance(assertion, VerifiedExternalIdentity):
            raise TypeError("verified external identity required")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with sqlite3.connect(str(self.path), timeout=5.0) as connection:
                connection.execute("PRAGMA foreign_keys = ON")
                LocalProfileStore._ensure_schema(connection)
                row = connection.execute(
                    "SELECT identity_link_id,user_id FROM external_identity_links "
                    "WHERE provider=? AND provider_subject=?",
                    (assertion.provider, assertion.subject),
                ).fetchone()
                if row is not None:
                    link_id, user_id = row
                elif not create:
                    return None
                else:
                    # Existing logins are read-only. Only first login takes a
                    # write reservation, then rechecks to close the race with
                    # another browser session authenticating simultaneously.
                    connection.execute("BEGIN IMMEDIATE")
                    row = connection.execute(
                        "SELECT identity_link_id,user_id FROM external_identity_links "
                        "WHERE provider=? AND provider_subject=?",
                        (assertion.provider, assertion.subject),
                    ).fetchone()
                    if row is not None:
                        link_id, user_id = row
                    else:
                        now = datetime.now().isoformat()
                        user_id = "user-" + uuid.uuid4().hex
                        link_id = "identity-" + uuid.uuid4().hex
                        connection.execute(
                            "INSERT INTO profiles(user_id,profile_kind,student_identifier,created_at,updated_at) "
                            "VALUES(?,?,?,?,?)",
                            (user_id, "student", None, now, now),
                        )
                        connection.execute(
                            "INSERT INTO profile_meta(user_id,schema_version,revision,updated_at) "
                            "VALUES(?,?,?,?)",
                            (user_id, PROFILE_SCHEMA_VERSION, 0, now),
                        )
                        connection.execute(
                            "INSERT INTO external_identity_links(identity_link_id,provider,provider_subject,"
                            "user_id,created_at,last_seen_at) VALUES(?,?,?,?,?,?)",
                            (link_id, assertion.provider, assertion.subject, user_id, now, now),
                        )
                connection.commit()
                return ExternalIdentityResolution(
                    ProfileIdentity(user_id, True, "student"), link_id
                )
        except LocalPersistenceError:
            raise
        except (sqlite3.Error, OSError, ValueError) as exc:
            raise LocalPersistenceError(
                "认证档案暂时无法打开，当前可靠内容未改变。"
            ) from exc

    def link_external_identity(self, identity, assertion):
        """Explicitly attach another verified provider identity to a profile."""
        if not isinstance(identity, ProfileIdentity) or not identity.persistent:
            raise ValueError("persistent profile identity required")
        if not isinstance(assertion, VerifiedExternalIdentity):
            raise TypeError("verified external identity required")
        try:
            with sqlite3.connect(str(self.path), timeout=5.0) as connection:
                connection.execute("PRAGMA foreign_keys = ON")
                LocalProfileStore._ensure_schema(connection)
                connection.execute("BEGIN IMMEDIATE")
                profile = connection.execute(
                    "SELECT 1 FROM profiles WHERE user_id=?", (identity.user_id,)
                ).fetchone()
                if profile is None:
                    raise LocalPersistenceError("目标个人档案不存在，未建立认证关联。")
                existing = connection.execute(
                    "SELECT user_id FROM external_identity_links WHERE provider=? AND provider_subject=?",
                    (assertion.provider, assertion.subject),
                ).fetchone()
                if existing is not None:
                    if existing[0] != identity.user_id:
                        raise LocalPersistenceConflict("该认证身份已关联其他档案，未修改任何记录。")
                    row = connection.execute(
                        "SELECT identity_link_id FROM external_identity_links "
                        "WHERE provider=? AND provider_subject=?",
                        (assertion.provider, assertion.subject),
                    ).fetchone()
                    connection.commit()
                    return row[0]
                now = datetime.now().isoformat()
                link_id = "identity-" + uuid.uuid4().hex
                connection.execute(
                    "INSERT INTO external_identity_links(identity_link_id,provider,provider_subject,"
                    "user_id,created_at,last_seen_at) VALUES(?,?,?,?,?,?)",
                    (link_id, assertion.provider, assertion.subject, identity.user_id, now, now),
                )
                connection.commit()
                return link_id
        except (LocalPersistenceConflict, LocalPersistenceError):
            raise
        except (sqlite3.Error, OSError) as exc:
            raise LocalPersistenceError("认证身份关联失败，原档案未改变。") from exc

    def associate_student_identifier(self, identity, student_identifier):
        """Attach optional profile metadata without treating it as authentication."""
        if not isinstance(identity, ProfileIdentity) or not identity.persistent:
            raise ValueError("persistent profile identity required")
        normalized = normalize_student_identifier(student_identifier)
        try:
            with sqlite3.connect(str(self.path), timeout=5.0) as connection:
                connection.execute("PRAGMA foreign_keys = ON")
                LocalProfileStore._ensure_schema(connection)
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT student_identifier FROM profiles WHERE user_id=?", (identity.user_id,)
                ).fetchone()
                if row is None:
                    raise LocalPersistenceError("目标个人档案不存在，未保存学号。")
                if row[0] not in (None, normalized):
                    raise LocalPersistenceConflict("该档案已有不同学号，未自动覆盖。")
                connection.execute(
                    "UPDATE profiles SET student_identifier=?,updated_at=? WHERE user_id=?",
                    (normalized, datetime.now().isoformat(), identity.user_id),
                )
                connection.commit()
        except (LocalPersistenceConflict, LocalPersistenceError):
            raise
        except sqlite3.IntegrityError as exc:
            raise LocalPersistenceConflict("该学号已关联其他档案，未修改当前档案。") from exc
        except (sqlite3.Error, OSError) as exc:
            raise LocalPersistenceError("学号资料暂时无法保存，原档案未改变。") from exc

    def has_student_identifier(self, identity):
        if not isinstance(identity, ProfileIdentity) or not identity.persistent:
            return False
        if not self.path.exists():
            return False
        try:
            with sqlite3.connect(str(self.path), timeout=5.0) as connection:
                connection.execute("PRAGMA foreign_keys = ON")
                LocalProfileStore._ensure_schema(connection)
                row = connection.execute(
                    "SELECT student_identifier FROM profiles WHERE user_id=?", (identity.user_id,)
                ).fetchone()
                return row is not None and row[0] is not None
        except (sqlite3.Error, OSError, LocalPersistenceError) as exc:
            raise LocalPersistenceError("学号资料状态暂时无法确认。") from exc

    def has_legacy_data(self):
        if not self.path.exists():
            return False
        try:
            with sqlite3.connect(str(self.path), timeout=5.0) as connection:
                connection.execute("PRAGMA foreign_keys = ON")
                LocalProfileStore._ensure_schema(connection)
                row = connection.execute(
                    "SELECT revision FROM profile_meta WHERE user_id=?", (LEGACY_USER_ID,)
                ).fetchone()
                if row is None:
                    return False
                if int(row[0]) > 0:
                    return True
                return any(connection.execute(
                    "SELECT 1 FROM {} WHERE user_id=? LIMIT 1".format(table),
                    (LEGACY_USER_ID,),
                ).fetchone() for table in (
                    "day_snapshots", "estimate_snapshot", "personal_settings", "material_snapshot"
                ))
        except (sqlite3.Error, OSError, LocalPersistenceError) as exc:
            raise LocalPersistenceError("旧本机档案状态暂时无法确认。") from exc

    def import_legacy_into(self, identity):
        if not isinstance(identity, ProfileIdentity) or identity.kind != "student":
            raise ValueError("student profile required")
        try:
            with sqlite3.connect(str(self.path), timeout=5.0) as connection:
                connection.execute("PRAGMA foreign_keys = ON")
                LocalProfileStore._ensure_schema(connection)
                connection.execute("BEGIN IMMEDIATE")
                target = connection.execute(
                    "SELECT revision FROM profile_meta WHERE user_id=?", (identity.user_id,)
                ).fetchone()
                source = connection.execute(
                    "SELECT revision FROM profile_meta WHERE user_id=?", (LEGACY_USER_ID,)
                ).fetchone()
                if target is None or source is None:
                    raise LocalPersistenceError("档案迁移条件不完整，未修改任何记录。")
                occupied = int(target[0]) > 0 or any(connection.execute(
                    "SELECT 1 FROM {} WHERE user_id=? LIMIT 1".format(table),
                    (identity.user_id,),
                ).fetchone() for table in (
                    "day_snapshots", "estimate_snapshot", "personal_settings", "material_snapshot"
                ))
                if occupied:
                    raise LocalPersistenceConflict("目标个人档案已有内容，未覆盖或合并旧记录。")
                connection.execute(
                    "INSERT INTO day_snapshots(user_id,plan_date,campus_id,reference_datetime,"
                    "reference_source,saved_at,payload_json) "
                    "SELECT ?,plan_date,campus_id,reference_datetime,reference_source,saved_at,payload_json "
                    "FROM day_snapshots WHERE user_id=?",
                    (identity.user_id, LEGACY_USER_ID),
                )
                connection.execute(
                    "INSERT INTO estimate_snapshot(user_id,payload_json,added_json) "
                    "SELECT ?,payload_json,added_json FROM estimate_snapshot WHERE user_id=?",
                    (identity.user_id, LEGACY_USER_ID),
                )
                connection.execute(
                    "INSERT INTO personal_settings(user_id,payload_json,updated_at) "
                    "SELECT ?,payload_json,updated_at FROM personal_settings WHERE user_id=?",
                    (identity.user_id, LEGACY_USER_ID),
                )
                connection.execute(
                    "INSERT INTO material_snapshot(user_id,payload_json) "
                    "SELECT ?,payload_json FROM material_snapshot WHERE user_id=?",
                    (identity.user_id, LEGACY_USER_ID),
                )
                connection.execute(
                    "UPDATE profile_meta SET revision=?,updated_at=? WHERE user_id=?",
                    (int(source[0]), datetime.now().isoformat(), identity.user_id),
                )
                connection.commit()
        except (LocalPersistenceConflict, LocalPersistenceError):
            raise
        except (sqlite3.Error, OSError) as exc:
            raise LocalPersistenceError("旧本机档案迁移失败，原记录已保留。") from exc

    def store_for(self, identity):
        if not isinstance(identity, ProfileIdentity) or not identity.persistent:
            raise ValueError("persistent profile identity required")
        return LocalProfileStore(self.path, identity.user_id, identity.kind)


def encode_live_final_turn(bundle):
    from src.p2_session import LiveFinalTurn, P2SessionTurn
    if not isinstance(bundle, LiveFinalTurn):
        raise TypeError("final_turn must be LiveFinalTurn")
    turn = bundle.turn
    if not isinstance(turn, P2SessionTurn):
        raise TypeError("bundle turn invalid")
    return {
        "snapshot_version": 1,
        "version": bundle.version,
        "result": _encode_value(bundle.result),
        "execution_context": _encode_value(bundle.execution_context),
        "movement_blocks": _encode_value(bundle.movement_blocks),
        "extra_questions": _encode_value(bundle.extra_questions),
        "feedback_decision": _encode_value(bundle.feedback_decision),
        "concurrent_allocations": _encode_value(bundle.concurrent_allocations),
        "turn": {
            "user_text": turn.user_text,
            "commitment_questions": _encode_value(turn.commitment_questions),
            "commitment_warnings": _encode_value(turn.commitment_warnings),
            "cache_key": turn.cache_key,
            "router_questions": _encode_value(turn.router_questions),
            "router_warnings": _encode_value(turn.router_warnings),
            "movement_questions": _encode_value(turn.movement_questions),
            "movement_warnings": _encode_value(turn.movement_warnings),
            "companion_copy": _encode_value(turn.companion_copy),
        },
    }


def decode_live_final_turn(payload):
    from src.p2_session import LiveFinalTurn, P2SessionTurn
    if not isinstance(payload, dict) or payload.get("snapshot_version") != 1:
        raise ValueError("live final turn snapshot version invalid")
    result = _decode_value(payload["result"])
    context = _decode_value(payload["execution_context"])
    movements = tuple(_decode_value(payload["movement_blocks"]))
    concurrent = tuple(_decode_value(payload["concurrent_allocations"]))
    item = payload["turn"]
    if not isinstance(item, dict):
        raise ValueError("turn snapshot invalid")
    turn = P2SessionTurn(
        user_text=_required_text(item, "user_text"),
        result=result,
        commitment_questions=tuple(_decode_value(item["commitment_questions"])),
        commitment_warnings=tuple(_decode_value(item["commitment_warnings"])),
        cache_key=_required_text(item, "cache_key"),
        router_questions=tuple(_decode_value(item["router_questions"])),
        router_warnings=tuple(_decode_value(item["router_warnings"])),
        movement_plan=None,
        movement_blocks=movements,
        movement_questions=tuple(_decode_value(item["movement_questions"])),
        movement_warnings=tuple(_decode_value(item["movement_warnings"])),
        companion_copy=_decode_value(item["companion_copy"]),
        execution_context=context,
        concurrent_allocations=concurrent,
        agent_intelligence=None,
    )
    bundle = LiveFinalTurn(
        version=int(payload["version"]),
        turn=turn,
        state=result.updated_state,
        result=result,
        execution_context=context,
        movement_blocks=movements,
        extra_questions=tuple(_decode_value(payload["extra_questions"])),
        feedback_decision=_decode_value(payload["feedback_decision"]),
        concurrent_allocations=concurrent,
        agent_intelligence=None,
    )
    _validate_restored_bundle_refs(bundle)
    return bundle


def encode_task_estimate_draft(draft):
    from src.task_estimation import TaskEstimateDraft
    if not isinstance(draft, TaskEstimateDraft):
        raise TypeError("estimate draft invalid")
    if draft.material.kind != "text":
        raise ValueError("uploaded images are not persisted")
    return {"snapshot_version": 1, "draft": _encode_value(draft)}


def decode_task_estimate_draft(payload):
    from src.task_estimation import TaskEstimateDraft
    if not isinstance(payload, dict) or payload.get("snapshot_version") != 1:
        raise ValueError("estimate draft snapshot version invalid")
    value = _decode_value(payload.get("draft"))
    if not isinstance(value, TaskEstimateDraft) or value.material.kind != "text":
        raise ValueError("persisted estimate draft invalid")
    return value


def _class_registry():
    """Allowlist every domain type that may occur in a saved root snapshot."""
    global _ALLOWED_CLASS_REGISTRY
    if _ALLOWED_CLASS_REGISTRY is not None:
        return _ALLOWED_CLASS_REGISTRY
    from src.p1_models import FieldEvidence, SourceKind
    from src.p1_window_models import AvailabilityLevel, FixedCommitment
    from src.p2_agentic_models import (
        DayPlanIntent, LifecycleAction, P2AgenticDayResult, ReconciliationResult,
        ReconciliationUpdate, ReviewResult, TaskEstimate, TotalSourceChoice,
    )
    from src.p2_allocation_models import DayAllocationPlan, TaskAllocation
    from src.p2_companion_copy import CompanionCopy
    from src.p2_day_plan import DayPlanSummary
    from src.p2_models import DayPlanningState, DayWindow, TaskProgress, TaskState
    from src.p3_map_schema import TransportMode
    from src.p3_route_planner import MovementBlock
    from src.p3_time_estimator import TimeEstimateMethod
    from src.p4_concurrency import (
        ConcurrentAllocation, ConcurrentExecutionAuthorization, ConcurrentExecutionSource,
    )
    from src.p4_execution_context import (
        CurrentLocationContext, CurrentLocationSource, ExecutableTaskBinding,
        ExecutionConfirmation, ExecutionConfirmationKind, ExecutionLocation,
        ExecutionLocationSource, ExecutionPlanContext, TaskExecutionProfile,
    )
    from src.p4_feedback_decision import (
        ConcurrencyDecision, FeedbackDecision, TaskOrderingConstraint, TaskPriorityChange,
    )
    from src.p5_day_preferences import DayPreferenceProfile, TaskSpecificPreference
    from src.task_estimation import TaskEstimateDraft, TaskEstimateMaterial, TaskEstimateResult
    from src.material_inbox import MaterialInbox, MaterialDraft, MaterialItem, MaterialTime
    from src.personal_settings import (
        CourseOccurrenceOverride, CourseTemplate, PersonalSettings, SavedCampusPlace,
    )

    classes = (
        FieldEvidence, SourceKind, AvailabilityLevel, FixedCommitment,
        DayPlanIntent, LifecycleAction, P2AgenticDayResult, ReconciliationResult,
        ReconciliationUpdate, ReviewResult, TaskEstimate, TotalSourceChoice,
        DayAllocationPlan, TaskAllocation, CompanionCopy, DayPlanSummary,
        DayPlanningState, DayWindow, TaskProgress, TaskState, TransportMode,
        MovementBlock, TimeEstimateMethod, ConcurrentAllocation,
        ConcurrentExecutionAuthorization, ConcurrentExecutionSource,
        CurrentLocationContext, CurrentLocationSource, ExecutableTaskBinding,
        ExecutionConfirmation, ExecutionConfirmationKind, ExecutionLocation,
        ExecutionLocationSource, ExecutionPlanContext, TaskExecutionProfile,
        ConcurrencyDecision, FeedbackDecision, TaskOrderingConstraint,
        TaskPriorityChange, DayPreferenceProfile, TaskSpecificPreference,
        TaskEstimateDraft, TaskEstimateMaterial, TaskEstimateResult,
        MaterialInbox, MaterialDraft, MaterialItem, MaterialTime,
        CourseOccurrenceOverride, CourseTemplate, PersonalSettings, SavedCampusPlace,
    )
    _ALLOWED_CLASS_REGISTRY = {
        "{}.{}".format(cls.__module__, cls.__name__): cls for cls in classes
    }
    return _ALLOWED_CLASS_REGISTRY


def _encode_value(value):
    registry = _class_registry()
    if isinstance(value, Enum):
        key = "{}.{}".format(type(value).__module__, type(value).__name__)
        if key not in registry:
            raise TypeError("unsupported enum in local snapshot")
        return {"$enum": key, "value": value.value}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat()}
    if is_dataclass(value):
        key = "{}.{}".format(type(value).__module__, type(value).__name__)
        if key not in registry:
            raise TypeError("unsupported dataclass in local snapshot: {}".format(key))
        return {
            "$type": key,
            "fields": {field.name: _encode_value(getattr(value, field.name)) for field in fields(value)},
        }
    if isinstance(value, tuple):
        return {"$tuple": [_encode_value(item) for item in value]}
    if isinstance(value, list):
        return {"$list": [_encode_value(item) for item in value]}
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("snapshot mappings require string keys")
        return {"$dict": {key: _encode_value(item) for key, item in value.items()}}
    raise TypeError("unsupported value in local snapshot: {}".format(type(value).__name__))


def _decode_value(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if not isinstance(value, dict):
        raise ValueError("snapshot value invalid")
    if "$datetime" in value:
        return _decode_datetime(value["$datetime"])
    registry = _class_registry()
    if "$enum" in value:
        cls = registry.get(value["$enum"])
        if cls is None or not issubclass(cls, Enum):
            raise ValueError("snapshot enum type invalid")
        return cls(value["value"])
    if "$type" in value:
        cls = registry.get(value["$type"])
        raw_fields = value.get("fields")
        if cls is None or not is_dataclass(cls) or not isinstance(raw_fields, dict):
            raise ValueError("snapshot dataclass type invalid")
        from dataclasses import MISSING
        definitions = {field.name: field for field in fields(cls)}
        if not set(raw_fields).issubset(definitions):
            raise ValueError("snapshot dataclass fields invalid")
        missing_required = tuple(
            name for name, definition in definitions.items()
            if name not in raw_fields
            and definition.default is MISSING
            and definition.default_factory is MISSING
        )
        if missing_required:
            raise ValueError("snapshot dataclass fields invalid")
        return cls(**{name: _decode_value(item) for name, item in raw_fields.items()})
    if "$tuple" in value:
        return tuple(_decode_value(item) for item in value["$tuple"])
    if "$list" in value:
        return [_decode_value(item) for item in value["$list"]]
    if "$dict" in value:
        raw = value["$dict"]
        if not isinstance(raw, dict):
            raise ValueError("snapshot mapping invalid")
        return {key: _decode_value(item) for key, item in raw.items()}
    raise ValueError("snapshot value tag invalid")


def _decode_datetime(value):
    if not isinstance(value, str):
        raise ValueError("datetime snapshot invalid")
    return datetime.fromisoformat(value)


def _required_text(value, key):
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ValueError("{} missing from snapshot".format(key))
    return item


def _validate_added_mapping(value):
    for draft_id, task_ref in value.items():
        if not isinstance(draft_id, str) or not draft_id.strip():
            raise ValueError("estimate draft id invalid")
        if not isinstance(task_ref, str) or not task_ref.strip():
            raise ValueError("estimate task ref invalid")


def _decode_added_mapping(value):
    if not value:
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("estimate added mapping invalid")
    _validate_added_mapping(parsed)
    return parsed


def _validate_restored_bundle_refs(bundle):
    """Reject incomplete snapshots before they can enter a live session."""
    task_refs = {item.task_ref for item in bundle.state.tasks}
    commitment_refs = {item.commitment_ref for item in bundle.state.commitments}
    if any(item.task_ref not in task_refs for item in bundle.execution_context.bindings):
        raise ValueError("execution binding references a missing task")
    if any(
        item.task_ref not in task_refs
        for item in bundle.result.allocation_plan.allocations
    ):
        raise ValueError("allocation references a missing task")
    for item in bundle.execution_context.concurrency_authorizations:
        if item.task_ref not in task_refs or item.commitment_ref not in commitment_refs:
            raise ValueError("concurrency authorization references a missing fact")
    for item in bundle.concurrent_allocations:
        if item.task_ref not in task_refs or item.commitment_ref not in commitment_refs:
            raise ValueError("concurrent allocation references a missing fact")
    known = task_refs | commitment_refs
    for item in bundle.movement_blocks:
        for ref in (
            getattr(item, "origin_activity_ref", None),
            getattr(item, "destination_activity_ref", None),
        ):
            if ref is not None and ref not in known:
                raise ValueError("movement references a missing activity")
