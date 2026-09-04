"""Ingest — get a file into a DataFrame without spending more memory than needed.

The upload path used to be the single largest memory event in LANA's
lifetime. Measured on a 93 MB CSV, process RSS peaked at ~724 MB: the request
body was held once as a list of chunks, again as the joined ``bytes``, again
as a ``BytesIO``, and then the parser's own working set and the profiling
temporaries landed on top. None of those copies were necessary.

This module fixes that in three steps, in the order they matter:

1. **The body never accumulates in RAM.** It streams into a
   ``SpooledTemporaryFile``, which keeps small uploads in memory and spills
   large ones to disk. Peak drops by roughly twice the file size, and the
   ceiling stops depending on how big the file is.
2. **Admission is decided before the parse, not after.** For CSV, a small
   sample is read and scaled by the file size to project what the full frame
   will cost. A file that cannot fit is refused with a number the user can
   act on, instead of the process being killed partway through.
3. **The resident frame is shrunk losslessly.** Integers are downcast to the
   narrowest type that holds their actual range and low-cardinality strings
   become categoricals. Measured 43% smaller on a realistic mixed frame,
   with every value bit-identical afterwards.

What this module deliberately does **not** do is downcast floats. Halving a
``float64`` to ``float32`` would save as much memory as everything above
combined, and it would silently change every mean, standard deviation and
regression coefficient LANA reports. A statistics tool does not trade
precision for memory behind the user's back.
"""

from __future__ import annotations

import io
import tempfile
from dataclasses import dataclass, field
from typing import Any, BinaryIO

import pandas as pd

from ..resources import HOST

# Uploads below this stay entirely in RAM — spilling a small file to disk buys
# nothing and costs a write. Above it, the spool moves to a temporary file.
SPOOL_THRESHOLD_BYTES = 16 * 1024 ** 2

# Streaming read size. Large enough that syscall overhead is irrelevant, small
# enough that the in-flight chunk is never itself a memory concern.
READ_CHUNK_BYTES = 1024 ** 2

# Below this row count, dtypes are left exactly as pandas inferred them. The
# saving would be trivial and unsurprising behaviour matters more on the small
# files that make up most uploads.
OPTIMIZE_MIN_ROWS = 50_000

# A string column is worth storing as a categorical when repeats dominate.
# Kept well below profile.py's 0.90 free-text ratio so that a column which
# would be classified as TEXT is never converted into a CATEGORICAL one.
CATEGORY_MAX_UNIQUE_RATIO = 0.5

# Rows read to project the cost of a full CSV parse. Enough for a stable
# bytes-per-row figure; small enough to be free.
PROJECTION_SAMPLE_ROWS = 5_000


@dataclass
class IngestReport:
    """What ingest did, in terms the caller can show the user."""

    rows: int
    columns: int
    frame_bytes: int
    source_bytes: int
    spilled_to_disk: bool = False
    optimized: bool = False
    bytes_saved: int = 0
    dtype_changes: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "rows": self.rows,
            "columns": self.columns,
            "frame_mb": round(self.frame_bytes / 1024 ** 2, 2),
            "source_mb": round(self.source_bytes / 1024 ** 2, 2),
            "spilled_to_disk": self.spilled_to_disk,
            "optimized": self.optimized,
        }
        if self.optimized and self.bytes_saved:
            out["saved_mb"] = round(self.bytes_saved / 1024 ** 2, 2)
            out["saved_pct"] = round(
                100 * self.bytes_saved / max(1, self.frame_bytes + self.bytes_saved), 1
            )
            out["dtype_changes"] = dict(self.dtype_changes)
        return out


class UploadTooLarge(Exception):
    """Raised when a file cannot be admitted, with a message for the user."""


def frame_bytes(df: pd.DataFrame) -> int:
    """Resident size of a frame, counting actual string payloads."""
    try:
        return int(df.memory_usage(index=True, deep=True).sum())
    except Exception:
        return int(df.memory_usage(index=True).sum())


# ── Streaming the body to a spool ────────────────────────────────────────────

