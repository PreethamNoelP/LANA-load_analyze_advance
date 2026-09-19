"""Sandboxed SQL execution over a session's DataFrame.

Why this exists
---------------
``app/llm/context.py`` grounds an answer in a *precomputed* fact ledger. That
design has a ceiling the eval measured directly: the ledger holds what
``build_context()`` happened to compute — 30 columns, 8 correlations, 2
regressions, group averages for at most 3 categoricals — and a question needing
anything else cannot be answered correctly no matter how good the model is.
Three of the seven residual failures in the 40-case run were exactly this
("scope gaps, not hallucinations", docs/engineering-changelog.md 2026-08-19).

Executing a query removes the ceiling: the number comes from running SQL over
the real rows, so it is correct by construction rather than correct if someone
precomputed it. That is a strictly stronger form of grounding than retrieval,
and it is why this path takes precedence over the ledger when it succeeds.

The ledger is *not* replaced. It still builds the prompt the planner reads, it
still answers when SQL planning fails, and it remains what
``app.llm.validation`` checks prose against. See ``app/llm/sql_answer.py`` for
how the two compose.

Why the safety model is what it is
----------------------------------
A model writing SQL that a server executes is a code-execution path reached
from a text box. DuckDB is not a toy database — it reads files, speaks HTTP
with ``httpfs``, and can ``ATTACH`` arbitrary databases — so a naive
integration turns "ask a question about your spreadsheet" into arbitrary local
file read and SSRF.

Four independent layers, each sufficient to stop the obvious attack and none
trusted alone:

1. **DuckDB's own sandbox** (``_configure``). ``enable_external_access=false``
   removes the file system and network from the engine entirely, and
   ``lock_configuration=true`` stops a query turning it back on. Verified
   against ``read_csv_auto``, ``COPY ... TO``, ``ATTACH`` and ``INSTALL`` —
   every one raises ``PermissionException`` and the re-enable attempt raises
   ``InvalidInputException``. This is the layer that actually holds.
2. **Single statement** (``validate_sql``). DuckDB's own parser counts them,
   so ``SELECT 1; DROP TABLE t`` is refused before anything runs.
3. **Read-only shape.** The statement must begin ``SELECT`` or ``WITH``.
4. **A lexical denylist.** Defence in depth against a future DuckDB release
   that adds a file-touching function the sandbox does not yet cover.

Layer 1 is load-bearing; 2-4 exist so a single regression in it is not a
breach. Bounded rows and a wall-clock deadline complete the picture, because a
correctly-sandboxed query can still be a denial of service.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

logger = logging.getLogger("lana.sql")

try:
    import duckdb

    DUCKDB_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only where duckdb is absent
    duckdb = None
    DUCKDB_AVAILABLE = False


# The view name the session's frame is registered under. Stated in the planner
# prompt and required by validate_sql, so a model inventing a table name fails
# fast with a message naming the real one rather than a DuckDB catalog error.
TABLE_NAME = "dataset"

# Result bounds. A grounded answer quotes a handful of figures; a query
# returning 100k rows is either a mistake or an attempt to exhaust memory, and
# in both cases the first rows are what the model will actually read.
MAX_RESULT_ROWS = 200
MAX_RESULT_CELLS = 5_000

# Wall-clock deadline for one query. DuckDB releases the GIL during execution,
# so interrupt() from a watchdog thread is effective. A cross join on a large
# frame is the realistic way to hang this path.
DEFAULT_TIMEOUT_SECONDS = 20.0

# Longest SQL accepted. Real analytical queries are far shorter; this bounds
# both the parser and the denylist scan.
MAX_SQL_CHARS = 4_000


class SqlRejected(Exception):
    """The statement was refused before execution. Carries a user-safe reason."""


class SqlExecutionError(Exception):
    """The statement was accepted but failed or timed out during execution."""


# Comments are stripped before the shape and denylist checks: `/* */ DROP` and
# `--\nDROP` are the standard way to hide a keyword from a naive scanner.
_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)

# Statement types that are not a read. Checked as whole words at the start of
# the statement; the sandbox blocks the dangerous ones anyway, but a clear
# "this is not a read-only query" beats a PermissionException from the engine.
_FORBIDDEN_LEADING = (
    "insert", "update", "delete", "drop", "create", "alter", "truncate",
    "attach", "detach", "copy", "install", "load", "set", "reset", "export",
    "import", "pragma", "call", "checkpoint", "vacuum", "analyze", "begin",
    "commit", "rollback", "grant", "revoke", "use", "prepare", "execute",
    "deallocate", "force",
)

# Tokens that reach outside the registered frame. Redundant with the sandbox
# by design — this list is the thing that still holds if a DuckDB upgrade adds
# a reader the sandbox has not yet been taught about.
#
# Split into two classes because matching them the same way cannot be both
# safe and correct, and this file's own tests found it in both directions:
#
# * DuckDB's readers come in families — `read_csv`, `read_csv_auto`,
#   `read_json_auto` — so a whole-word `\bread_csv\b` matches none of the
#   `_auto` variants (`_` is a word character, so there is no boundary after
#   the prefix). `read_csv_auto('/etc/passwd')` passed validation and was
#   stopped only by the sandbox: exactly the single-layer situation this list
#   exists to prevent.
# * But matching them as bare prefixes refuses a column legitimately named
#   `glob_region`, and refusing real data is a failure too.
#
# So a *function* is matched as a prefix that is actually called — identifier
# characters, optional closing quote, then `(`. A bare identifier never is.
_DENIED_FUNCTIONS = (
    "read_csv", "read_parquet", "read_json", "read_ndjson", "read_text",
    "read_blob", "read_xlsx", "sniff_csv", "glob", "parquet_scan", "csv_scan",
    "iceberg_scan", "delta_scan", "postgres_scan", "mysql_scan", "sqlite_scan",
    "http_get", "load_extension", "duckdb_extensions", "shell", "system",
    "getenv", "pg_terminate",
)

# Statement keywords that must not appear anywhere, including inside a CTE or
# subquery where the leading-word check would not see them.
_DENIED_KEYWORDS = (
    "install", "attach", "detach", "copy", "export", "import", "httpfs",
)

_DENY_PATTERNS = tuple(
    [
        # An optional opening quote lets `"read_csv_auto"('x')` be caught too,
        # since a quoted identifier is still callable.
        (token, re.compile(rf'["`]?\b{re.escape(token)}\w*["`]?\s*\(', re.IGNORECASE))
        for token in _DENIED_FUNCTIONS
    ]
    + [
        (token, re.compile(rf"\b{re.escape(token)}\b", re.IGNORECASE))
        for token in _DENIED_KEYWORDS
    ]
)


@dataclass(frozen=True)
class SqlResult:
    """One executed query and everything needed to show its provenance.

    ``rows`` is already bounded and JSON-safe. ``truncated`` is surfaced rather
    than inferred so an answer built from a partial result can say so — the
    same rule the rest of LANA follows about sampling.
    """

    sql: str
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    elapsed_ms: float

    @property
    def is_empty(self) -> bool:
        return self.row_count == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "sql": self.sql,
            "columns": list(self.columns),
            "rows": [list(r) for r in self.rows],
            "row_count": self.row_count,
            "truncated": self.truncated,
            "elapsed_ms": round(self.elapsed_ms, 2),
        }

    def to_markdown(self, max_rows: int = 25) -> str:
        """The result as a compact table for the answering model to read."""
        if not self.columns:
            return "(no columns)"
        if self.is_empty:
            return "(the query returned no rows)"
        head = " | ".join(str(c) for c in self.columns)
        rule = " | ".join("---" for _ in self.columns)
        body = [
            " | ".join(_render_cell(v) for v in row) for row in self.rows[:max_rows]
        ]
        table = "\n".join([head, rule, *body])
        if self.row_count > max_rows:
            table += f"\n... ({self.row_count - max_rows:,} more rows)"
        return table


def _render_cell(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, float):
        if value.is_integer() and abs(value) < 1e15:
            return f"{int(value):,}"
        return f"{value:,.6g}"
    return str(value)


def _strip_comments(sql: str) -> str:
    return _LINE_COMMENT.sub(" ", _BLOCK_COMMENT.sub(" ", sql))


def validate_sql(sql: str, table_name: str = TABLE_NAME) -> str:
    """Refuse anything that is not a single read-only query over the dataset.

    Returns the statement with surrounding whitespace and a trailing semicolon
    removed. Raises :class:`SqlRejected` with a reason safe to show a user —
    these messages are fed back to the planning model as a repair hint, so
    they have to name the actual problem.
    """
    if not sql or not sql.strip():
        raise SqlRejected("The query is empty.")
    if len(sql) > MAX_SQL_CHARS:
        raise SqlRejected(
            f"The query is {len(sql):,} characters; the limit is {MAX_SQL_CHARS:,}."
        )

    stripped = _strip_comments(sql).strip().rstrip(";").strip()
    if not stripped:
        raise SqlRejected("The query contains only comments.")

    # DuckDB's own parser decides what counts as a statement. A hand-rolled
    # semicolon split gets this wrong for a semicolon inside a string literal,
    # in both directions.
    if DUCKDB_AVAILABLE:
        try:
            statements = duckdb.extract_statements(stripped)
        except Exception as exc:
            raise SqlRejected(f"The query could not be parsed: {exc}") from exc
        if len(statements) != 1:
            raise SqlRejected(
                f"Only one statement may be run at a time; this is {len(statements)}."
            )

    lowered = stripped.lower()
    leading = re.match(r"[a-z_]+", lowered)
    first_word = leading.group(0) if leading else ""
    if first_word in _FORBIDDEN_LEADING:
        raise SqlRejected(
            f"'{first_word.upper()}' is not allowed — only read-only SELECT "
            f"queries over '{table_name}' can be run."
        )
    if first_word not in ("select", "with"):
        raise SqlRejected(
            f"The query must start with SELECT or WITH; it starts with "
            f"'{first_word or stripped[:12]}'."
        )

    for token, pattern in _DENY_PATTERNS:
        if pattern.search(lowered):
            raise SqlRejected(
                f"'{token.strip()}' is not permitted — queries may only read "
                f"the '{table_name}' table already loaded in this session."
            )

    return stripped


def _configure(con: Any) -> None:
    """Strip the engine down to an in-memory query processor.

    Order matters: ``lock_configuration`` must be set last, because it also
    locks out the settings above it.
    """
    for statement in (
        "SET enable_external_access=false",
        "SET allow_community_extensions=false",
        "SET autoinstall_known_extensions=false",
        "SET autoload_known_extensions=false",
        "SET lock_configuration=true",
    ):
        try:
            con.execute(statement)
        except Exception:
            # A DuckDB build without one of these options must not silently
            # become a build without any of them.
            logger.warning("DuckDB hardening statement failed: %s", statement)


@dataclass
class _Deadline:
    """Interrupts a DuckDB connection if the query outlives its budget.

    DuckDB releases the GIL while executing, so a timer thread calling
    ``interrupt()`` actually lands. Without this a cross join is an untimed
    hang holding a worker thread.
    """

    connection: Any
    seconds: float
    _timer: threading.Timer | None = field(default=None, init=False)
    fired: bool = field(default=False, init=False)

    def __enter__(self) -> _Deadline:
        def _fire() -> None:
            self.fired = True
            try:
                self.connection.interrupt()
            except Exception:  # pragma: no cover - engine already finished
                pass

        self._timer = threading.Timer(self.seconds, _fire)
        self._timer.daemon = True
        self._timer.start()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._timer is not None:
            self._timer.cancel()


def execute_sql(
    df: pd.DataFrame,
    sql: str,
    *,
    table_name: str = TABLE_NAME,
    max_rows: int = MAX_RESULT_ROWS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    validate: bool = True,
) -> SqlResult:
    """Run one read-only query against ``df`` and return a bounded result.

    The frame is *registered*, not copied: DuckDB reads pandas memory in
    place, so this costs no additional resident bytes for the data itself —
    which matters because the session store's whole budget is built on knowing
    how much memory a frame occupies.
    """
    if not DUCKDB_AVAILABLE:
        raise SqlExecutionError(
            "DuckDB is not installed, so executed-SQL grounding is unavailable. "
            "Install it with `pip install duckdb`."
        )

    statement = validate_sql(sql, table_name) if validate else sql.strip().rstrip(";")

    started = time.perf_counter()
    con = duckdb.connect(":memory:")
    try:
        _configure(con)
        # Registered *after* hardening so the view cannot be created by a
        # connection that still had file access.
        con.register(table_name, df)
        with _Deadline(con, timeout_seconds) as deadline:
            try:
                cursor = con.execute(statement)
                # One row over the cap, so truncation is detected without
                # fetching an unbounded result to measure it.
                fetched = cursor.fetchmany(max_rows + 1)
                columns = [d[0] for d in (cursor.description or [])]
            except Exception as exc:
                if deadline.fired:
                    raise SqlExecutionError(
                        f"The query took longer than {timeout_seconds:.0f}s and was "
                        f"stopped. Narrow it — add a filter, or aggregate instead "
                        f"of listing rows."
                    ) from exc
                raise SqlExecutionError(_safe_engine_error(exc)) from exc
    finally:
        con.close()

    truncated = len(fetched) > max_rows
    rows = [list(r) for r in fetched[:max_rows]]

    # A very wide result is as expensive to carry into a prompt as a long one,
    # and neither is readable. Columns are kept whole; rows are what gets cut.
    if columns and len(rows) * len(columns) > MAX_RESULT_CELLS:
        keep = max(1, MAX_RESULT_CELLS // len(columns))
        rows = rows[:keep]
        truncated = True

    return SqlResult(
        sql=statement,
        columns=columns,
        rows=[[_jsonable_cell(v) for v in row] for row in rows],
        row_count=len(rows),
        truncated=truncated,
        elapsed_ms=(time.perf_counter() - started) * 1000.0,
    )


def _jsonable_cell(value: Any) -> Any:
    """Coerce a DuckDB cell into something JSON and the validator can read."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        # NaN/Inf are not valid JSON; the API's _jsonable does this too, but a
        # SqlResult is also consumed directly by the fact extractor.
        return None if value != value or value in (float("inf"), float("-inf")) else value
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return _jsonable_cell(value.item())
        except Exception:
            pass
    return str(value)


