"""Back up and restore LANA's durable state — accounts, persisted sessions,
and the audit trail.

    python -m scripts.backup run
    python -m scripts.backup list
    python -m scripts.backup restore <archive-name> --data-dir <target>

Why this exists
----------------
A restart of LANA itself loses nothing durable — the in-memory session
store was always meant to be disposable. What *is* durable, and therefore
the only thing worth backing up, is whatever lives on disk: the account
database (if `LANA_ACCOUNTS=true`), persisted sessions (if
`LANA_PERSIST_SESSIONS=true`), and the audit trail (if auditing is on).
Losing the disk under any of those — a bad `rm`, a failed upgrade, a dead
volume — is the incident this script exists for.

What is NOT backed up, and why
-------------------------------
`coordination.db` (rate-limit buckets, LLM concurrency leases with a 300s
TTL — see `backend/coordination.py`) is purely ephemeral cross-worker
state. It reinitialises cleanly on every normal restart already; a restore
that skips it changes nothing a restart wouldn't have changed anyway.

`LANA_ENCRYPTION_KEY` is never part of a backup, deliberately. Storing a key
beside the data it encrypts protects against nothing — the same reasoning
`app/crypto.py` already states about not reading it from a file in the data
directory. If session frames are encrypted, the key has to be kept
somewhere else entirely; without it, a restore cannot recover them either,
exactly as a live instance cannot. See "Encryption at rest" in SECURITY.md.

Path resolution
----------------
`--data-dir` defaults to whatever the running app would use
(`app.config.config.limits.data_dir`, i.e. `LANA_DATA_DIR` or "data") —
this module imports `app.config`, so a `.env` file in the working directory
is picked up the same way it is for `uvicorn backend.main:app`.
`--backup-dir` defaults to `LANA_BACKUP_DIR`, else a `backups/` directory
next to the data directory — deliberately *not* inside it, so that losing
the data directory does not also take the backups with it. Retention
(`run`'s `--keep`, default `LANA_BACKUP_RETENTION` or 7) prunes the oldest
archives after each successful run.
"""

from __future__ import annotations

import argparse
import os
import sys
import tarfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.accounts import account_db_path  # noqa: E402
from app.audit import KEEP_FILES, build_audit_log  # noqa: E402
from app.config import config  # noqa: E402

_ARCHIVE_PREFIX = "lana-backup-"
_ARCHIVE_SUFFIX = ".tar.gz"
_SESSIONS_ARCNAME = "sessions"


def _resolve_data_dir(explicit: str | None) -> Path:
    return Path(explicit or config.limits.data_dir)


def _resolve_backup_dir(explicit: str | None, data_dir: Path) -> Path:
    return Path(
        explicit or os.getenv("LANA_BACKUP_DIR", "").strip()
        or (data_dir.parent / "backups")
    )


def _resolve_keep(explicit: int | None) -> int:
    return explicit if explicit is not None else int(
        os.getenv("LANA_BACKUP_RETENTION", "7")
    )


def _session_dirs(data_dir: Path) -> list[Path]:
    """Every persisted session folder.

    Identified by holding ``raw.parquet`` — the one file every persisted
    session always has (see ``backend/persistence.py``) — found by globbing
    the filesystem rather than reading ``sessions.db``. A backup should not
    need to parse that schema, or hold the encryption key, just to know
    which files exist.
    """
    if not data_dir.is_dir():
        return []
    return sorted(
        p for p in data_dir.iterdir()
        if p.is_dir() and (p / "raw.parquet").exists()
    )


def _audit_paths(data_dir: Path) -> list[Path]:
    """The active audit log plus every rotated sibling that still exists.

    Reuses ``build_audit_log``'s own resolution order (``LANA_AUDIT_LOG``,
    else ``<data_dir>/audit.jsonl``) rather than re-deriving it — and,
    critically, the rotated files too: ``app/audit.py`` rotates at 8MB and
    keeps up to ``KEEP_FILES`` old ones, so a backup that only grabs the
    active file silently loses everything rotation already moved aside.
    """
    log = build_audit_log(str(data_dir))
    if log.path is None:
        return []
    base = Path(log.path)
    paths = [base] if base.exists() else []
    for i in range(1, KEEP_FILES + 1):
        sibling = base.with_name(f"{base.stem}.{i}{base.suffix}")
        if sibling.exists():
            paths.append(sibling)
    return paths


def _manifest(data_dir: Path) -> dict:
    """What this data directory actually has to back up, resolved once."""
    accounts_db = account_db_path()
    sessions_db = data_dir / "sessions.db"
    return {
        "accounts_db": accounts_db if accounts_db.exists() else None,
        "sessions_db": sessions_db if sessions_db.exists() else None,
        "session_dirs": _session_dirs(data_dir),
        "audit_paths": _audit_paths(data_dir),
    }


