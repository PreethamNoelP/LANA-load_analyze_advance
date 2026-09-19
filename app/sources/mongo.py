"""MongoDB collections as DataFrames.

The interesting problem here is not connecting — it is that a Mongo collection
has no schema, and LANA's entire downstream pipeline assumes a rectangle.

Three things follow from that, and they are the substance of this module:

**Flattening.** A document is a tree. ``pd.DataFrame(list_of_docs)`` produces
object columns full of dicts, which profile as "free text", chart as nothing,
and break Parquet persistence. Nested documents are flattened to dotted
columns (``address.city``) up to a bounded depth, so the fields a user
actually wants to analyse become real columns. Arrays are left as JSON text:
exploding them changes the row count, which would silently make every count
and average in the session wrong.

**Sampling for the schema.** Different documents have different fields, so the
column set depends on which documents you look at. The preview samples the
first N and says so, rather than presenting one document's shape as the
collection's schema.

**``_id``.** Always present, never useful, and an ObjectId that breaks JSON
serialisation. Converted to string and kept — dropping a field the user can
see in their own database would be the more surprising behaviour.
"""

from __future__ import annotations

import json
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
    SourceError,
    SourceUnavailable,
    normalize_frame,
    redact,
)

try:
    import pymongo
    from pymongo.errors import PyMongoError

    PYMONGO_AVAILABLE = True
except ImportError:  # pragma: no cover
    pymongo = None
    PyMongoError = Exception
    PYMONGO_AVAILABLE = False

CONNECT_TIMEOUT_MS = 8_000

# How deep to flatten nested documents. Three levels covers the realistic
# shapes (``order.customer.address.city``); beyond that the dotted name is
# longer than the data is useful and the column count explodes.
MAX_FLATTEN_DEPTH = 3

# Ceiling on columns produced by flattening. A heterogeneous collection can
# otherwise yield thousands of one-off fields, which is not a table.
MAX_FLATTENED_COLUMNS = 300


def flatten_document(
    document: dict[str, Any], *, depth: int = MAX_FLATTEN_DEPTH
) -> dict[str, Any]:
    """Turn one nested document into a flat dict of dotted keys.

    Arrays become JSON strings rather than being exploded, because exploding
    changes the row count — and a row count that silently disagrees with the
    source collection would make every downstream count, mean and share wrong
    in a way the user has no way to see.
    """
    flat: dict[str, Any] = {}

    def walk(node: Any, prefix: str, level: int) -> None:
        if isinstance(node, dict) and level < depth:
            for key, value in node.items():
                walk(value, f"{prefix}.{key}" if prefix else str(key), level + 1)
            return
        if isinstance(node, (dict, list)):
            flat[prefix] = json.dumps(node, default=str)
            return
        flat[prefix] = node

    walk(document, "", 0)
    return flat