# DuckDB error text can quote the query and internal paths. The query is the
# user's own, but the paths are the operator's, and this string is shown in the
# browser — the same reasoning as _sanitize_llm_error in backend/main.py.
_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s\"']+|/(?:home|usr|var|etc|root)/[^\s\"']+")
_MAX_ENGINE_ERROR_CHARS = 240


def _safe_engine_error(exc: Exception) -> str:
    text = _PATH_RE.sub("[path]", str(exc))
    text = " ".join(text.split())
    if len(text) > _MAX_ENGINE_ERROR_CHARS:
        text = text[:_MAX_ENGINE_ERROR_CHARS] + "…"
    return f"The query could not be run: {text}"


def schema_for_prompt(df: pd.DataFrame, table_name: str = TABLE_NAME,
                       max_columns: int = 60) -> str:
    """A CREATE TABLE-shaped schema for the planning model to read.

    Types are the SQL types DuckDB will actually see, not pandas dtypes: a
    planner told a column is ``object`` writes different (worse) SQL than one
    told it is ``VARCHAR``.
    """
    lines = [f"Table `{table_name}` ({len(df):,} rows):"]
    for name in list(df.columns)[:max_columns]:
        lines.append(f"  {_quote_ident(str(name))} {_sql_type(df[name])}")
    remaining = len(df.columns) - max_columns
    if remaining > 0:
        lines.append(f"  -- and {remaining} more columns not shown")
    return "\n".join(lines)


def _sql_type(series: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(series):
        return "BOOLEAN"
    if pd.api.types.is_integer_dtype(series):
        return "BIGINT"
    if pd.api.types.is_float_dtype(series):
        return "DOUBLE"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "TIMESTAMP"
    return "VARCHAR"


def _quote_ident(name: str) -> str:
    """Double-quote an identifier, escaping embedded quotes.

    Uploaded column names are arbitrary text — ``order id``, ``total($)``,
    ``a"b`` are all real — so every generated identifier is quoted rather than
    hoping it happens to be bare-word safe.
    """
    return '"' + name.replace('"', '""') + '"'
