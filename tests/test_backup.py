"""Local automated backup: what gets archived, what's deliberately skipped,
retention, and restore.
"""

from __future__ import annotations

import tarfile

import pytest

from scripts import backup


def _make_data_dir(tmp_path, *, accounts=True, sessions=True, audit=True,
                   coordination=True):
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    if accounts:
        (data_dir / "accounts.db").write_bytes(b"fake-accounts-db")
    if sessions:
        (data_dir / "sessions.db").write_bytes(b"fake-sessions-index")
        session_dir = data_dir / "11111111-1111-1111-1111-111111111111"
        session_dir.mkdir()
        (session_dir / "raw.parquet").write_bytes(b"fake-parquet")
        (session_dir / "cleaned.parquet").write_bytes(b"fake-cleaned-parquet")
        (session_dir / "ledger.json").write_text('{"steps": []}')
        # A leftover directory that is NOT a session (no raw.parquet) must
        # not be swept into the backup.
        (data_dir / "not_a_session").mkdir()
        (data_dir / "not_a_session" / "readme.txt").write_text("hi")
    if audit:
        (data_dir / "audit.jsonl").write_text('{"a": 1}\n')
        (data_dir / "audit.1.jsonl").write_text('{"a": 0}\n')
    if coordination:
        (data_dir / "coordination.db").write_bytes(b"fake-coordination-db")

    return data_dir


@pytest.fixture(autouse=True)
def _isolated_accounts_db(tmp_path, monkeypatch):
    """account_db_path() reads LANA_ACCOUNTS_DB; point it at the fake file
    each test creates, rather than the real accounts.db default."""
    monkeypatch.setenv("LANA_ACCOUNTS_DB", str(tmp_path / "data" / "accounts.db"))


def test_run_archives_everything_except_coordination(tmp_path):
    data_dir = _make_data_dir(tmp_path)
    backup_dir = tmp_path / "backups"

    code = backup.main([
        "--data-dir", str(data_dir), "--backup-dir", str(backup_dir), "run",
    ])
    assert code == 0

    archives = list(backup_dir.glob("lana-backup-*.tar.gz"))
    assert len(archives) == 1

    with tarfile.open(archives[0]) as tar:
        names = set(tar.getnames())

    assert "accounts.db" in names
    assert "sessions.db" in names
    assert "sessions/11111111-1111-1111-1111-111111111111/raw.parquet" in names
    assert "sessions/11111111-1111-1111-1111-111111111111/cleaned.parquet" in names
    assert "sessions/11111111-1111-1111-1111-111111111111/ledger.json" in names
    assert "audit.jsonl" in names
    assert "audit.1.jsonl" in names

    # Ephemeral coordination state and non-session directories are excluded.
    assert not any("coordination" in n for n in names)
    assert not any("not_a_session" in n for n in names)


def test_run_on_a_bare_instance_succeeds_with_nothing_to_archive(tmp_path):
    """Accounts off, persistence off, auditing off — a valid, non-error state."""
    data_dir = _make_data_dir(
        tmp_path, accounts=False, sessions=False, audit=False, coordination=False,
    )
    backup_dir = tmp_path / "backups"

    code = backup.main([
        "--data-dir", str(data_dir), "--backup-dir", str(backup_dir), "run",
    ])
    assert code == 0

    archives = list(backup_dir.glob("lana-backup-*.tar.gz"))
    assert len(archives) == 1
    with tarfile.open(archives[0]) as tar:
        assert tar.getnames() == []


def test_restore_reproduces_the_original_files(tmp_path):
    data_dir = _make_data_dir(tmp_path)
    backup_dir = tmp_path / "backups"
    backup.main([
        "--data-dir", str(data_dir), "--backup-dir", str(backup_dir), "run",
    ])
    archive_name = next(backup_dir.glob("lana-backup-*.tar.gz")).name

    restored = tmp_path / "restored"
    code = backup.main([
        "--data-dir", str(restored), "--backup-dir", str(backup_dir),
        "restore", archive_name,
    ])
    assert code == 0

    assert (restored / "accounts.db").read_bytes() == b"fake-accounts-db"
    assert (restored / "sessions.db").read_bytes() == b"fake-sessions-index"
    sid = "11111111-1111-1111-1111-111111111111"
    assert (restored / sid / "raw.parquet").read_bytes() == b"fake-parquet"
    assert (restored / sid / "cleaned.parquet").exists()
    assert (restored / sid / "ledger.json").exists()
    assert (restored / "audit.jsonl").exists()
    assert (restored / "audit.1.jsonl").exists()
    # The layout is flat under the target — no lingering "sessions/" prefix.
    assert not (restored / "sessions").exists()


def test_restore_refuses_a_non_empty_target(tmp_path):
    data_dir = _make_data_dir(tmp_path)
    backup_dir = tmp_path / "backups"
    backup.main([
        "--data-dir", str(data_dir), "--backup-dir", str(backup_dir), "run",
    ])
    archive_name = next(backup_dir.glob("lana-backup-*.tar.gz")).name

    target = tmp_path / "live"
    target.mkdir()
    (target / "sessions.db").write_bytes(b"already here")

    code = backup.main([
        "--data-dir", str(target), "--backup-dir", str(backup_dir),
        "restore", archive_name,
    ])
    assert code == 1
    # Nothing was overwritten.
    assert (target / "sessions.db").read_bytes() == b"already here"


def test_retention_prunes_the_oldest_backups_first(tmp_path, monkeypatch):
    data_dir = _make_data_dir(tmp_path, accounts=False, sessions=False,
                              audit=False, coordination=False)
    backup_dir = tmp_path / "backups"

    import time as time_mod
    real_strftime = time_mod.strftime
    call_count = {"n": 0}

    def _fake_strftime(fmt, *a, **k):
        # Guarantee distinct, increasing archive names even if this test
        # runs faster than the format string's one-second resolution.
        call_count["n"] += 1
        return real_strftime(fmt, *a, **k) + f"-{call_count['n']:03d}"

    monkeypatch.setattr(backup.time, "strftime", _fake_strftime)

    for _ in range(5):
        backup.main([
            "--data-dir", str(data_dir), "--backup-dir", str(backup_dir),
            "run", "--keep", "3",
        ])

    archives = sorted(backup_dir.glob("lana-backup-*.tar.gz"))
    assert len(archives) == 3
    # The three that survived are the three most recently created.
    names = [a.name for a in archives]
    assert names == sorted(names)


def test_list_reports_nothing_when_there_are_no_backups(tmp_path, capsys):
    code = backup.main([
        "--data-dir", str(tmp_path / "data"),
        "--backup-dir", str(tmp_path / "backups"), "list",
    ])
    assert code == 0
    assert "No backups" in capsys.readouterr().out


def test_restoring_an_unknown_archive_is_a_clean_error(tmp_path, capsys):
    code = backup.main([
        "--data-dir", str(tmp_path / "restored"),
        "--backup-dir", str(tmp_path / "backups"),
        "restore", "does-not-exist.tar.gz",
    ])
    assert code == 1
    assert "no such backup" in capsys.readouterr().err
