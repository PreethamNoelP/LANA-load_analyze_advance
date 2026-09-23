"""An append-only record of who did what to which dataset.

Why this is not the application log
-----------------------------------
``app/observability.py`` already emits a structured line per request and a
metric per outcome, and that is the right tool for "why was yesterday slow".
It is the wrong tool for "who exported the payroll extract, and when" — three
differences, each of which matters to whoever has to answer that question:

* **Retention.** Logs go to stdout and live as long as the container or the
  shipper's retention window. An audit record has to outlive both, so it is
  written to the data directory alongside the sessions it describes.
* **Selection.** A log records everything at a level. An audit trail records
  a deliberately small, stable set of *consequential* actions — data entering
  the system, being transformed, leaving it, and access being refused — so it
  can be read end to end by a person rather than grepped by an engineer.
* **Shape.** Every entry has the same fields, always, so it can be filtered
  and diffed without a parser that knows about each event type.

What is deliberately not recorded
---------------------------------
No cell values, no column names, no secrets, no tokens. An audit trail that
copies the data it is auditing is a second, less protected copy of that data —
which is how audit logging becomes the breach. Entries name *what happened to
which session*, and the session itself remains the only place the data is.

The principal is the same non-secret identity used for rate limiting and
session ownership: a hash prefix of the token, or ``ip:<address>`` when no
token is configured. It never carries the token.

Tamper evidence, and its exact limit
------------------------------------
Each entry carries ``prev``, the hash of the entry before it, and ``hash``,
the hash of itself including that link. The file is therefore a chain, and
:meth:`AuditLog.verify` detects three things a plain append-only file cannot:
an entry edited in place, an entry removed from the middle, and a truncated
tail.

The limit, stated precisely because this is exactly the claim that gets
overstated: someone who can write to the file and knows the scheme can
recompute the chain from the point they altered, and the result verifies.
Detecting that needs a key they do not have (an HMAC) or a copy they cannot
reach (shipping entries off-host). Neither is claimed here.

What the chain does buy is real: casual tampering, a truncating crash and a
partial deletion all become visible, and those are what actually happen. A
determined attacker with write access and knowledge of the format is a
different threat, and the honest answer to it is to ship the log somewhere
they do not control.

Off unless there is somewhere to put it: like session persistence, a plain
``uvicorn --reload`` dev run should not start writing files into a
contributor's checkout. Enabled automatically when persistence is on, or
explicitly with ``LANA_AUDIT_LOG``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("lana.audit")

# Actions worth a permanent record. A closed set rather than free-form
# strings, so a reader can enumerate what LANA is capable of recording and a
# typo cannot invent a category nobody will ever search for.
DATA_LOADED = "data.loaded"
DATA_CLEANED = "data.cleaned"
DATA_EXPORTED = "data.exported"
DATA_VERSION_SWITCHED = "data.version_switched"
QUESTION_ANSWERED = "question.answered"
INVESTIGATION_RUN = "investigation.run"
SOURCE_TESTED = "source.tested"
ACCESS_DENIED = "access.denied"
AUTH_FAILED = "auth.failed"
# Successful sign-ins matter as much as failed ones: "nobody signed in that
# night" is only an answer if successes are recorded too.
AUTH_SUCCEEDED = "auth.succeeded"
# A reset request and its completion are recorded separately: the first is
# something anyone can trigger against any username (that's the point of the
# generic response), the second is proof a token was actually redeemed.
PASSWORD_RESET_REQUESTED = "auth.password_reset_requested"
PASSWORD_RESET_COMPLETED = "auth.password_reset_completed"

ACTIONS = frozenset({
    DATA_LOADED, DATA_CLEANED, DATA_EXPORTED, DATA_VERSION_SWITCHED,
    QUESTION_ANSWERED, INVESTIGATION_RUN, SOURCE_TESTED, ACCESS_DENIED,
    AUTH_FAILED, AUTH_SUCCEEDED, PASSWORD_RESET_REQUESTED,
    PASSWORD_RESET_COMPLETED,
})

# Rotate at this size and keep this many old files. Bounded because an audit
# log that fills the disk takes the application down, which is a worse outcome
# than losing the oldest entries — and unbounded growth is the usual reason
# audit logging gets switched off in production.
MAX_BYTES = 8 * 1024 * 1024
KEEP_FILES = 3

# Longest value kept for any single detail field. Details come from user input
# (a filename, a connector label) and must not be able to make one entry
# arbitrarily large.
MAX_DETAIL_CHARS = 200

# Entries returned by one read. The trail is for review, not for streaming.
MAX_READ_ENTRIES = 500


# The hash standing in for "the entry before the first one". A fixed, known
# value, so the start of a chain is verifiable rather than being whatever
# happened to be there.
GENESIS_HASH = "0" * 64


@dataclass(frozen=True)
class AuditEntry:
    """One consequential action, in the shape every entry shares."""

    ts: float
    action: str
    principal: str
    outcome: str = "ok"
    session_id: str | None = None
    request_id: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def body(self) -> dict[str, Any]:
        """The entry's own content, without its chain fields."""
        return {
            "ts": round(self.ts, 3),
            "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.ts)),
            "action": self.action,
            "principal": self.principal,
            "outcome": self.outcome,
            "session_id": self.session_id,
            "request_id": self.request_id,
            "detail": self.detail,
        }

    def to_dict(self, prev_hash: str = GENESIS_HASH) -> dict[str, Any]:
        """The full record, linked to the entry before it."""
        payload = self.body()
        payload["prev"] = prev_hash
        payload["hash"] = entry_hash(payload)
        return payload


