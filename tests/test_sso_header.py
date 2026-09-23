"""Trusted-header SSO: LANA trusts an identity a reverse proxy already verified.

The one security-critical property this feature has is that the identity
header is only ever trusted alongside a shared secret only the proxy should
know — see app.config.AppConfig.trusted_header_active and the module
docstring in backend/main.py's _header_identity. These tests exist mostly to
pin that: a request cannot get in by presenting the identity header alone,
and the feature stays inert unless both env vars are set together.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

CSV = b"region,revenue\nnorth,100\nsouth,200\n"

SECRET_HEADER = "X-Lana-Proxy-Secret"
IDENTITY_HEADER = "X-Forwarded-Email"
SECRET = "s3cret-only-the-proxy-knows"


@pytest.fixture
def api(tmp_path, monkeypatch):
    """A backend in accounts mode, with trusted-header SSO configured."""
    monkeypatch.setenv("LANA_ACCOUNTS", "true")
    monkeypatch.setenv("LANA_ACCOUNTS_DB", str(tmp_path / "accounts.db"))

    import backend.main as backend_main
    importlib.reload(backend_main)

    monkeypatch.setattr(backend_main.config, "trusted_header_name", IDENTITY_HEADER)
    monkeypatch.setattr(backend_main.config, "trusted_header_secret", SECRET)

    yield TestClient(backend_main.app), backend_main

    monkeypatch.delenv("LANA_ACCOUNTS", raising=False)
    monkeypatch.delenv("LANA_ACCOUNTS_DB", raising=False)
    importlib.reload(backend_main)


def _proxy_headers(identity: str, secret: str = SECRET) -> dict:
    return {SECRET_HEADER: secret, IDENTITY_HEADER: identity}


def test_the_correct_secret_and_identity_provisions_and_signs_in(api):
    client, _ = api
    response = client.post(
        "/upload", files={"file": ("t.csv", CSV, "text/csv")},
        headers=_proxy_headers("alice@client.example"),
    )
    assert response.status_code == 200


def test_the_identity_header_alone_is_not_trusted(api):
    """The header a client could set directly must not be enough by itself."""
    client, _ = api
    response = client.post(
        "/upload", files={"file": ("t.csv", CSV, "text/csv")},
        headers={IDENTITY_HEADER: "alice@client.example"},
    )
    assert response.status_code == 401


def test_a_wrong_secret_is_rejected_even_with_a_valid_looking_identity(api):
    client, _ = api
    response = client.post(
        "/upload", files={"file": ("t.csv", CSV, "text/csv")},
        headers=_proxy_headers("alice@client.example", secret="wrong-secret"),
    )
    assert response.status_code == 401


def test_the_feature_is_inert_unless_both_env_vars_are_set(api):
    """Fail closed: one var configured and not the other must not activate it."""
    client, backend_main = api
    backend_main.config.trusted_header_secret = ""  # only the name is set

    response = client.post(
        "/upload", files={"file": ("t.csv", CSV, "text/csv")},
        headers=_proxy_headers("alice@client.example"),
    )
    assert response.status_code == 401


def test_the_first_header_identity_becomes_admin_later_ones_dont(api):
    client, backend_main = api
    client.post(
        "/upload", files={"file": ("t.csv", CSV, "text/csv")},
        headers=_proxy_headers("alice@client.example"),
    )
    client.post(
        "/upload", files={"file": ("t.csv", CSV, "text/csv")},
        headers=_proxy_headers("bob@client.example"),
    )
    alice = backend_main._accounts.get_by_username("alice@client.example")
    bob = backend_main._accounts.get_by_username("bob@client.example")
    assert alice.role == "admin"
    assert bob.role == "member"


def test_two_header_identities_cannot_see_each_others_sessions(api):
    client, _ = api
    sid = client.post(
        "/upload", files={"file": ("secret.csv", CSV, "text/csv")},
        headers=_proxy_headers("alice@client.example"),
    ).json()["session_id"]

    assert client.get(
        f"/session/{sid}", headers=_proxy_headers("alice@client.example")
    ).status_code == 200
    # 404 rather than 403 — same reasoning as local accounts: a 403 would
    # confirm the id exists to someone who should not be able to tell.
    assert client.get(
        f"/session/{sid}", headers=_proxy_headers("bob@client.example")
    ).status_code == 404


def test_local_password_sign_in_still_works_with_header_mode_configured(api):
    """A break-glass path if the proxy is ever down or misconfigured."""
    client, backend_main = api
    backend_main._accounts.create_user("carol", "a-long-enough-password")

    response = client.post(
        "/auth/login", json={"username": "carol", "password": "a-long-enough-password"}
    )
    assert response.status_code == 200

    assert client.post(
        "/upload", files={"file": ("t.csv", CSV, "text/csv")}
    ).status_code == 200


def test_header_mode_is_off_when_accounts_mode_is_off(tmp_path, monkeypatch):
    """Trusted-header identity only ever matters inside accounts mode."""
    import backend.main as backend_main
    importlib.reload(backend_main)  # accounts off, per this suite's defaults

    monkeypatch.setattr(backend_main.config, "trusted_header_name", IDENTITY_HEADER)
    monkeypatch.setattr(backend_main.config, "trusted_header_secret", SECRET)
    client = TestClient(backend_main.app)

    # Open mode: every endpoint is open anyway, header or not — the point is
    # just that this does not error or behave differently either way.
    response = client.post(
        "/upload", files={"file": ("t.csv", CSV, "text/csv")},
        headers=_proxy_headers("alice@client.example"),
    )
    assert response.status_code == 200

    importlib.reload(backend_main)