class MongoSource(DataSource):
    kind = "mongodb"
    description = (
        "A MongoDB collection. Nested documents are flattened to dotted "
        "columns; arrays are kept as JSON so row counts stay truthful."
    )
    capabilities = SourceCapabilities(
        lists_entities=True,
        needs_entity=True,
        accepts_query=True,
        uses_secret=True,
        target_label="Connection URI",
        target_placeholder="mongodb://host:27017/mydb",
        entity_label="Collection",
    )

    def validate_spec(self) -> None:
        if not PYMONGO_AVAILABLE:
            raise SourceUnavailable(
                "pymongo is not installed, so MongoDB sources are unavailable. "
                "Install it with `pip install pymongo`."
            )
        uri = self._require(
            self.spec.target, "Connection URI", "e.g. mongodb://host:27017/mydb"
        )
        if not uri.startswith(("mongodb://", "mongodb+srv://")):
            raise SourceConfigError(
                "A MongoDB URI must start with 'mongodb://' or 'mongodb+srv://'."
            )

    def _database_name(self) -> str | None:
        explicit = self.spec.options.get("database")
        if explicit:
            return str(explicit)
        try:
            parsed = pymongo.uri_parser.parse_uri(self.spec.target)
            return parsed.get("database")
        except Exception:
            return None

    def _client(self) -> Any:
        kwargs: dict[str, Any] = {
            "serverSelectionTimeoutMS": CONNECT_TIMEOUT_MS,
            "connectTimeoutMS": CONNECT_TIMEOUT_MS,
        }
        if self.spec.secret:
            kwargs["password"] = self.spec.secret
        try:
            return pymongo.MongoClient(self.spec.target, **kwargs)
        except Exception as exc:
            raise SourceConnectionError(
                f"Could not create a MongoDB client: {redact(str(exc))[:300]}"
            ) from exc

    def test_connection(self) -> ConnectionTest:
        client = None
        try:
            client = self._client()
            client.admin.command("ping")
            database_name = self._database_name()
            if not database_name:
                names = client.list_database_names()
                return ConnectionTest(
                    ok=True,
                    detail=(
                        "Connected, but the URI names no database. Add one "
                        "(mongodb://host:27017/mydb) or set the database option."
                    ),
                    entities=sorted(names),
                )
            collections = sorted(client[database_name].list_collection_names())
            return ConnectionTest(
                ok=True,
                detail=(
                    f"Connected to database '{database_name}'. "
                    f"{len(collections)} collections visible."
                ),
                entities=collections,
            )
        except PyMongoError as exc:
            return ConnectionTest(
                False, f"Could not connect: {redact(str(exc))[:300]}"
            )
        except SourceError as exc:
            return ConnectionTest(False, str(exc))
        finally:
            if client is not None:
                client.close()

    def _filter(self) -> dict[str, Any]:
        raw = self.spec.options.get("query")
        if not raw:
            return {}
        if isinstance(raw, dict):
            return raw
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise SourceConfigError(
                "The Mongo filter must be valid JSON, e.g. "
                '{"status": "active"}.'
            ) from exc
        if not isinstance(parsed, dict):
            raise SourceConfigError("The Mongo filter must be a JSON object.")
        return parsed

    def fetch(self, *, limit: int = DEFAULT_ROW_LIMIT) -> FetchResult:
        if not self.spec.entity:
            raise SourceConfigError("Choose a collection before reading this source.")
        database_name = self._database_name()
        if not database_name:
            raise SourceConfigError(
                "No database in the URI. Use mongodb://host:27017/mydb, or set "
                "the database option."
            )

        query = self._filter()
        client = None
        try:
            client = self._client()
            cursor = client[database_name][self.spec.entity].find(query).limit(limit)
            documents = list(cursor)
        except PyMongoError as exc:
            raise SourceConnectionError(
                f"Could not read collection '{self.spec.entity}': "
                f"{redact(str(exc))[:300]}"
            ) from exc
        finally:
            if client is not None:
                client.close()

        notes: list[str] = []
        if not documents:
            frame = pd.DataFrame()
            notes.append("The collection returned no documents for this filter.")
        else:
            flattened = [flatten_document(d) for d in documents]
            frame = pd.DataFrame(flattened)
            if len(frame.columns) > MAX_FLATTENED_COLUMNS:
                # Keep the most-populated fields: a heterogeneous collection's
                # long tail of one-off keys is noise, and saying so is better
                # than returning a 4,000-column frame.
                keep = (
                    frame.notna().sum().sort_values(ascending=False)
                    .head(MAX_FLATTENED_COLUMNS).index
                )
                dropped = len(frame.columns) - len(keep)
                frame = frame[list(keep)]
                notes.append(
                    f"Documents flattened to {len(frame.columns)} columns; "
                    f"{dropped} rarely-populated fields were dropped."
                )
            else:
                notes.append(
                    f"Flattened {len(documents):,} documents to "
                    f"{len(frame.columns)} columns."
                )

        truncated = len(documents) >= limit
        if truncated:
            notes.append(
                f"Row cap of {limit:,} applied — the collection may hold more."
            )
        notes.append(
            "Column set inferred from the documents read; Mongo collections "
            "have no fixed schema, so other documents may carry other fields."
        )

        return FetchResult(
            frame=normalize_frame(frame),
            label=f"{database_name}.{self.spec.entity}",
            row_limit_applied=truncated,
            notes=notes,
        )

    def preview(self) -> FetchResult:
        return self.fetch(limit=PREVIEW_ROWS)
