"""CSV export must not hand back something a spreadsheet will execute.

Values in an export came from a file LANA did not write. A cell beginning
`=`, `+`, `@` or `-` is a formula to Excel, LibreOffice and Sheets, which is
a live code-execution path from "someone sent me a CSV" to "my machine ran
their command" — and LANA is the step in that chain that can stop it.

The other half of these tests is the part that is easy to get wrong: not
mangling legitimate data while doing it.
"""

import io

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend.main import _escape_formula_cells, app


@pytest.fixture()
def client():
    return TestClient(app, raise_server_exceptions=False)


def _exported(client, csv: bytes) -> pd.DataFrame:
    """Round-trip a CSV through upload and export, returning what came back."""
    r = client.post("/upload", files={"file": ("t.csv", csv, "text/csv")})
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    export = client.get(f"/export/csv/{sid}")
    assert export.status_code == 200, export.text
    return pd.read_csv(io.BytesIO(export.content))


# ── The attack ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("payload", [
    "=1+1",
    "=HYPERLINK(\"http://evil.test\",\"click\")",
    "+1+1",
    "@SUM(A1)",
    "-1+1+cmd|' /C calc'!A0",
])
def test_formula_payloads_are_neutralised_on_export(payload):
    block = pd.DataFrame({"note": [payload, "harmless"]})
    out = _escape_formula_cells(block)
    assert out["note"][0] == f"'{payload}"
    assert out["note"][1] == "harmless"


def test_payload_survives_the_full_upload_export_round_trip(client):
    csv = b'name,note\nAlice,"=1+1"\nBob,ok\n'
    got = _exported(client, csv)
    assert got["note"][0] == "'=1+1"
    assert got["note"][1] == "ok"


def test_a_formula_in_a_column_name_is_not_our_problem_but_values_are(client):
    # Header text is written by whoever made the file too; this records the
    # current boundary rather than claiming coverage the code does not have.
    csv = b"region,amount\n=cmd,5\n"
    got = _exported(client, csv)
    assert got["region"][0] == "'=cmd"


# ── Not mangling real data ────────────────────────────────────────────────────

def test_negative_numbers_in_a_numeric_column_are_untouched():
    block = pd.DataFrame({"amount": [-5.2, 3.1, -0.0004]})
    out = _escape_formula_cells(block)
    pd.testing.assert_frame_equal(out, block)


def test_a_negative_number_stored_as_text_is_untouched():
    # The cheap fix — escape everything starting with '-' — would turn every
    # one of these into a string with an apostrophe in front of it.
    block = pd.DataFrame({"reading": ["-5.2", "-1e4", "-0", "12"]})
    out = _escape_formula_cells(block)
    pd.testing.assert_frame_equal(out, block)


def test_ordinary_text_is_untouched():
    block = pd.DataFrame({"note": ["hello", "a=b", "2+2", "user@example.com", None]})
    out = _escape_formula_cells(block)
    pd.testing.assert_frame_equal(out, block)


def test_a_frame_with_nothing_to_escape_is_returned_unchanged(client):
    # Identity, not equality: an export of clean data must not pay for a copy.
    block = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    assert _escape_formula_cells(block) is block


def test_categorical_columns_are_escaped_too():
    block = pd.DataFrame({"tag": pd.Categorical(["=1+1", "fine", "=1+1"])})
    out = _escape_formula_cells(block)
    assert list(out["tag"]) == ["'=1+1", "fine", "'=1+1"]


def test_escaping_does_not_disturb_the_streamed_export_of_clean_data(client):
    csv = b"name,score\nAlice,1\nBob,2\n"
    got = _exported(client, csv)
    pd.testing.assert_frame_equal(got, pd.DataFrame({"name": ["Alice", "Bob"], "score": [1, 2]}))
