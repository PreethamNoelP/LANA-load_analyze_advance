"""Optional disk-backed persistence for sessions — SQLite index + Parquet frames.

Off by default (``LANA_PERSIST_SESSIONS=false``): a plain ``uvicorn --reload``
dev run should not start writing a ``data/`` directory into a contributor's
checkout with no warning. The Docker image turns it on explicitly, because a
restart there silently losing every uploaded dataset is the worse default.

Every write here is best-effort: a full disk or a permissions error is logged
and swallowed rather than failing the request that triggered it. Persistence
is a durability improvement on top of the in-memory store, not a replacement
for it — the request that just uploaded or cleaned data already has the
correct in-memory result regardless of whether the write to disk succeeds.
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import threading
from pathlib import Path
from typing import Any

import pandas as pd

from app.data.lineage import CleaningLedger

logger = logging.getLogger("lana")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    active_version TEXT NOT NULL,
    last_used REAL NOT NULL,
    has_cleaned INTEGER NOT NULL,
    owner TEXT NOT NULL DEFAULT ''
)
"""

# Added after the table shipped, so an existing data directory has a sessions
# table without it. SQLite has no "ADD COLUMN IF NOT EXISTS", and a failed
# migration must not stop the app booting against an older volume.
_MIGRATIONS = (
    "ALTER TABLE sessions ADD COLUMN owner TEXT NOT NULL DEFAULT ''",
)