async def spool_upload(read, limit_bytes: int, limit_label: str) -> tuple[Any, int]:
    """Stream an upload into a spooled temp file, aborting past ``limit_bytes``.

    ``read`` is an awaitable chunk reader (``UploadFile.read``). Returns the
    rewound spool and the total byte count. The caller owns closing the spool.

    The size check runs per chunk, so an oversized file is rejected while it
    is still arriving rather than after it has all been buffered.
    """
    spool = tempfile.SpooledTemporaryFile(max_size=SPOOL_THRESHOLD_BYTES, mode="w+b")
    total = 0
    try:
        while chunk := await read(READ_CHUNK_BYTES):
            total += len(chunk)
            if total > limit_bytes:
                raise UploadTooLarge(
                    f"File exceeds the {limit_label} limit. This machine has "
                    f"{HOST.available_mb:,.0f} MB free, and parsing needs several "
                    f"times a file's size in memory."
                )
            spool.write(chunk)
    except BaseException:
        spool.close()
        raise
    spool.seek(0)
    return spool, total


def spilled(spool: Any) -> bool:
    """True when the spool grew past its threshold and is backed by a real file.

    ``_rolled`` is CPython's own flag for exactly this; the ``_file`` fallback
    covers an implementation that does not expose it. Reported to the caller
    rather than acted on — it is diagnostic, not control flow.
    """
    rolled = getattr(spool, "_rolled", None)
    if rolled is not None:
        return bool(rolled)
    return not isinstance(getattr(spool, "_file", None), io.BytesIO)


# ── Admission control: project the cost before paying it ─────────────────────

def project_csv_frame_bytes(
    spool: BinaryIO,
    source_bytes: int,
    sample_rows: int = PROJECTION_SAMPLE_ROWS,
) -> tuple[int, int] | None:
    """Estimate ``(frame_bytes, rows)`` for a full CSV parse from a small sample.

    Reads the first ``sample_rows`` rows, measures what they cost after the
    same optimisation the full parse will apply, and scales by how much of the
    file those rows represent. Returns None when the shape cannot be
    established (an unparseable or single-row file), leaving the caller to fall
    back on the raw file-size limit.
    """
    try:
        spool.seek(0)
        sample = pd.read_csv(spool, nrows=sample_rows)
    except Exception:
        return None
    finally:
        spool.seek(0)

    if sample.empty:
        return None

    optimized, _ = optimize_dtypes(sample, force=True)
    sample_frame_bytes = frame_bytes(optimized)

    # Bytes of source consumed by those rows, approximated by their share of
    # the file. Measuring the sample's own serialised length is more accurate
    # than assuming a fixed row width across heterogeneous columns.
    try:
        sample_source_bytes = len(optimized.to_csv(index=False).encode())
    except Exception:
        return None
    if sample_source_bytes <= 0:
        return None

    scale = source_bytes / sample_source_bytes
    return int(sample_frame_bytes * scale), int(len(sample) * scale)


# ── Lossless dtype optimisation ──────────────────────────────────────────────

def optimize_dtypes(
    df: pd.DataFrame,
    force: bool = False,
) -> tuple[pd.DataFrame, dict[str, str]]:
    """Shrink a frame without changing a single value.

    Two transformations, both exactly reversible:

    * **Integer downcasting** to the narrowest signed/unsigned type that holds
      the column's observed range. ``int64`` to ``int8`` is an 8x saving on a
      flag or a small count, and pandas' own ``to_numeric`` guarantees no
      value changes.
    * **Categorical encoding** for string columns where repeats dominate. The
      distinct values are stored once and the column becomes small integer
      codes. Skipped above ``CATEGORY_MAX_UNIQUE_RATIO`` so that free-text
      columns — which profile.py must still classify as TEXT — are untouched.

    Floats are deliberately left alone; see the module docstring.

    Returns the frame and a map of ``column -> "old -> new"`` for the columns
    that changed, so the caller can report what happened.
    """
    if not force and len(df) < OPTIMIZE_MIN_ROWS:
        return df, {}

    changes: dict[str, str] = {}
    out = df
    copied = False

    for column in df.columns:
        series = df[column]
        original = str(series.dtype)
        new_series = None

        if pd.api.types.is_bool_dtype(series):
            continue
        if pd.api.types.is_integer_dtype(series):
            try:
                candidate = pd.to_numeric(series, downcast="integer")
            except (TypeError, ValueError):
                continue
            if str(candidate.dtype) != original:
                new_series = candidate
        elif pd.api.types.is_string_dtype(series) or pd.api.types.is_object_dtype(series):
            if isinstance(series.dtype, pd.CategoricalDtype):
                continue
            non_null = int(series.notna().sum())
            if non_null == 0:
                continue
            try:
                unique = int(series.nunique(dropna=True))
            except TypeError:
                # Unhashable values (lists, dicts from a nested JSON) cannot be
                # categorised at all.
                continue
            if unique and unique / non_null <= CATEGORY_MAX_UNIQUE_RATIO:
                try:
                    new_series = series.astype("category")
                except (TypeError, ValueError):
                    continue

        if new_series is None:
            continue

        # Copy once, lazily, and only if something actually changes — an
        # unconditional .copy() would double peak memory for no reason.
        if not copied:
            out = df.copy(deep=False)
            copied = True
        out[column] = new_series
        changes[str(column)] = f"{original} -> {new_series.dtype}"

    return out, changes


