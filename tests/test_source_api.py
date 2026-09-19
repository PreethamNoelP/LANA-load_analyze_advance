"""The connector HTTP surface, and the claim that every source is equal.

The substantive test here is the last one: a dataset loaded from a connector
must reach *every* downstream capability — profiling, cleaning, charts,
statistics, regression, lineage, export — exactly as an uploaded file does.
That is the whole promise of the DataSource abstraction, and it is the kind of
promise that quietly stops being true unless something checks it.
"""

from __future__ import annotations

import sqlite3

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import backend.main as backend_main


@pytest.fixture
def client():
    return TestClient(backend_main.app)


@pytest.fixture
def shop_db(tmp_path):
    """A small SQLite database, used as a stand-in for any SQL source."""
    path = tmp_path / "shop.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE orders (region TEXT, revenue REAL, units INTEGER)"
    )
    connection.executemany(
        "INSERT INTO orders VALUES (?, ?, ?)",
        [
            ("north", 120.0, 3), ("south", 60.5, 1), ("north", 200.0, 5),
            ("east", 90.25, 2), ("south", 75.0, 2), ("north", 310.0, 8),
            ("east", 45.5, 1), ("west", 150.0, 4),
        ],
    )
    connection.commit()
    connection.close()
    return path


def _sql_spec(db, **overrides):
    spec = {"kind": "sql", "target": f"sqlite:///{db}", "entity": "orders"}
    spec.update(overrides)
    return spec


# ── Listing ─────────────────────────────────────────────────────────────────

def test_sources_are_listed_with_their_capabilities(client):
    body = client.get("/sources").json()
    kinds = {entry["kind"] for entry in body["sources"]}
    assert {"file", "sql", "mongodb", "url"} <= kinds
    for entry in body["sources"]:
        assert "capabilities" in entry
        assert "available" in entry


def test_an_unavailable_driver_is_listed_with_a_reason_not_hidden(client, monkeypatch):
    """Vanishing from the list leaves the user guessing why Mongo is missing."""
    import app.sources.mongo as mongo_module

    monkeypatch.setattr(mongo_module, "PYMONGO_AVAILABLE", False)
    body = client.get("/sources").json()
    mongo = next(e for e in body["sources"] if e["kind"] == "mongodb")
    assert mongo["available"] is False
    assert "pymongo" in mongo["unavailable_reason"]


# ── Test / preview / load ───────────────────────────────────────────────────

def test_testing_a_connection_lists_entities(client, shop_db):
    body = client.post("/sources/test", json=_sql_spec(shop_db)).json()
    assert body["ok"] is True
    assert "orders" in body["entities"]


