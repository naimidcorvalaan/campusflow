"""Safe SQLite backup and verification for CampusFlow test servers.

The backup command uses SQLite's online backup API instead of copying a live
database file.  Restore is intentionally a dry-run plan in this first server
readiness version; replacing a live profile database remains an explicit
operator action outside this script.
"""

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from src.local_persistence import default_profile_path  # noqa: E402


REQUIRED_TABLES = frozenset({
    "profiles", "profile_meta", "day_snapshots",
    "estimate_snapshot", "personal_settings", "external_identity_links",
})


class BackupError(RuntimeError):
    pass


@dataclass(frozen=True)
class BackupVerification:
    path: Path
    schema_versions: tuple
    profile_count: int


def verify_database(path):
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise BackupError("数据库文件不存在：{}".format(source))
    try:
        uri = "file:{}?mode=ro".format(source.as_posix())
        with sqlite3.connect(uri, uri=True, timeout=5.0) as connection:
            check = connection.execute("PRAGMA quick_check").fetchone()
            if check is None or check[0] != "ok":
                raise BackupError("SQLite quick_check 未通过。")
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            missing = REQUIRED_TABLES - tables
            if missing:
                raise BackupError("数据库缺少 CampusFlow v4 必要表。")
            versions = tuple(sorted({
                int(row[0]) for row in connection.execute(
                    "SELECT DISTINCT schema_version FROM profile_meta"
                )
            }))
            count = int(connection.execute("SELECT COUNT(*) FROM profiles").fetchone()[0])
            return BackupVerification(source, versions, count)
    except BackupError:
        raise
    except (sqlite3.Error, OSError, ValueError) as exc:
        raise BackupError("数据库无法验证；没有修改源文件。") from exc


def backup_database(source_path, backup_directory, now=None):
    source = verify_database(source_path).path
    destination_root = Path(backup_directory).expanduser().resolve()
    destination_root.mkdir(parents=True, exist_ok=True)
    moment = now or datetime.now(timezone.utc)
    stamp = moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = destination_root / "campusflow-backup-{}.sqlite3".format(stamp)
    if destination.exists():
        raise BackupError("备份目标已存在，未覆盖。")
    try:
        source_uri = "file:{}?mode=ro".format(source.as_posix())
        with sqlite3.connect(source_uri, uri=True, timeout=5.0) as source_db:
            with sqlite3.connect(str(destination), timeout=5.0) as target_db:
                source_db.backup(target_db)
        verify_database(destination)
        return destination
    except BackupError:
        # A failed new backup is not a reliable artifact.  The source is never
        # modified; only this newly-created destination may be removed.
        if destination.exists():
            destination.unlink()
        raise
    except (sqlite3.Error, OSError, ValueError) as exc:
        if destination.exists():
            destination.unlink()
        raise BackupError("SQLite 在线备份失败；源数据库没有被修改。") from exc


def restore_dry_run(backup_path, target_path):
    backup = verify_database(backup_path)
    target = Path(target_path).expanduser().resolve()
    if backup.path == target:
        raise BackupError("备份文件和恢复目标不能相同。")
    current = verify_database(target) if target.exists() else None
    return {
        "backup": backup,
        "target": target,
        "target_exists": current is not None,
        "target_profiles": current.profile_count if current else 0,
    }


def _parser():
    parser = argparse.ArgumentParser(description="CampusFlow SQLite 安全备份工具")
    subparsers = parser.add_subparsers(dest="command", required=True)
    backup = subparsers.add_parser("backup", help="使用 SQLite 在线备份生成快照")
    backup.add_argument("--database", default=str(default_profile_path()))
    backup.add_argument("--output-dir")
    verify = subparsers.add_parser("verify", help="只读验证数据库或备份")
    verify.add_argument("--database", required=True)
    restore = subparsers.add_parser("restore-dry-run", help="验证恢复来源与目标，不执行恢复")
    restore.add_argument("--backup", required=True)
    restore.add_argument("--target", default=str(default_profile_path()))
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        if args.command == "backup":
            source = Path(args.database).expanduser().resolve()
            output = args.output_dir or str(source.parent / "backups")
            destination = backup_database(source, output)
            print("backup_created={}".format(destination))
            return 0
        if args.command == "verify":
            result = verify_database(args.database)
            print("database_ok=true")
            print("schema_versions={}".format(",".join(map(str, result.schema_versions))))
            print("profile_count={}".format(result.profile_count))
            return 0
        plan = restore_dry_run(args.backup, args.target)
        print("restore_executed=false")
        print("backup_ok=true")
        print("target_exists={}".format(str(plan["target_exists"]).lower()))
        print("No files were replaced. Stop the service and follow the documented restore procedure.")
        return 0
    except BackupError as exc:
        print("backup_error={}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
