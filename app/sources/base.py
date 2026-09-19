"""The DataSource contract every connector implements.

Why an abstraction at all
-------------------------
LANA's value is everything that happens *after* a DataFrame exists: profiling,
explainable cleaning, the grounded fact ledger, executed-SQL answers,
validation, charts, statistics, export. None of that cares where the rows came
from. Before this package there was exactly one way in — a file upload — and
the ingest path was wired directly into the ``/upload`` route, so "read from
Postgres" would have meant a second route duplicating admission control,
dtype optimisation, session creation and profiling.

So the contract is deliberately narrow: a connector's only job is to produce a
``pd.DataFrame`` and describe itself honestly. Everything downstream is
untouched and unaware. Adding a source is one subclass and one ``register``
call; it is not allowed to require changes anywhere else, and
``tests/test_sources.py`` asserts that by driving every registered connector
through the same conformance suite.

Three rules every connector follows
-----------------------------------
1. **Credentials never come back out.** A spec knows its secret; every string
   a connector produces for a human — labels, errors, logs — goes through
   ``redact``. A connection string in an error message is a credential leak
   with extra steps, and error text reaches the browser.
2. **Bounded by default.** Remote sources have no natural size limit, so every
   fetch carries a row cap and reports whether it bit. The host-derived memory
   budget that governs uploads governs these too — a 40 GB table must be
   refused, not discovered by being OOM-killed.
3. **Failures are actionable and specific.** "Could not connect" helps nobody.
   Each connector maps its driver's exceptions to a message naming what to fix,
   because the person reading it is usually configuring a connection, not
   debugging LANA.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

import pandas as pd


class SourceError(Exception):
    """Base for every connector failure. Carries user-safe text only."""


class SourceUnavailable(SourceError):
    """The driver for this source is not installed."""


class SourceConfigError(SourceError):
    """The spec is malformed or incomplete — fixable by the user."""


class SourceConnectionError(SourceError):
    """The source could not be reached or authenticated against."""


class SourceRefused(SourceError):
    """The request was well-formed but refused by policy (size, SSRF, limits)."""


# Rows a connector returns unless told otherwise. Bounded because a remote
# source has no natural end: `SELECT * FROM events` is a reasonable thing for
# a user to try and an unreasonable thing to materialise.
DEFAULT_ROW_LIMIT = 200_000

# Rows fetched for a preview. Enough for pandas to infer types usefully and
# for a person to recognise their own data; small enough to be instant.
PREVIEW_ROWS = 100


@dataclass(frozen=True)
class SourceSpec:
    """Everything needed to reach one source, including its secret.

    ``secret`` is separated from ``options`` so it is structurally impossible
    to serialise the spec for display and leak it by accident: ``describe()``
    and ``to_public_dict()`` cannot reach it without being rewritten to.
    """

    kind: str
    # Connection target: a URI, a URL, a file path. May itself embed a
    # credential (``postgresql://user:pass@host/db``), which is why it is
    # redacted rather than shown.
    target: str = ""
    # Which table, collection, sheet or endpoint within the source.
    entity: str | None = None
    secret: str | None = field(default=None, repr=False)
    options: dict[str, Any] = field(default_factory=dict)

    def to_public_dict(self) -> dict[str, Any]:
        """The spec as it may safely be logged, echoed or stored."""
        return {
            "kind": self.kind,
            "target": redact(self.target),
            "entity": self.entity,
            "has_secret": self.secret is not None,
            "options": {k: v for k, v in self.options.items() if k != "secret"},
        }


@dataclass
class ConnectionTest:
    """Result of probing a source without pulling data from it."""

    ok: bool
    detail: str
    entities: list[str] = field(default_factory=list)
    truncated_entities: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "detail": self.detail,
            "entities": list(self.entities),
            "truncated_entities": self.truncated_entities,
        }


@dataclass
class FetchResult:
    """A DataFrame plus an honest account of how it was obtained."""

    frame: pd.DataFrame
    # Human-readable, credential-free. Becomes the session's "filename", so it
    # is what the user sees in the UI and in exported reports.
    label: str
    row_limit_applied: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "rows": len(self.frame),
            "columns": [str(c) for c in self.frame.columns],
            "row_limit_applied": self.row_limit_applied,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class SourceCapabilities:
    """What a connector can do, so the UI can adapt without special-casing."""

    lists_entities: bool = False
    needs_entity: bool = False
    accepts_query: bool = False
    uses_secret: bool = False
    # Shown in the connection form as the label for `target`.
    target_label: str = "Connection"
    target_placeholder: str = ""
    entity_label: str = "Table"

    def to_dict(self) -> dict[str, Any]:
        return {
            "lists_entities": self.lists_entities,
            "needs_entity": self.needs_entity,
            "accepts_query": self.accepts_query,
            "uses_secret": self.uses_secret,
            "target_label": self.target_label,
            "target_placeholder": self.target_placeholder,
            "entity_label": self.entity_label,
        }


class DataSource(ABC):
    """One way of getting rows into LANA.

    Subclasses set ``kind``, ``capabilities`` and ``description``, then
    implement the three methods below. Nothing else in the codebase should
    need to know which subclass it is holding.
    """

    kind: ClassVar[str] = ""
    description: ClassVar[str] = ""
    capabilities: ClassVar[SourceCapabilities] = SourceCapabilities()

    def __init__(self, spec: SourceSpec) -> None:
        self.spec = spec
        self.validate_spec()

    # ── Contract ─────────────────────────────────────────────────────────────

    def validate_spec(self) -> None:  # noqa: B027 - optional hook, not abstract
        """Reject a malformed spec at construction, before any I/O.

        Overridden by connectors with required fields. Raising here means a
        bad connection form never opens a socket. Deliberately concrete and
        empty: a connector with nothing to validate should not be forced to
        write ``pass``, and making it abstract would do exactly that.
        """

    @abstractmethod
    def test_connection(self) -> ConnectionTest:
        """Reach the source, authenticate, and report what is available.

        Must not transfer a meaningful amount of data — this is the call
        behind a "Test connection" button and should be fast enough to sit in
        a form's submit handler.
        """

    @abstractmethod
    def fetch(self, *, limit: int = DEFAULT_ROW_LIMIT) -> FetchResult:
        """Pull rows and return them as a DataFrame."""

    def preview(self) -> FetchResult:
        """A small sample, for confirming this is the right data.

        The default is a capped ``fetch``; connectors that can push the limit
        down to the server override it so a preview of a billion-row table
        does not read a billion rows.
        """
        return self.fetch(limit=PREVIEW_ROWS)

    # ── Shared helpers ───────────────────────────────────────────────────────

    def describe(self) -> str:
        """A credential-free label for this connection."""
        entity = f" · {self.spec.entity}" if self.spec.entity else ""
        return f"{self.kind}: {redact(self.spec.target)}{entity}"

    @staticmethod
    def _require(value: str | None, field_name: str, hint: str) -> str:
        if not value or not str(value).strip():
            raise SourceConfigError(f"{field_name} is required — {hint}")
        return str(value).strip()


# ── Credential redaction ────────────────────────────────────────────────────
# Applied to every string a connector produces for a human. Deliberately
# aggressive: a false positive turns a hostname into "***" in an error
# message, which is an inconvenience. A false negative puts a production
# database password in a browser tab, a log aggregator and a screenshot.

# user:password@host — the form every database URI uses.
_URI_CREDENTIAL_RE = re.compile(r"://([^/@:\s]+):([^/@\s]+)@")
# Query-string secrets: ?api_key=..., &token=..., &password=...
_QUERY_SECRET_RE = re.compile(
    r"([?&](?:api[_-]?key|access[_-]?token|token|password|passwd|pwd|secret|"
    r"auth|signature|sig|key)=)([^&\s]+)",
    re.IGNORECASE,
)
# Bearer/provider tokens appearing loose in text.
_BEARER_RE = re.compile(
    r"\b(?:bearer\s+|sk-|gsk_|ghp_|xox[baprs]-|AKIA)[A-Za-z0-9._\-]{8,}",
    re.IGNORECASE,
)
# MongoDB/JDBC style: password=... in a semicolon-delimited option string.
_KV_SECRET_RE = re.compile(
    r"\b(password|passwd|pwd|secret|api[_-]?key|token)\s*=\s*([^;,\s]+)",
    re.IGNORECASE,
)


def redact(text: str | None) -> str:
    """Strip anything credential-shaped from a string bound for a human."""
    if not text:
        return ""
    out = str(text)
    out = _URI_CREDENTIAL_RE.sub(r"://\1:***@", out)
    out = _QUERY_SECRET_RE.sub(r"\1***", out)
    out = _KV_SECRET_RE.sub(r"\1=***", out)
    out = _BEARER_RE.sub("***", out)
    return out


def normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Make a connector's output look like a parsed upload.

    Every connector runs its result through this so downstream code sees one
    shape regardless of origin. Mongo hands back nested dicts and ObjectIds,
    SQL drivers return Decimal and UUID, REST returns whatever JSON held —
    none of which pandas, matplotlib, DuckDB or Parquet handle uniformly.

    What it guarantees:

    * column names are strings (Mongo keys can be anything; a non-string
      column name breaks Parquet persistence and SQL quoting);
    * duplicate column names are suffixed rather than silently shadowing;
    * object columns holding dict/list are JSON-encoded to text, so a nested
      document is inspectable instead of being an unrenderable blob;
    * remaining exotic scalars (Decimal, UUID, ObjectId) become str or float.
    """
    import json
    from decimal import Decimal

    if frame.empty and len(frame.columns) == 0:
        return frame

    frame = frame.copy()

    seen: dict[str, int] = {}
    columns: list[str] = []
    for raw in frame.columns:
        name = str(raw)
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 0
        columns.append(name)
    frame.columns = columns

    for column in frame.columns:
        if frame[column].dtype != object:
            continue
        sample = frame[column].dropna().head(50)
        if sample.empty:
            continue
        if any(isinstance(v, (dict, list)) for v in sample):
            frame[column] = frame[column].map(
                lambda v: json.dumps(v, default=str)
                if isinstance(v, (dict, list))
                else v
            )
            continue
        if any(isinstance(v, Decimal) for v in sample):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
            continue
        if any(
            not isinstance(v, (str, bytes, bool, int, float, type(None)))
            for v in sample
        ):
            frame[column] = frame[column].map(lambda v: v if v is None else str(v))

    return frame
