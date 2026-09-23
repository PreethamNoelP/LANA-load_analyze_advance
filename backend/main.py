import hashlib
import hmac
import json
import logging
import math
import os
import re
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import accounts as accounts_mod
from app import audit as audit_mod
from app import mailer as mailer_mod
from app import observability as obs
from app.analysis.regression import perform_linear_regression
from app.analysis.sql_engine import DUCKDB_AVAILABLE
from app.analysis.statistics import (
    analyze_correlations,
    compute_statistics,
    generate_context,
    generate_recommendations,
)
from app.config import config
from app.data import ingest
from app.data.cleaner import apply_cleaning, detect_issues
from app.data.profile import dataset_quality
from app.llm import get_provider
from app.llm.context import build_context
from app.llm.injection import scan_frame
from app.llm.sql_answer import SqlPlanningFailed, answer_with_sql
from app.llm.validation import capability_summary, validate_answer
from app.resources import HOST, UPLOAD_PEAK_MULTIPLIER, can_admit
from app.sources import (
    SourceError,
    SourceSpec,
    SourceUnavailable,
    available_sources,
    build_source,
)
from app.visualization.charts import CHART_TYPES, create_chart
from backend.coordination import build_coordinator
from backend.session_store import CLEANED, ORIGINAL, Session, SessionStore

obs.configure_logging()

try:
    from app.export.exporters import generate_pdf_report, generate_word_report
    EXPORT_OK = True
except ImportError:
    EXPORT_OK = False

logger = logging.getLogger("lana")

app = FastAPI(title="LANA API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Disabled unless LANA_AUTH_TOKEN is set — LANA has always assumed a single
# local user with no login. This exists for the case where that stops being
# true (e.g. running on a home server or a shared machine reachable by more
# than just you), not as a multi-tenant auth system: every request either
# carries the one shared token or it doesn't, there is no concept of "which
# user" beyond that. /health stays open so a container healthcheck or the
# frontend's own liveness poll doesn't need the token wired in separately.
# /metrics is exempt for the same reason — a scraper is infrastructure, not a
# user — and it exposes counts and latencies, never data or column names.
# /auth/status is exempt for a chicken-and-egg reason: a browser has to be
# able to ask "is a token required here?" before it can possibly have one.
# It reveals only whether auth is switched on, which is observable anyway from
# the 401 any other endpoint returns.
# /auth/login and /auth/logout are exempt for the same chicken-and-egg reason
# as /auth/status: a caller cannot present a credential to the endpoint whose
# job is to issue one. Logout is exempt so a browser holding a stale or
# revoked cookie can still clear it rather than being stuck with a credential
# the server no longer honours and no way to drop it.
# /auth/forgot-password and /auth/reset-password are exempt for the same
# reason as /auth/login: recovering a forgotten password is, definitionally,
# something you do while not signed in.
_AUTH_EXEMPT_PATHS = {
    "/health", "/metrics", "/auth/session", "/auth/status",
    "/auth/login", "/auth/logout",
    "/auth/forgot-password", "/auth/reset-password",
}

# The header a reverse proxy must set, alongside the configured identity
# header, before LANA trusts that identity header at all. This is what makes
# trusted-header auth safe to enable on a Docker network where other
# containers could otherwise forge X-Forwarded-* headers directly — see
# app.config.AppConfig.trusted_header_active and SECURITY.md.
TRUSTED_PROXY_SECRET_HEADER = "x-lana-proxy-secret"

# Name of the cookie holding a browser's proof of authentication.
#
# This replaces baking LANA_AUTH_TOKEN into the JavaScript bundle at build
# time, which was not access control at all: anyone who could load the page
# could read the token out of the bundle, and rotating it meant rebuilding the
# frontend image. The browser now POSTs the token once to /auth/session and
# receives an HttpOnly cookie, so the secret is never readable by page
# scripts, never in the bundle, and revocable by changing the server's token.
#
# HttpOnly blocks exfiltration via XSS. SameSite=Strict is what stops a
# cross-site request from riding the cookie, which matters because the API is
# served with allow_credentials=True.
AUTH_COOKIE_NAME = "lana_session"

# Requests per minute per principal, and the burst a client may spend at once.
# Generous for interactive use — a user clicking through tabs generates maybe
# twenty requests a minute — and low enough that a runaway script or a script
# kiddie with curl cannot occupy the machine.
RATE_CAPACITY = float(config.limits.rate_capacity)
RATE_REFILL_PER_SECOND = config.limits.rate_refill_per_second

# The LLM endpoints get their own, much tighter bucket on top of the general
# one. A question costs seconds of local GPU/CPU; everything else costs
# milliseconds, so one limit cannot be right for both.
LLM_RATE_CAPACITY = float(config.limits.llm_rate_capacity)
LLM_RATE_REFILL_PER_SECOND = config.limits.llm_rate_refill_per_second

_RATE_EXEMPT_PATHS = {"/health", "/metrics"}
_LLM_PATHS = ("/query", "/query/stream")

# A forgot-password request costs an email send against a real inbox, so it
# gets its own, far tighter bucket on top of the general one — see
# LimitsConfig.password_reset_rate_capacity.
PASSWORD_RESET_RATE_CAPACITY = float(config.limits.password_reset_rate_capacity)
PASSWORD_RESET_RATE_REFILL_PER_SECOND = config.limits.password_reset_rate_refill_per_second
_PASSWORD_RESET_PATHS = ("/auth/forgot-password",)

_coordinator = build_coordinator(
    config.limits.data_dir if config.limits.persist_sessions else None
)

# Durable record of consequential actions — data in, transformed, out, and
# refused. Follows persistence for the same reason the coordinator does: that
# is the setting where there is a data directory to write to and a deployment
# that outlives one process. See app/audit.py.
_audit = audit_mod.build_audit_log(
    config.limits.data_dir if config.limits.persist_sessions else None
)


def _record_audit(action: str, *, outcome: str = "ok", session_id: str | None = None,
                  principal: str | None = None, **detail) -> None:
    """Append one audit entry for the request being handled.

    The principal and request id come from the request-scoped ContextVars
    rather than parameters, so every call site records them correctly without
    having to remember to thread them through — the same argument
    ``_get_session`` makes about ownership.
    """
    _audit.record(audit_mod.AuditEntry(
        ts=time.time(),
        action=action,
        principal=principal if principal is not None else obs.principal_var.get(),
        outcome=outcome,
        session_id=session_id,
        request_id=obs.request_id_var.get(),
        detail=audit_mod.scrub(detail),
    ))


# Real accounts, when this instance uses them. None means it does not, and
# every path below falls back to the shared-token behaviour unchanged.
#
# Three authentication modes exist rather than two because removing the shared
# token would break every instance already running on it. They are tried in
# order of specificity: accounts, then token, then open.
_accounts = accounts_mod.build_account_store()


def accounts_active() -> bool:
    return _accounts is not None


def _header_identity(request: Request):
    """The user asserted by a trusted reverse proxy, or None.

    Only ever consulted when ``config.trusted_header_active`` — both the
    header name and the shared secret are configured, so this fails closed
    rather than trusting a header the moment someone sets one env var. The
    secret comparison happens *before* the identity header is even read: a
    request missing or failing the secret gets no identity, full stop,
    regardless of what it claims in the identity header.
    """
    if not config.trusted_header_active:
        return None
    supplied_secret = request.headers.get(TRUSTED_PROXY_SECRET_HEADER, "")
    if not hmac.compare_digest(supplied_secret, config.trusted_header_secret):
        return None
    identity = request.headers.get(config.trusted_header_name, "").strip()
    if not identity:
        return None
    return _accounts.get_or_create_by_external_id(
        identity, default_role=config.trusted_header_default_role
    )


def _current_user(request: Request):
    """The signed-in user for this request, or None.

    Trusted-header identity is checked first when configured, so a request
    arriving through the proxy never needs a session cookie at all. This is
    additive, not exclusive: a local password sign-in still works even with
    header mode configured, so an admin retains a way in if the proxy is
    ever down — the same reasoning SECURITY.md gives for keeping token mode
    around after accounts mode shipped.

    Absent header mode, reads the same cookie the shared-token mode uses.
    The value means different things in the two modes — a session token
    here, the shared secret there — which is fine because only one mode is
    ever active.
    """
    if _accounts is None:
        return None
    header_user = _header_identity(request)
    if header_user is not None:
        return header_user
    return _accounts.user_for_token(_supplied_token(request))


def _principal(request: Request) -> str:
    """A stable, non-secret identity for the caller.

    With auth on, every caller shares one token, so the principal is a hash of
    it — the same for everyone, which is correct: they *are* the same
    principal, and that is exactly what SECURITY.md says the token is. The
    hash rather than the token itself means a log line, a metric label or a
    rate-limit key can never carry the secret.

    With auth off, the client address stands in, so a rate limit still
    distinguishes two machines on a LAN even though neither authenticates.
    """
    if _accounts is not None:
        user = _current_user(request)
        if user is not None:
            # The identity that makes session ownership mean something between
            # two colleagues rather than between two shared-secret holders.
            return user.principal
        # An unauthenticated caller under accounts mode still needs a
        # rate-limit key, and it must not be a single shared bucket that one
        # attacker can exhaust for everyone. The address is the only thing
        # known about them.
        client = request.client.host if request.client else "unknown"
        return f"anon:{client}"

    if config.auth_token:
        supplied = _supplied_token(request)
        if supplied:
            return "tok:" + hashlib.sha256(supplied.encode()).hexdigest()[:16]
    client = request.client.host if request.client else "unknown"
    return f"ip:{client}"


def _supplied_token(request: Request) -> str:
    """The token from an Authorization header or the session cookie."""
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:]
    return request.cookies.get(AUTH_COOKIE_NAME, "")


