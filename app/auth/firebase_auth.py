"""Firebase Firestore authentication.

This module is entirely optional. If FIREBASE_CREDENTIALS_PATH is not set,
the app runs in guest mode — all analytics features work, auth is bypassed.
"""

from __future__ import annotations

import os
from typing import Optional

import bcrypt

_db = None
_init_attempted = False


def initialize_firebase(credentials_path: Optional[str] = None):
    """Initialize Firebase Admin SDK and return Firestore client.

    Returns None (silently) if credentials are not configured.
    """
    global _db, _init_attempted
    if _init_attempted:
        return _db
    _init_attempted = True

    cred_path = credentials_path or os.getenv("FIREBASE_CREDENTIALS_PATH", "")
    if not cred_path:
        return None

    try:
        import firebase_admin
        from firebase_admin import credentials, firestore

        if not firebase_admin._apps:
            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred)
        _db = firestore.client()
        return _db
    except Exception as e:
        print(f"[LANA] Firebase init failed: {e}")
        return None


def get_db():
    return _db


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(stored_hash: str, provided: str) -> bool:
    try:
        return bcrypt.checkpw(provided.encode(), stored_hash.encode())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# User operations
# ---------------------------------------------------------------------------

def login_user(db, email: str, password: str) -> bool:
    if db is None:
        return False
    try:
        doc = (
            db.collection("LANA")
            .document("Authentication")
            .collection("users")
            .document(email)
            .get()
        )
        if not doc.exists:
            return False
        data = doc.to_dict()
        return verify_password(data.get("Password", ""), password)
    except Exception:
        return False


def create_account(db, email: str, password: str, username: str) -> tuple[bool, str]:
    if db is None:
        return False, "Database not connected."
    if len(password) < 8:
        return False, "Password must be at least 8 characters."
    try:
        db.collection("LANA").document("Authentication").collection("users").document(
            email
        ).set(
            {
                "Email": email,
                "Password": hash_password(password),
                "Username": username,
            }
        )
        return True, "Account created successfully."
    except Exception as e:
        return False, str(e)