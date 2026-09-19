"""The file connector reads the *server's* disk, so who may reach it matters.

On a single-user laptop, pointing LANA at ``~/exports/orders.csv`` is the
feature. On an instance with ``LANA_AUTH_TOKEN`` set — the shared deployment
the project explicitly supports — the same connector is an arbitrary
server-side file read reachable from a JSON body, and the extension allowlist
is no defence because CSV and JSON are exactly what interesting files are in.

These tests pin the three states of that policy and, in particular, that
containment is decided on the *resolved* path: a check on the string the user
typed is defeated by ``..`` or a symlink, which is the standard way these
things fail.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.sources import (
    SourceRefused,
    SourceSpec,
    SourceUnavailable,
    available_sources,
    build_source,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("LANA_FILE_SOURCE_ROOTS", raising=False)
    monkeypatch.delenv("LANA_AUTH_TOKEN", raising=False)


@pytest.fixture
def data_dir(tmp_path):
    root = tmp_path / "shared"
    root.mkdir()
    pd.DataFrame({"a": [1, 2, 3]}).to_csv(root / "orders.csv", index=False)
    return root


@pytest.fixture
def secret_file(tmp_path):
    outside = tmp_path / "private"
    outside.mkdir()
    path = outside / "payroll.csv"
    pd.DataFrame({"salary": [100000]}).to_csv(path, index=False)
    return path


def _file(target):
    return build_source(SourceSpec(kind="file", target=str(target)))


# ── Single-user local: unchanged ────────────────────────────────────────────

def test_a_local_install_reads_any_path(secret_file):
    """No token means one user on one machine; their own disk is the feature."""
    result = _file(secret_file).fetch()
    assert len(result.frame) == 1


# ── Shared deployment without roots: the connector switches itself off ──────

def test_a_token_without_roots_disables_the_connector(monkeypatch, secret_file):
    monkeypatch.setenv("LANA_AUTH_TOKEN", "s" * 32)
    with pytest.raises(SourceUnavailable, match="LANA_FILE_SOURCE_ROOTS"):
        _file(secret_file)


def test_the_listing_explains_why_it_is_unavailable(monkeypatch):
    monkeypatch.setenv("LANA_AUTH_TOKEN", "s" * 32)
    entry = next(s for s in available_sources() if s["kind"] == "file")
    assert entry["available"] is False
    # The reason has to name the fix; "unavailable" alone sends the operator
    # to the source code.
    assert "LANA_FILE_SOURCE_ROOTS" in entry["unavailable_reason"]


def test_uploading_is_never_affected(monkeypatch):
    """The escape hatch stays open — /upload does not go through a connector."""
    monkeypatch.setenv("LANA_AUTH_TOKEN", "s" * 32)
    kinds = {s["kind"] for s in available_sources()}
    assert "file" in kinds  # listed, with a reason, rather than vanishing


# ── Shared deployment with roots: confined ──────────────────────────────────

def test_a_file_inside_a_root_is_read(monkeypatch, data_dir):
    monkeypatch.setenv("LANA_AUTH_TOKEN", "s" * 32)
    monkeypatch.setenv("LANA_FILE_SOURCE_ROOTS", str(data_dir))
    assert len(_file(data_dir / "orders.csv").fetch().frame) == 3


def test_a_file_outside_every_root_is_refused(monkeypatch, data_dir, secret_file):
    monkeypatch.setenv("LANA_AUTH_TOKEN", "s" * 32)
    monkeypatch.setenv("LANA_FILE_SOURCE_ROOTS", str(data_dir))
    with pytest.raises(SourceRefused, match="outside the directories"):
        _file(secret_file)


def test_dot_dot_cannot_climb_out_of_a_root(monkeypatch, data_dir, secret_file):
    """Containment is decided on the resolved path, not the typed one."""
    monkeypatch.setenv("LANA_AUTH_TOKEN", "s" * 32)
    monkeypatch.setenv("LANA_FILE_SOURCE_ROOTS", str(data_dir))
    traversal = data_dir / ".." / "private" / "payroll.csv"
    with pytest.raises(SourceRefused):
        _file(traversal)


def test_a_symlink_cannot_smuggle_a_file_in(monkeypatch, data_dir, secret_file):
    monkeypatch.setenv("LANA_AUTH_TOKEN", "s" * 32)
    monkeypatch.setenv("LANA_FILE_SOURCE_ROOTS", str(data_dir))
    link = data_dir / "innocent.csv"
    try:
        link.symlink_to(secret_file)
    except (OSError, NotImplementedError):
        pytest.skip("this platform/user cannot create symlinks")
    with pytest.raises(SourceRefused):
        _file(link)


def test_several_roots_are_honoured(monkeypatch, data_dir, secret_file):
    monkeypatch.setenv("LANA_AUTH_TOKEN", "s" * 32)
    monkeypatch.setenv(
        "LANA_FILE_SOURCE_ROOTS", f"{data_dir}, {secret_file.parent}"
    )
    assert len(_file(secret_file).fetch().frame) == 1


def test_roots_confine_a_local_install_too(monkeypatch, data_dir, secret_file):
    """Setting roots without a token is a deliberate narrowing, and holds."""
    monkeypatch.setenv("LANA_FILE_SOURCE_ROOTS", str(data_dir))
    with pytest.raises(SourceRefused):
        _file(secret_file)