def _token_ok(supplied: str) -> bool:
    # Constant-time comparison: a length/early-exit-timing side channel is
    # a real (if minor) way to help an attacker guess the token.
    return bool(config.auth_token) and hmac.compare_digest(
        supplied, config.auth_token
    )


def _path_template(path: str) -> str:
    """Collapse ids out of a path so metrics have bounded cardinality.

    ``/session/9f3c…`` and ``/session/1a2b…`` are the same endpoint. Labelling
    them separately would mint one time series per upload, which is the
    classic way to take down a Prometheus server with your own instrumentation.
    """
    parts = []
    for part in path.split("/"):
        if not part:
            continue
        if _looks_like_id(part):
            parts.append("{id}")
        else:
            parts.append(part)
    return "/" + "/".join(parts) if parts else "/"


_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _looks_like_id(part: str) -> bool:
    return bool(_UUID_RE.match(part)) or (len(part) > 16 and not part.isalpha())


@app.middleware("http")
async def _observe_request(request: Request, call_next):
    """Assign a request id, time the request, and record it.

    Outermost middleware, so the id exists before anything else can log and
    the timing covers auth and rate limiting rather than just the handler.
    """
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    id_token = obs.request_id_var.set(request_id)
    principal = _principal(request)
    principal_token = obs.principal_var.set(principal)
    template = _path_template(request.url.path)

    obs.http_in_flight.inc()
    started = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        elapsed = time.perf_counter() - started
        obs.http_in_flight.dec()
        obs.http_latency.observe(elapsed, method=request.method, path=template)
        obs.http_requests.inc(
            method=request.method, path=template, status=str(status)
        )
        # One line per request, carrying the id every other line of this
        # request also carries. This is the record that makes "why was
        # yesterday slow" answerable at all.
        logger.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "template": template,
                "status": status,
                "duration_ms": round(elapsed * 1000, 2),
            },
        )
        obs.principal_var.reset(principal_token)
        obs.request_id_var.reset(id_token)


@app.middleware("http")
async def _require_auth_token(request: Request, call_next):
    if _accounts is not None and request.url.path not in _AUTH_EXEMPT_PATHS:
        if _current_user(request) is None:
            logger.warning(
                "unauthenticated request", extra={"path": request.url.path}
            )
            _record_audit(
                audit_mod.AUTH_FAILED, outcome="denied",
                principal=_principal(request), path=request.url.path,
            )
            return JSONResponse(
                {"detail": "Sign in to use this instance."}, status_code=401
            )
        return await call_next(request)

    if config.auth_token and request.url.path not in _AUTH_EXEMPT_PATHS:
        if not _token_ok(_supplied_token(request)):
            logger.warning(
                "unauthenticated request", extra={"path": request.url.path}
            )
            # A rejected credential is the event an operator most wants a
            # durable record of, and the one a log rotation is most likely to
            # have discarded by the time anyone asks.
            _record_audit(
                audit_mod.AUTH_FAILED, outcome="denied",
                principal=_principal(request), path=request.url.path,
            )
            return JSONResponse(
                {"detail": "Missing or invalid auth token."}, status_code=401
            )
    return await call_next(request)


@app.middleware("http")
async def _rate_limit(request: Request, call_next):
    """Token-bucket limiting, per principal, shared across workers.

    SECURITY.md listed "No rate limiting" under known limitations. It was a
    real gap: every endpoint here does real work — parsing a file, rendering a
    chart, running a correlation scan — and none of it was bounded by anything
    but the LLM semaphore.
    """
    if request.url.path in _RATE_EXEMPT_PATHS:
        return await call_next(request)

    principal = _principal(request)
    decision = _coordinator.check_rate(
        f"http:{principal}", RATE_CAPACITY, RATE_REFILL_PER_SECOND
    )
    if not decision.allowed:
        obs.rate_limited.inc(scope="http")
        return JSONResponse(
            {
                "detail": (
                    "Too many requests. Slow down and try again in a moment."
                )
            },
            status_code=429,
            headers={"Retry-After": str(max(1, int(decision.retry_after_seconds)))},
        )

    if request.url.path in _LLM_PATHS:
        llm_decision = _coordinator.check_rate(
            f"llm:{principal}", LLM_RATE_CAPACITY, LLM_RATE_REFILL_PER_SECOND
        )
        if not llm_decision.allowed:
            obs.rate_limited.inc(scope="llm")
            return JSONResponse(
                {
                    "detail": (
                        "You are asking questions faster than the model can "
                        "answer them. Wait a moment and try again."
                    )
                },
                status_code=429,
                headers={
                    "Retry-After": str(max(1, int(llm_decision.retry_after_seconds)))
                },
            )

    if request.url.path in _PASSWORD_RESET_PATHS:
        reset_decision = _coordinator.check_rate(
            f"pwreset:{principal}", PASSWORD_RESET_RATE_CAPACITY,
            PASSWORD_RESET_RATE_REFILL_PER_SECOND,
        )
        if not reset_decision.allowed:
            obs.rate_limited.inc(scope="password_reset")
            return JSONResponse(
                {"detail": "Too many reset requests. Try again later."},
                status_code=429,
                headers={
                    "Retry-After": str(max(1, int(reset_decision.retry_after_seconds)))
                },
            )

    return await call_next(request)


class AuthReq(BaseModel):
    token: str = Field(min_length=1, max_length=512)


class LoginReq(BaseModel):
    username: str = Field(min_length=1, max_length=accounts_mod.MAX_USERNAME)
    password: str = Field(min_length=1, max_length=accounts_mod.MAX_PASSWORD)


class ForgotPasswordReq(BaseModel):
    username: str = Field(min_length=1, max_length=accounts_mod.MAX_USERNAME)


class ResetPasswordReq(BaseModel):
    token: str = Field(min_length=1, max_length=512)
    new_password: str = Field(min_length=1, max_length=accounts_mod.MAX_PASSWORD)


def _set_auth_cookie(response: JSONResponse, value: str, request: Request,
                     max_age: int) -> None:
    """The one place a credential cookie is written, so its flags cannot drift.

    HttpOnly blocks exfiltration by an XSS payload. SameSite=Strict is what
    stops a cross-site request riding the cookie, which matters because the
    API is served with allow_credentials=True. Secure is set only when the
    request arrived over HTTPS — forcing it would make the cookie unsettable
    on the documented plain-HTTP LAN setup, with no error the user could see.
    """
    response.set_cookie(
        AUTH_COOKIE_NAME, value, httponly=True, samesite="strict",
        secure=request.url.scheme == "https", max_age=max_age, path="/",
    )


@app.post("/auth/login")
def login(req: LoginReq, request: Request):
    """Sign in with a username and password. Accounts mode only.

    Rate limited by the same per-principal bucket as everything else, which
    under accounts mode keys unauthenticated callers by address — so password
    guessing is bounded per source rather than globally.
    """
    if _accounts is None:
        raise HTTPException(
            400,
            "This instance does not use accounts. "
            "It is either open or uses a shared token.",
        )

    user = _accounts.authenticate(req.username, req.password)
    if user is None:
        # One message for wrong user, wrong password and disabled account.
        # Distinguishing them tells an attacker which usernames exist here.
        logger.warning("failed sign-in", extra={"username": req.username[:64]})
        _record_audit(
            audit_mod.AUTH_FAILED, outcome="denied",
            principal=_principal(request), username=req.username[:64],
        )
        raise HTTPException(401, "That username and password did not match.")

    token = _accounts.open_session(user)
    response = JSONResponse({
        "required": True, "authenticated": True, "user": user.to_dict(),
    })
    _set_auth_cookie(response, token, request, accounts_mod.SESSION_TTL_SECONDS)
    logger.info("sign-in", extra={"username": user.username})
    _record_audit(
        audit_mod.AUTH_SUCCEEDED, principal=user.principal,
        username=user.username, role=user.role,
    )
    return response


@app.post("/auth/session")
def open_auth_session(req: AuthReq, request: Request):
    """Exchange the shared token for an HttpOnly cookie.

    The browser calls this once. Afterwards the cookie authenticates it and
    the token never touches JavaScript again — which is the whole point, since
    the previous arrangement shipped the token inside the bundle.
    """
    if not config.auth_token:
        return {"required": False, "authenticated": True}
    if not _token_ok(req.token):
        logger.warning("failed auth exchange")
        raise HTTPException(401, "That token is not valid.")

    response = JSONResponse({"required": True, "authenticated": True})
    response.set_cookie(
        AUTH_COOKIE_NAME,
        config.auth_token,
        httponly=True,
        samesite="strict",
        # Set only over HTTPS when the request arrived over it. Forcing it on
        # would break the documented plain-HTTP LAN setup by making the cookie
        # unsettable, with no error the user could see.
        secure=request.url.scheme == "https",
        max_age=config.limits.auth_cookie_max_age,
        path="/",
    )
    return response


