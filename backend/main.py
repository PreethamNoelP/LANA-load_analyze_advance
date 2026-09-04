import sys
import json
import logging
import os
import re
import uuid
import math
import threading
from pathlib import Path
from typing import Literal
import numpy as np
import pandas as pd
from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import config
from app.resources import HOST, UPLOAD_PEAK_MULTIPLIER, can_admit
from app.llm import get_provider
from app.llm.context import build_context
from app.llm.validation import capability_summary, validate_answer
from app.llm.ollama_provider import OllamaProvider
from app.analysis.statistics import (
    analyze_correlations,
    compute_statistics,
    generate_context,
    generate_recommendations,
)
from app.analysis.regression import perform_linear_regression
from app.data import ingest
from app.data.cleaner import apply_cleaning, detect_issues
from app.data.profile import dataset_quality
from app.visualization.charts import CHART_TYPES, create_chart
from backend.session_store import CLEANED, ORIGINAL, Session, SessionStore

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

# Limits are derived from the machine LANA is actually running on, not from
# constants. The same 2 GB session budget is reckless on an 8 GB laptop and
# needlessly stingy on a workstation, and LANA's whole premise is that it runs
# on the user's own hardware. An explicit environment variable always wins —
# the operator knows something the probe does not.
MAX_UPLOAD_MB = int(os.getenv(
    "LANA_MAX_UPLOAD_MB", str(max(8, int(HOST.upload_limit_bytes() / 1024 ** 2)))
))
MAX_SESSIONS = int(os.getenv("LANA_MAX_SESSIONS", "30"))
# Resident-bytes ceiling across all sessions. Session count alone is not a
# memory bound — a handful of wide uploads can exhaust the host well before
# the count limit is reached.
MAX_SESSION_MB = int(os.getenv(
    "LANA_MAX_SESSION_MB", str(max(256, int(HOST.session_budget_bytes() / 1024 ** 2)))
))
SESSION_TTL_SECONDS = float(os.getenv("LANA_SESSION_TTL_SECONDS", "3600"))

# Caps how many LLM requests run at once — a local Ollama model serves one
# request at a time well; without this, a burst of concurrent visitors all
# queue behind it and every answer appears to hang.
MAX_CONCURRENT_LLM = int(os.getenv("LANA_MAX_CONCURRENT_LLM", "2"))
_llm_semaphore = threading.Semaphore(MAX_CONCURRENT_LLM)
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

def _get_session(session_id: str) -> Session:
    session = _store.get(session_id)
    if session is None:
        raise HTTPException(404, "Session not found — upload a dataset first.")
    return session


def _original(session_id: str) -> pd.DataFrame:
    """The untouched uploaded frame. Never overwritten by cleaning."""
    return _get_session(session_id).raw


