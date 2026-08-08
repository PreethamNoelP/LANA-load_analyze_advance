"""Session storage — raw data, derived versions, and their lineage.

Replaces three parallel module-level dicts that had to be kept in sync by
hand and were mutated from FastAPI's threadpool without a lock. Everything
about one upload now lives in one object behind one mutex, so a version
switch cannot land on a session whose data another request just evicted.

Eviction is bounded on two axes: session count and total resident bytes.
Count alone is not enough — thirty 400 MB uploads will exhaust the host long
before the session limit is reached.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from app.data.lineage import CleaningLedger

ORIGINAL = "original"
CLEANED = "cleaned"


def _frame_bytes(df: pd.DataFrame) -> int:
    """Approximate resident size of a DataFrame in bytes.

    ``deep=True`` walks object columns to count the actual string payloads,
    which is what makes text-heavy uploads dominate memory. It costs one pass
    over the data and runs once per stored version.
    """
    try:
        return int(df.memory_usage(index=True, deep=True).sum())
    except Exception:
        return int(df.memory_usage(index=True).sum())


@dataclass
class Session:
    """One upload: the untouched raw frame plus any derived version."""

    session_id: str
    filename: str
    raw: pd.DataFrame
    last_used: float = field(default_factory=time.monotonic)

    cleaned: pd.DataFrame | None = None
    ledger: CleaningLedger | None = None
    active_version: str = ORIGINAL

    _raw_bytes: int = 0
    _cleaned_bytes: int = 0

    def __post_init__(self) -> None:
        self._raw_bytes = _frame_bytes(self.raw)

    # ── Version access ───────────────────────────────────────────────────────

    @property
    def active(self) -> pd.DataFrame:
        """The frame all downstream analysis should read."""
        if self.active_version == CLEANED and self.cleaned is not None:
            return self.cleaned
        return self.raw

    @property
    def has_cleaned(self) -> bool:
        return self.cleaned is not None

    def set_cleaned(self, df: pd.DataFrame, ledger: CleaningLedger) -> None:
        """Store a derived version. The raw frame is never overwritten."""
        self.cleaned = df
        self.ledger = ledger
        self.active_version = CLEANED
        self._cleaned_bytes = _frame_bytes(df)

    def set_version(self, version: str) -> None:
        if version not in (ORIGINAL, CLEANED):
            raise ValueError(f"Unknown version '{version}'.")
        if version == CLEANED and self.cleaned is None:
            raise ValueError("No cleaned version exists yet.")
        self.active_version = version

    @property
    def nbytes(self) -> int:
        return self._raw_bytes + self._cleaned_bytes

    def lineage_narrative(self) -> str | None:
        """The cleaning story for the active version, if it was transformed."""
        if self.active_version != CLEANED or self.ledger is None:
            return None
        return self.ledger.narrative(len(self.raw))

    def status(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "version": self.active_version,
            "has_cleaned": self.has_cleaned,
            "original_rows": len(self.raw),
            "memory_mb": round(self.nbytes / (1024 * 1024), 2),
        }
        if self.cleaned is not None:
            result["cleaned_rows"] = len(self.cleaned)
        if self.ledger is not None:
            result["lineage"] = self.ledger.to_dict(len(self.raw))
        return result


class SessionStore:
    """Thread-safe LRU store bounded by session count, bytes and idle time."""

    def __init__(
        self,
        max_sessions: int = 30,
        max_bytes: int = 2 * 1024 ** 3,
        ttl_seconds: float = 3600.0,
    ) -> None:
        self.max_sessions = max_sessions
        self.max_bytes = max_bytes
        self.ttl_seconds = ttl_seconds
        self._sessions: OrderedDict[str, Session] = OrderedDict()
        self._lock = threading.RLock()

    # ── Writing ──────────────────────────────────────────────────────────────

    def create(self, session_id: str, filename: str, df: pd.DataFrame) -> Session:
        session = Session(session_id=session_id, filename=filename, raw=df)
        with self._lock:
            self._sessions[session_id] = session
            self._sessions.move_to_end(session_id)
            self._evict_locked()
        return session

    # ── Reading ──────────────────────────────────────────────────────────────

    def get(self, session_id: str) -> Session | None:
        """Fetch a session, marking it recently used and expiring stale ones."""
        with self._lock:
            self._expire_locked()
            session = self._sessions.get(session_id)
            if session is None:
                return None
            session.last_used = time.monotonic()
            self._sessions.move_to_end(session_id)
            return session

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = sum(s.nbytes for s in self._sessions.values())
            return {
                "sessions": len(self._sessions),
                "max_sessions": self.max_sessions,
                "resident_mb": round(total / (1024 * 1024), 2),
                "max_mb": round(self.max_bytes / (1024 * 1024), 2),
            }

    # ── Eviction ─────────────────────────────────────────────────────────────

    def _expire_locked(self) -> None:
        if self.ttl_seconds <= 0:
            return
        cutoff = time.monotonic() - self.ttl_seconds
        for sid in [s for s, sess in self._sessions.items() if sess.last_used < cutoff]:
            self._sessions.pop(sid, None)

    def _evict_locked(self) -> None:
        """Drop least-recently-used sessions until both bounds are satisfied.

        The newest session is never evicted, even when it alone exceeds the
        byte budget — returning a 200 and then losing the upload immediately
        would be worse than briefly exceeding the target.
        """
        self._expire_locked()
        while len(self._sessions) > self.max_sessions:
            self._sessions.popitem(last=False)
        while len(self._sessions) > 1 and sum(s.nbytes for s in self._sessions.values()) > self.max_bytes:
            self._sessions.popitem(last=False)

    def enforce_limits(self) -> None:
        """Re-check bounds after a session grew (e.g. a cleaned version was added)."""
        with self._lock:
            self._evict_locked()
