"""Real accounts: two people on one instance must be two principals.

The shared token made session ownership vacuous — everyone holding it hashed
to the same principal, so the check ran and permitted everything. SECURITY.md
listed it as the largest remaining gap. These tests are mostly about the
boundaries where an account system quietly fails to be one: a login that tells
you which usernames exist, a sign-out that does not revoke, a password change
that leaves old sessions live, a last-admin demotion that locks everyone out.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from app import accounts as acc
from backend.main import app as _open_mode_app

CSV = b"region,revenue\nnorth,100\nsouth,200\n"


@pytest.fixture()
def client():
    """A backend in open mode — no accounts, no token. Module-level `app`,
    not the `api` fixture's reloaded one, so it reflects whatever mode the
    test process is already in (open, per this suite's defaults)."""
    return TestClient(_open_mode_app, raise_server_exceptions=False)


@pytest.fixture
def store(tmp_path):
    return acc.AccountStore(tmp_path / "accounts.db")


# ── Password hashing ────────────────────────────────────────────────────────

def test_a_password_round_trips():
    verifier = acc.hash_password("correct horse battery staple")
    assert acc.verify_password("correct horse battery staple", verifier)
    assert not acc.verify_password("wrong horse battery staple", verifier)


def test_the_plaintext_is_never_in_the_verifier():
    verifier = acc.hash_password("hunter2hunter2")
    assert "hunter2" not in verifier


def test_two_hashes_of_one_password_differ():
    """Per-hash salt: identical passwords must not be identifiable as such."""
    assert acc.hash_password("same-password") != acc.hash_password("same-password")


def test_a_corrupt_verifier_fails_one_login_rather_than_raising():
    # A damaged row should cost one sign-in, not take the endpoint down.
    assert acc.verify_password("anything", "not-a-verifier") is False
    assert acc.verify_password("anything", "scrypt$bad$bad$bad$zz$zz") is False


def test_the_hash_carries_its_own_parameters():
    # So raising the cost later does not invalidate every existing password.
    verifier = acc.hash_password("a-long-enough-password")
    assert verifier.startswith(f"scrypt${acc.SCRYPT_N}${acc.SCRYPT_R}$")
    assert not acc.needs_rehash(verifier)
    assert acc.needs_rehash("scrypt$1024$8$1$aa$bb")


def test_a_weakly_hashed_password_is_upgraded_on_next_login(store, monkeypatch):
    user = store.create_user("alice", "a-long-enough-password")
    # Simulate a hash written when the cost was lower.
    with store._connect() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (f"scrypt$1024$8$1${'aa' * 16}${'bb' * 32}", user.id),
        )
    # That stored hash is not of this password, so login fails and nothing is
    # upgraded — the point is that a wrong password never triggers a rewrite.
    assert store.authenticate("alice", "a-long-enough-password") is None


# ── Users ───────────────────────────────────────────────────────────────────

def test_the_first_user_is_an_admin_whatever_was_asked_for(store):
    # An instance whose only account cannot manage users is unrecoverable
    # without editing the database by hand.
    user = store.create_user("alice", "a-long-enough-password", role=acc.ROLE_MEMBER)
    assert user.role == acc.ROLE_ADMIN


def test_later_users_default_to_member(store):
    store.create_user("alice", "a-long-enough-password")
    bob = store.create_user("bob", "another-long-password")
    assert bob.role == acc.ROLE_MEMBER


def test_usernames_are_unique_case_insensitively(store):
    store.create_user("alice", "a-long-enough-password")
    with pytest.raises(acc.AccountError, match="already exists"):
        store.create_user("ALICE", "another-long-password")


@pytest.mark.parametrize("bad", ["", "a", "x" * 65, "has space", "semi;colon"])
def test_a_malformed_username_is_refused(store, bad):
    with pytest.raises(acc.AccountError):
        store.create_user(bad, "a-long-enough-password")


def test_a_short_password_is_refused(store):
    with pytest.raises(acc.AccountError, match="at least"):
        store.create_user("alice", "short")


def test_the_last_admin_cannot_be_demoted_disabled_or_deleted(store):
    store.create_user("alice", "a-long-enough-password")
    store.create_user("bob", "another-long-password")

    for call in (
        lambda: store.set_role("alice", acc.ROLE_MEMBER),
        lambda: store.set_disabled("alice", True),
        lambda: store.delete_user("alice"),
    ):
        with pytest.raises(acc.AccountError, match="only admin"):
            call()