class PersistenceBackend:
    """Reads and writes ``Session`` state under ``data_dir``.

    Layout: ``<data_dir>/sessions.db`` (SQLite metadata index) plus one
    ``<data_dir>/<session_id>/`` folder per session holding ``raw.parquet``,
    an optional ``cleaned.parquet``, and an optional ``ledger.json``.
    Parquet, not CSV, because it round-trips dtypes exactly — a column LANA
    parsed as datetime or category must not come back as a string after a
    restart.
    """

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.db_path = data_dir / "sessions.db"
        # SQLite connections are not shared across threads; opening one per
        # call (rather than holding one open) is the simplest way to stay
        # correct under FastAPI's threadpool without adding a connection pool
        # for what is, at most, a few writes per user action.
        self._lock = threading.Lock()
        data_dir.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(_SCHEMA)
            for statement in _MIGRATIONS:
                try:
                    conn.execute(statement)
                except sqlite3.OperationalError:
                    pass  # already applied — the only expected failure here

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=30)

    def _session_dir(self, session_id: str) -> Path:
        d = self.data_dir / session_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ── Writing ──────────────────────────────────────────────────────────────

    def save(self, session: Any, *, frames: bool = True) -> None:
        """Persist a session's current state.

        ``frames=False`` skips the Parquet writes and only updates the
        metadata row — for a version switch, where no data actually changed
        and re-writing potentially large frames would be pure waste.
        """
        try:
            with self._lock:
                sdir = self._session_dir(session.session_id)
                if frames:
                    session.raw.to_parquet(sdir / "raw.parquet")
                    cleaned_path = sdir / "cleaned.parquet"
                    ledger_path = sdir / "ledger.json"
                    if session.cleaned is not None:
                        session.cleaned.to_parquet(cleaned_path)
                        ledger_path.write_text(
                            json.dumps(session.ledger.to_persisted_dict())
                            if session.ledger is not None else "{}",
                            encoding="utf-8",
                        )
                    else:
                        cleaned_path.unlink(missing_ok=True)
                        ledger_path.unlink(missing_ok=True)
                with self._connect() as conn:
                    conn.execute(
                        "INSERT INTO sessions "
                        "(session_id, filename, active_version, last_used, has_cleaned, owner) "
                        "VALUES (?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(session_id) DO UPDATE SET "
                        "filename=excluded.filename, active_version=excluded.active_version, "
                        "last_used=excluded.last_used, has_cleaned=excluded.has_cleaned, "
                        "owner=excluded.owner",
                        (
                            session.session_id, session.filename, session.active_version,
                            session.last_used, int(session.cleaned is not None),
                            getattr(session, "owner", "") or "",
                        ),
                    )
        except Exception:
            logger.warning(
                "Failed to persist session %s to disk — it will not survive a "
                "restart, but the in-memory copy is unaffected.",
                session.session_id, exc_info=True,
            )

    def delete(self, session_id: str) -> None:
        try:
            with self._lock:
                shutil.rmtree(self.data_dir / session_id, ignore_errors=True)
                with self._connect() as conn:
                    conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        except Exception:
            logger.warning(
                "Failed to remove persisted session %s — its files may remain "
                "on disk despite being evicted from memory.",
                session_id, exc_info=True,
            )

    # ── Reading ──────────────────────────────────────────────────────────────

    def load_all(self) -> list[dict[str, Any]]:
        """Every session that has a readable raw frame on disk.

        A row whose files are missing or corrupt is skipped and its on-disk
        remains cleaned up, rather than failing application startup — a
        damaged persisted session is a reason to drop it, not to refuse to
        boot.
        """
        results: list[dict[str, Any]] = []
        try:
            with self._connect() as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("SELECT * FROM sessions").fetchall()
        except Exception:
            logger.warning("Could not read session index at %s", self.db_path, exc_info=True)
            return results

        for row in rows:
            sid = row["session_id"]
            sdir = self.data_dir / sid
            raw_path = sdir / "raw.parquet"
            if not raw_path.exists():
                self.delete(sid)
                continue
            try:
                raw = pd.read_parquet(raw_path)
                cleaned = None
                ledger = None
                cleaned_path = sdir / "cleaned.parquet"
                if cleaned_path.exists():
                    cleaned = pd.read_parquet(cleaned_path)
                    ledger_path = sdir / "ledger.json"
                    if ledger_path.exists():
                        ledger = CleaningLedger.from_persisted_dict(
                            json.loads(ledger_path.read_text(encoding="utf-8"))
                        )
            except Exception:
                logger.warning("Skipping unreadable persisted session %s", sid, exc_info=True)
                self.delete(sid)
                continue
            results.append({
                "session_id": sid,
                "filename": row["filename"],
                "raw": raw,
                "cleaned": cleaned,
                "ledger": ledger,
                "active_version": row["active_version"],
                "last_used": row["last_used"],
                "owner": _row_owner(row),
            })
        return results

    def load_one(self, session_id: str) -> dict[str, Any] | None:
        """Read one session from disk, or None if it is not there.

        This is what makes multi-worker deployment work. Worker A handles the
        upload and holds the frames in memory; worker B receives the next
        request for that session id, misses its own cache, and reads the
        frames worker A persisted instead of returning a 404. Without it,
        every request after the first is a coin flip.

        Deliberately a separate method from ``load_all``: startup wants every
        session and pays for it once, while this is on the hot path of a cache
        miss and must read exactly one.
        """
        try:
            with self._connect() as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
                ).fetchone()
        except Exception:
            logger.warning("Could not read session %s from the index", session_id,
                           exc_info=True)
            return None
        if row is None:
            return None

        sdir = self.data_dir / session_id
        raw_path = sdir / "raw.parquet"
        if not raw_path.exists():
            self.delete(session_id)
            return None
        try:
            raw = pd.read_parquet(raw_path)
            cleaned = None
            ledger = None
            cleaned_path = sdir / "cleaned.parquet"
            if cleaned_path.exists():
                cleaned = pd.read_parquet(cleaned_path)
                ledger_path = sdir / "ledger.json"
                if ledger_path.exists():
                    ledger = CleaningLedger.from_persisted_dict(
                        json.loads(ledger_path.read_text(encoding="utf-8"))
                    )
        except Exception:
            logger.warning("Persisted session %s is unreadable", session_id, exc_info=True)
            self.delete(session_id)
            return None

        return {
            "session_id": session_id,
            "filename": row["filename"],
            "raw": raw,
            "cleaned": cleaned,
            "ledger": ledger,
            "active_version": row["active_version"],
            "last_used": row["last_used"],
            "owner": _row_owner(row),
        }


def _row_owner(row: Any) -> str:
    """The owner column, tolerating a row written before it existed."""
    try:
        return row["owner"] or ""
    except (IndexError, KeyError):
        return ""