# ── The parse itself ─────────────────────────────────────────────────────────

def _read_csv_chunked(
    spool: BinaryIO,
    chunk_rows: int,
) -> tuple[pd.DataFrame, dict[str, str]]:
    """Read a CSV in row blocks, shrinking each block before it accumulates.

    Optimising per chunk rather than once at the end is what keeps the peak
    down: the frame being built up is already in its compact form, so the
    concatenation never has to hold a full-size copy alongside it.

    Categoricals are resolved back to plain strings before concatenating.
    Two chunks that saw different value sets produce different category
    dtypes, and concatenating those silently yields an object column of mixed
    codes — a correctness trap that costs more than the memory it saves.
    Integer downcasting has no such hazard and is kept.
    """
    frames: list[pd.DataFrame] = []
    changes: dict[str, str] = {}
    for chunk in pd.read_csv(spool, chunksize=chunk_rows):
        optimized, chunk_changes = optimize_dtypes(chunk, force=True)
        for column, change in chunk_changes.items():
            if "category" in change:
                optimized[column] = optimized[column].astype(chunk[column].dtype)
            else:
                changes.setdefault(column, change)
        frames.append(optimized)

    if not frames:
        return pd.DataFrame(), changes

    if len(frames) == 1:
        # One chunk means the loop above already saw every value, but it also
        # reverted that chunk's categoricals — the revert exists to keep two
        # chunks from disagreeing about a category set, and with a single chunk
        # there is nothing to disagree with. Re-apply, or a file that lands
        # just over the chunked-read threshold silently keeps none of the
        # string savings a smaller file would have got.
        result = frames[0]
    else:
        result = pd.concat(frames, ignore_index=True)
    frames.clear()

    # Now that every value is present, categorising is unambiguous.
    result, post = optimize_dtypes(result, force=True)
    changes.update(post)
    return result, changes


# Above this many source bytes, CSVs are read in blocks rather than in one go.
CHUNKED_READ_MIN_BYTES = 32 * 1024 ** 2
CHUNKED_READ_ROWS = 250_000


def read_frame(
    spool: BinaryIO,
    ext: str,
    source_bytes: int,
    was_spilled: bool = False,
) -> tuple[pd.DataFrame, IngestReport]:
    """Parse an upload into an optimised DataFrame, reporting what it cost.

    CSV is the only format read incrementally, because it is the only one of
    the three that supports it — Excel and JSON parsers must see the whole
    document to produce anything, so for those the spool's job is simply to
    keep the raw bytes off the heap.
    """
    spool.seek(0)
    changes: dict[str, str] = {}
    saved = 0

    if ext == ".csv" and source_bytes >= CHUNKED_READ_MIN_BYTES:
        # Chunked path: each block is shrunk before it accumulates, so there is
        # no single moment where a full-size unoptimised copy exists. That also
        # means there is no "before" size to compare against — the saving is
        # real but not measurable here, so it is left unreported rather than
        # guessed at.
        df, changes = _read_csv_chunked(spool, CHUNKED_READ_ROWS)
    else:
        if ext == ".csv":
            df = pd.read_csv(spool)
        elif ext in (".xlsx", ".xls"):
            df = pd.read_excel(spool)
        else:
            df = pd.read_json(spool)
        before = frame_bytes(df)
        df, changes = optimize_dtypes(df)
        if changes:
            saved = max(0, before - frame_bytes(df))

    report = IngestReport(
        rows=len(df),
        columns=len(df.columns),
        frame_bytes=frame_bytes(df),
        source_bytes=source_bytes,
        spilled_to_disk=was_spilled,
        optimized=bool(changes),
        bytes_saved=saved,
        dtype_changes=changes,
    )
    return df, report