@app.get("/auth/status")
def auth_status(request: Request):
    """Which authentication mode this instance uses, and who the caller is.

    ``mode`` is reported rather than left to be inferred, because the sign-in
    form differs between them: accounts mode needs a username and password,
    token mode needs one secret, and open mode needs nothing. A client that
    guessed would show the wrong form.
    """
    if _accounts is not None:
        user = _current_user(request)
        return {
            "mode": "accounts",
            "required": True,
            "authenticated": user is not None,
            "user": user.to_dict() if user else None,
        }
    if not config.auth_token:
        return {"mode": "open", "required": False, "authenticated": True}
    return {
        "mode": "token",
        "required": True,
        "authenticated": _token_ok(_supplied_token(request)),
        "user": None,
    }


@app.post("/auth/logout")
def close_auth_session(request: Request):
    # Revoked server-side under accounts mode, not merely forgotten by the
    # browser: a token that stays valid after sign-out is still a working
    # credential for anyone who captured it.
    if _accounts is not None:
        _accounts.close_session(_supplied_token(request))
    response = JSONResponse({"authenticated": False})
    response.delete_cookie(AUTH_COOKIE_NAME, path="/")
    return response


_FORGOT_PASSWORD_RESPONSE = {
    "detail": "If that account exists, a reset link has been sent to it.",
}


@app.post("/auth/forgot-password")
def forgot_password(req: ForgotPasswordReq, request: Request):
    """Request a password-reset email. Accounts mode only.

    Always returns the same response whether or not the username exists —
    the alternative turns this endpoint into a way to enumerate who has an
    account here, the exact thing /auth/login already avoids. Rate limited
    by its own, much tighter bucket (see PASSWORD_RESET_RATE_CAPACITY) so it
    cannot be used to flood a real inbox.
    """
    if _accounts is None:
        raise HTTPException(
            400,
            "This instance does not use accounts, so there is no password "
            "to reset.",
        )

    token = _accounts.create_password_reset(req.username)
    if token is not None:
        user = _accounts.get_by_username(req.username)
        reset_link = f"{config.smtp.public_url}/reset-password?token={token}"
        sent = mailer_mod.send_password_reset_email(config.smtp, req.username, reset_link)
        logger.info(
            "password reset requested", extra={"username": req.username[:64], "sent": sent}
        )
        _record_audit(
            audit_mod.PASSWORD_RESET_REQUESTED,
            principal=user.principal if user else _principal(request),
            username=req.username[:64],
        )
    return _FORGOT_PASSWORD_RESPONSE


@app.post("/auth/reset-password")
def reset_password(req: ResetPasswordReq, request: Request):
    """Redeem a reset token for a new password. Accounts mode only.

    Invalidates every existing session for the account (AccountStore.
    consume_password_reset), the same as a password change made from being
    signed in — a reset is usually a response to the old password being
    forgotten or compromised, and either way stale sessions should not
    survive it.
    """
    if _accounts is None:
        raise HTTPException(
            400,
            "This instance does not use accounts, so there is no password "
            "to reset.",
        )

    try:
        user = _accounts.consume_password_reset(req.token, req.new_password)
    except accounts_mod.AccountError as exc:
        raise HTTPException(400, str(exc)) from exc

    if user is None:
        raise HTTPException(400, "That reset link is invalid or has expired.")

    logger.info("password reset completed", extra={"username": user.username})
    _record_audit(
        audit_mod.PASSWORD_RESET_COMPLETED, principal=user.principal,
        username=user.username,
    )
    return {"detail": "Your password has been reset. Sign in with your new password."}


# A bearer token a scraper presents to read /metrics. Empty (the default)
# keeps the endpoint open, which is right for the local case and for a
# Prometheus on the same host: a scraper is infrastructure, not a user, and
# nothing here names a column, a value or a filename.
#
# It is worth having because "reveals load and error rates, not data" is only
# true of the *content*. Request volume and upload sizes over time are
# themselves information in some deployments, and an operator who judges that
# should not have to reach for a proxy to act on it.
METRICS_TOKEN = os.getenv("LANA_METRICS_TOKEN", "")


@app.get("/metrics")
def metrics(request: Request):
    """Prometheus exposition. Counts and latencies only — never data.

    Open by default, like /health. Set LANA_METRICS_TOKEN to require
    `Authorization: Bearer <token>` if load patterns are sensitive where this
    runs.
    """
    if METRICS_TOKEN:
        header = request.headers.get("authorization", "")
        supplied = header[7:] if header.lower().startswith("bearer ") else ""
        if not hmac.compare_digest(supplied, METRICS_TOKEN):
            return JSONResponse(
                {"detail": "Missing or invalid metrics token."}, status_code=401
            )
    stats = _store.stats()
    obs.sessions_active.set(float(stats["sessions"]))
    obs.sessions_bytes.set(float(stats["frame_mb"]) * 1024 ** 2)
    return Response(
        content=obs.REGISTRY.render(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )

# All read from app.config.config.limits — the single place that reads these
# env vars, host-adaptive defaults included. Kept as module-level names here
# since they're referenced throughout this file.
MAX_UPLOAD_MB = config.limits.max_upload_mb
MAX_SESSIONS = config.limits.max_sessions
MAX_SESSION_MB = config.limits.max_session_mb
SESSION_TTL_SECONDS = config.limits.session_ttl_seconds

# Caps how many LLM requests run at once — a local Ollama model serves one
# request at a time well; without this, a burst of concurrent visitors all
# queue behind it and every answer appears to hang.
#
# Enforced through the coordinator rather than a threading.Semaphore, because
# a semaphore is process-local: `uvicorn --workers 4` produced four caps of
# two instead of one cap of two, i.e. four times the intended load on one
# Ollama instance. See backend/coordination.py.
MAX_CONCURRENT_LLM = config.limits.max_concurrent_llm
_LLM_BUSY_MSG = "The AI is busy answering other questions right now — try again in a moment."
_LLM_DOWN_MSG = (
    "The local AI model is not reachable. Check that Ollama is running "
    "(`ollama serve`) and that the model in your .env has been pulled "
    "(`ollama pull <model>`). Every other tab — profiling, cleaning, charts, "
    "statistics — works without it."
)


# Provider error text is written by the upstream endpoint, not by LANA, and it
# is echoed to the browser. Left raw it discloses the operator's configuration
# — a 404 from a mistyped base URL returns the provider's hostname and route to
# every visitor. Strip anything that identifies the endpoint or looks like a
# credential, keep the part that actually helps ("model not found").
_URL_RE = re.compile(r"https?://\S+")
_ADDR_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b|\blocalhost:\d+\b", re.I)
_TOKEN_RE = re.compile(r"\b(?:sk|gsk|ghp|xoxb|Bearer)[-_ ][A-Za-z0-9._-]{8,}", re.I)
_MAX_ERROR_CHARS = 300


def _sanitize_llm_error(text: str) -> str:
    for pattern, repl in ((_URL_RE, "[endpoint]"), (_ADDR_RE, "[address]"),
                          (_TOKEN_RE, "[redacted]")):
        text = pattern.sub(repl, text)
    text = " ".join(text.split())
    return text[:_MAX_ERROR_CHARS] + ("…" if len(text) > _MAX_ERROR_CHARS else "")


def _llm_error(exc: Exception) -> tuple[int, str]:
    """Map a provider exception to an HTTP status and a safe, actionable message.

    A connection failure is by far the most common one, has nothing to do with
    the question asked, and is fixable by the user — so it becomes a 503 with
    instructions rather than a 500 carrying a raw traceback string. Anything
    else is a genuine fault, reported with its detail sanitised.
    """
    text = str(exc)
    if any(k in text.lower() for k in ("connect", "refused", "timed out", "timeout",
                                       "unreachable", "no route", "actively refused")):
        return 503, _LLM_DOWN_MSG
    # Full detail stays in the server log for whoever runs the process.
    logger.warning("LLM request failed", exc_info=exc)
    return 500, f"LLM error: {_sanitize_llm_error(text)}"

_store = SessionStore(
    max_sessions=MAX_SESSIONS,
    max_bytes=MAX_SESSION_MB * 1024 * 1024,
    ttl_seconds=SESSION_TTL_SECONDS,
    persist_dir=config.limits.data_dir if config.limits.persist_sessions else None,
)


def _log_security_posture() -> None:
    """State this process's security-relevant configuration once, at startup.

    Every one of these is a deliberate choice with a real consequence, and
    every one of them is invisible at runtime — an operator who inherits a
    running LANA has no way to tell whether the person who deployed it turned
    the SSRF guard off. One line at boot, in the log they already collect,
    answers that without them having to read the environment of a container.

    Logged at module import rather than through a startup event: import
    happens exactly once per worker, which is what "startup" means here, and
    it avoids FastAPI's deprecated ``on_event`` hook.
    """
    from app import crypto
    from app.sources.files import allowed_roots
    from app.sources.security import private_urls_allowed

    if _accounts is not None:
        auth_mode = "accounts+sso" if config.trusted_header_active else "accounts"
    elif config.auth_token:
        auth_mode = "token"
    else:
        auth_mode = "open"

    posture = {
        "auth": auth_mode,
        "persist_sessions": config.limits.persist_sessions,
        "encryption_at_rest": crypto.describe(),
        "audit": bool(getattr(_audit, "enabled", False)),
        "sql_grounding": SQL_GROUNDING_ENABLED,
        "private_source_urls": private_urls_allowed(),
        "file_source_roots": len(allowed_roots()),
        "metrics_auth": bool(METRICS_TOKEN),
        "llm_local": config.llm.provider == "ollama"
        and _is_local_host(config.llm.ollama_host),
        "cors_origins": len(config.allowed_origins),
        "password_reset": config.smtp.configured,
    }
    logger.info("security posture", extra=posture)

    # The one combination that is a live exposure rather than a choice: no
    # token, and CORS opened to something other than the local dev defaults,
    # which is what an operator does when they are serving LANA to a browser
    # somewhere else. Warned about specifically, because the general startup
    # line above would be lost in a log the first time it mattered.
    if auth_mode == "open" and any(
        "localhost" not in origin and "127.0.0.1" not in origin
        for origin in config.allowed_origins
    ):
        logger.warning(
            "LANA is configured for a non-local origin but LANA_AUTH_TOKEN is "
            "not set: anyone who can reach this port can upload data, read "
            "sessions and occupy the model. See SECURITY.md."
        )


# ── Serialisation helpers ─────────────────────────────────────────────────────

def _clean(val):
    if isinstance(val, np.generic):
        val = val.item()
    # `==` is unsafe here — pd.NaT == pd.NaT is False by design — so use `is`.
    if val is pd.NaT or val is pd.NA:
        return None
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return None
    return val


def _jsonable(obj):
    """Recursively strip NaN/Inf and numpy scalars from a nested structure.

    Responses now carry nested profiles, ledgers and diagnostics; a single
    NaN anywhere inside would otherwise be serialised as the literal `NaN`,
    which is not valid JSON and fails to parse in the browser.
    """
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(v) for v in obj]
    return _clean(obj)


