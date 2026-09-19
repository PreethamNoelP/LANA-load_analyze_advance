"""Cross-process coordination: LLM concurrency leases and rate limiting.

The problem
-----------
``backend/main.py`` capped concurrent LLM calls with a ``threading.Semaphore``
and the session store held frames in a module-level dict. Both are
*process-local*, so ``uvicorn --workers 4`` silently produced four independent
caps (four times the intended load on one Ollama instance) and four disjoint
session stores (a user's second request lands on a worker that has never heard
of their upload). Nothing in the code or the Dockerfile prevented that, which
made "single process" an undocumented deployment constraint rather than a
choice.

Two backends, one interface
---------------------------
``InProcessCoordinator`` is the default and behaves exactly as the old
semaphore did — no files, no locking, no change for the single-user local case
the project is built around.

``SqliteCoordinator`` puts the leases and the token buckets in the SQLite
database that already backs session persistence, so every worker sharing that
volume shares one cap and one rate limit. It is selected automatically when
persistence is enabled, because that is exactly the configuration where
multiple workers make sense.

Why SQLite and not Redis
------------------------
Redis would be the reflex, and it would be wrong here. LANA is local-first and
ships as ``docker compose up``; adding a mandatory network service to make a
concurrency cap correct would be a real cost paid by every single-user
install to benefit a deployment shape that is not the common one. SQLite in
WAL mode handles this load trivially — these are sub-millisecond writes at
single-digit QPS — and the file is already there.

The honest boundary, stated because it is easy to overclaim: this coordinates
workers **sharing a filesystem**. It is not a distributed lock service, and
LANA does not claim to run as multiple replicas across hosts. That is recorded
in SECURITY.md and README.md rather than implied away.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("lana.coordination")

# A lease older than this is treated as abandoned. Must exceed the longest
# legitimate LLM call, or a slow answer has its slot stolen while still
# running — which would defeat the cap rather than enforce it. LLM_TIMEOUT
# defaults to 90s, so this is deliberately well clear of it.
LEASE_TTL_SECONDS = 300.0


@dataclass(frozen=True)
class RateDecision:
    """Whether a request may proceed, and what to tell the caller if not."""

    allowed: bool
    remaining: float
    retry_after_seconds: float = 0.0


class Coordinator(ABC):
    """Concurrency leases and rate limiting, however they are implemented."""

    @abstractmethod
    def acquire_slot(self, holder: str, max_slots: int) -> bool:
        """Take one of ``max_slots`` leases. False if they are all held."""

    @abstractmethod
    def release_slot(self, holder: str) -> None:
        """Give a lease back. Safe to call for a holder that has none."""

    @abstractmethod
    def check_rate(self, key: str, capacity: float, refill_per_second: float) -> RateDecision:
        """Spend one token from ``key``'s bucket."""

    def close(self) -> None:  # noqa: B027 - optional hook, not abstract
        """Release backend resources.

        Concrete and empty on purpose: the in-process backend holds nothing to
        release, and making this abstract would force it to implement a method
        whose correct body is nothing at all.
        """