def entry_hash(payload: dict[str, Any]) -> str:
    """Hash one entry, over everything except the hash field itself.

    ``sort_keys`` makes the digest independent of dict ordering, so a record
    read back from disk and re-hashed matches what was written. Without it,
    verification would fail on key order alone.
    """
    material = {k: v for k, v in payload.items() if k != "hash"}
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def scrub(detail: dict[str, Any] | None) -> dict[str, Any]:
    """Bound and flatten a detail mapping before it is written.

    Applies the connector redaction to every string, because the most likely
    thing to arrive here by accident is a connection label, and a label built
    from a URI carries a password. Cheap insurance against the caller that
    forgets.
    """
    if not detail:
        return {}
    from app.sources.base import redact

    clean: dict[str, Any] = {}
    for key, value in list(detail.items())[:20]:
        name = str(key)[:40]
        if isinstance(value, str):
            clean[name] = redact(value)[:MAX_DETAIL_CHARS]
        elif isinstance(value, (int, float, bool)) or value is None:
            clean[name] = value
        else:
            clean[name] = str(value)[:MAX_DETAIL_CHARS]
    return clean


class AuditLog:
    """Append-only JSONL, rotated by size.

    One line per entry, so a partial write damages at most the entry being
    written rather than the file — the reader skips unparseable lines for
    exactly that reason.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # Cached so the common case — append one entry — does not re-read the
        # file to find the previous hash. Seeded from disk on first use, so a
        # restart continues the existing chain instead of starting a fresh one
        # that verification would report as a break.
        self._last_hash: str | None = None

    def record(self, entry: AuditEntry) -> None:
        """Write one entry. Never raises.

        An audit write failing must not fail the user's request: the action it
        describes has already happened, and turning a successful export into a
        500 because a disk is full helps nobody. The failure is logged, which
        is itself visible to whoever is watching.
        """
        try:
            with self._lock:
                self._rotate_if_needed()
                if self._last_hash is None:
                    self._last_hash = self._read_last_hash()
                payload = entry.to_dict(self._last_hash)
                line = json.dumps(payload, separators=(",", ":"))
                with self.path.open("a", encoding="utf-8") as handle:
                    # A process killed mid-write leaves a line with no
                    # terminator. Appending straight onto it would fuse the
                    # fragment and this entry into one unparseable line, so
                    # the crash would cost *two* records instead of one — and
                    # the second is the one nobody knows is missing. Close the
                    # torn line first.
                    if self._ends_mid_line():
                        handle.write("\n")
                    handle.write(line + "\n")
                self._last_hash = payload["hash"]
        except Exception:
            logger.warning("Could not write an audit entry", exc_info=True)

    def _read_last_hash(self) -> str:
        """The hash of the final entry on disk, or the genesis value."""
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                lines = handle.readlines()
        except OSError:
            return GENESIS_HASH
        for raw in reversed(lines):
            raw = raw.strip()
            if not raw:
                continue
            try:
                return json.loads(raw).get("hash") or GENESIS_HASH
            except json.JSONDecodeError:
                continue
        return GENESIS_HASH

    def verify(self) -> dict[str, Any]:
        """Walk the chain and report the first break, if any.

        A caller gets the position of the break rather than a bare boolean,
        because "the log was altered" is not actionable and "entry 412 does
        not match its own hash" is.
        """
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                lines = [ln.strip() for ln in handle if ln.strip()]
        except FileNotFoundError:
            return {"ok": True, "entries": 0, "broken_at": None,
                    "reason": "no audit log has been written yet"}
        except OSError as exc:
            return {"ok": False, "entries": 0, "broken_at": None,
                    "reason": f"could not read the log: {exc}"}

        expected = GENESIS_HASH
        for index, raw in enumerate(lines):
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                return {"ok": False, "entries": index, "broken_at": index,
                        "reason": "entry is not valid JSON"}
            if record.get("prev") != expected:
                return {
                    "ok": False, "entries": index, "broken_at": index,
                    "reason": "entry does not follow the one before it — "
                              "something was deleted, reordered or inserted",
                }
            if record.get("hash") != entry_hash(record):
                return {
                    "ok": False, "entries": index, "broken_at": index,
                    "reason": "entry does not match its own hash — it was "
                              "modified after it was written",
                }
            expected = record["hash"]

        return {"ok": True, "entries": len(lines), "broken_at": None,
                "reason": "every entry follows the one before it"}

    def _ends_mid_line(self) -> bool:
        """Whether the file's last byte is something other than a newline."""
        try:
            size = self.path.stat().st_size
            if size == 0:
                return False
            with self.path.open("rb") as handle:
                handle.seek(-1, 2)
                return handle.read(1) != b"\n"
        except OSError:
            return False

    def _rotate_if_needed(self) -> None:
        try:
            if not self.path.exists() or self.path.stat().st_size < MAX_BYTES:
                return
        except OSError:
            return
        # audit.2.jsonl -> audit.3.jsonl, ..., audit.jsonl -> audit.1.jsonl
        for index in range(KEEP_FILES - 1, 0, -1):
            source = self.path.with_suffix(f".{index}.jsonl")
            target = self.path.with_suffix(f".{index + 1}.jsonl")
            if source.exists():
                source.replace(target)
        self.path.replace(self.path.with_suffix(".1.jsonl"))
        # A rotated file starts a new chain. Recorded as a deliberate break
        # rather than pretending continuity across files: verify() reads one
        # file, so claiming the chain spans rotation would be a claim it
        # cannot actually check.
        self._last_hash = GENESIS_HASH

    def read(
        self,
        *,
        principal: str | None = None,
        session_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Recent entries, newest first, optionally narrowed.

        ``principal`` is a filter rather than an option in practice: the API
        passes the caller's own identity, so reading the trail never reveals
        another principal's activity — the same rule session access follows.

        Only the current file is read. An entry old enough to have rotated out
        is out of scope for the in-app view; the rotated files are on disk for
        whoever is doing a real investigation.
        """
        limit = max(1, min(int(limit), MAX_READ_ENTRIES))
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                lines = handle.readlines()
        except FileNotFoundError:
            return []
        except OSError:
            logger.warning("Could not read the audit trail", exc_info=True)
            return []

        entries: list[dict[str, Any]] = []
        for raw in reversed(lines):
            raw = raw.strip()
            if not raw:
                continue
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                continue  # a torn final write — skip it, do not fail the read
            if principal is not None and record.get("principal") != principal:
                continue
            if session_id is not None and record.get("session_id") != session_id:
                continue
            entries.append(record)
            if len(entries) >= limit:
                break
        return entries


class NullAuditLog:
    """What runs when there is nowhere to write. Same interface, does nothing.

    A null object rather than an ``if self._audit is not None`` at every call
    site: the check that gets forgotten is the event that goes unrecorded.
    """

    enabled = False
    path = None

    def record(self, entry: AuditEntry) -> None:
        return None

    def read(self, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    def verify(self) -> dict[str, Any]:
        return {"ok": True, "entries": 0, "broken_at": None,
                "reason": "auditing is not enabled on this instance"}


AuditLog.enabled = True  # type: ignore[attr-defined]


def build_audit_log(data_dir: str | Path | None) -> AuditLog | NullAuditLog:
    """Pick a backend from configuration.

    ``LANA_AUDIT_LOG`` names a file explicitly and turns the trail on wherever
    it points. Otherwise the trail follows session persistence: if sessions
    are durable there is a data directory to write to and a reason to keep a
    history; if they are not, this is a dev run and LANA writes nothing.
    """
    explicit = os.getenv("LANA_AUDIT_LOG", "").strip()
    if explicit:
        return AuditLog(Path(explicit))
    if data_dir:
        return AuditLog(Path(data_dir) / "audit.jsonl")
    return NullAuditLog()
