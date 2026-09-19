"""The connector contract, applied uniformly to every registered source.

Two halves:

* a **conformance suite** that every connector must pass, so the promise that
  "adding a source requires no changes elsewhere" is enforced rather than
  asserted. Anything registered is picked up automatically.
* **per-connector** tests for the behaviour that is genuinely specific —
  Mongo's flattening, REST's record-finding, SQL's identifier quoting, and
  above all the SSRF guard, which is the single most dangerous thing this
  package adds.

Only SQLite and local files are exercised against a live backend; Postgres and
Mongo servers are not available in CI, so those connectors are tested through
their pure logic (spec validation, statement construction, flattening) and
their failure paths. That boundary is stated rather than papered over — see
the module docstring in tests/test_sources_integration.py for the marker used
to run the real-server tests when one is available.
"""

from __future__ import annotations

import json
import sqlite3

import pandas as pd
import pytest

from app.sources import (
    DataSource,
    SourceConfigError,
    SourceRefused,
    SourceSpec,
    available_sources,
    build_source,
    registry,
)
from app.sources.base import normalize_frame, redact
from app.sources.mongo import flatten_document
from app.sources.security import check_url

# ── Conformance: every registered connector ─────────────────────────────────

ALL_KINDS = sorted(registry())


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_every_connector_declares_its_identity(kind):
    source_class = registry()[kind]
    assert source_class.kind == kind
    assert source_class.description, f"{kind} must describe itself for the picker"
    assert issubclass(source_class, DataSource)


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_every_connector_implements_the_contract(kind):
    source_class = registry()[kind]
    for method in ("test_connection", "fetch", "preview"):
        assert callable(getattr(source_class, method, None)), (
            f"{kind} is missing {method}()"
        )


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_every_connector_rejects_an_empty_target(kind):
    """A blank connection form must fail before any I/O happens."""
    with pytest.raises(Exception) as exc:
        build_source(SourceSpec(kind=kind, target=""))
    assert exc.type.__name__ in (
        "SourceConfigError", "SourceUnavailable", "SourceRefused"
    )


def test_the_source_listing_is_generated_from_the_registry():
    listing = available_sources()
    assert {entry["kind"] for entry in listing} == set(ALL_KINDS)
    for entry in listing:
        assert "capabilities" in entry
        assert isinstance(entry["available"], bool)


def test_an_unknown_kind_names_the_available_ones():
    with pytest.raises(SourceConfigError, match="Unknown source type"):
        build_source(SourceSpec(kind="carrier_pigeon", target="x"))


def test_a_spec_never_serialises_its_secret():
    spec = SourceSpec(
        kind="sql",
        target="postgresql://user:hunter2@db.internal/app",
        secret="hunter2",
    )
    public = spec.to_public_dict()
    blob = json.dumps(public)
    assert "hunter2" not in blob
    assert public["has_secret"] is True


# ── Credential redaction ────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,must_not_contain", [
    ("postgresql://admin:s3cr3t@db.host/app", "s3cr3t"),
    ("mongodb://root:letmein@10.0.0.4:27017/db", "letmein"),
    ("https://api.example.com/v1?api_key=abcd1234efgh", "abcd1234efgh"),
    ("https://api.example.com/v1?token=xyz987654321", "xyz987654321"),
    ("Authorization: Bearer sk-abcdef1234567890", "sk-abcdef1234567890"),
    ("Server=db;Password=Passw0rd!;Database=app", "Passw0rd!"),
])
def test_redaction_removes_credentials(raw, must_not_contain):
    assert must_not_contain not in redact(raw)


def test_redaction_keeps_the_useful_part():
    assert "db.host" in redact("postgresql://admin:s3cr3t@db.host/app")


