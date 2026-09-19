"""The audit trail: what it records, what it refuses to record, who can read it.

LANA had structured logs and metrics but no durable answer to "who exported
that dataset, and when" — logs go to stdout and live as long as the container.
This is the record that outlives it.

The tests worth having here are about the boundaries rather than the plumbing:
a trail that leaks credentials is worse than no trail, one that lets a caller
read someone else's activity has become its own disclosure, and one that fails
a user's request when the disk is full has made reliability worse to improve
auditability.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import backend.main as backend_main
from app import audit as audit_mod

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

CSV = b"region,revenue\nnorth,100\nsouth,200\nnorth,300\n"


@pytest.fixture
def log(tmp_path):
    return audit_mod.AuditLog(tmp_path / "audit.jsonl")


def _entry(**kwargs):
    base = {"ts": 1_780_000_000.0, "action": audit_mod.DATA_LOADED,
            "principal": "ip:127.0.0.1"}
    base.update(kwargs)
    return audit_mod.AuditEntry(**base)


# ── The log itself ──────────────────────────────────────────────────────────

def test_an_entry_round_trips(log):
    log.record(_entry(session_id="abc", detail={"rows": 3}))
    entries = log.read()
    assert len(entries) == 1
    assert entries[0]["action"] == audit_mod.DATA_LOADED
    assert entries[0]["session_id"] == "abc"
    assert entries[0]["detail"]["rows"] == 3
    # A human-readable timestamp beside the epoch one, so the file is
    # readable without a tool.
    assert entries[0]["time"].endswith("Z")


def test_entries_come_back_newest_first(log):
    for i in range(3):
        log.record(_entry(ts=1_780_000_000.0 + i, session_id=str(i)))
    assert [e["session_id"] for e in log.read()] == ["2", "1", "0"]


def test_a_torn_line_does_not_break_the_read(log):
    log.record(_entry(session_id="good"))
    with log.path.open("a", encoding="utf-8") as handle:
        handle.write('{"action": "data.loa')  # a write cut short
    log.record(_entry(session_id="later"))

    sessions = [e["session_id"] for e in log.read()]
    assert sessions == ["later", "good"]


def test_a_write_failure_never_raises(tmp_path, monkeypatch):
    log = audit_mod.AuditLog(tmp_path / "audit.jsonl")

    def explode(*args, **kwargs):
        raise OSError("no space left on device")

    monkeypatch.setattr(type(log.path), "open", explode, raising=False)
    # The action being recorded already happened. Turning a successful export
    # into a 500 because the audit disk is full helps nobody.
    log.record(_entry())


def test_the_file_rotates_rather_than_growing_without_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_mod, "MAX_BYTES", 400)
    log = audit_mod.AuditLog(tmp_path / "audit.jsonl")
    for i in range(40):
        log.record(_entry(ts=1_780_000_000.0 + i, session_id=f"s{i}"))

    assert log.path.with_suffix(".1.jsonl").exists()
    assert log.path.stat().st_size < 4_000


# ── What must never be written ──────────────────────────────────────────────

def test_a_credential_in_a_detail_is_redacted(log):
    log.record(_entry(
        action=audit_mod.SOURCE_TESTED,
        detail=audit_mod.scrub({"target": "postgresql://admin:hunter2@db.internal/sales"}),
    ))
    written = log.path.read_text(encoding="utf-8")
    assert "hunter2" not in written
    assert "***" in written


def test_a_bearer_token_in_a_detail_is_redacted():
    scrubbed = audit_mod.scrub({"note": "Authorization: Bearer sk-abcdefghijklmnop"})
    assert "sk-abcdefghijklmnop" not in scrubbed["note"]


def test_a_long_detail_is_truncated():
    scrubbed = audit_mod.scrub({"label": "x" * 10_000})
    assert len(scrubbed["label"]) <= audit_mod.MAX_DETAIL_CHARS


def test_detail_keys_are_bounded():
    scrubbed = audit_mod.scrub({f"k{i}": i for i in range(100)})
    assert len(scrubbed) <= 20


# ── Selection ───────────────────────────────────────────────────────────────

def test_reading_is_scoped_to_one_principal(log):
    log.record(_entry(principal="tok:aaaa", session_id="mine"))
    log.record(_entry(principal="tok:bbbb", session_id="theirs"))

    mine = log.read(principal="tok:aaaa")
    assert [e["session_id"] for e in mine] == ["mine"]


def test_reading_can_be_narrowed_to_one_session(log):
    log.record(_entry(session_id="a"))
    log.record(_entry(session_id="b"))
    assert [e["session_id"] for e in log.read(session_id="b")] == ["b"]


def test_a_missing_file_reads_as_empty(tmp_path):
    assert audit_mod.AuditLog(tmp_path / "never-written.jsonl").read() == []


# ── Configuration ───────────────────────────────────────────────────────────

def test_no_data_dir_means_nothing_is_written(monkeypatch):
    """A dev run must not start writing files into a contributor's checkout."""
    monkeypatch.delenv("LANA_AUDIT_LOG", raising=False)
    log = audit_mod.build_audit_log(None)
    assert isinstance(log, audit_mod.NullAuditLog)
    log.record(_entry())          # a no-op, not an error
    assert log.read() == []