def test_an_admin_can_go_once_another_exists(store):
    store.create_user("alice", "a-long-enough-password")
    store.create_user("bob", "another-long-password")
    store.set_role("bob", acc.ROLE_ADMIN)
    store.delete_user("alice")          # now permitted
    assert [u.username for u in store.list_users()] == ["bob"]


# ── Authentication ──────────────────────────────────────────────────────────

def test_authenticate_accepts_the_right_password(store):
    store.create_user("alice", "a-long-enough-password")
    assert store.authenticate("alice", "a-long-enough-password").username == "alice"


@pytest.mark.parametrize("username,password", [
    ("alice", "wrong-password-here"),
    ("nobody", "a-long-enough-password"),
])
def test_a_bad_login_is_indistinguishable_from_an_unknown_user(
    store, username, password
):
    # Separate answers turn the endpoint into a way to enumerate who has an
    # account here.
    store.create_user("alice", "a-long-enough-password")
    assert store.authenticate(username, password) is None


def test_a_disabled_user_cannot_sign_in(store):
    store.create_user("alice", "a-long-enough-password")
    store.create_user("bob", "another-long-password")
    store.set_disabled("bob", True)
    assert store.authenticate("bob", "another-long-password") is None


# ── Sessions ────────────────────────────────────────────────────────────────

def test_a_session_token_resolves_to_its_user(store):
    user = store.create_user("alice", "a-long-enough-password")
    token = store.open_session(user)
    assert store.user_for_token(token).id == user.id


def test_the_token_is_stored_hashed_not_in_the_clear(store):
    user = store.create_user("alice", "a-long-enough-password")
    token = store.open_session(user)
    with store._connect() as conn:
        rows = conn.execute("SELECT token_hash FROM auth_sessions").fetchall()
    # A readable session table is a table of working credentials.
    assert token not in [r["token_hash"] for r in rows]


def test_signing_out_revokes_the_token_server_side(store):
    user = store.create_user("alice", "a-long-enough-password")
    token = store.open_session(user)
    store.close_session(token)
    assert store.user_for_token(token) is None


def test_an_expired_token_is_rejected(store):
    user = store.create_user("alice", "a-long-enough-password")
    token = store.open_session(user, ttl_seconds=-1)
    assert store.user_for_token(token) is None


def test_changing_a_password_ends_existing_sessions(store):
    user = store.create_user("alice", "a-long-enough-password")
    token = store.open_session(user)
    store.set_password("alice", "a-different-long-password")
    # A password change is usually a response to exposure; leaving old
    # sessions live would make it cosmetic.
    assert store.user_for_token(token) is None


def test_disabling_a_user_ends_their_sessions(store):
    store.create_user("alice", "a-long-enough-password")
    bob = store.create_user("bob", "another-long-password")
    token = store.open_session(bob)
    store.set_disabled("bob", True)
    assert store.user_for_token(token) is None


def test_a_garbage_token_resolves_to_nobody(store):
    assert store.user_for_token("not-a-real-token") is None
    assert store.user_for_token("") is None


# ── Trusted-header identity (JIT provisioning) ──────────────────────────────

def test_a_new_header_identity_is_provisioned_and_becomes_admin(store):
    # The first identity seen this way is still the only administrator this
    # instance has, exactly like the first CLI-created account.
    user = store.get_or_create_by_external_id("alice@client.example")
    assert user.role == acc.ROLE_ADMIN
    assert user.auth_source == acc.AUTH_SOURCE_HEADER


def test_a_later_header_identity_defaults_to_member(store):
    store.get_or_create_by_external_id("alice@client.example")
    bob = store.get_or_create_by_external_id("bob@client.example")
    assert bob.role == acc.ROLE_MEMBER


def test_the_same_header_identity_resolves_to_the_same_user(store):
    first = store.get_or_create_by_external_id("alice@client.example")
    second = store.get_or_create_by_external_id("alice@client.example")
    assert first.id == second.id


def test_a_header_provisioned_account_has_no_usable_local_password(store):
    user = store.get_or_create_by_external_id("alice@client.example")
    # It has *some* hash (the schema requires one), but not one anybody knows.
    assert store.authenticate(user.username, "") is None
    assert store.authenticate(user.username, "password123456") is None


# ── Password reset ──────────────────────────────────────────────────────────

def test_a_reset_token_lets_you_set_a_new_password(store):
    store.create_user("alice", "the-original-password")
    token = store.create_password_reset("alice")
    assert token is not None

    user = store.consume_password_reset(token, "a-brand-new-password")
    assert user.username == "alice"
    assert store.authenticate("alice", "a-brand-new-password") is not None
    assert store.authenticate("alice", "the-original-password") is None