# ── SSRF guard ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",   # cloud metadata
    "http://127.0.0.1:8000/health",                # loopback
    "http://localhost:8000/health",
    "http://10.0.0.5:9200/_search",                # RFC1918
    "http://192.168.1.1/admin",
    "http://172.16.0.1/",
    "http://[::1]:8000/",                          # IPv6 loopback
    "http://0.0.0.0:8000/",
])
def test_private_and_metadata_addresses_are_refused(url, monkeypatch):
    monkeypatch.delenv("LANA_ALLOW_PRIVATE_SOURCE_URLS", raising=False)
    with pytest.raises(SourceRefused):
        check_url(url)


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "gopher://evil.example.com/",
    "ftp://files.example.com/data.csv",
    "jar:http://example.com/a.jar!/",
])
def test_non_http_schemes_are_refused(url):
    with pytest.raises(SourceRefused, match="http"):
        check_url(url)


def test_a_disallowed_port_is_refused(monkeypatch):
    monkeypatch.delenv("LANA_ALLOW_PRIVATE_SOURCE_URLS", raising=False)
    with pytest.raises(SourceRefused, match="Port"):
        check_url("http://example.com:6379/")


def test_the_escape_hatch_permits_private_addresses(monkeypatch):
    monkeypatch.setenv("LANA_ALLOW_PRIVATE_SOURCE_URLS", "true")
    checked = check_url("http://127.0.0.1:8000/health")
    assert checked.host == "127.0.0.1"


def test_a_hostname_with_any_private_address_is_refused(monkeypatch):
    """Checking one resolved address is not enough.

    A name with an A record for a public IP and another for 127.0.0.1 passes
    a first-match check and then connects wherever the OS chooses.
    """
    monkeypatch.delenv("LANA_ALLOW_PRIVATE_SOURCE_URLS", raising=False)
    monkeypatch.setattr(
        "app.sources.security._resolve_all",
        lambda host, port: ["93.184.216.34", "127.0.0.1"],
    )
    with pytest.raises(SourceRefused, match="private or loopback"):
        check_url("http://split-horizon.example.com/")


def test_an_unresolvable_host_is_refused_not_crashed(monkeypatch):
    monkeypatch.delenv("LANA_ALLOW_PRIVATE_SOURCE_URLS", raising=False)
    with pytest.raises(SourceRefused):
        check_url("http://this-host-does-not-exist.invalid/")


def test_a_rest_source_validates_its_url_at_construction(monkeypatch):
    monkeypatch.delenv("LANA_ALLOW_PRIVATE_SOURCE_URLS", raising=False)
    with pytest.raises(SourceRefused):
        build_source(SourceSpec(kind="url", target="http://169.254.169.254/"))


# ── File connector, against real files ──────────────────────────────────────

@pytest.fixture
def csv_file(tmp_path):
    path = tmp_path / "orders.csv"
    pd.DataFrame({"region": ["north", "south"], "revenue": [10.5, 20.25]}).to_csv(
        path, index=False
    )
    return path


def test_file_source_reads_a_csv(csv_file):
    source = build_source(SourceSpec(kind="file", target=str(csv_file)))
    assert source.test_connection().ok
    result = source.fetch()
    assert list(result.frame.columns) == ["region", "revenue"]
    assert len(result.frame) == 2
    assert result.label == "orders.csv"


def test_file_source_applies_a_row_cap(csv_file):
    source = build_source(SourceSpec(kind="file", target=str(csv_file)))
    result = source.fetch(limit=1)
    assert len(result.frame) == 1
    assert result.row_limit_applied is True


def test_file_source_reports_a_missing_file(tmp_path):
    source = build_source(SourceSpec(kind="file", target=str(tmp_path / "nope.csv")))
    test = source.test_connection()
    assert test.ok is False
    assert "No file" in test.detail


def test_file_source_refuses_legacy_xls(tmp_path):
    with pytest.raises(SourceConfigError, match="xlsx"):
        build_source(SourceSpec(kind="file", target=str(tmp_path / "old.xls")))


# ── SQL connector, against a real SQLite database ───────────────────────────