def test_persistence_turns_the_trail_on(tmp_path, monkeypatch):
    monkeypatch.delenv("LANA_AUDIT_LOG", raising=False)
    log = audit_mod.build_audit_log(tmp_path)
    assert isinstance(log, audit_mod.AuditLog)
    assert log.path == tmp_path / "audit.jsonl"


def test_an_explicit_path_overrides_everything(tmp_path, monkeypatch):
    target = tmp_path / "custom" / "trail.jsonl"
    monkeypatch.setenv("LANA_AUDIT_LOG", str(target))
    log = audit_mod.build_audit_log(None)
    assert isinstance(log, audit_mod.AuditLog)
    assert log.path == target


# ── Through the API ─────────────────────────────────────────────────────────

@pytest.fixture
def api(tmp_path, monkeypatch):
    """A client whose audit trail writes to a temp file."""
    monkeypatch.setattr(
        backend_main, "_audit", audit_mod.AuditLog(tmp_path / "audit.jsonl")
    )
    return TestClient(backend_main.app)


def test_an_upload_is_recorded(api):
    sid = api.post("/upload", files={"file": ("sales.csv", CSV, "text/csv")}) \
             .json()["session_id"]

    entries = api.get("/audit").json()["entries"]
    loaded = [e for e in entries if e["action"] == audit_mod.DATA_LOADED]
    assert loaded and loaded[0]["session_id"] == sid
    assert loaded[0]["detail"]["rows"] == 3
    assert loaded[0]["detail"]["label"] == "sales.csv"


def test_an_export_is_recorded_with_its_format(api):
    sid = api.post("/upload", files={"file": ("t.csv", CSV, "text/csv")}) \
             .json()["session_id"]
    assert api.get(f"/export/csv/{sid}").status_code == 200

    exported = [e for e in api.get("/audit").json()["entries"]
                if e["action"] == audit_mod.DATA_EXPORTED]
    assert exported and exported[0]["detail"]["format"] == "csv"
    assert exported[0]["detail"]["rows"] == 3


def test_cleaning_is_recorded_without_naming_columns(api):
    sid = api.post("/upload", files={"file": ("t.csv", CSV, "text/csv")}) \
             .json()["session_id"]
    api.post(f"/clean/apply/{sid}", json={
        "operations": [{"type": "remove_duplicates"}]
    })

    cleaned = [e for e in api.get("/audit").json()["entries"]
               if e["action"] == audit_mod.DATA_CLEANED]
    assert cleaned
    detail = cleaned[0]["detail"]
    assert detail["kinds"] == "remove_duplicates"
    assert detail["rows_before"] == 3
    # Column-level detail belongs to the lineage ledger, which travels with
    # the data. Copying it here would be a second, less protected record of
    # the dataset's shape.
    assert "region" not in json.dumps(detail)


def test_every_entry_carries_the_request_id(api):
    api.post("/upload", files={"file": ("t.csv", CSV, "text/csv")})
    entry = api.get("/audit").json()["entries"][0]
    # The same id the request log line carries, so an audit entry can be
    # joined to the eleven other lines from that request.
    assert entry["request_id"]


def test_the_endpoint_says_when_nothing_is_being_recorded(monkeypatch):
    monkeypatch.setattr(backend_main, "_audit", audit_mod.NullAuditLog())
    client = TestClient(backend_main.app)
    payload = client.get("/audit").json()
    # "Nothing happened" and "nothing is watched" must not look the same.
    assert payload == {"enabled": False, "entries": []}


def test_the_trail_never_contains_the_auth_token(tmp_path, monkeypatch):
    token = "s" * 48
    monkeypatch.setattr(backend_main.config, "auth_token", token)
    log = audit_mod.AuditLog(tmp_path / "audit.jsonl")
    monkeypatch.setattr(backend_main, "_audit", log)
    client = TestClient(backend_main.app)

    assert client.get("/session/nope").status_code == 401
    assert client.get(
        "/session/nope", headers={"Authorization": "Bearer wrong"}
    ).status_code == 401

    written = log.path.read_text(encoding="utf-8")
    assert token not in written
    assert "wrong" not in written
    assert audit_mod.AUTH_FAILED in written
