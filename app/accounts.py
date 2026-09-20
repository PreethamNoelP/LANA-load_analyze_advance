"""Real user accounts, so two people on one instance are two principals.

The gap this closes
-------------------
``LANA_AUTH_TOKEN`` is one shared secret. Everyone holding it hashes to the
same principal, so session ownership — which is enforced correctly — enforces
nothing between colleagues: they *are* the same principal, and each can read
the other's uploads. SECURITY.md said so plainly under known limitations, and
it was the largest remaining gap for anything beyond one person.

No amount of refining the token fixes that. It needs identities.

What this is, and what it deliberately is not
---------------------------------------------
It is: a local user store with per-user password hashing, revocable server-side
sessions, and two roles. That makes the existing ownership check meaningful and
gives an audit trail a name to attribute actions to.

It is not an identity provider. No SSO, no OIDC, no SAML, no password reset
flow, no email. Those need an organisation's directory behind them, and a
local-first tool that shipped a half-implementation of them would be claiming
something it cannot deliver. An instance that needs SSO should sit behind a
proxy that does it.

Why accounts are created by an administrator, not by self-registration
----------------------------------------------------------------------
Open registration on a data-analysis tool is almost always wrong: the first
stranger to find the port becomes a user. Accounts are created by whoever runs
the instance, through ``python -m scripts.manage_users``. The first account
created is an admin, because an instance with no administrator cannot make one.

Password hashing
----------------
``hashlib.scrypt``, from the standard library. Memory-hard, so a stolen
database resists GPU cracking in a way PBKDF2 does not, and it costs no new
dependency — the same reasoning ``app/observability.py`` applies to metrics.
Parameters are stored *with* each hash, so raising them later does not
invalidate existing passwords: a user logging in against an old hash is
re-hashed at the current cost.

Session tokens
--------------
A random 256-bit token, stored **hashed**. A readable session table is a table
of working credentials; if this database leaks, the tokens in it must not be
replayable. Sessions are rows, so revocation is a delete — which is the
difference between this and a stateless JWT that stays valid until it expires
no matter what the server has since learned.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

# scrypt cost parameters. n=2**15 with r=8 takes roughly 100 ms and 32 MB per
# hash on a normal machine — slow enough to make offline cracking expensive,
# fast enough that a login does not feel broken. Stored per-hash so these can
# be raised without a migration.
SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SALT_BYTES = 16

# How long a signed-in browser stays signed in. Matches the shared-token
# cookie's default for consistency.
SESSION_TTL_SECONDS = 43_200

# Usernames are an identifier, not free text: they appear in the audit trail
# and in ownership records, so they are bounded and predictable.
MIN_USERNAME = 2
MAX_USERNAME = 64
# Passwords are bounded only to stop a megabyte of input becoming a megabyte
# of scrypt work. The floor is deliberately a floor and not a composition rule
# — length is what matters, and "must contain a symbol" mostly produces
# Password1! across an entire organisation.
MIN_PASSWORD = 10
MAX_PASSWORD = 1024

ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"
ROLES = (ROLE_ADMIN, ROLE_MEMBER)


class AccountError(Exception):
    """Something the caller can fix. Safe to show a user."""


@dataclass(frozen=True)
class User:
    id: int
    username: str
    role: str
    disabled: bool = False

    @property
    def principal(self) -> str:
        """The identity string the rest of LANA keys ownership and audit on.

        The numeric id rather than the username, so renaming a user later
        cannot orphan their sessions or rewrite their history.
        """
        return f"user:{self.id}"

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    def to_dict(self) -> dict:
        return {"username": self.username, "role": self.role}


_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS users (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
        password_hash TEXT NOT NULL,
        role          TEXT NOT NULL DEFAULT 'member',
        disabled      INTEGER NOT NULL DEFAULT 0,
        created_at    REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS auth_sessions (
        token_hash TEXT PRIMARY KEY,
        user_id    INTEGER NOT NULL,
        created_at REAL NOT NULL,
        expires_at REAL NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id)",
)


def hash_password(password: str) -> str:
    """A verifier string carrying its own parameters: scrypt$n$r$p$salt$hash."""
    salt = secrets.token_bytes(SALT_BYTES)
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=salt,
        n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_DKLEN,
        # scrypt needs roughly 128*n*r bytes; the default OpenSSL cap is
        # 32 MB, which n=2**15, r=8 sits exactly at. Stated explicitly so a
        # future parameter rise fails loudly here rather than at runtime.
        maxmem=132 * 1024 * 1024,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${derived.hex()}"


def verify_password(password: str, verifier: str) -> bool:
    """Constant-time check against a stored verifier. False on anything odd.

    A malformed verifier returns False rather than raising: a corrupt row
    should fail one login, not take the login endpoint down.
    """
    try:
        scheme, n, r, p, salt_hex, hash_hex = verifier.split("$")
        if scheme != "scrypt":
            return False
        derived = hashlib.scrypt(
            password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
            n=int(n), r=int(r), p=int(p), dklen=len(hash_hex) // 2,
            maxmem=132 * 1024 * 1024,
        )
    except (ValueError, TypeError, MemoryError):
        return False
    return hmac.compare_digest(derived.hex(), hash_hex)


def needs_rehash(verifier: str) -> bool:
    """Whether a stored hash was made with weaker parameters than current."""
    try:
        scheme, n, r, p, _salt, _hash = verifier.split("$")
    except ValueError:
        return True
    return scheme != "scrypt" or (int(n), int(r), int(p)) != (
        SCRYPT_N, SCRYPT_R, SCRYPT_P
    )


class AccountStore:
    """Users and their sessions, in SQLite beside the rest of LANA's state."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            for statement in _SCHEMA:
                conn.execute(statement)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # ── Users ───────────────────────────────────────────────────────────────

    def count(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def create_user(self, username: str, password: str,
                    role: str | None = None) -> User:
        """Add a user. The first one is an admin whatever the caller asked for.

        An instance whose only account is a non-admin has no way to create
        another, which is a state that can only be escaped by editing the
        database by hand.
        """
        username = _clean_username(username)
        _check_password(password)

        with self._lock, self._connect() as conn:
            first = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
            resolved = ROLE_ADMIN if first else (role or ROLE_MEMBER)
            if resolved not in ROLES:
                raise AccountError(
                    f"Unknown role '{resolved}'. Use one of: {', '.join(ROLES)}."
                )
            try:
                cursor = conn.execute(
                    "INSERT INTO users (username, password_hash, role, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (username, hash_password(password), resolved, time.time()),
                )
            except sqlite3.IntegrityError as exc:
                raise AccountError(
                    f"A user called '{username}' already exists."
                ) from exc
            return User(id=cursor.lastrowid, username=username, role=resolved)

    def get_by_username(self, username: str) -> User | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, username, role, disabled FROM users WHERE username = ?",
                (username.strip(),),
            ).fetchone()
        return _row_to_user(row)

    def get(self, user_id: int) -> User | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, username, role, disabled FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
        return _row_to_user(row)

    def list_users(self) -> list[User]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, username, role, disabled FROM users ORDER BY id"
            ).fetchall()
        return [_row_to_user(r) for r in rows]

    def set_password(self, username: str, password: str) -> None:
        _check_password(password)
        with self._lock, self._connect() as conn:
            changed = conn.execute(
                "UPDATE users SET password_hash = ? WHERE username = ?",
                (hash_password(password), username.strip()),
            ).rowcount
            if not changed:
                raise AccountError(f"No user called '{username}'.")
            # Every existing session is invalidated. A password change is
            # usually a response to it having been exposed, and leaving old
            # sessions live would make the change cosmetic.
            conn.execute(
                "DELETE FROM auth_sessions WHERE user_id = "
                "(SELECT id FROM users WHERE username = ?)",
                (username.strip(),),
            )

    def set_role(self, username: str, role: str) -> None:
        if role not in ROLES:
            raise AccountError(f"Unknown role '{role}'. Use: {', '.join(ROLES)}.")
        with self._lock, self._connect() as conn:
            if role != ROLE_ADMIN and _would_orphan_admins(conn, username):
                raise AccountError(
                    "This is the only admin. Promote someone else first, or "
                    "the instance will have nobody who can manage users."
                )
            if not conn.execute(
                "UPDATE users SET role = ? WHERE username = ?",
                (role, username.strip()),
            ).rowcount:
                raise AccountError(f"No user called '{username}'.")

    def set_disabled(self, username: str, disabled: bool) -> None:
        with self._lock, self._connect() as conn:
            if disabled and _would_orphan_admins(conn, username):
                raise AccountError(
                    "This is the only admin — disabling it would lock everyone "
                    "out of user management."
                )
            if not conn.execute(
                "UPDATE users SET disabled = ? WHERE username = ?",
                (int(disabled), username.strip()),
            ).rowcount:
                raise AccountError(f"No user called '{username}'.")
            if disabled:
                conn.execute(
                    "DELETE FROM auth_sessions WHERE user_id = "
                    "(SELECT id FROM users WHERE username = ?)",
                    (username.strip(),),
                )

    def delete_user(self, username: str) -> None:
        with self._lock, self._connect() as conn:
            if _would_orphan_admins(conn, username):
                raise AccountError(
                    "This is the only admin. Promote someone else before "
                    "deleting it."
                )
            if not conn.execute(
                "DELETE FROM users WHERE username = ?", (username.strip(),)
            ).rowcount:
                raise AccountError(f"No user called '{username}'.")

    # ── Authentication ──────────────────────────────────────────────────────

    def authenticate(self, username: str, password: str) -> User | None:
        """Check a password. None for wrong user, wrong password or disabled.

        One indistinguishable answer for all three on purpose: separate
        responses turn the endpoint into a way to enumerate who has an
        account here. A dummy hash runs when the user does not exist, so the
        response time does not answer the question either.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, username, password_hash, role, disabled "
                "FROM users WHERE username = ?",
                (username.strip(),),
            ).fetchone()

        if row is None:
            # Cost-matched to a real verification, so timing cannot separate
            # "no such user" from "wrong password".
            verify_password(password, hash_password("timing-equalizer"))
            return None
        if not verify_password(password, row["password_hash"]):
            return None
        if row["disabled"]:
            return None

        if needs_rehash(row["password_hash"]):
            # The user just proved the password, so this is the only moment
            # the plaintext is available to upgrade the stored cost.
            with self._lock, self._connect() as conn:
                conn.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?",
                    (hash_password(password), row["id"]),
                )

        return User(id=row["id"], username=row["username"], role=row["role"])

    def open_session(self, user: User,
                     ttl_seconds: float = SESSION_TTL_SECONDS) -> str:
        """Mint a session token. Returns the plaintext; only the hash is kept."""
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM auth_sessions WHERE expires_at < ?", (now,))
            conn.execute(
                "INSERT INTO auth_sessions (token_hash, user_id, created_at, "
                "expires_at) VALUES (?, ?, ?, ?)",
                (_token_hash(token), user.id, now, now + ttl_seconds),
            )
        return token

    def user_for_token(self, token: str) -> User | None:
        """Resolve a session token to its user, or None if it is not valid."""
        if not token:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT u.id, u.username, u.role, u.disabled "
                "FROM auth_sessions s JOIN users u ON u.id = s.user_id "
                "WHERE s.token_hash = ? AND s.expires_at > ?",
                (_token_hash(token), time.time()),
            ).fetchone()
        if row is None or row["disabled"]:
            return None
        return _row_to_user(row)

    def close_session(self, token: str) -> None:
        if not token:
            return
        with self._lock, self._connect() as conn:
            conn.execute(
                "DELETE FROM auth_sessions WHERE token_hash = ?",
                (_token_hash(token),),
            )

    def close_all_sessions(self, user_id: int) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))


# ── Helpers ─────────────────────────────────────────────────────────────────

def _token_hash(token: str) -> str:
    """SHA-256 of a session token.

    Not scrypt: a session token is 256 bits of randomness rather than a
    human-chosen password, so there is no dictionary to slow down, and a
    memory-hard hash on every single request would be pure latency.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _row_to_user(row) -> User | None:
    if row is None:
        return None
    return User(
        id=row["id"], username=row["username"], role=row["role"],
        disabled=bool(row["disabled"]) if "disabled" in row.keys() else False,
    )


