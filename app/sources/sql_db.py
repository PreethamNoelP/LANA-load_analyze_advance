"""SQL databases: PostgreSQL, MySQL, SQLite, and anything else SQLAlchemy drives.

Design notes
------------
**Why SQLAlchemy and not a driver per engine.** One dependency, one URL
format, one exception hierarchy, and support for every engine a user is
likely to have without LANA needing to know about it. The engine-specific
driver (``psycopg``, ``pymysql``) is still needed at runtime, so a missing one
is reported as "install this", naming the package.

**Why the row cap is pushed into the query.** ``pd.read_sql`` with a
``LIMIT``-less statement materialises the whole result server-side and streams
all of it. On a fact table that is the difference between a preview and an
outage — on the *database*, which is someone else's production system. The cap
is wrapped around whatever the user supplied so it applies to a raw table read
and a hand-written query alike.

**Why identifiers are quoted through the dialect.** A table named ``order`` or
``Select`` is legal and common; so is one containing a quote character.
Letting SQLAlchemy's dialect do the quoting gets the per-engine rules right
and removes the injection surface that string-formatting a table name into SQL
would otherwise create.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from .base import (
    DEFAULT_ROW_LIMIT,
    PREVIEW_ROWS,
    ConnectionTest,
    DataSource,
    FetchResult,
    SourceCapabilities,
    SourceConfigError,
    SourceConnectionError,
    SourceUnavailable,
    normalize_frame,
    redact,
)

try:
    import sqlalchemy
    from sqlalchemy import text as sql_text

    SQLALCHEMY_AVAILABLE = True
except ImportError:  # pragma: no cover
    sqlalchemy = None
    sql_text = None
    SQLALCHEMY_AVAILABLE = False

# Tables listed in a connection test. A warehouse can have thousands; the list
# exists to help someone pick, not to be exhaustive.
MAX_LISTED_TABLES = 200

# Seconds to wait for a connection before reporting it unreachable. Short: the
# common failure is a wrong host or a closed port, and a user watching a form
# should not wait 30s to learn they typed the hostname wrong.
CONNECT_TIMEOUT_SECONDS = 10


class SqlSource(DataSource):
    kind = "sql"
    description = (
        "A SQL database — PostgreSQL, MySQL, SQLite or any other engine "
        "SQLAlchemy supports. Read a whole table, or supply your own query."
    )
    capabilities = SourceCapabilities(
        lists_entities=True,
        needs_entity=True,
        accepts_query=True,
        uses_secret=True,
        target_label="Connection URL",
        target_placeholder="postgresql+psycopg://user@host:5432/dbname",
        entity_label="Table",
    )

    def validate_spec(self) -> None:
        if not SQLALCHEMY_AVAILABLE:
            raise SourceUnavailable(
                "SQLAlchemy is not installed, so SQL sources are unavailable. "
                "Install it with `pip install sqlalchemy`."
            )
        url = self._require(
            self.spec.target, "Connection URL",
            "e.g. postgresql+psycopg://user@host:5432/dbname",
        )
        try:
            self._url = sqlalchemy.engine.make_url(url)
        except Exception as exc:
            raise SourceConfigError(
                f"That connection URL could not be parsed: {redact(str(exc))}"
            ) from exc
        if not self.spec.entity and not self.spec.options.get("query"):
            # Not fatal at construction: listing tables is a legitimate reason
            # to build a source with neither. fetch() enforces it.
            pass

    def _engine(self) -> Any:
        url = self._url
        if self.spec.secret:
            # The password is supplied out-of-band rather than embedded in the
            # URL the user typed, so it never has to appear in a form field, a
            # log line, or a saved connection record.
            url = url.set(password=self.spec.secret)
        connect_args = {}
        if url.get_backend_name() not in ("sqlite",):
            connect_args["connect_timeout"] = CONNECT_TIMEOUT_SECONDS
        try:
            return sqlalchemy.create_engine(
                url, pool_pre_ping=True, connect_args=connect_args
            )
        except ModuleNotFoundError as exc:
            raise SourceUnavailable(
                f"The driver for this database is not installed: {exc}. "
                f"For PostgreSQL install `psycopg`, for MySQL `pymysql`."
            ) from exc
        except Exception as exc:
            raise SourceConnectionError(
                f"Could not create a connection: {redact(str(exc))}"
            ) from exc

    def test_connection(self) -> ConnectionTest:
        try:
            engine = self._engine()
            with engine.connect() as connection:
                inspector = sqlalchemy.inspect(connection)
                tables = list(inspector.get_table_names())
                try:
                    tables += [
                        f"{v} (view)" for v in inspector.get_view_names()
                    ]
                except Exception:
                    # Not every dialect implements view listing; a missing
                    # view list must not fail an otherwise-good connection.
                    pass
            engine.dispose()
        except SourceUnavailable:
            raise
        except Exception as exc:
            return ConnectionTest(
                False,
                f"Could not connect: {redact(str(exc))[:300]}",
            )

        truncated = len(tables) > MAX_LISTED_TABLES
        return ConnectionTest(
            ok=True,
            detail=(
                f"Connected to {self._url.get_backend_name()} database "
                f"'{self._url.database}'. {len(tables)} tables visible."
            ),
            entities=sorted(tables)[:MAX_LISTED_TABLES],
            truncated_entities=truncated,
        )

    def _statement(self, limit: int) -> str:
        """The query to run, always wrapped in a row cap."""
        custom = self.spec.options.get("query")
        if custom:
            inner = str(custom).strip().rstrip(";")
            return f"SELECT * FROM ({inner}) AS lana_source LIMIT {int(limit)}"
        if not self.spec.entity:
            raise SourceConfigError(
                "Choose a table, or supply a query, before reading this source."
            )
        # Dialect-aware quoting: correct for a table called "order", and the
        # reason a table name is never string-formatted into SQL here.
        preparer = self._url.get_dialect()().identifier_preparer
        quoted = preparer.quote(self.spec.entity)
        return f"SELECT * FROM {quoted} LIMIT {int(limit)}"

    def fetch(self, *, limit: int = DEFAULT_ROW_LIMIT) -> FetchResult:
        statement = self._statement(limit)
        engine = self._engine()
        try:
            with engine.connect() as connection:
                frame = pd.read_sql(sql_text(statement), connection)
        except (SourceConfigError, SourceUnavailable):
            # Already carry the right message; rewrapping them as a connection
            # failure would replace an actionable error with a vague one.
            raise
        except Exception as exc:
            raise SourceConnectionError(
                f"Query failed: {redact(str(exc))[:300]}"
            ) from exc
        finally:
            engine.dispose()

        truncated = len(frame) >= limit
        label = self.spec.entity or "query"
        return FetchResult(
            frame=normalize_frame(frame),
            label=f"{self._url.database or 'database'}.{label}",
            row_limit_applied=truncated,
            notes=(
                [f"Row cap of {limit:,} applied at the database."]
                if truncated else []
            ),
        )

    def preview(self) -> FetchResult:
        return self.fetch(limit=PREVIEW_ROWS)
