"""End-to-end smoke tests for the LANA API.

Covers the highest-risk paths: upload parsing/serialization, stats math,
the cleaning state machine, exports (including non-latin-1 data), and the
session store limits. No LLM required — /query is exercised only indirectly
via the context builder used by exports.
"""

import json

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


def test_clean_preview_reports_column_types(client):
    sid = upload(client)["session_id"]
    issues = client.get(f"/clean/preview/{sid}").json()
    assert issues["column_types"]["score"] == "int64"
    assert issues["column_types"]["price"] == "float64"
    assert "name" in issues["column_types"]


def test_cast_type_changes_column_dtype(client):
    sid = upload(client)["session_id"]
    assert "score" in client.get(f"/session/{sid}").json()["numeric_columns"]

    r = client.post(f"/clean/apply/{sid}", json={"operations": [
        {"type": "cast_type", "column": "score", "dtype": "text"},
    ]})
    assert r.status_code == 200

    assert "score" not in client.get(f"/session/{sid}").json()["numeric_columns"]


def test_cast_type_numeric_coerces_invalid_values_to_null(client):
    # "name" is all non-numeric text — coercing to numeric should null every
    # value out, not crash, and the column should now count as numeric.
    sid = upload(client)["session_id"]
    r = client.post(f"/clean/apply/{sid}", json={"operations": [
        {"type": "cast_type", "column": "name", "dtype": "numeric"},
    ]})
    assert r.status_code == 200, r.text

    after = client.get(f"/session/{sid}").json()
    assert "name" in after["numeric_columns"]
    assert all(row["name"] is None for row in after["preview"])


def test_cast_type_datetime_and_category_serialize_cleanly(client):
    csv = b"event,day\nA,2024-01-15\nB,2024-02-01\nC,not-a-date\n"
    sid = upload(client, csv, "dates.csv")["session_id"]

    r = client.post(f"/clean/apply/{sid}", json={"operations": [
        {"type": "cast_type", "column": "day", "dtype": "datetime"},
        {"type": "cast_type", "column": "event", "dtype": "category"},
    ]})
    assert r.status_code == 200, r.text

    days = [row["day"] for row in client.get(f"/session/{sid}").json()["preview"]]
    assert days[0].startswith("2024-01-15")
    assert days[2] is None  # "not-a-date" coerced to null, not left as garbage


def test_cast_type_rejects_unknown_dtype(client):
    sid = upload(client)["session_id"]
    r = client.post(f"/clean/apply/{sid}", json={"operations": [
        {"type": "cast_type", "column": "name", "dtype": "bogus"},
    ]})
    assert r.status_code == 422


def test_version_endpoint_validates_input(client):
    sid = upload(client)["session_id"]
    assert client.post(f"/clean/version/{sid}", json={"version": "bogus"}).status_code == 400
    # No cleaned version exists yet
    assert client.post(f"/clean/version/{sid}", json={"version": "cleaned"}).status_code == 400


# ── Session store ─────────────────────────────────────────────────────────────

def test_session_store_evicts_least_recently_used(client, monkeypatch):
    monkeypatch.setattr(backend_main._store, "max_sessions", 2)
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

def _sse_events(response_text):
    return [json.loads(line[len("data: "):]) for line in response_text.strip().split("\n\n") if line]


