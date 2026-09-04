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
from app.data.profile import ColumnProfile, profile_dataframe

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

    # Profiles are the most-recomputed thing in LANA. `profile_dataframe` was
    # being called on the upload, again on /profile, again inside build_context
    # for *every question*, again on /clean/preview, again inside
    # analyze_correlations, again for the heatmap, and again on every export —
    # roughly 0.7 s per call on a 200k x 30 frame, all of it recomputing an
    # identical answer, because a stored frame never changes. Cleaning produces
    # a *new* version rather than mutating one, so a profile is valid for the
    # life of its version and is cached per version here.
    _profiles: dict[str, dict[str, ColumnProfile]] = field(default_factory=dict)
    # Same argument for the grounded LLM context, which is strictly more
    # expensive (~2.1 s) because it profiles *and* runs a correlation scan.
    _contexts: dict[str, Any] = field(default_factory=dict)
    _cache_lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

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
        # A re-clean replaces the cleaned frame, so anything derived from the
        # previous one is stale. The original's entries stay valid — that frame
        # was not touched.
        with self._cache_lock:
            self._profiles.pop(CLEANED, None)
            self._contexts.pop(CLEANED, None)

    # ── Derived-value cache ──────────────────────────────────────────────────

    def profiles(self) -> dict[str, ColumnProfile]:
        """Column profiles for the active version, computed at most once.

        Callers that already hold this should pass it down rather than letting
        a helper re-derive it; every function in ``app/`` that profiles a whole
        frame accepts a ``profiles=`` argument for that reason.
        """
        version = self.active_version
        with self._cache_lock:
            cached = self._profiles.get(version)
        if cached is not None:
            return cached
        # Computed outside the lock: profiling is seconds of CPU on a large
        # frame, and holding the mutex through it would serialise every other
        # request touching this session. A concurrent duplicate computation is
        # wasteful but harmless, and both produce the same answer.
        computed = profile_dataframe(self.active)
        with self._cache_lock:
            return self._profiles.setdefault(version, computed)

    def context(self, build):
        """Grounded LLM context for the active version, computed at most once.

        ``build`` is a callable taking ``(df, profiles)``. Passed in rather
        than imported so this module keeps depending only on the data layer.
        """
        version = self.active_version
        with self._cache_lock:
            cached = self._contexts.get(version)
        if cached is not None:
            return cached
        computed = build(self.active, self.profiles())
        with self._cache_lock:
            return self._contexts.setdefault(version, computed)

    def invalidate_cache(self) -> None:
        """Drop every derived value. For callers that mutate a frame in place."""
        with self._cache_lock:
            self._profiles.clear()
            self._contexts.clear()

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
                "frame_mb": round(total / (1024 * 1024), 2),
                "max_frame_mb": round(self.max_bytes / (1024 * 1024), 2),
                # Named explicitly because the distinction matters: this counts
                # the bytes of the stored frames, which is what the budget
                # governs. Process RSS is materially higher — the interpreter
                # and its libraries are ~240 MB before any data, and each
                # request adds working copies on top. A reader who takes
                # `frame_mb` for total memory use will underestimate by several
                # times, so the budget is derived with that headroom built in
                # (see app/resources.py) rather than left to be inferred.
                "measures": "stored frame bytes, not process RSS",
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