def _clean_record(rec: dict) -> dict:
    return {k: _clean(v) for k, v in rec.items()}


def _preview(df: pd.DataFrame, rows: int = 8) -> list[dict]:
    return [_clean_record(r) for r in df.head(rows).to_dict(orient="records")]


# ── Session accessors ─────────────────────────────────────────────────────────

@contextmanager
def _llm_slot():
    """Hold one of the process-wide LLM slots, or raise 429.

    The holder id is unique per call rather than per principal, so two
    questions from the same browser take two slots — which is the point, since
    each occupies the model independently.
    """
    holder = f"{os.getpid()}:{uuid.uuid4().hex[:12]}"
    if not _coordinator.acquire_slot(holder, MAX_CONCURRENT_LLM):
        obs.llm_rejected.inc(reason="busy")
        raise HTTPException(429, _LLM_BUSY_MSG)
    try:
        yield
    finally:
        _coordinator.release_slot(holder)


def _get_session(session_id: str, request: Request | None = None) -> Session:
    """Fetch a session, enforcing ownership where there is any.

    Before this, any caller who knew (or guessed) a session id could read that
    session — SECURITY.md listed "No per-session ownership" as a known
    limitation. A session now records the principal that created it, and a
    different principal gets the same 404 a missing session gets.

    404 rather than 403 on purpose: a 403 confirms the id exists, which turns
    the endpoint into an oracle for enumerating other people's sessions. The
    unowned case — every session created before this existed, and every
    session in a no-auth single-user install — skips the check entirely, so
    the local default is unchanged.

    The caller's identity comes from the request-scoped ContextVar rather than
    a ``request`` argument, deliberately: threading a parameter through all
    eighteen endpoints would mean ownership is enforced only where someone
    remembered to pass it, and the one that gets forgotten is the hole. The
    ``request`` parameter is kept for callers that have one to hand and for
    tests that want to be explicit.
    """
    session = _store.get(session_id)
    if session is None:
        raise HTTPException(404, "Session not found — upload a dataset first.")

    caller = _principal(request) if request is not None else obs.principal_var.get()
    if session.owner and caller and session.owner != caller:
        logger.warning(
            "session ownership mismatch", extra={"session_id": session_id}
        )
        _record_audit(
            audit_mod.ACCESS_DENIED, outcome="denied", session_id=session_id,
            principal=caller, reason="not the owner",
        )
        raise HTTPException(404, "Session not found — upload a dataset first.")
    return session


def _original(session_id: str) -> pd.DataFrame:
    """The untouched uploaded frame. Never overwritten by cleaning."""
    return _get_session(session_id).raw


def _session(session_id: str) -> pd.DataFrame:
    """The frame for the session's active version (original or cleaned)."""
    return _get_session(session_id).active


# ── Routes ────────────────────────────────────────────────────────────────────

# The LLM reachability probe is cached, because /health is called by a
# container healthcheck every 30s, by the frontend's liveness poll, and by
# anything watching the service — and when Ollama is *down*, each probe pays a
# full connection timeout.
#
# Measured, not assumed: eval/load_test.py with Ollama stopped reported
# /health at p95 7.9s and p99 8.0s under 16 concurrent clients, by far the
# slowest endpoint in the application. A liveness endpoint that takes eight
# seconds precisely when a dependency is down is a healthcheck that fails the
# container for the wrong reason.
#
# Five seconds is short enough that "I just started Ollama" is reflected
# almost immediately, and long enough that a burst of health polls costs one
# probe rather than one each.
_LLM_PROBE_TTL_SECONDS = 5.0
_llm_probe_cache: tuple[float, dict] | None = None
_llm_probe_lock = threading.Lock()


def reset_llm_probe_cache() -> None:
    """Forget the cached reachability result.

    Exists for tests, which swap the provider and then expect /health to
    reflect the swap. Without it the cache makes those tests depend on
    execution order and elapsed wall-clock, which is the kind of flake that
    gets a suite ignored.
    """
    global _llm_probe_cache
    _llm_probe_cache = None


_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "0.0.0.0")


def _is_local_host(url: str) -> bool:
    """Whether a provider URL points at this machine.

    host.docker.internal counts: it resolves to the host running the
    container, which is still the user's own machine and is what
    docker-compose.yml configures by default.
    """
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    return host in _LOCAL_HOSTS or host == "host.docker.internal"


def _llm_status() -> dict:
    """Reachability of the configured provider, cached briefly."""
    global _llm_probe_cache

    now = time.monotonic()
    cached = _llm_probe_cache
    if cached is not None and now - cached[0] < _LLM_PROBE_TTL_SECONDS:
        return cached[1]

    # Non-blocking: a caller that arrives while another thread is probing gets
    # the previous answer rather than queueing behind a connection timeout.
    # Staleness here is bounded by the TTL and is the entire point.
    if not _llm_probe_lock.acquire(blocking=False):
        return cached[1] if cached is not None else {
            "available": None, "name": None, "checking": True,
        }
    try:
        try:
            provider = get_provider()
            status = {
                "available": provider.is_available(),
                "name": provider.name,
                # Stated rather than left to be inferred from the provider
                # name. "100% local, no data leaves your machine" is LANA's
                # headline claim and it stops being true the moment someone
                # points LLM_PROVIDER at a hosted endpoint — which is a
                # legitimate thing to do, and exactly why it should be visible
                # in the interface instead of only in a .env file nobody
                # rereads.
                "local": config.llm.provider == "ollama"
                and _is_local_host(config.llm.ollama_host),
            }
        except Exception as e:
            status = {
                "available": False, "name": None,
                "error": _sanitize_llm_error(str(e)),
            }
        _llm_probe_cache = (time.monotonic(), status)
        return status
    finally:
        _llm_probe_lock.release()


@app.get("/health")
def health():
    """Liveness plus the resource picture the limits were derived from.

    Exposed because the limits are no longer constants a reader can look up in
    the source — they depend on the machine. When an upload is refused for
    being too large, this is where the user sees why.

    Also the only place in the running app that proactively checks whether
    the configured LLM is actually reachable, rather than waiting for a user
    to ask a question and hit a 503. A misconfigured LLM_PROVIDER value is
    caught here too, instead of surfacing as an unhandled error on first use.
    """
    return {
        "ok": True,
        "sessions": _store.stats(),
        "host": HOST.to_dict(),
        "llm": _llm_status(),
        "limits": {
            "max_upload_mb": MAX_UPLOAD_MB,
            "max_session_mb": MAX_SESSION_MB,
            "max_sessions": MAX_SESSIONS,
            "session_ttl_seconds": SESSION_TTL_SECONDS,
            "max_concurrent_llm": MAX_CONCURRENT_LLM,
        },
    }