class InProcessCoordinator(Coordinator):
    """Single-process behaviour, equivalent to the original semaphore."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._holders: set[str] = set()
        self._buckets: dict[str, tuple[float, float]] = {}

    def acquire_slot(self, holder: str, max_slots: int) -> bool:
        with self._lock:
            if len(self._holders) >= max_slots:
                return False
            self._holders.add(holder)
            return True

    def release_slot(self, holder: str) -> None:
        with self._lock:
            self._holders.discard(holder)

    def check_rate(self, key: str, capacity: float, refill_per_second: float) -> RateDecision:
        now = time.monotonic()
        with self._lock:
            tokens, updated = self._buckets.get(key, (capacity, now))
            tokens = min(capacity, tokens + (now - updated) * refill_per_second)
            if tokens < 1.0:
                self._buckets[key] = (tokens, now)
                deficit = 1.0 - tokens
                return RateDecision(
                    False, tokens,
                    deficit / refill_per_second if refill_per_second > 0 else 60.0,
                )
            self._buckets[key] = (tokens - 1.0, now)
            return RateDecision(True, tokens - 1.0)


class SqliteCoordinator(Coordinator):
    """Leases and buckets shared by every worker on one filesystem.

    One connection per thread (SQLite objects are not shareable across
    threads), WAL so a reader never blocks a writer, and ``BEGIN IMMEDIATE``
    around the read-modify-write in both operations — without it, two workers
    both read "1 of 2 slots used" and both take the second one.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._initialise()

    def _connection(self) -> sqlite3.Connection:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = sqlite3.connect(
                self.db_path, timeout=10.0, isolation_level=None
            )
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA busy_timeout=10000")
            self._local.connection = connection
        return connection

    def _initialise(self) -> None:
        connection = self._connection()
        connection.execute(
            "CREATE TABLE IF NOT EXISTS llm_leases ("
            "  holder TEXT PRIMARY KEY,"
            "  expires_at REAL NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS rate_buckets ("
            "  key TEXT PRIMARY KEY,"
            "  tokens REAL NOT NULL,"
            "  updated_at REAL NOT NULL)"
        )

    def acquire_slot(self, holder: str, max_slots: int) -> bool:
        connection = self._connection()
        now = time.time()
        try:
            connection.execute("BEGIN IMMEDIATE")
            # Expired leases are reaped here rather than on a timer: the only
            # moment their existence matters is when someone wants a slot.
            connection.execute("DELETE FROM llm_leases WHERE expires_at < ?", (now,))
            held = connection.execute("SELECT COUNT(*) FROM llm_leases").fetchone()[0]
            if held >= max_slots:
                connection.execute("ROLLBACK")
                return False
            connection.execute(
                "INSERT OR REPLACE INTO llm_leases (holder, expires_at) VALUES (?, ?)",
                (holder, now + LEASE_TTL_SECONDS),
            )
            connection.execute("COMMIT")
            return True
        except sqlite3.Error:
            _rollback(connection)
            # A coordination failure must not take the feature down. Failing
            # open here means the cap is briefly not enforced; failing closed
            # would make a transient lock error look like a total outage.
            logger.warning("Lease acquisition failed; allowing the request", exc_info=True)
            return True

    def release_slot(self, holder: str) -> None:
        try:
            self._connection().execute(
                "DELETE FROM llm_leases WHERE holder = ?", (holder,)
            )
        except sqlite3.Error:
            # The lease expires on its own; losing the release only delays
            # the slot's return, so this is not worth failing a request over.
            logger.warning("Lease release failed for %s", holder, exc_info=True)

    def check_rate(self, key: str, capacity: float, refill_per_second: float) -> RateDecision:
        connection = self._connection()
        now = time.time()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT tokens, updated_at FROM rate_buckets WHERE key = ?", (key,)
            ).fetchone()
            tokens, updated = row if row else (capacity, now)
            tokens = min(capacity, tokens + max(0.0, now - updated) * refill_per_second)

            if tokens < 1.0:
                connection.execute(
                    "INSERT OR REPLACE INTO rate_buckets (key, tokens, updated_at) "
                    "VALUES (?, ?, ?)", (key, tokens, now),
                )
                connection.execute("COMMIT")
                deficit = 1.0 - tokens
                return RateDecision(
                    False, tokens,
                    deficit / refill_per_second if refill_per_second > 0 else 60.0,
                )

            connection.execute(
                "INSERT OR REPLACE INTO rate_buckets (key, tokens, updated_at) "
                "VALUES (?, ?, ?)", (key, tokens - 1.0, now),
            )
            connection.execute("COMMIT")
            return RateDecision(True, tokens - 1.0)
        except sqlite3.Error:
            _rollback(connection)
            logger.warning("Rate check failed; allowing the request", exc_info=True)
            return RateDecision(True, capacity)

    def close(self) -> None:
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            connection.close()
            self._local.connection = None


def _rollback(connection: sqlite3.Connection) -> None:
    try:
        connection.execute("ROLLBACK")
    except sqlite3.Error:
        pass


def build_coordinator(data_dir: str | Path | None) -> Coordinator:
    """Pick a backend. SQLite when there is a shared directory, else in-process.

    ``LANA_COORDINATION`` forces one explicitly (``memory`` / ``sqlite``) for
    the case where an operator knows better than this heuristic — a single
    worker with persistence on, say, where the SQLite round trip is pure cost.
    """
    choice = os.getenv("LANA_COORDINATION", "auto").lower()
    if choice == "memory":
        return InProcessCoordinator()
    if choice == "sqlite":
        if not data_dir:
            raise ValueError(
                "LANA_COORDINATION=sqlite needs LANA_DATA_DIR to be set."
            )
        return SqliteCoordinator(Path(data_dir) / "coordination.db")
    if data_dir:
        return SqliteCoordinator(Path(data_dir) / "coordination.db")
    return InProcessCoordinator()