def _describe(manifest: dict) -> list[str]:
    lines = [
        f"accounts.db: {manifest['accounts_db']}" if manifest["accounts_db"]
        else "accounts.db: not present (accounts mode not in use, or none "
             "created yet)",
        f"sessions.db + {len(manifest['session_dirs'])} session folder(s)"
        if manifest["sessions_db"]
        else "sessions.db: not present (LANA_PERSIST_SESSIONS is off, or "
             "nothing has been uploaded yet)",
        f"audit trail: {len(manifest['audit_paths'])} file(s)"
        if manifest["audit_paths"]
        else "audit trail: not present (auditing is off on this instance)",
    ]
    return lines


def _list_archives(backup_dir: Path) -> list[Path]:
    if not backup_dir.is_dir():
        return []
    return sorted(backup_dir.glob(f"{_ARCHIVE_PREFIX}*{_ARCHIVE_SUFFIX}"))


def _prune(backup_dir: Path, keep: int) -> list[Path]:
    archives = _list_archives(backup_dir)
    excess = archives[:-keep] if keep > 0 else archives
    for archive in excess:
        archive.unlink()
    return excess


def cmd_run(args: argparse.Namespace) -> int:
    data_dir = _resolve_data_dir(args.data_dir)
    backup_dir = _resolve_backup_dir(args.backup_dir, data_dir)
    keep = _resolve_keep(args.keep)
    backup_dir.mkdir(parents=True, exist_ok=True)

    manifest = _manifest(data_dir)
    for line in _describe(manifest):
        print(line)

    name = f"{_ARCHIVE_PREFIX}{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}{_ARCHIVE_SUFFIX}"
    archive_path = backup_dir / name
    with tarfile.open(archive_path, "w:gz") as tar:
        if manifest["accounts_db"]:
            tar.add(manifest["accounts_db"], arcname="accounts.db")
        if manifest["sessions_db"]:
            tar.add(manifest["sessions_db"], arcname="sessions.db")
        for session_dir in manifest["session_dirs"]:
            tar.add(session_dir, arcname=f"{_SESSIONS_ARCNAME}/{session_dir.name}")
        for audit_path in manifest["audit_paths"]:
            tar.add(audit_path, arcname=audit_path.name)

    print(f"Wrote {archive_path}")
    for removed in _prune(backup_dir, keep):
        print(f"Removed old backup: {removed.name}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    data_dir = _resolve_data_dir(args.data_dir)
    backup_dir = _resolve_backup_dir(args.backup_dir, data_dir)
    archives = _list_archives(backup_dir)
    if not archives:
        print(f"No backups in {backup_dir}.")
        return 0
    for archive in archives:
        size_mb = archive.stat().st_size / 1024 ** 2
        print(f"{archive.name}  ({size_mb:.1f} MB)")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    data_dir = _resolve_data_dir(args.data_dir)
    backup_dir = _resolve_backup_dir(args.backup_dir, data_dir)

    archive_path = Path(args.archive)
    if not archive_path.exists():
        archive_path = backup_dir / args.archive
    if not archive_path.exists():
        print(f"error: no such backup: {args.archive}", file=sys.stderr)
        return 1

    if data_dir.exists() and any(data_dir.iterdir()):
        print(
            f"error: {data_dir} is not empty. Restoring into a data "
            "directory LANA might be running against risks corrupting it "
            "mid-write. Point --data-dir at an empty directory, stop LANA, "
            "then move the restored files into place yourself.",
            file=sys.stderr,
        )
        return 1

    data_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "r:gz") as tar:
        for member in tar.getmembers():
            if member.name.startswith(f"{_SESSIONS_ARCNAME}/"):
                member.name = member.name[len(_SESSIONS_ARCNAME) + 1:]
            try:
                tar.extract(member, path=data_dir, filter="data")
            except TypeError:
                # Python builds without PEP 706's `filter` kwarg backported.
                tar.extract(member, path=data_dir)

    print(f"Restored into {data_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.backup",
        description="Back up and restore LANA's durable state.",
    )
    parser.add_argument(
        "--data-dir", default=None,
        help="Where LANA's data lives (default: LANA_DATA_DIR, else \"data\").",
    )
    parser.add_argument(
        "--backup-dir", default=None,
        help="Where backups are written/read (default: LANA_BACKUP_DIR, "
             "else a backups/ folder next to --data-dir).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Snapshot now, then prune old backups.")
    run.add_argument(
        "--keep", type=int, default=None,
        help="Archives to keep (default: LANA_BACKUP_RETENTION, else 7).",
    )

    sub.add_parser("list", help="List existing backups.")

    restore = sub.add_parser("restore", help="Extract a backup into --data-dir.")
    restore.add_argument(
        "archive", help="Archive filename (looked up in --backup-dir) or a path.",
    )

    args = parser.parse_args(argv)
    if args.command == "run":
        return cmd_run(args)
    if args.command == "list":
        return cmd_list(args)
    if args.command == "restore":
        return cmd_restore(args)
    raise AssertionError(f"unhandled command {args.command!r}")


if __name__ == "__main__":
    raise SystemExit(main())