@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    ext = Path(file.filename).suffix.lower()
    # `.xls` is deliberately absent. It was accepted here for a long time and
    # could never actually be parsed: the legacy binary format needs `xlrd`,
    # which is not a dependency, so those uploads died at the parser with
    # "Missing optional dependency" — an advertised format that never worked.
    # Refusing it here with a fix the user can act on beats promising it.
    if ext == ".xls":
        raise HTTPException(
            400,
            "The legacy '.xls' format is not supported. Open it in Excel or "
            "LibreOffice and save it as '.xlsx' (or export it as CSV), then "
            "upload that.",
        )
    if ext not in (".csv", ".xlsx", ".json"):
        raise HTTPException(400, f"Unsupported type '{ext}'. Use CSV, Excel (.xlsx), or JSON.")

    # The body streams into a spooled temp file rather than accumulating in a
    # list and then being joined. The old path held the payload three times
    # over (chunk list, joined bytes, BytesIO) before pandas even started; on
    # a 93 MB CSV that alone accounted for most of a measured 590 MB peak.
    # A spool keeps small uploads in RAM and lets large ones spill to disk, so
    # the memory ceiling stops scaling with the file size.
    limit_bytes = MAX_UPLOAD_MB * 1024 * 1024
    try:
        spool, total = await ingest.spool_upload(
            file.read, limit_bytes, f"{MAX_UPLOAD_MB} MB"
        )
    except ingest.UploadTooLarge as e:
        raise HTTPException(413, f"{e} Set LANA_MAX_UPLOAD_MB to override.") from e

    try:
        # Admission is decided before the parse, not after it. Projecting the
        # frame's cost means an unaffordable file is refused with a number the
        # user can act on, rather than the process being killed halfway
        # through materialising it.
        #
        # Every format goes through this, not just CSV. Excel and JSON cannot
        # be sampled the way CSV can, so their projections are coarser (see
        # ingest.project_frame_bytes) — but a coarse gate on the formats that
        # can carry a decompression bomb beats the previous arrangement, where
        # an .xlsx declaring gigabytes of sheet XML was admitted unexamined
        # purely because it compressed down under the upload limit.
        projection = await run_in_threadpool(
            ingest.project_frame_bytes, spool, ext, total
        )
        if projection is not None:
            budget = MAX_SESSION_MB * 1024 * 1024
            if projection.frame_bytes > budget:
                projected_mb = projection.frame_bytes / 1024 ** 2
                scale = f" ({projection.rows:,} rows)" if projection.rows else ""
                raise HTTPException(
                    413,
                    f"This file would need about {projected_mb:,.0f} MB of memory"
                    f"{scale} — {projection.basis} — and the budget on this machine "
                    f"is {MAX_SESSION_MB:,} MB. Upload a subset of the columns or "
                    f"rows, or raise LANA_MAX_SESSION_MB if you have headroom.",
                )
            # Second gate, against the machine's state *now* rather than at
            # startup. Refusing here keeps LANA from being the process that
            # pushes a laptop into swapping.
            needed = int(projection.frame_bytes * UPLOAD_PEAK_MULTIPLIER)
            ok, free = can_admit(needed)
            if not ok:
                raise HTTPException(
                    503,
                    f"Not enough free memory right now: parsing this file needs "
                    f"roughly {needed / 1024 ** 2:,.0f} MB and only "
                    f"{free / 1024 ** 2:,.0f} MB is free. Close some applications "
                    f"and try again, or upload a smaller extract.",
                )

        try:
            # Parsing runs in a worker thread — pandas' readers are synchronous
            # and can take seconds on large files, which would otherwise block
            # every other request sharing this process's event loop.
            df, report = await run_in_threadpool(
                ingest.read_frame, spool, ext, total, ingest.spilled(spool)
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(400, f"Could not parse file: {e}") from e
    finally:
        # Releases the spool's memory, and deletes its backing file if it
        # spilled. Nothing is left on disk after the request.
        spool.close()

    if df.empty:
        raise HTTPException(400, "The file parsed successfully but contains no rows.")

    sid = str(uuid.uuid4())
    session = _store.create(sid, file.filename, df, owner=obs.principal_var.get())
    _store.persist(session)
    obs.uploads.inc(kind="file", outcome="ok")
    obs.upload_rows.observe(float(len(df)))
    logger.info(
        "upload complete",
        extra={"session_id": sid, "rows": len(df), "columns": len(df.columns)},
    )
    _record_audit(
        audit_mod.DATA_LOADED, session_id=sid, kind="file",
        label=file.filename, rows=len(df), columns=len(df.columns),
    )

    # Profiling is the first thing a data scientist does; surfacing it at
    # upload means the user sees what they are working with before they act.
    # Cached on the session, so the six other places that need these profiles
    # read them instead of spending another full pass over the frame.
    profiles = await run_in_threadpool(session.profiles)
    quality = dataset_quality(profiles, len(df))

    # Text in the data that reads like an instruction to a model. This does
    # not block anything and does not claim to have neutralised the risk —
    # see app/llm/injection.py on why nothing can. It tells the person whose
    # data it is, which is the defence that actually works: someone who knows
    # a cell says "ignore previous instructions" reads the answers very
    # differently from someone who does not.
    injection = await run_in_threadpool(scan_frame, df)
    if injection.found:
        logger.warning(
            "dataset contains instruction-shaped text",
            extra={"session_id": sid, "findings": len(injection.findings)},
        )

    return _jsonable({
        "session_id": sid,
        "filename": file.filename,
        "injection": injection.to_dict(),
        "rows": len(df),
        "columns": df.columns.tolist(),
        "numeric_columns": df.select_dtypes("number").columns.tolist(),
        "preview": _preview(df),
        "quality": quality,
        "profiles": {name: p.to_dict() for name, p in profiles.items()},
        # What ingest actually did, so a large upload can explain itself.
        "ingest": report.to_dict(),
    })


# ── Connector-backed sources ─────────────────────────────────────────────────
# Everything below produces a DataFrame and then joins the *same* pipeline the
# upload route uses: admission against the host budget, session creation,
# profiling, quality scoring. Nothing downstream of _admit_frame knows or cares
# which connector produced the rows, which is what makes "same capabilities
# regardless of source" true rather than aspirational.


class SourceSpecReq(BaseModel):
    kind: str = Field(max_length=50)
    target: str = Field(default="", max_length=2000)
    entity: str | None = Field(default=None, max_length=500)
    # Write-only by construction: it is read into a SourceSpec and never
    # echoed back. `to_public_dict()` cannot reach it.
    secret: str | None = Field(default=None, max_length=2000)
    options: dict = Field(default_factory=dict)

    def to_spec(self) -> SourceSpec:
        return SourceSpec(
            kind=self.kind, target=self.target, entity=self.entity,
            secret=self.secret, options=dict(self.options or {}),
        )


def _source_http_error(exc: SourceError) -> HTTPException:
    """Map a connector failure to the status code that describes it.

    Distinguishing these matters to the UI: a 400 means "fix your input", a
    403 means "policy refused this", 501 means "install a driver". Collapsing
    them to 500 would make every connector problem look like a LANA bug.
    """
    from app.sources import SourceConfigError, SourceConnectionError, SourceRefused

    if isinstance(exc, SourceUnavailable):
        return HTTPException(501, str(exc))
    if isinstance(exc, SourceRefused):
        return HTTPException(403, str(exc))
    if isinstance(exc, SourceConfigError):
        return HTTPException(400, str(exc))
    if isinstance(exc, SourceConnectionError):
        return HTTPException(502, str(exc))
    return HTTPException(400, str(exc))


@app.get("/sources")
def list_sources():
    """Every connector this build offers, generated from the registry.

    The frontend's source picker renders this verbatim, so registering a new
    connector makes it appear in the UI with no frontend change.
    """
    return {"sources": available_sources()}


@app.post("/sources/test")
def test_source(req: SourceSpecReq):
    """Probe a connection without loading data. Backs the 'Test' button."""
    try:
        source = build_source(req.to_spec())
        result = source.test_connection()
    except SourceError as exc:
        raise _source_http_error(exc) from exc

    logger.info(
        "source tested",
        extra={"kind": req.kind, "ok": result.ok},
    )
    # Recorded because a connection attempt reaches outside LANA — it is the
    # app touching someone else's database or an external URL, which is worth
    # a record whether or not it succeeded.
    _record_audit(
        audit_mod.SOURCE_TESTED, outcome="ok" if result.ok else "failed",
        kind=req.kind, target=req.target,
    )
    return result.to_dict()


@app.post("/sources/preview")
def preview_source(req: SourceSpecReq):
    """A small sample plus inferred schema, for confirming this is the data."""
    try:
        source = build_source(req.to_spec())
        result = source.preview()
    except SourceError as exc:
        raise _source_http_error(exc) from exc

    frame = result.frame
    return _jsonable({
        "label": result.label,
        "rows": len(frame),
        "columns": [str(c) for c in frame.columns],
        "dtypes": {str(c): str(frame[c].dtype) for c in frame.columns},
        "preview": _preview(frame),
        "notes": result.notes,
        "row_limit_applied": result.row_limit_applied,
    })


def _admit_frame(df: pd.DataFrame, label: str) -> None:
    """Apply the host memory budget to a frame that did not arrive as a file.

    The upload route projects a file's cost *before* parsing it, which is not
    possible here — a connector has already materialised the frame by the time
    its size is knowable. So the check is after the fact but before the frame
    is retained, which still prevents the session store from accumulating what
    the machine cannot hold.
    """
    budget = MAX_SESSION_MB * 1024 * 1024
    size = int(df.memory_usage(index=True, deep=True).sum())
    if size > budget:
        raise HTTPException(
            413,
            f"'{label}' needs about {size / 1024 ** 2:,.0f} MB of memory and the "
            f"budget on this machine is {MAX_SESSION_MB:,} MB. Filter the source "
            f"(fewer columns or rows), or raise LANA_MAX_SESSION_MB.",
        )
    ok, free = can_admit(size)
    if not ok:
        raise HTTPException(
            503,
            f"Not enough free memory right now: this dataset needs roughly "
            f"{size / 1024 ** 2:,.0f} MB and only {free / 1024 ** 2:,.0f} MB is "
            f"free. Close some applications and try again.",
        )


@app.post("/sources/load")
async def load_source(req: SourceSpecReq, request: Request):
    """Pull a connector's rows into a session, identical to an upload.

    From the response down, this is indistinguishable from /upload: the same
    session id, preview, quality score and profiles, so every other endpoint
    and every frontend tab works against a Mongo collection exactly as it does
    against a CSV.
    """
    try:
        source = build_source(req.to_spec())
        # Connectors are synchronous and can block for seconds on a slow
        # network or a large query, so they run off the event loop for the
        # same reason pandas' readers do in /upload.
        result = await run_in_threadpool(
            source.fetch, limit=config.limits.max_source_rows
        )
    except SourceError as exc:
        obs.uploads.inc(kind=req.kind, outcome="error")
        raise _source_http_error(exc) from exc

    df = result.frame
    if df.empty:
        obs.uploads.inc(kind=req.kind, outcome="empty")
        raise HTTPException(
            400,
            f"'{result.label}' connected successfully but returned no rows."
        )

    _admit_frame(df, result.label)
    # Same lossless shrink the upload path applies. `force=True` because a
    # connector result is often small enough to fall under OPTIMIZE_MIN_ROWS
    # while still being repeated-text heavy — a SQL `region` column is exactly
    # the case categorical encoding exists for, regardless of row count.
    df, dtype_changes = await run_in_threadpool(ingest.optimize_dtypes, df, True)
    if dtype_changes:
        result.notes.append(
            f"Optimised {len(dtype_changes)} column dtypes losslessly."
        )

    sid = str(uuid.uuid4())
    session = _store.create(sid, result.label, df, owner=_principal(request))
    _store.persist(session)

    profiles = await run_in_threadpool(session.profiles)
    quality = dataset_quality(profiles, len(df))

    obs.uploads.inc(kind=req.kind, outcome="ok")
    obs.upload_rows.observe(float(len(df)))
    logger.info(
        "source loaded",
        extra={
            "kind": req.kind, "session_id": sid,
            "rows": len(df), "columns": len(df.columns),
        },
    )
    _record_audit(
        audit_mod.DATA_LOADED, session_id=sid, kind=req.kind,
        label=result.label, rows=len(df), columns=len(df.columns),
    )

    injection = await run_in_threadpool(scan_frame, df)
    if injection.found:
        logger.warning(
            "dataset contains instruction-shaped text",
            extra={"session_id": sid, "findings": len(injection.findings)},
        )

    return _jsonable({
        "session_id": sid,
        "filename": result.label,
        "injection": injection.to_dict(),
        "source": {"kind": req.kind, **result.to_dict()},
        "rows": len(df),
        "columns": df.columns.tolist(),
        "numeric_columns": df.select_dtypes("number").columns.tolist(),
        "preview": _preview(df),
        "quality": quality,
        "profiles": {name: p.to_dict() for name, p in profiles.items()},
    })


@app.get("/session/{session_id}")
def session_info(session_id: str):
    """Metadata + preview for the currently active version (original or cleaned).

    Includes ``quality`` (cheap here — reads the session's cached profiles,
    not a fresh scan) so a session restored from a stored id after a page
    refresh renders the same KPI tiles a fresh upload does, rather than
    silently missing the quality score until the next cleaning action
    happens to refresh it.
    """
    session = _get_session(session_id)
    df = session.active
    profiles = session.profiles()
    return _jsonable({
        "session_id": session_id,
        "filename": session.filename,
        "rows": len(df),
        "columns": df.columns.tolist(),
        "numeric_columns": df.select_dtypes("number").columns.tolist(),
        "preview": _preview(df),
        "version": session.active_version,
        "has_cleaned": session.has_cleaned,
        "quality": dataset_quality(profiles, len(df)),
    })


@app.get("/profile/{session_id}")
def profile(session_id: str):
    """Full column-by-column profile and quality assessment of the active version."""
    session = _get_session(session_id)
    df = session.active
    profiles = session.profiles()
    return _jsonable({
        "version": session.active_version,
        "rows": len(df),
        "quality": dataset_quality(profiles, len(df)),
        "profiles": {name: p.to_dict() for name, p in profiles.items()},
    })


@app.get("/lineage/{session_id}")
def lineage(session_id: str):
    """The transformation log linking the raw upload to the active version."""
    session = _get_session(session_id)
    if session.ledger is None:
        return {
            "version": session.active_version,
            "summary": {"steps": 0, "rows_original": len(session.raw),
                        "rows_final": len(session.raw), "rows_removed": 0},
            "steps": [],
            "narrative": "No transformations were applied — this is the raw uploaded data.",
        }
    return _jsonable({
        "version": session.active_version,
        **session.ledger.to_dict(len(session.raw)),
    })


# Bounds on request bodies. /upload is carefully size-limited; without these
# the JSON endpoints were not, which is the kind of inconsistent hardening that
# is worse than none — it invites the assumption that everything is covered.
# The numbers are generous for real use and only exclude payloads that could
# not be a genuine question or cleaning plan.
MAX_QUESTION_CHARS = 4_000
MAX_OPERATIONS = 200
MAX_TEXT_MAPPING_ENTRIES = 1_000


class QueryReq(BaseModel):
    session_id: str = Field(max_length=200)
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)