def test_a_bad_connection_reports_failure_without_a_500(client, tmp_path):
    response = client.post(
        "/sources/test",
        json={"kind": "sql", "target": f"sqlite:////{tmp_path}/no/such.db", "entity": "t"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is False


def test_previewing_returns_schema_and_sample(client, shop_db):
    body = client.post("/sources/preview", json=_sql_spec(shop_db)).json()
    assert body["columns"] == ["region", "revenue", "units"]
    assert body["dtypes"]["revenue"].startswith("float")
    assert len(body["preview"]) > 0


def test_loading_creates_a_normal_session(client, shop_db):
    body = client.post("/sources/load", json=_sql_spec(shop_db)).json()
    assert body["rows"] == 8
    assert body["numeric_columns"] == ["revenue", "units"]
    assert body["source"]["kind"] == "sql"
    assert client.get(f"/session/{body['session_id']}").status_code == 200


def test_an_unknown_source_kind_is_a_400(client):
    response = client.post("/sources/test", json={"kind": "telepathy", "target": "x"})
    assert response.status_code == 400
    assert "Unknown source type" in response.json()["detail"]


def test_a_missing_driver_is_a_501_not_a_500(client, monkeypatch):
    import app.sources.mongo as mongo_module

    monkeypatch.setattr(mongo_module, "PYMONGO_AVAILABLE", False)
    response = client.post(
        "/sources/test",
        json={"kind": "mongodb", "target": "mongodb://h:27017/db", "entity": "c"},
    )
    assert response.status_code == 501


def test_an_ssrf_attempt_is_a_403(client, monkeypatch):
    monkeypatch.delenv("LANA_ALLOW_PRIVATE_SOURCE_URLS", raising=False)
    response = client.post(
        "/sources/test",
        json={"kind": "url", "target": "http://169.254.169.254/latest/meta-data/"},
    )
    assert response.status_code == 403
    assert "private" in response.json()["detail"].lower()


def test_an_empty_result_is_refused_with_an_explanation(client, shop_db):
    response = client.post("/sources/load", json=_sql_spec(
        shop_db, options={"query": "SELECT * FROM orders WHERE region = 'mars'"}
    ))
    assert response.status_code == 400
    assert "no rows" in response.json()["detail"]


def test_a_secret_is_never_echoed_back(client, shop_db):
    """A response that reflects the credential undoes the point of accepting it."""
    response = client.post(
        "/sources/preview", json=_sql_spec(shop_db, secret="hunter2-supersecret")
    )
    assert "hunter2-supersecret" not in response.text


# ── The promise: every source reaches every capability ──────────────────────

def test_a_connector_dataset_gets_the_full_pipeline(client, shop_db):
    """The DataSource abstraction's whole reason to exist.

    If this passes, "you get the same profiling, cleaning, charts, statistics
    and export regardless of source" is a fact about the code rather than a
    claim in a README. Every call below is the same one the frontend makes
    against an uploaded CSV.
    """
    sid = client.post("/sources/load", json=_sql_spec(shop_db)).json()["session_id"]

    assert client.get(f"/profile/{sid}").json()["rows"] == 8
    assert client.get(f"/lineage/{sid}").json()["summary"]["steps"] == 0
    assert "recommendations" in client.get(f"/recommendations/{sid}").json()
    assert client.get(f"/clean/preview/{sid}").status_code == 200
    assert client.get(f"/clean/status/{sid}").json()["version"] == "original"

    stats = client.get(f"/stats/{sid}", params={"column": "revenue"}).json()
    assert stats["count"] == 8

    correlation = client.get(f"/correlation/{sid}")
    assert correlation.status_code == 200

    regression = client.post(
        "/regression", json={"session_id": sid, "x_col": "units", "y_col": "revenue"}
    )
    assert regression.status_code == 200
    assert "r2_score" in regression.json()

    chart = client.post(
        "/chart",
        json={"session_id": sid, "chart_type": "Histogram", "column": "revenue"},
    )
    assert chart.status_code == 200
    assert chart.content[:8] == b"\x89PNG\r\n\x1a\n"

    csv = client.get(f"/export/csv/{sid}")
    assert csv.status_code == 200
    assert "revenue" in csv.text

    assert client.get(f"/export/pdf/{sid}").content[:5] == b"%PDF-"


def test_cleaning_works_on_a_connector_dataset(client, shop_db):
    sid = client.post("/sources/load", json=_sql_spec(shop_db)).json()["session_id"]
    applied = client.post(
        f"/clean/apply/{sid}",
        json={"operations": [{"type": "remove_duplicates"}]},
    )
    assert applied.status_code == 200
    assert applied.json()["rows_before"] == 8
    assert client.get(f"/clean/status/{sid}").json()["has_cleaned"] is True


def test_a_connector_dataset_is_queryable_by_executed_sql(client, shop_db, monkeypatch):
    """Executed-SQL grounding must not care that the rows came from SQL already.

    The quoted figure is a revenue total rather than a row count on purpose:
    the validator skips integers under 20 as prose ("the top 5", "3 findings"),
    so a count of 8 would be correctly ignored and prove nothing about
    executed provenance.
    """
    total = 1051.25  # sum of the revenue column in the fixture

    class _Provider:
        def __init__(self):
            self.calls = 0

        def generate(self, prompt, system_prompt=None):
            self.calls += 1
            if self.calls == 1:
                return 'SELECT SUM("revenue") AS total_revenue FROM dataset'
            return f"Total revenue is {total:,.2f}."

    monkeypatch.setattr(backend_main, "get_provider", lambda: _Provider())
    sid = client.post("/sources/load", json=_sql_spec(shop_db)).json()["session_id"]
    body = client.post(
        "/query", json={"session_id": sid, "question": "total revenue?"}
    ).json()

    assert body["grounding"] == "sql"
    assert body["sql"]["sql"].lower().startswith("select sum")
    assert body["sql"]["rows"] == [[total]]
    # The figure is credited to the executed query, not to a ledger fact that
    # happened to land within tolerance.
    assert body["validation"]["executed"] >= 1
    assert body["validation"]["trustworthy"] is True


def test_dtypes_are_optimised_on_load_like_an_upload(client, shop_db):
    """Low-cardinality text becomes categorical, exactly as /upload does it."""
    sid = client.post("/sources/load", json=_sql_spec(shop_db)).json()["session_id"]
    session = backend_main._store.get(sid)
    assert isinstance(session.raw["region"].dtype, pd.CategoricalDtype)