@pytest.fixture
def sqlite_db(tmp_path):
    path = tmp_path / "shop.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE orders (region TEXT, revenue REAL)")
    connection.executemany(
        "INSERT INTO orders VALUES (?, ?)",
        [("north", 100.0), ("south", 50.0), ("north", 75.0)],
    )
    # A table whose name is a reserved word, to prove identifier quoting.
    connection.execute('CREATE TABLE "order" (id INTEGER)')
    connection.execute('INSERT INTO "order" VALUES (1)')
    connection.commit()
    connection.close()
    return path


def test_sql_source_lists_tables(sqlite_db):
    source = build_source(SourceSpec(kind="sql", target=f"sqlite:///{sqlite_db}"))
    test = source.test_connection()
    assert test.ok
    assert "orders" in test.entities


def test_sql_source_reads_a_table(sqlite_db):
    source = build_source(
        SourceSpec(kind="sql", target=f"sqlite:///{sqlite_db}", entity="orders")
    )
    result = source.fetch()
    assert len(result.frame) == 3
    assert result.frame["revenue"].sum() == pytest.approx(225.0)


def test_sql_source_quotes_a_reserved_word_table(sqlite_db):
    """A table called "order" is legal and common. String-formatting it into
    SQL would be both a syntax error and an injection surface."""
    source = build_source(
        SourceSpec(kind="sql", target=f"sqlite:///{sqlite_db}", entity="order")
    )
    assert len(source.fetch().frame) == 1


def test_sql_source_pushes_the_row_cap_into_the_query(sqlite_db):
    source = build_source(
        SourceSpec(kind="sql", target=f"sqlite:///{sqlite_db}", entity="orders")
    )
    assert "LIMIT 2" in source._statement(2)
    assert len(source.fetch(limit=2).frame) == 2


def test_sql_source_wraps_a_custom_query_in_a_cap(sqlite_db):
    source = build_source(SourceSpec(
        kind="sql", target=f"sqlite:///{sqlite_db}",
        options={"query": "SELECT region, SUM(revenue) AS total FROM orders GROUP BY region"},
    ))
    statement = source._statement(100)
    assert "LIMIT 100" in statement
    result = source.fetch()
    assert set(result.frame["region"]) == {"north", "south"}


def test_sql_source_without_a_table_or_query_says_so(sqlite_db):
    source = build_source(SourceSpec(kind="sql", target=f"sqlite:///{sqlite_db}"))
    with pytest.raises(SourceConfigError, match="Choose a table"):
        source.fetch()


def test_sql_source_reports_a_bad_connection_rather_than_raising():
    source = build_source(
        SourceSpec(kind="sql", target="sqlite:////nonexistent/dir/x.db", entity="t")
    )
    assert source.test_connection().ok is False


# ── Mongo document flattening (no server required) ──────────────────────────

def test_nested_documents_become_dotted_columns():
    flat = flatten_document({
        "_id": "abc",
        "customer": {"name": "Ada", "address": {"city": "London"}},
        "total": 42.5,
    })
    assert flat["customer.name"] == "Ada"
    assert flat["customer.address.city"] == "London"
    assert flat["total"] == 42.5


def test_arrays_are_kept_as_json_not_exploded():
    """Exploding would change the row count, making every later count wrong."""
    flat = flatten_document({"items": [{"sku": "a"}, {"sku": "b"}], "n": 2})
    assert isinstance(flat["items"], str)
    assert json.loads(flat["items"]) == [{"sku": "a"}, {"sku": "b"}]


def test_flattening_stops_at_the_depth_limit():
    document = {"a": {"b": {"c": {"d": {"e": 1}}}}}
    flat = flatten_document(document, depth=2)
    assert any(isinstance(v, str) for v in flat.values())


def test_mongo_source_requires_a_mongodb_uri():
    with pytest.raises(SourceConfigError, match="mongodb"):
        build_source(SourceSpec(kind="mongodb", target="postgresql://host/db"))