class CleanOperation(BaseModel):
    # Must match the operations implemented in app/data/cleaner.py — unknown
    # values are rejected with 422 instead of silently doing nothing.
    type: Literal[
        "remove_duplicates", "fill_nulls", "flag_outliers", "winsorize",
        "remove_outliers", "normalize", "fix_text", "cast_type",
    ]
    column: str | None = None
    # 'flag' fills nothing — it records which rows were missing and leaves the
    # nulls in place, which is the non-destructive default for high missingness.
    method: Literal["mean", "median", "zero", "mode", "drop", "flag", "minmax", "zscore"] | None = None
    mapping: dict | None = Field(default=None, max_length=MAX_TEXT_MAPPING_ENTRIES)
    dtype: Literal["numeric", "datetime", "category", "text"] | None = None
    outlier_method: Literal["iqr", "modified_zscore"] | None = None
    add_indicator: bool | None = None


class CleanReq(BaseModel):
    operations: list[CleanOperation] = Field(max_length=MAX_OPERATIONS)


class VersionReq(BaseModel):
    version: str


def _build_query_context(session: Session):
    """Grounded context for the session's active version, including lineage.

    Cached per version on the session. Building it means profiling every
    column and running a correlation scan — ~2.1 s on a 200k x 30 frame — and
    the result is identical for every question asked of the same version, so
    paying for it once per version rather than once per question removes that
    entire wait from the second question onward.
    """
    return session.context(
        lambda df, profiles: build_context(
            df,
            lineage_narrative=session.lineage_narrative(),
            version=session.active_version,
            profiles=profiles,
            # The configured window, so a wide dataset is trimmed deliberately
            # here rather than truncated from the front by the runtime. The
            # answer budget travels with it: the space the model is allowed to
            # fill with its reply is space the context cannot have.
            token_budget=config.llm.num_ctx,
            max_answer_tokens=config.llm.max_tokens,
        )
    )


# Whether executed-SQL grounding is usable at all in this process. Both
# conditions are real: the operator can turn it off, and DuckDB is an optional
# dependency the app must run without.
SQL_GROUNDING_ENABLED = config.limits.sql_grounding and DUCKDB_AVAILABLE


def _answer_with_sql_grounding(session: Session, question: str):
    """Try the executed-query path. Returns None to fall back to the ledger.

    Order matters and is the substance of the change: SQL is attempted *first*
    because its answer is computed from the rows rather than retrieved from a
    summary, which removes the fact-ledger coverage ceiling that caused three
    of the seven residual failures in the measured 40-case run. The ledger
    remains the fallback, so a question the planner cannot express in SQL is
    answered exactly as well as it was before, never worse.
    """
    if not SQL_GROUNDING_ENABLED:
        return None
    try:
        provider = get_provider()
        with obs.sql_latency.time():
            result = answer_with_sql(provider, session.active, question)
    except SqlPlanningFailed as exc:
        obs.sql_queries.inc(outcome="planning_failed")
        logger.info("SQL grounding unavailable for this question: %s", exc)
        return None
    except Exception as exc:
        # An LLM transport failure is not a SQL problem, and retrying it on the
        # ledger path would just fail again more slowly. Re-raised so the
        # caller reports it as the 503 it is.
        if _llm_error(exc)[0] == 503:
            raise
        obs.sql_queries.inc(outcome="error")
        logger.warning("SQL grounding failed; falling back to the ledger", exc_info=exc)
        return None

    obs.sql_queries.inc(outcome="ok")
    return result


def _with_sql_facts(context, sql_answer):
    """A per-request view of the context, carrying this query's facts.

    The context returned by ``_build_query_context`` is **cached on the
    session** and shared by every question asked of that version. Extending
    its ``facts`` list in place — which is what this code used to do, despite
    a comment claiming it copied — leaked one question's executed-SQL facts
    into every later question on the same dataset, with three consequences:

    * **A hallucinated figure could be reported as verified.** The validator
      accepts a number that matches any fact in the context within tolerance.
      Once question 1's query result was permanently in the ledger, question 5
      could state a number that exists nowhere in *its* answer's evidence,
      collide with a stale fact from question 1, and be shown to the user with
      a green "verified against the data" mark. That is precisely the failure
      the whole validation layer exists to prevent.
    * **Unbounded growth.** Every question added its result's facts to a list
      that was never trimmed, for the life of the session.
    * **A data race.** Two questions answered concurrently for one session
      mutated the same list.

    ``replace`` builds a new ``GroundedContext`` with a fresh list and shares
    everything else (text, ranges, vocabulary, coverage) by reference — those
    are read-only to the validator, so this costs one small list per question
    rather than rebuilding the context.
    """
    if sql_answer is None:
        return context
    return replace(context, facts=[*context.facts, *sql_answer.facts])


