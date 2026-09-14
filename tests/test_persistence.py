"""Session persistence — SQLite index + Parquet frames.

Exercised directly against ``SessionStore``/``PersistenceBackend`` with a
temp directory, not through the shared app-level ``_store`` — that global is
created once at import time with persistence off, and tests must not depend
on ordering to see it any other way.
"""

import pandas as pd

from app.data.lineage import CleaningLedger
from backend.persistence import PersistenceBackend
from backend.session_store import CLEANED, ORIGINAL, SessionStore

DF = pd.DataFrame({"name": ["Alice", "Bob", "Carol"], "score": [1, 2, 3]})


def _cleaned_ledger(raw: pd.DataFrame, cleaned: pd.DataFrame) -> CleaningLedger:
    ledger = CleaningLedger()
    ledger.record("remove_duplicates", raw, cleaned, rationale="test")
    return ledger


# ── PersistenceBackend round-trip ─────────────────────────────────────────────

def test_save_and_load_raw_only_session(tmp_path):
    backend = PersistenceBackend(tmp_path)
    store = SessionStore()
    session = store.create("sid-1", "t.csv", DF)
    backend.save(session)

    [loaded] = backend.load_all()
    assert loaded["session_id"] == "sid-1"
    assert loaded["filename"] == "t.csv"
    assert loaded["active_version"] == ORIGINAL
    assert loaded["cleaned"] is None
    pd.testing.assert_frame_equal(loaded["raw"], DF)


def test_save_and_load_session_with_cleaned_version_and_ledger(tmp_path):
    backend = PersistenceBackend(tmp_path)
    store = SessionStore()
    session = store.create("sid-2", "t.csv", DF)
    cleaned = DF.iloc[:2].reset_index(drop=True)
    ledger = _cleaned_ledger(DF, cleaned)
    session.set_cleaned(cleaned, ledger)
    backend.save(session)

    [loaded] = backend.load_all()
    assert loaded["active_version"] == CLEANED
    pd.testing.assert_frame_equal(loaded["cleaned"], cleaned)
    # The ledger must survive with every field intact, not just its summary.
    assert loaded["ledger"].records[0].operation == "remove_duplicates"
    assert loaded["ledger"].records[0].rationale == "test"
    assert loaded["ledger"].to_dict(len(DF))["summary"]["steps"] == 1


def test_metadata_only_save_does_not_touch_frames(tmp_path):
    backend = PersistenceBackend(tmp_path)
    store = SessionStore()
    session = store.create("sid-3", "t.csv", DF)
    backend.save(session)
    raw_path = tmp_path / "sid-3" / "raw.parquet"
    mtime_before = raw_path.stat().st_mtime_ns

    session.last_used += 1  # something changes, but not the frame
    backend.save(session, frames=False)
    assert raw_path.stat().st_mtime_ns == mtime_before


def test_delete_removes_files_and_index_row(tmp_path):
    backend = PersistenceBackend(tmp_path)
    store = SessionStore()
    session = store.create("sid-4", "t.csv", DF)
    backend.save(session)
    assert (tmp_path / "sid-4").exists()

    backend.delete("sid-4")
    assert not (tmp_path / "sid-4").exists()
    assert backend.load_all() == []


def test_load_all_skips_and_cleans_up_a_session_missing_its_raw_frame(tmp_path):
    backend = PersistenceBackend(tmp_path)
    store = SessionStore()
    session = store.create("sid-5", "t.csv", DF)
    backend.save(session)
    (tmp_path / "sid-5" / "raw.parquet").unlink()

    assert backend.load_all() == []
    # The dangling index row was cleaned up, not left to fail again next time.
    with backend._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


# ── SessionStore + persistence integration ────────────────────────────────────

def test_sessions_survive_a_simulated_restart(tmp_path):
    store1 = SessionStore(persist_dir=tmp_path)
    session = store1.create("sid-6", "t.csv", DF)
    store1.persist(session)

    # A fresh SessionStore pointed at the same directory stands in for the
    # process restarting — nothing here shares memory with store1.
    store2 = SessionStore(persist_dir=tmp_path)
    restored = store2.get("sid-6")
    assert restored is not None
    assert restored.filename == "t.csv"
    pd.testing.assert_frame_equal(restored.raw, DF)


def test_eviction_deletes_persisted_data_too(tmp_path):
    store = SessionStore(max_sessions=1, persist_dir=tmp_path)
    s1 = store.create("sid-7", "a.csv", DF)
    store.persist(s1)
    assert (tmp_path / "sid-7").exists()

    s2 = store.create("sid-8", "b.csv", DF)  # evicts sid-7 under max_sessions=1
    store.persist(s2)
    assert store.get("sid-7") is None
    assert not (tmp_path / "sid-7").exists()


def test_persistence_disabled_by_default_leaves_no_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = SessionStore()  # persist_dir=None, matching the app's own default
    session = store.create("sid-9", "t.csv", DF)
    store.persist(session)  # must be a harmless no-op
    assert not (tmp_path / "data").exists()