def test_mongo_filter_must_be_valid_json():
    source = build_source(SourceSpec(
        kind="mongodb", target="mongodb://host:27017/db",
        entity="orders", options={"query": "{not json"},
    ))
    with pytest.raises(SourceConfigError, match="valid JSON"):
        source._filter()


# ── REST record-finding (no network required) ───────────────────────────────

@pytest.fixture
def rest_source(monkeypatch):
    monkeypatch.delenv("LANA_ALLOW_PRIVATE_SOURCE_URLS", raising=False)
    monkeypatch.setattr(
        "app.sources.security._resolve_all", lambda host, port: ["93.184.216.34"]
    )
    return build_source(SourceSpec(kind="url", target="https://api.example.com/v1"))


@pytest.mark.parametrize("payload,expected_rows", [
    ([{"a": 1}, {"a": 2}], 2),
    ({"data": [{"a": 1}, {"a": 2}, {"a": 3}]}, 3),
    ({"results": [{"a": 1}]}, 1),
    ({"items": [{"a": 1}, {"a": 2}]}, 2),
    ({"a": 1, "b": 2}, 1),
])
def test_records_are_found_in_the_usual_api_shapes(rest_source, payload, expected_rows):
    frame, _how = rest_source._frame_from_json(payload)
    assert len(frame) == expected_rows


def test_a_nested_records_path_can_be_given_explicitly(rest_source):
    rest_source.spec.options["records_path"] = "result.rows"
    frame, how = rest_source._frame_from_json(
        {"result": {"rows": [{"a": 1}, {"a": 2}]}}
    )
    assert len(frame) == 2
    assert "result.rows" in how


def test_an_unfindable_array_asks_for_records_path(rest_source):
    with pytest.raises(SourceConfigError, match="records_path"):
        rest_source._frame_from_json({"meta": {"page": 1}, "nested": {"deep": {}}})


def test_a_wrong_records_path_is_reported(rest_source):
    rest_source.spec.options["records_path"] = "nope"
    with pytest.raises(SourceConfigError, match="does not exist"):
        rest_source._frame_from_json({"data": [{"a": 1}]})


def test_nested_json_records_are_normalized_to_columns(rest_source):
    frame, _ = rest_source._frame_from_json(
        {"data": [{"id": 1, "customer": {"city": "London"}}]}
    )
    assert "customer.city" in frame.columns


# ── Frame normalisation, shared by every connector ──────────────────────────

def test_non_string_column_names_become_strings():
    frame = normalize_frame(pd.DataFrame({1: [1], None: [2]}))
    assert all(isinstance(c, str) for c in frame.columns)


def test_duplicate_column_names_are_suffixed_not_shadowed():
    frame = pd.DataFrame([[1, 2]], columns=["a", "a"])
    assert list(normalize_frame(frame).columns) == ["a", "a_1"]


def test_nested_values_become_json_text():
    frame = normalize_frame(pd.DataFrame({"payload": [{"x": 1}, {"y": 2}]}))
    assert frame["payload"].map(lambda v: isinstance(v, str)).all()
    assert json.loads(frame["payload"].iloc[0]) == {"x": 1}


def test_decimals_become_numeric():
    from decimal import Decimal

    frame = normalize_frame(pd.DataFrame({"amount": [Decimal("1.5"), Decimal("2.5")]}))
    assert pd.api.types.is_numeric_dtype(frame["amount"])
    assert frame["amount"].sum() == pytest.approx(4.0)


def test_exotic_scalars_become_strings():
    import uuid

    frame = normalize_frame(pd.DataFrame({"id": [uuid.uuid4(), uuid.uuid4()]}))
    assert frame["id"].map(lambda v: isinstance(v, str)).all()


def test_normalisation_leaves_ordinary_frames_alone():
    original = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    pd.testing.assert_frame_equal(normalize_frame(original), original)