def _record_validation(validation, path: str, session_id: str | None = None) -> None:
    """Metrics for the claim verdicts, and one audit entry for the answer.

    Together because they describe the same event and must not be able to
    disagree about it: an answer counted as flagged in the metrics and
    unflagged in the trail would make both useless for the question they
    exist to answer.
    """
    if session_id is not None:
        _record_audit(
            audit_mod.QUESTION_ANSWERED,
            outcome="flagged" if validation.warnings else "ok",
            session_id=session_id, grounding=path,
            numbers_checked=len(validation.claims),
            verified=validation.verified_count,
        )
    for claim in validation.claims:
        obs.validation_claims.inc(
            verdict=claim.status, provenance=claim.provenance or "none", path=path,
        )
    obs.validation_answers.inc(
        flagged=str(bool(validation.warnings)).lower(), path=path
    )


@app.post("/query")
def query(req: QueryReq, request: Request):
    session = _get_session(req.session_id, request)
    started = time.perf_counter()

    with _llm_slot():
        try:
            sql_answer = _answer_with_sql_grounding(session, req.question)
        except Exception as e:
            obs.llm_requests.inc(path="sql", outcome="error")
            raise HTTPException(*_llm_error(e)) from e

        # The ledger context is built either way: when SQL succeeds it supplies
        # the column ranges, vocabulary and centreless-column set the validator
        # needs to judge the prose *around* the executed figures, and when SQL
        # is skipped it is the grounding itself.
        # Never the cached object itself once SQL facts are involved — see
        # _with_sql_facts for why sharing it across questions is a trust bug.
        context = _with_sql_facts(_build_query_context(session), sql_answer)

        if sql_answer is not None:
            answer = sql_answer.answer
            path = "sql"
        else:
            try:
                provider = get_provider()
                answer = provider.answer_question(req.question, context.text)
            except Exception as e:
                obs.llm_requests.inc(path="ledger", outcome="error")
                raise HTTPException(*_llm_error(e)) from e
            path = "ledger"

    # Every answer is checked against the facts that produced it — a fluent
    # local model will otherwise supply a confident number for a question the
    # context cannot answer.
    validation = validate_answer(answer, context)
    _record_validation(validation, path, session_id=req.session_id)
    obs.llm_requests.inc(path=path, outcome="ok")
    obs.llm_latency.observe(time.perf_counter() - started, path=path)

    payload = {
        "answer": answer,
        "validation": validation.to_dict(),
        "context_coverage": context.coverage,
        "grounding": path,
    }
    if sql_answer is not None:
        # The query and its result travel with the answer. This is the
        # provenance the UI shows: not "trust me", but the exact statement that
        # produced every figure, which the user can read and re-run.
        payload["sql"] = sql_answer.to_dict()
    return _jsonable(payload)


def _sse_event(payload: dict) -> str:
    # JSON-encoding the payload (rather than writing the raw chunk after
    # "data: ") keeps embedded newlines/quotes from breaking SSE framing.
    return f"data: {json.dumps(_jsonable(payload))}\n\n"


def _query_stream_gen(session: Session, question: str):
    holder = f"{os.getpid()}:{uuid.uuid4().hex[:12]}"
    if not _coordinator.acquire_slot(holder, MAX_CONCURRENT_LLM):
        obs.llm_rejected.inc(reason="busy")
        yield _sse_event({"error": _LLM_BUSY_MSG})
        return
    started = time.perf_counter()
    try:
        # Planning and executing a query is not streamable — there is nothing
        # to show until the result exists — so the SQL attempt happens up
        # front and only the *answer* is streamed. The user sees the query
        # that is about to ground their answer while the tokens arrive, which
        # is better feedback than a spinner.
        try:
            sql_answer = _answer_with_sql_grounding(session, question)
        except Exception as e:
            yield _sse_event({"error": _llm_error(e)[1]})
            return

        context = _with_sql_facts(_build_query_context(session), sql_answer)

        if sql_answer is not None:
            # Announced on both paths, so the client can label an answer's
            # provenance without inferring it from which other events arrived.
            yield _sse_event({"grounding": "sql"})
            yield _sse_event({"sql": sql_answer.to_dict()})
            # The answer text was already produced by answer_with_sql. Emitted
            # as one delta so the client's rendering path is identical for both
            # grounding modes.
            yield _sse_event({"delta": sql_answer.answer})
            answer = sql_answer.answer
            path = "sql"
        else:
            yield _sse_event({"grounding": "ledger"})
            pieces: list[str] = []
            try:
                provider = get_provider()
                for chunk in provider.answer_question_stream(question, context.text):
                    pieces.append(chunk)
                    yield _sse_event({"delta": chunk})
            except Exception as e:
                obs.llm_requests.inc(path="ledger", outcome="error")
                yield _sse_event({"error": _llm_error(e)[1]})
                return
            answer = "".join(pieces)
            path = "ledger"

        # Validation runs on the assembled answer once streaming completes, so
        # the user sees text immediately and the trust signal arrives with it.
        validation = validate_answer(answer, context)
        _record_validation(validation, path, session_id=session.session_id)
        obs.llm_requests.inc(path=path, outcome="ok")
        obs.llm_latency.observe(time.perf_counter() - started, path=path)
        # Emitted when there is something to report either way — a warning, or
        # confirmation that the figures matched. An answer containing no
        # numbers has nothing to say, so the wire stays quiet.
        if validation.warnings or validation.verified_count:
            yield _sse_event({"validation": validation.to_dict()})
        yield _sse_event({"done": True})
    finally:
        _coordinator.release_slot(holder)


