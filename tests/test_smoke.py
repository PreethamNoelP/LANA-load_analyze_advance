"""End-to-end smoke tests for the LANA API.

Covers the highest-risk paths: upload parsing/serialization, stats math,
the cleaning state machine, exports (including non-latin-1 data), and the
session store limits. No LLM required — /query is exercised only indirectly
via the context builder used by exports.
"""

import pytest
from fastapi.testclient import TestClient

import backend.main as backend_main
from backend.main import app

# Contains one exact duplicate row, one null price, and case/whitespace
# variants of the same name — exercises every issue detector at once.
CSV = (
    b"name,score,price\n"
    b" Alice ,1,1.5\n"
    b"alice,2,\n"
    b"ALICE,3,2.5\n"
    b" Alice ,1,1.5\n"
    b"Bob,4,9.0\n"
)


@pytest.fixture()
def client():
    return TestClient(app, raise_server_exceptions=False)


def upload(client, content=CSV, name="t.csv"):
    r = client.post("/upload", files={"file": (name, content, "text/csv")})
    assert r.status_code == 200, r.text
    return r.json()


# ── Upload ────────────────────────────────────────────────────────────────────

def test_upload_returns_metadata_and_serializable_preview(client):
    data = upload(client)
    assert data["rows"] == 5
    assert data["columns"] == ["name", "score", "price"]
    assert "score" in data["numeric_columns"]
    assert data["preview"][1]["price"] is None  # NaN must serialize as null


def test_upload_rejects_unknown_extension(client):
    r = client.post("/upload", files={"file": ("t.parquet", b"xx", "application/octet-stream")})
    assert r.status_code == 400


def test_upload_rejects_oversized_file(client, monkeypatch):
    monkeypatch.setattr(backend_main, "MAX_UPLOAD_MB", 0)
    r = client.post("/upload", files={"file": ("t.csv", b"a\n1\n", "text/csv")})
    assert r.status_code == 413


# ── Stats ─────────────────────────────────────────────────────────────────────

def test_stats_on_integer_column(client):
    # Regression: numpy.int64 values used to crash JSON serialization (500).
    sid = upload(client)["session_id"]
    r = client.get(f"/stats/{sid}", params={"column": "score"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["min"] == 1 and body["max"] == 4 and body["count"] == 5


def test_stats_error_paths(client):
    sid = upload(client)["session_id"]
    assert client.get("/stats/not-a-session", params={"column": "score"}).status_code == 404
    assert client.get(f"/stats/{sid}", params={"column": "nope"}).status_code == 400
    assert client.get(f"/stats/{sid}", params={"column": "name"}).status_code == 400


# ── Cleaning state machine ────────────────────────────────────────────────────

def test_clean_detect_apply_and_version_switching(client):
    sid = upload(client)["session_id"]

    issues = client.get(f"/clean/preview/{sid}").json()
    assert issues["duplicates"]["count"] == 1
    assert "price" in issues["nulls"]
    assert "name" in issues["text_inconsistencies"]

    r = client.post(f"/clean/apply/{sid}", json={"operations": [
        {"type": "remove_duplicates"},
        {"type": "fill_nulls", "column": "price", "method": "mean"},
    ]})
    assert r.status_code == 200
    assert r.json()["rows_after"] == 4

    status = client.get(f"/clean/status/{sid}").json()
    assert status["version"] == "cleaned"
    assert status["has_cleaned"] is True
    assert status["original_rows"] == 5
    assert status["cleaned_rows"] == 4

    # /session must reflect the active version
    assert client.get(f"/session/{sid}").json()["rows"] == 4
    assert client.post(f"/clean/version/{sid}", json={"version": "original"}).status_code == 200
    assert client.get(f"/session/{sid}").json()["rows"] == 5


def test_clean_rejects_unknown_operation(client):
    # Regression: typo'd operations used to silently no-op and report success.
    sid = upload(client)["session_id"]
    r = client.post(f"/clean/apply/{sid}", json={"operations": [{"type": "remove_dupes"}]})
    assert r.status_code == 422
    r = client.post(f"/clean/apply/{sid}", json={"operations": [
        {"type": "fill_nulls", "column": "price", "method": "avg"},
    ]})
    assert r.status_code == 422


def test_version_endpoint_validates_input(client):
    sid = upload(client)["session_id"]
    assert client.post(f"/clean/version/{sid}", json={"version": "bogus"}).status_code == 400
    # No cleaned version exists yet
    assert client.post(f"/clean/version/{sid}", json={"version": "cleaned"}).status_code == 400


# ── Session store ─────────────────────────────────────────────────────────────

def test_session_store_evicts_least_recently_used(client, monkeypatch):
    monkeypatch.setattr(backend_main, "MAX_SESSIONS", 2)
    s1 = upload(client)["session_id"]
    s2 = upload(client)["session_id"]
    s3 = upload(client)["session_id"]  # evicts s1
    assert client.get(f"/session/{s1}").status_code == 404
    assert client.get(f"/session/{s2}").status_code == 200
    assert client.get(f"/session/{s3}").status_code == 200


# ── Exports ───────────────────────────────────────────────────────────────────

def test_exports_handle_non_latin1_data(client):
    # Regression: fpdf 1.x crashed with UnicodeEncodeError on such datasets.
    csv = "población,θ_angle,city\n1,0.1,München\n2,0.2,東京\n".encode("utf-8")
    sid = upload(client, csv, "unicode.csv")["session_id"]

    pdf = client.get(f"/export/pdf/{sid}")
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-"

    docx = client.get(f"/export/docx/{sid}")
    assert docx.status_code == 200 and docx.content[:2] == b"PK"

    assert client.get(f"/export/csv/{sid}").status_code == 200


# ── Analysis ──────────────────────────────────────────────────────────────────

def test_regression_endpoint(client):
    sid = upload(client)["session_id"]
    r = client.post("/regression", json={"session_id": sid, "x_col": "score", "y_col": "price"})
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"r2_score", "coefficient", "intercept", "rmse", "interpretation"}


def test_chart_endpoint_returns_png(client):
    sid = upload(client)["session_id"]
    r = client.post("/chart", json={"session_id": sid, "column": "score", "chart_type": "Histogram"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"