def test_query_stream_endpoint(client, monkeypatch):
    sid = upload(client)["session_id"]

    class _FakeProvider:
        def answer_question_stream(self, question, data_context):
            yield "Hello "
            yield "world"

    monkeypatch.setattr(backend_main, "get_provider", lambda: _FakeProvider())
    r = client.post("/query/stream", json={"session_id": sid, "question": "hi"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert _sse_events(r.text) == [{"delta": "Hello "}, {"delta": "world"}, {"done": True}]


def test_query_stream_reports_llm_errors_as_sse_event(client, monkeypatch):
    sid = upload(client)["session_id"]

    class _FailingProvider:
        def answer_question_stream(self, question, data_context):
            yield "partial "
            raise RuntimeError("model unreachable")

    monkeypatch.setattr(backend_main, "get_provider", lambda: _FailingProvider())
    r = client.post("/query/stream", json={"session_id": sid, "question": "hi"})
    assert r.status_code == 200  # headers already sent; error travels inside the stream
    events = _sse_events(r.text)
    assert events[0] == {"delta": "partial "}
    assert "model unreachable" in events[1]["error"]


def test_query_stream_rejects_unknown_session(client):
    assert client.post("/query/stream", json={"session_id": "nope", "question": "hi"}).status_code == 404


def test_correlation_endpoint(client):
    sid = upload(client)["session_id"]
    r = client.get(f"/correlation/{sid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["method"] == "pearson"
    pair = next(p for p in body["pairs"] if {p["column_a"], p["column_b"]} == {"score", "price"})
    assert -1.0 <= pair["correlation"] <= 1.0
    assert pair["n"] == 4  # one row has a null price, dropped for this pair

    r = client.get(f"/correlation/{sid}", params={"method": "spearman"})
    assert r.status_code == 200 and r.json()["method"] == "spearman"

    assert client.get(f"/correlation/{sid}", params={"method": "bogus"}).status_code == 422
    assert client.get("/correlation/not-a-session").status_code == 404


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


def test_correlation_response_explains_multiple_testing(client):
    sid = upload(client)["session_id"]
    body = client.get(f"/correlation/{sid}").json()
    assert "significant_count" in body and "tests_run" in body
    assert body["fdr_alpha"] == 0.05
    assert all("q_value" in p for p in body["pairs"])


# ── Profiling and lineage ─────────────────────────────────────────────────────

def test_upload_returns_a_quality_assessment(client):
    data = upload(client)
    assert 0 <= data["quality"]["score"] <= 100
    assert data["quality"]["grade"] in ("excellent", "good", "fair", "poor")
    assert data["profiles"]["price"]["null_count"] == 1


def test_profile_endpoint(client):
    sid = upload(client)["session_id"]
    body = client.get(f"/profile/{sid}").json()
    assert body["version"] == "original"
    assert body["rows"] == 5
    assert set(body["profiles"]) == {"name", "score", "price"}
    assert client.get("/profile/not-a-session").status_code == 404


def test_lineage_is_empty_before_any_cleaning(client):
    sid = upload(client)["session_id"]
    body = client.get(f"/lineage/{sid}").json()
    assert body["summary"]["steps"] == 0
    assert "raw uploaded data" in body["narrative"]


def test_clean_apply_returns_a_full_transformation_log(client):
    sid = upload(client)["session_id"]
    r = client.post(f"/clean/apply/{sid}", json={"operations": [
        {"type": "remove_duplicates"},
        {"type": "fill_nulls", "column": "price", "method": "median"},
    ]})
    assert r.status_code == 200, r.text
    lineage = r.json()["lineage"]

    assert lineage["summary"]["steps"] == 2
    assert lineage["summary"]["rows_removed"] == 1
    assert lineage["summary"]["fully_reversible"] is False  # a row was dropped
    assert [s["operation"] for s in lineage["steps"]] == ["remove_duplicates", "fill_nulls"]
    assert all(s["rationale"] for s in lineage["steps"])
    assert "Data loss" in lineage["narrative"]

    # The same log is retrievable afterwards.
    assert client.get(f"/lineage/{sid}").json()["summary"]["steps"] == 2


def test_reapplying_cleaning_starts_from_the_raw_data(client):
    # Transformations must never silently compound: the second apply is
    # evaluated against the original upload, not the previous result.
    sid = upload(client)["session_id"]
    first = client.post(f"/clean/apply/{sid}", json={
        "operations": [{"type": "remove_duplicates"}]}).json()
    assert first["rows_after"] == 4

    second = client.post(f"/clean/apply/{sid}", json={
        "operations": [{"type": "fill_nulls", "column": "price", "method": "median"}]}).json()
    assert second["rows_before"] == 5
    assert second["rows_after"] == 5


def test_flagging_outliers_via_the_api_keeps_every_row(client):
    csv = b"v\n" + b"\n".join(str(i).encode() for i in list(range(40)) + [9999])
    sid = upload(client, csv, "spread.csv")["session_id"]

    r = client.post(f"/clean/apply/{sid}", json={"operations": [
        {"type": "flag_outliers", "column": "v"},
    ]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rows_removed"] == 0
    assert body["columns_after"] > body["columns_before"]

    after = client.get(f"/session/{sid}").json()
    assert after["rows"] == 41
    assert "v__outlier" in after["columns"]


def test_clean_status_carries_the_lineage(client):
    sid = upload(client)["session_id"]
    client.post(f"/clean/apply/{sid}", json={"operations": [{"type": "remove_duplicates"}]})
    status = client.get(f"/clean/status/{sid}").json()
    assert status["version"] == "cleaned"
    assert status["lineage"]["summary"]["rows_removed"] == 1
    assert "memory_mb" in status


# ── Grounded answers ──────────────────────────────────────────────────────────

def test_query_returns_a_validation_verdict(client, monkeypatch):
    sid = upload(client)["session_id"]

    class _Provider:
        def answer_question(self, question, data_context):
            # A number nowhere near any column's range — the hallucination
            # shape this layer exists to catch.
            return "Average price was 8231947.5 across the dataset."

    monkeypatch.setattr(backend_main, "get_provider", lambda: _Provider())
    body = client.post("/query", json={"session_id": sid, "question": "avg price?"}).json()
    assert body["validation"]["trustworthy"] is False
    assert body["validation"]["unsupported"] >= 1
    assert body["context_coverage"]["columns_detailed"] == 3


def test_query_context_includes_grounded_facts(client, monkeypatch):
    sid = upload(client)["session_id"]
    captured = {}

    class _Provider:
        def answer_question(self, question, data_context):
            captured["context"] = data_context
            return "ok"

    monkeypatch.setattr(backend_main, "get_provider", lambda: _Provider())
    client.post("/query", json={"session_id": sid, "question": "hi"})
    assert "DATASET FACTS" in captured["context"]
    assert "LIMITS OF THIS CONTEXT" in captured["context"]


def test_stream_emits_a_validation_event_when_the_answer_is_doubtful(client, monkeypatch):
    sid = upload(client)["session_id"]

    class _Provider:
        def answer_question_stream(self, question, data_context):
            yield "Total revenue reached "
            yield "77123456.9 units."

    monkeypatch.setattr(backend_main, "get_provider", lambda: _Provider())
    r = client.post("/query/stream", json={"session_id": sid, "question": "revenue?"})
    events = _sse_events(r.text)
    assert events[-1] == {"done": True}
    assert events[-2]["validation"]["trustworthy"] is False