def _would_orphan_admins(conn: sqlite3.Connection, username: str) -> bool:
    """Whether demoting/disabling/deleting this user removes the last admin."""
    row = conn.execute(
        "SELECT role FROM users WHERE username = ?", (username.strip(),)
    ).fetchone()
    if row is None or row["role"] != ROLE_ADMIN:
        return False
    remaining = conn.execute(
        "SELECT COUNT(*) FROM users WHERE role = ? AND disabled = 0 "
        "AND username != ?",
        (ROLE_ADMIN, username.strip()),
    ).fetchone()[0]
    return remaining == 0


def _clean_username(username: str) -> str:
    name = (username or "").strip()
    if not MIN_USERNAME <= len(name) <= MAX_USERNAME:
        raise AccountError(
            f"A username must be between {MIN_USERNAME} and {MAX_USERNAME} "
            f"characters."
        )
    if not all(c.isalnum() or c in "._-@" for c in name):
        raise AccountError(
            "A username may contain letters, digits, and . _ - @ only."
        )
    return name


def _check_password(password: str) -> None:
    if not MIN_PASSWORD <= len(password or "") <= MAX_PASSWORD:
        raise AccountError(
            f"A password must be at least {MIN_PASSWORD} characters "
            f"(and at most {MAX_PASSWORD})."
        )


def accounts_enabled() -> bool:
    """Whether this instance authenticates people rather than a shared secret.

    Read at call time so a test can toggle it. Requires an explicit opt-in:
    turning accounts on silently the moment a database file appeared would
    lock out an instance whose operator did not know it had one.
    """
    return os.getenv("LANA_ACCOUNTS", "false").lower() in ("1", "true", "yes")


def account_db_path() -> Path:
    """Where the user database lives. Beside the sessions, under the data dir."""
    explicit = os.getenv("LANA_ACCOUNTS_DB", "").strip()
    if explicit:
        return Path(explicit)
    return Path(os.getenv("LANA_DATA_DIR", "data")) / "accounts.db"


def build_account_store() -> AccountStore | None:
    """The store, or None when this instance does not use accounts."""
    if not accounts_enabled():
        return None
    return AccountStore(account_db_path())