def _session(session_id: str) -> pd.DataFrame:
    """The frame for the session's active version (original or cleaned)."""
    return _get_session(session_id).active


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    """Liveness plus the resource picture the limits were derived from.

    Exposed because the limits are no longer constants a reader can look up in
    the source — they depend on the machine. When an upload is refused for
    being too large, this is where the user sees why.
    """
    return {
        "ok": True,
        "sessions": _store.stats(),
        "host": HOST.to_dict(),
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
    if ext not in (".csv", ".xlsx", ".xls", ".json"):
        raise HTTPException(400, f"Unsupported type '{ext}'. Use CSV, Excel, or JSON.")

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
        raise HTTPException(413, f"{e} Set LANA_MAX_UPLOAD_MB to override.")

    try:
        # Admission is decided before the parse, not after it. Projecting the
        # frame's cost from a small sample means an unaffordable file is
        # refused with a number the user can act on, rather than the process
        # being killed halfway through materialising it.
        if ext == ".csv":
            projection = await run_in_threadpool(
                ingest.project_csv_frame_bytes, spool, total
            )
            budget = MAX_SESSION_MB * 1024 * 1024
            if projection and projection[0] > budget:
                projected_mb = projection[0] / 1024 ** 2
                raise HTTPException(
                    413,
                    f"This file would need about {projected_mb:,.0f} MB of memory "
                    f"({projection[1]:,} rows), and the budget on this machine is "
                    f"{MAX_SESSION_MB:,} MB. Upload a subset of the columns or rows, "
                    f"or raise LANA_MAX_SESSION_MB if you have headroom.",
                )
            # Second gate, against the machine's state *now* rather than at
            # startup. Refusing here keeps LANA from being the process that
            # pushes a laptop into swapping.
            if projection:
                needed = int(projection[0] * UPLOAD_PEAK_MULTIPLIER)
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
            raise HTTPException(400, f"Could not parse file: {e}")
    finally:
        # Releases the spool's memory, and deletes its backing file if it
        # spilled. Nothing is left on disk after the request.
        spool.close()

    if df.empty:
        raise HTTPException(400, "The file parsed successfully but contains no rows.")

    sid = str(uuid.uuid4())
    session = _store.create(sid, file.filename, df)

    # Profiling is the first thing a data scientist does; surfacing it at
    # upload means the user sees what they are working with before they act.
    # Cached on the session, so the six other places that need these profiles
    # read them instead of spending another full pass over the frame.
    profiles = await run_in_threadpool(session.profiles)
    quality = dataset_quality(profiles, len(df))

    return _jsonable({
        "session_id": sid,
        "filename": file.filename,
        "rows": len(df),
        "columns": df.columns.tolist(),
        "numeric_columns": df.select_dtypes("number").columns.tolist(),
        "preview": _preview(df),
        "quality": quality,
        "profiles": {name: p.to_dict() for name, p in profiles.items()},
        # What ingest actually did, so a large upload can explain itself.
        "ingest": report.to_dict(),
    })


@app.get("/session/{session_id}")
def session_info(session_id: str):
    """Metadata + preview for the currently active version (original or cleaned)."""
    session = _get_session(session_id)
    df = session.active
    return _jsonable({
        "session_id": session_id,
        "filename": session.filename,
        "rows": len(df),
        "columns": df.columns.tolist(),
        "numeric_columns": df.select_dtypes("number").columns.tolist(),
        "preview": _preview(df),
        "version": session.active_version,
        "has_cleaned": session.has_cleaned,
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


class QueryReq(BaseModel):
    session_id: str
    question: str


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
    mapping: dict | None = None
    dtype: Literal["numeric", "datetime", "category", "text"] | None = None
    outlier_method: Literal["iqr", "modified_zscore"] | None = None
    add_indicator: bool | None = None


class CleanReq(BaseModel):
    operations: list[CleanOperation]


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
            # here rather than truncated from the front by the runtime.
            token_budget=config.llm.num_ctx,
        )
    )


@app.post("/query")
def query(req: QueryReq):
    session = _get_session(req.session_id)
    context = _build_query_context(session)
    if not _llm_semaphore.acquire(blocking=False):
        raise HTTPException(429, _LLM_BUSY_MSG)
    try:
        provider = get_provider()
        answer = provider.answer_question(req.question, context.text)
    except Exception as e:
        raise HTTPException(*_llm_error(e))
    finally:
        _llm_semaphore.release()

    # Every answer is checked against the facts that produced it — a fluent
    # local model will otherwise supply a confident number for a question the
    # context cannot answer.
    validation = validate_answer(answer, context)
    return _jsonable({
        "answer": answer,
        "validation": validation.to_dict(),
        "context_coverage": context.coverage,
    })


def _sse_event(payload: dict) -> str:
    # JSON-encoding the payload (rather than writing the raw chunk after
    # "data: ") keeps embedded newlines/quotes from breaking SSE framing.
    return f"data: {json.dumps(_jsonable(payload))}\n\n"


def _query_stream_gen(session: Session, question: str):
    if not _llm_semaphore.acquire(blocking=False):
        yield _sse_event({"error": _LLM_BUSY_MSG})
        return
    try:
        context = _build_query_context(session)
        pieces: list[str] = []
        try:
            provider = get_provider()
            for chunk in provider.answer_question_stream(question, context.text):
                pieces.append(chunk)
                yield _sse_event({"delta": chunk})
        except Exception as e:
            yield _sse_event({"error": _llm_error(e)[1]})
            return
        # Validation runs on the assembled answer once streaming completes, so
        # the user sees text immediately and the trust signal arrives with it.
        validation = validate_answer("".join(pieces), context)
        # Emitted when there is something to report either way — a warning, or
        # confirmation that the figures matched. An answer containing no
        # numbers has nothing to say, so the wire stays quiet.
        if validation.warnings or validation.verified_count:
            yield _sse_event({"validation": validation.to_dict()})
        yield _sse_event({"done": True})
    finally:
        _llm_semaphore.release()


@app.post("/query/stream")
def query_stream(req: QueryReq):
    session = _get_session(req.session_id)
    return StreamingResponse(
        _query_stream_gen(session, req.question),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
        raise HTTPException(400, str(e))
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
        raise HTTPException(400, str(e))
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
    try:
        return {"models": OllamaProvider().list_local_models()}
    except Exception:
        return {"models": []}


# Rows serialised per chunk when streaming an export. Large enough that the
# per-call overhead is negligible, small enough that one chunk is never itself
# a memory concern.
CSV_EXPORT_CHUNK_ROWS = 50_000


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
        yield block.to_csv(index=False, header=header).encode("utf-8")
        header = False


@app.get("/export/csv/{session_id}")
def export_csv(session_id: str):
    df = _session(session_id)
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
        "context": generate_context(df),
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
        raise HTTPException(500, f"PDF generation failed: {e}")
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
        raise HTTPException(500, f"Word report generation failed: {e}")
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
    summary = ledger.summary(len(df))

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
    except ValueError:
        raise HTTPException(400, "No cleaned version available. Apply cleaning first.")
    return {"version": req.version}


@app.get("/clean/status/{session_id}")
def clean_status(session_id: str):
    """Whether a cleaned version exists, which is active, and how it was produced."""
    return _jsonable(_get_session(session_id).status())
