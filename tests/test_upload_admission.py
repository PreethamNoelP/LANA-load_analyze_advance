"""Every upload format is costed before it is parsed — not just CSV.

CSV always had a sampled projection gating admission. Excel and JSON did not,
so a small file that inflates enormously (an .xlsx is a zip: 200 KB of archive
can declare gigabytes of sheet XML) was admitted unexamined purely because the
compressed bytes fitted under the upload limit, and the cost was discovered by
parsing it.
"""

import io
import zipfile

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import backend.main as backend_main
from app.data import ingest
from backend.main import app


@pytest.fixture()
def client():
    return TestClient(app, raise_server_exceptions=False)


def _xlsx(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    return buf.getvalue()


# ── Reading a zip's declared size without inflating it ────────────────────────

def test_declared_size_reads_the_archive_directory():
    payload = _xlsx(pd.DataFrame({"a": np.arange(2_000), "b": ["text"] * 2_000}))
    declared = ingest.declared_uncompressed_bytes(io.BytesIO(payload))

    assert declared is not None
    # The point of the check: the declared volume exceeds the compressed file,
    # which is the gap the old gate could not see.
    assert declared > len(payload)


def test_declared_size_declines_on_anything_that_is_not_a_zip():
    assert ingest.declared_uncompressed_bytes(io.BytesIO(b"col\n1\n")) is None
    assert ingest.declared_uncompressed_bytes(io.BytesIO(b"")) is None


def test_reading_the_directory_leaves_the_spool_usable():
    # The projection runs before the parse and hands the same handle on; a
    # consumed or closed spool here would break every Excel upload.
    payload = _xlsx(pd.DataFrame({"a": [1, 2, 3]}))
    spool = io.BytesIO(payload)
    ingest.declared_uncompressed_bytes(spool)
    assert pd.read_excel(spool).shape == (3, 1)


def test_a_zip_bomb_projects_far_above_its_file_size():
    # 50 MB of one repeated byte compresses to a few KB. Nothing parses this;
    # the point is that the projection sees the declared size, not the stored
    # one, which is exactly what stops it at the door.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/worksheets/sheet1.xml", b"\0" * (50 * 1024 ** 2))
    payload = buf.getvalue()

    projection = ingest.project_frame_bytes(io.BytesIO(payload), ".xlsx", len(payload))
    assert projection is not None
    assert projection.frame_bytes > 100 * len(payload)


# ── The projection each format gets ───────────────────────────────────────────

def test_csv_still_gets_its_sampled_projection_with_a_row_count():
    df = pd.DataFrame({"a": np.arange(20_000), "b": ["text value"] * 20_000})
    payload = df.to_csv(index=False).encode()

    projection = ingest.project_frame_bytes(io.BytesIO(payload), ".csv", len(payload))
    assert projection is not None
    assert projection.rows is not None
    assert "rows" in projection.basis


def test_json_is_projected_from_its_source_size():
    payload = pd.DataFrame({"a": np.arange(500)}).to_json(orient="records").encode()
    projection = ingest.project_frame_bytes(io.BytesIO(payload), ".json", len(payload))

    assert projection is not None
    assert projection.rows is None
    assert projection.frame_bytes > 0


def test_excel_projection_over_estimates_rather_than_under(tmp_path):
    # The multiplier exists to fail safe. Verify it actually does on real data:
    # the projection must not come in under what the parse really costs.
    df = pd.DataFrame({
        "id": np.arange(5_000),
        "region": np.resize(["north", "south", "east", "west"], 5_000),
        "amount": np.linspace(0, 1000, 5_000),
    })
    payload = _xlsx(df)
    projection = ingest.project_frame_bytes(io.BytesIO(payload), ".xlsx", len(payload))
    actual, _ = ingest.read_frame(io.BytesIO(payload), ".xlsx", len(payload))

    assert projection is not None
    assert projection.frame_bytes >= ingest.frame_bytes(actual)


# ── The gate, end to end ──────────────────────────────────────────────────────

def test_an_excel_upload_over_budget_is_refused_before_parsing(client, monkeypatch):
    # Regression: this path had no gate at all, so the only thing standing
    # between a crafted workbook and the parser was the raw upload limit.
    monkeypatch.setattr(backend_main, "MAX_SESSION_MB", 0)
    payload = _xlsx(pd.DataFrame({"a": [1, 2, 3]}))

    r = client.post("/upload", files={
        "file": ("t.xlsx", payload,
                 "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    })
    assert r.status_code == 413
    assert "uncompressed" in r.json()["detail"]


def test_a_json_upload_over_budget_is_refused_before_parsing(client, monkeypatch):
    monkeypatch.setattr(backend_main, "MAX_SESSION_MB", 0)
    payload = pd.DataFrame({"a": [1, 2, 3]}).to_json(orient="records").encode()

    r = client.post("/upload", files={"file": ("t.json", payload, "application/json")})
    assert r.status_code == 413


def test_ordinary_uploads_of_every_format_still_pass_the_gate(client):
    df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})

    csv = client.post("/upload", files={"file": ("t.csv", df.to_csv(index=False).encode(), "text/csv")})
    xlsx = client.post("/upload", files={"file": ("t.xlsx", _xlsx(df), "application/vnd.ms-excel")})
    js = client.post("/upload", files={
        "file": ("t.json", df.to_json(orient="records").encode(), "application/json"),
    })

    assert csv.status_code == 200, csv.text
    assert xlsx.status_code == 200, xlsx.text
    assert js.status_code == 200, js.text
    assert {csv.json()["rows"], xlsx.json()["rows"], js.json()["rows"]} == {3}
