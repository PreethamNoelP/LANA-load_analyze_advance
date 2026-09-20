"""Optional encryption for the data LANA writes to disk.

What this protects against, and what it does not
------------------------------------------------
With ``LANA_PERSIST_SESSIONS=true``, uploaded data is written to
``LANA_DATA_DIR`` as Parquet. That is the user's own data on the user's own
disk, which is fine right up until the disk is a cloud volume, a backup, a
snapshot, or a laptop that gets stolen — at which point "at your filesystem's
protection level and no more" means "readable by whoever has the file".

Setting ``LANA_ENCRYPTION_KEY`` encrypts every persisted frame with
AES-256-GCM. That defeats **offline** access: a stolen volume, a leaked
backup, a decommissioned disk, a snapshot shared with the wrong account.

It does **not** defeat anyone who can read the running process's environment
or memory, because the key is right there. Encryption at rest never does. Any
description of this that implies otherwise is wrong, which is why the key is
deliberately *not* read from a file in the data directory: a key stored beside
the data it encrypts protects against nothing at all, and offering that as a
convenience would be offering a feature that only looks like security.

So the key comes from the environment — supplied by a secrets manager, a
systemd credential, a Docker secret, or a human at start-up.

Design
------
AES-GCM, not CBC or a hand-rolled scheme: it authenticates as well as
encrypts, so a modified file fails to decrypt rather than silently yielding
different data. Silent corruption in an analysis tool is worse than an error,
because the numbers would still look plausible.

A fresh 96-bit nonce per file, stored in front of the ciphertext. GCM is
catastrophically broken by nonce reuse, and a random nonce per write is the
one thing that must never be economised on here.

The header is versioned, so a future change of algorithm can be recognised
rather than producing a confusing decryption failure.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os

logger = logging.getLogger("lana.crypto")

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    CRYPTO_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only where the wheel is absent
    AESGCM = None
    CRYPTO_AVAILABLE = False

# Marks a file written by this module. Without it, a file encrypted by a
# future version would fail with a decryption error rather than a message
# saying which version wrote it.
MAGIC = b"LANA1\x00"
NONCE_BYTES = 12
KEY_BYTES = 32


class EncryptionError(Exception):
    """Encryption is configured but cannot be used, or a file will not open."""


def encryption_key() -> bytes | None:
    """The configured key, or None when encryption is off.

    Accepts a base64 or hex key of exactly 32 bytes, or any other string,
    which is stretched with SHA-256. The stretch is a convenience for a
    human-chosen key and is *not* a password KDF: a short passphrase here is a
    short passphrase, and the documentation says to generate a random key.
    """
    raw = os.getenv("LANA_ENCRYPTION_KEY", "").strip()
    if not raw:
        return None

    for decoder in (base64.b64decode, bytes.fromhex):
        try:
            candidate = decoder(raw)
        except Exception:
            continue
        if len(candidate) == KEY_BYTES:
            return candidate

    return hashlib.sha256(raw.encode("utf-8")).digest()


def encryption_enabled() -> bool:
    return encryption_key() is not None


def _cipher() -> AESGCM:
    key = encryption_key()
    if key is None:
        raise EncryptionError("No LANA_ENCRYPTION_KEY is configured.")
    if not CRYPTO_AVAILABLE:
        raise EncryptionError(
            "LANA_ENCRYPTION_KEY is set but the 'cryptography' package is not "
            "installed, so persisted data cannot be encrypted. Install it "
            "with `pip install cryptography`, or unset the key to store data "
            "unencrypted."
        )
    return AESGCM(key)


def encrypt(plaintext: bytes) -> bytes:
    """Encrypt with a fresh nonce. Layout: MAGIC | nonce | ciphertext+tag."""
    nonce = os.urandom(NONCE_BYTES)
    return MAGIC + nonce + _cipher().encrypt(nonce, plaintext, None)


def looks_encrypted(blob: bytes) -> bool:
    return blob[: len(MAGIC)] == MAGIC


def decrypt(blob: bytes) -> bytes:
    """Decrypt, or raise. Plaintext written before encryption was on passes through.

    That fallback is what lets encryption be switched on for an existing data
    directory without a migration step: old files still open, new ones are
    written encrypted. The reverse — a key removed while encrypted files
    remain — is a real error and says so, rather than handing back ciphertext
    that would fail as corrupt Parquet somewhere far from the cause.
    """
    if not looks_encrypted(blob):
        return blob
    if not encryption_enabled():
        raise EncryptionError(
            "This data was encrypted, but no LANA_ENCRYPTION_KEY is set. "
            "Restore the key to read it — without it the data cannot be "
            "recovered, which is the point."
        )
    nonce = blob[len(MAGIC): len(MAGIC) + NONCE_BYTES]
    payload = blob[len(MAGIC) + NONCE_BYTES:]
    try:
        return _cipher().decrypt(nonce, payload, None)
    except EncryptionError:
        raise
    except Exception as exc:
        raise EncryptionError(
            "This data could not be decrypted. Either LANA_ENCRYPTION_KEY is "
            "not the key it was written with, or the file has been altered."
        ) from exc


def describe() -> str:
    """One line for the startup posture log."""
    if not encryption_enabled():
        return "off"
    return "aes-256-gcm" if CRYPTO_AVAILABLE else "configured but unavailable"