@app.post("/query/stream")
def query_stream(req: QueryReq, request: Request):
    session = _get_session(req.session_id, request)
    return StreamingResponse(
        _query_stream_gen(session, req.question),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/audit")
def audit_trail(
    request: Request,
    limit: int = Query(100, ge=1, le=audit_mod.MAX_READ_ENTRIES),
    session_id: str | None = Query(None, max_length=200),
):
    """The caller's own recent actions, newest first.

    Scoped to the calling principal, always — the same rule sessions follow.
    Reading the trail must not become a way to observe someone else's
    activity, which would make the auditability feature its own disclosure.

    ``enabled`` is reported explicitly rather than inferred from an empty
    list: "nothing happened" and "nothing is being recorded" are very
    different answers, and a UI that conflated them would quietly imply the
    first when the second is true.
    """
    principal = _principal(request)
    return {
        "enabled": bool(getattr(_audit, "enabled", False)),
        "entries": _audit.read(
            principal=principal, session_id=session_id, limit=limit
        ),
    }


@app.get("/validator/capabilities")
def validator_capabilities():
    """What the answer-validation layer does and does not check, in plain terms.

    Sourced from app.llm.validation directly, not restated here, so this
    endpoint and the code that actually enforces it cannot drift apart.
    """
    return capability_summary()


@app.get("/stats/{session_id}")
def stats(session_id: str, column: str = Query(...)):
    df = _session(session_id)
    if column not in df.columns:
        raise HTTPException(400, f"Column '{column}' not found.")
    if not pd.api.types.is_numeric_dtype(df[column]):
        raise HTTPException(400, f"Column '{column}' is not numeric.")
    return _jsonable(compute_statistics(df[column]))


@app.get("/correlation/{session_id}")
def correlation(session_id: str, method: Literal["pearson", "spearman"] = Query("pearson")):
    session = _get_session(session_id)
    return _jsonable(
        analyze_correlations(session.active, method=method, profiles=session.profiles())
    )


class RegressionReq(BaseModel):
    session_id: str
    x_col: str
    y_col: str


@app.post("/regression")
def regression(req: RegressionReq):
    df = _session(req.session_id)
    try:
        result = perform_linear_regression(df, req.x_col, req.y_col)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return _jsonable(result.as_dict())


class ChartReq(BaseModel):
    session_id: str
    column: str
    chart_type: str
    x_col: str | None = None


@app.post("/chart")
def chart(req: ChartReq):
    session = _get_session(req.session_id)
    # Without this, an unknown type renders a PNG reading "Unknown chart type"
    # and returns it with a 200 — a failure the caller cannot detect.
    if req.chart_type not in CHART_TYPES:
        raise HTTPException(
            400, f"Unknown chart type '{req.chart_type}'. Supported: {', '.join(CHART_TYPES)}."
        )
    try:
        png = create_chart(
            session.active, req.chart_type, req.column,
            secondary_column=req.x_col, profiles=session.profiles(),
        )
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    return Response(content=png, media_type="image/png")


@app.get("/recommendations/{session_id}")
def recommendations(session_id: str):
    session = _get_session(session_id)
    return {
        "recommendations": generate_recommendations(
            session.active, profiles=session.profiles()
        )
    }


@app.get("/models")
def models():
    """Locally-available models for the configured provider.

    Only Ollama exposes a "what's pulled locally" listing; an
    openai_compat endpoint has no equivalent concept, so this reports
    the single model that provider is actually configured to use
    instead of silently returning an empty list for a provider it was
    never able to ask. Any failure (including a misconfigured
    LLM_PROVIDER) falls back to an empty list rather than a 500 -
    this is a convenience lookup, not a required call.
    """
    try:
        provider = get_provider()
        list_local = getattr(provider, "list_local_models", None)
        if list_local is not None:
            return {"models": list_local()}
        return {"models": [config.llm.model] if provider.is_available() else []}
    except Exception:
        return {"models": []}


# Rows serialised per chunk when streaming an export. Large enough that the
# per-call overhead is negligible, small enough that one chunk is never itself
# a memory concern.
CSV_EXPORT_CHUNK_ROWS = 50_000


# Characters that make a spreadsheet treat a cell as a formula rather than as
# text. Excel, LibreOffice and Sheets all evaluate these on open, so a value
# that arrived in an upload — from a file LANA did not write and cannot vouch
# for — must not be handed back in a form that executes.
_FORMULA_TRIGGERS = ("=", "+", "@", "\t", "\r")


def _escape_formula_cells(block: pd.DataFrame) -> pd.DataFrame:
    """Neutralise text cells a spreadsheet would execute as a formula.

    Two deliberate narrowings, both to avoid corrupting real data in a tool
    whose whole point is reporting it faithfully:

    * **Only text columns.** A numeric column's -5.2 is a number, and
      prefixing it would turn every negative value in an export into a string.
    * **A leading '-' is judged on what follows.** It begins both every
      negative number and a known payload (`-1+1+cmd|' /C calc'!A0`), so a
      value that parses as a number is left exactly as it was and anything
      else is escaped.

    Escaped cells are prefixed with an apostrophe, which is the convention
    every spreadsheet understands as "this is text". That does change the
    value on a round-trip back into LANA, which is why it is applied to as few
    cells as correctness allows.
    """
    text_columns = block.select_dtypes(include=["object", "string", "category"]).columns
    if len(text_columns) == 0:
        return block

    escaped: pd.DataFrame | None = None
    for column in text_columns:
        values = block[column]
        if isinstance(values.dtype, pd.CategoricalDtype):
            values = values.astype("object")
        try:
            as_text = values.astype("string")
        except (TypeError, ValueError):
            continue  # a column of unhashable/exotic objects — nothing str-like to escape

        first = as_text.str[:1]
        flagged = first.isin(_FORMULA_TRIGGERS).fillna(False)
        minus = (first == "-").fillna(False)
        if minus.any():
            # Vectorised, so the "is this just a negative number?" test costs
            # one pass rather than a Python call per cell.
            numeric_like = pd.to_numeric(as_text.where(minus), errors="coerce").notna()
            flagged = flagged | (minus & ~numeric_like)
        if not flagged.any():
            continue

        if escaped is None:
            # Copied lazily and per block, so the streaming export keeps its
            # bounded memory: at most one 50,000-row block is duplicated, and
            # only when that block actually contains something to escape.
            escaped = block.copy()
        # Substituted into the original column rather than replacing it with
        # the stringified copy, so every cell that was not flagged stays
        # byte-identical to what was uploaded.
        escaped[column] = values.mask(flagged, "'" + as_text)

    return escaped if escaped is not None else block


def _csv_chunks(df: pd.DataFrame):
    """Serialise a frame to CSV in row blocks, yielding encoded bytes.

    ``df.to_csv()`` with no path builds the entire file as one Python string
    and the caller then encodes it — two full-size copies resident at once,
    which on a 1M x 30 frame is roughly a gigabyte of transient allocation for
    a file the user is about to stream to disk anyway.

    This is an explicit trade, measured rather than assumed. On that frame:

        single call to_csv().encode()   20.2 s, ~1 GB transient
        50,000-row blocks              25.0 s, ~25 MB transient

    So it costs about 23% more CPU. Worth it here for two reasons: the memory
    it saves is the difference between working and being OOM-killed on an 8 GB
    laptop, and time-to-first-byte drops from 20 s to under one, so the browser
    shows a download progressing instead of a tab that appears to have hung.
    Block size was tuned by measurement — 100k and 250k were both slower.
    """
    header = True
    for start in range(0, len(df), CSV_EXPORT_CHUNK_ROWS):
        block = df.iloc[start:start + CSV_EXPORT_CHUNK_ROWS]
        yield _escape_formula_cells(block).to_csv(index=False, header=header).encode("utf-8")
        header = False


@app.get("/export/csv/{session_id}")
def export_csv(session_id: str):
    session = _get_session(session_id)
    df = session.active
    # Recorded before the stream starts, not after: the response body is
    # produced lazily by a generator, so "after" would mean after the client
    # finished reading — and an export abandoned halfway still left with rows.
    _record_audit(
        audit_mod.DATA_EXPORTED, session_id=session_id, format="csv",
        rows=len(df), version=session.active_version, label=session.filename,
    )
    return StreamingResponse(
        _csv_chunks(df),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="lana_data.csv"'},
    )


def _report_inputs(session: Session) -> dict:
    """Everything a self-contained report needs about the active version.

    The exported file travels to people who never used LANA, so it carries the
    same provenance and caveats the UI shows rather than bare statistics.
    """
    df = session.active
    profiles = session.profiles()
    return {
        # Profile-aware on purpose: without it this block reports a mean for
        # identifier columns and for LANA's own cleaning annotations, directly
        # contradicting the profile-filtered summary printed above it.
        "context": generate_context(df, profiles=profiles),
        "quality": dataset_quality(profiles, len(df)),
        "lineage": session.lineage_narrative(),
        "profiles": profiles,
    }


@app.get("/export/pdf/{session_id}")
def export_pdf(session_id: str):
    if not EXPORT_OK:
        raise HTTPException(501, "Export dependencies not installed.")
    session = _get_session(session_id)
    inputs = _report_inputs(session)
    try:
        pdf = generate_pdf_report(
            session.active, inputs["context"],
            quality=inputs["quality"], lineage=inputs["lineage"],
            profiles=inputs["profiles"],
        )
    except Exception as e:
        raise HTTPException(500, f"PDF generation failed: {e}") from e
    _record_audit(
        audit_mod.DATA_EXPORTED, session_id=session_id, format="pdf",
        rows=len(session.active), version=session.active_version,
        label=session.filename,
    )
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="lana_report.pdf"'})


@app.get("/export/docx/{session_id}")
def export_docx(session_id: str):
    if not EXPORT_OK:
        raise HTTPException(501, "Export dependencies not installed.")
    session = _get_session(session_id)
    inputs = _report_inputs(session)
    try:
        docx = generate_word_report(
            session.active, inputs["context"],
            quality=inputs["quality"], lineage=inputs["lineage"],
            profiles=inputs["profiles"],
        )
    except Exception as e:
        raise HTTPException(500, f"Word report generation failed: {e}") from e
    _record_audit(
        audit_mod.DATA_EXPORTED, session_id=session_id, format="docx",
        rows=len(session.active), version=session.active_version,
        label=session.filename,
    )
    return Response(
        content=docx,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": 'attachment; filename="lana_report.docx"'},
    )


# ── Cleaning endpoints ────────────────────────────────────────────────────────

@app.get("/clean/preview/{session_id}")
def clean_preview(session_id: str):
    """Detect data quality issues in the original DataFrame without modifying it."""
    session = _get_session(session_id)
    # Cleaning always reads the raw frame, so the cached profiles are reusable
    # only while the original is the active version. Recomputing otherwise is
    # correct: profiles of the cleaned frame describe different data.
    profiles = session.profiles() if session.active_version == ORIGINAL else None
    return _jsonable(detect_issues(session.raw, profiles=profiles))


@app.post("/clean/apply/{session_id}")
def clean_apply(session_id: str, req: CleanReq):
    """Apply cleaning operations to the raw data and store the result as a version.

    Always transforms the *raw* frame, never the previously cleaned one, so
    re-applying with different settings cannot compound transformations
    invisibly. The full lineage is returned with the result.
    """
    session = _get_session(session_id)
    df = session.raw
    ops = [op.model_dump(exclude_none=True) for op in req.operations]

    cleaned, ledger = apply_cleaning(df, ops)
    if len(cleaned) == 0:
        raise HTTPException(400, "Cleaning would remove all rows. Relax your settings and try again.")

    session.set_cleaned(cleaned, ledger)
    _store.enforce_limits()
    _store.persist(session)
    summary = ledger.summary(len(df))
    # The operation *types* only. Which columns were filled and with what is
    # the lineage ledger's job, and it travels with the data; duplicating it
    # here would put column names in a second file for no added answer.
    _record_audit(
        audit_mod.DATA_CLEANED, session_id=session_id,
        operations=len(ops), rows_before=len(df), rows_after=len(cleaned),
        kinds=",".join(sorted({o["type"] for o in ops})),
    )

    return _jsonable({
        "rows_before": len(df),
        "rows_after": len(cleaned),
        "rows_removed": summary["rows_removed"],
        "columns_before": len(df.columns),
        "columns_after": len(cleaned.columns),
        # Flat list kept for the existing UI; `lineage` carries the full record.
        "warnings": ledger.warnings,
        "lineage": ledger.to_dict(len(df)),
    })


@app.post("/clean/version/{session_id}")
def set_version(session_id: str, req: VersionReq):
    """Switch the active version (original or cleaned) for all downstream endpoints."""
    session = _get_session(session_id)
    if req.version not in (ORIGINAL, CLEANED):
        raise HTTPException(400, "version must be 'original' or 'cleaned'.")
    try:
        session.set_version(req.version)
    except ValueError as e:
        raise HTTPException(400, "No cleaned version available. Apply cleaning first.") from e
    # Metadata only — the frames themselves are unchanged by a version switch.
    _store.persist(session, frames=False)
    # Worth recording because every later answer, chart and export silently
    # changes meaning: "the numbers were wrong that afternoon" is usually this.
    _record_audit(
        audit_mod.DATA_VERSION_SWITCHED, session_id=session_id,
        version=req.version,
    )
    return {"version": req.version}


@app.get("/clean/status/{session_id}")
def clean_status(session_id: str):
    """Whether a cleaned version exists, which is active, and how it was produced."""
    return _jsonable(_get_session(session_id).status())


# Last line of the module on purpose: it reports SQL_GROUNDING_ENABLED and the
# connector policy, both of which are decided further down the file.
_log_security_posture()