def test_requesting_a_reset_for_an_unknown_user_returns_none(store):
    # The caller must give the same response either way — see
    # backend/main.py:forgot_password — so the distinction has to be made
    # here, not at the HTTP layer.
    assert store.create_password_reset("nobody-here") is None


def test_a_reset_token_is_single_use(store):
    store.create_user("alice", "the-original-password")
    token = store.create_password_reset("alice")
    store.consume_password_reset(token, "a-brand-new-password")
    assert store.consume_password_reset(token, "yet-another-password") is None


def test_an_expired_reset_token_is_rejected(store):
    store.create_user("alice", "the-original-password")
    token = store.create_password_reset("alice")
    with store._lock, store._connect() as conn:
        conn.execute(
            "UPDATE password_resets SET expires_at = 0 WHERE token_hash = ?",
            (acc._token_hash(token),),
        )
    assert store.consume_password_reset(token, "a-brand-new-password") is None


def test_a_garbage_reset_token_is_rejected(store):
    store.create_user("alice", "the-original-password")
    assert store.consume_password_reset("not-a-real-token", "a-new-password") is None
    assert store.consume_password_reset("", "a-new-password") is None


def test_the_reset_token_is_stored_hashed_not_in_the_clear(store):
    store.create_user("alice", "the-original-password")
    token = store.create_password_reset("alice")
    with store._connect() as conn:
        rows = conn.execute("SELECT token_hash FROM password_resets").fetchall()
    assert token not in [r["token_hash"] for r in rows]


def test_a_reset_requesting_a_new_token_invalidates_the_previous_one(store):
    # Otherwise an old, forgotten email sitting in an inbox stays a live way
    # in even after the account holder asked for (and used, or didn't) a
    # newer one.
    store.create_user("alice", "the-original-password")
    old_token = store.create_password_reset("alice")
    store.create_password_reset("alice")
    assert store.consume_password_reset(old_token, "a-brand-new-password") is None


def test_resetting_a_password_ends_existing_sessions(store):
    user = store.create_user("alice", "the-original-password")
    session_token = store.open_session(user)
    reset_token = store.create_password_reset("alice")
    store.consume_password_reset(reset_token, "a-brand-new-password")
    assert store.user_for_token(session_token) is None


def test_a_weak_new_password_is_refused_without_burning_the_token(store):
    store.create_user("alice", "the-original-password")
    token = store.create_password_reset("alice")
    with pytest.raises(acc.AccountError):
        store.consume_password_reset(token, "short")
    # The token must still work with a valid password afterwards.
    assert store.consume_password_reset(token, "a-brand-new-password") is not None


# ── Through the API ─────────────────────────────────────────────────────────

@pytest.fixture
def api(tmp_path, monkeypatch):
    """A backend running in accounts mode, with two users."""
    monkeypatch.setenv("LANA_ACCOUNTS", "true")
    monkeypatch.setenv("LANA_ACCOUNTS_DB", str(tmp_path / "accounts.db"))

    import backend.main as backend_main
    importlib.reload(backend_main)

    store = backend_main._accounts
    store.create_user("alice", "a-long-enough-password")
    store.create_user("bob", "another-long-password")

    yield TestClient(backend_main.app), backend_main

    # Restore the module for every other test file, which expects open mode.
    monkeypatch.delenv("LANA_ACCOUNTS", raising=False)
    monkeypatch.delenv("LANA_ACCOUNTS_DB", raising=False)
    importlib.reload(backend_main)


def _sign_in(client, username, password):
    response = client.post(
        "/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return response


def test_the_status_endpoint_names_the_mode(api):
    client, _ = api
    status = client.get("/auth/status").json()
    # The sign-in form differs per mode; a client that guessed would show the
    # wrong one.
    assert status["mode"] == "accounts"
    assert status["required"] is True
    assert status["authenticated"] is False


def test_an_unauthenticated_request_is_refused(api):
    client, _ = api
    client.cookies.clear()
    assert client.post(
        "/upload", files={"file": ("t.csv", CSV, "text/csv")}
    ).status_code == 401


def test_signing_in_then_uploading_works(api):
    client, _ = api
    _sign_in(client, "alice", "a-long-enough-password")
    assert client.post(
        "/upload", files={"file": ("t.csv", CSV, "text/csv")}
    ).status_code == 200


def test_a_wrong_password_is_401_with_one_message(api):
    client, _ = api
    wrong = client.post(
        "/auth/login", json={"username": "alice", "password": "not-the-password"}
    )
    missing = client.post(
        "/auth/login", json={"username": "ghost", "password": "not-the-password"}
    )
    assert wrong.status_code == missing.status_code == 401
    assert wrong.json()["detail"] == missing.json()["detail"]


def test_one_user_cannot_read_another_users_session(api):
    """The whole point. Under a shared token this test could not exist."""
    client, _ = api

    _sign_in(client, "alice", "a-long-enough-password")
    sid = client.post(
        "/upload", files={"file": ("secret.csv", CSV, "text/csv")}
    ).json()["session_id"]
    assert client.get(f"/session/{sid}").status_code == 200

    client.cookies.clear()
    _sign_in(client, "bob", "another-long-password")
    # 404 rather than 403: a 403 confirms the id exists and turns the endpoint
    # into an oracle for enumerating other people's sessions.
    assert client.get(f"/session/{sid}").status_code == 404


def test_signing_out_revokes_access_immediately(api):
    client, _ = api
    _sign_in(client, "alice", "a-long-enough-password")
    assert client.get("/auth/status").json()["authenticated"] is True

    client.post("/auth/logout")
    assert client.get("/auth/status").json()["authenticated"] is False
    assert client.post(
        "/upload", files={"file": ("t.csv", CSV, "text/csv")}
    ).status_code == 401


def test_health_stays_reachable_without_signing_in(api):
    client, _ = api
    client.cookies.clear()
    # A container healthcheck is infrastructure, not a user.
    assert client.get("/health").status_code == 200


def test_the_signed_in_user_is_reported_but_not_their_hash(api):
    client, _ = api
    _sign_in(client, "alice", "a-long-enough-password")
    status = client.get("/auth/status").json()
    assert status["user"] == {"username": "alice", "role": "admin"}
    assert "password" not in str(status).lower()


def test_a_sign_in_is_recorded_in_the_audit_trail(api, tmp_path, monkeypatch):
    client, backend_main = api
    from app import audit as audit_mod

    log = audit_mod.AuditLog(tmp_path / "audit.jsonl")
    monkeypatch.setattr(backend_main, "_audit", log)

    _sign_in(client, "alice", "a-long-enough-password")
    client.post("/auth/login", json={"username": "alice", "password": "nope-nope"})

    written = log.path.read_text(encoding="utf-8")
    assert audit_mod.AUTH_SUCCEEDED in written
    assert audit_mod.AUTH_FAILED in written
    # Neither the password nor anything derived from it may be recorded.
    assert "a-long-enough-password" not in written
    assert "nope-nope" not in written


# ── Password reset, through the API ─────────────────────────────────────────

def test_forgot_password_gives_the_same_response_either_way(api):
    client, _ = api
    real = client.post("/auth/forgot-password", json={"username": "alice"})
    fake = client.post("/auth/forgot-password", json={"username": "nobody-here"})
    assert real.status_code == fake.status_code == 200
    assert real.json() == fake.json()


def test_reset_password_with_a_valid_token_changes_it(api):
    client, backend_main = api
    token = backend_main._accounts.create_password_reset("alice")

    response = client.post(
        "/auth/reset-password",
        json={"token": token, "new_password": "a-brand-new-password"},
    )
    assert response.status_code == 200

    client.cookies.clear()
    _sign_in(client, "alice", "a-brand-new-password")


def test_reset_password_rejects_an_invalid_token(api):
    client, _ = api
    response = client.post(
        "/auth/reset-password",
        json={"token": "not-a-real-token", "new_password": "a-brand-new-password"},
    )
    assert response.status_code == 400


def test_reset_password_invalidates_the_signed_in_session(api):
    client, backend_main = api
    _sign_in(client, "alice", "a-long-enough-password")
    assert client.get("/auth/status").json()["authenticated"] is True

    token = backend_main._accounts.create_password_reset("alice")
    client.post(
        "/auth/reset-password",
        json={"token": token, "new_password": "a-brand-new-password"},
    )

    # The cookie from before the reset must no longer work.
    assert client.get("/auth/status").json()["authenticated"] is False


def test_forgot_password_is_refused_outside_accounts_mode(client):
    assert client.post(
        "/auth/forgot-password", json={"username": "alice"}
    ).status_code == 400


def test_forgot_password_has_its_own_tighter_rate_limit(api, monkeypatch):
    client, backend_main = api
    monkeypatch.setattr(backend_main, "PASSWORD_RESET_RATE_CAPACITY", 2.0)
    monkeypatch.setattr(backend_main, "PASSWORD_RESET_RATE_REFILL_PER_SECOND", 0.0)

    for _ in range(2):
        assert client.post(
            "/auth/forgot-password", json={"username": "alice"}
        ).status_code == 200
    limited = client.post("/auth/forgot-password", json={"username": "alice"})
    assert limited.status_code == 429
