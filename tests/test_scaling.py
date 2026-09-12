"""Tests for the resource-efficiency work: ingest, caching, sampling, budgets.

The theme running through these is that an optimisation is only allowed if it
does not change an answer. Several of them exist specifically to fail if a
future change trades accuracy for speed:

* ``test_dtype_optimization_is_lossless`` and
  ``test_chunked_read_matches_a_direct_read`` pin ingest to producing exactly
  the frame a plain ``read_csv`` would have.
* ``test_vectorized_correlation_matches_scipy_exactly`` pins the correlation
  scan to scipy's own numbers, because that rewrite replaced 300 library calls
  with an analytic formula and the whole point is that nobody can tell.
* ``test_streamed_csv_export_is_byte_identical`` pins the chunked export to the
  single-shot output it replaced.
"""

import io
import itertools
import warnings

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from scipy import stats as scipy_stats

from app.analysis.statistics import compute_correlations
from app.data import ingest
from app.llm.context import build_context, estimate_tokens
from app.resources import HOST, can_admit, plan_for
from app.visualization.charts import PLOT_POINT_LIMIT, create_chart
from backend.main import _csv_chunks, app
from backend.session_store import Session


@pytest.fixture()
def client():
    return TestClient(app, raise_server_exceptions=False)


def _mixed_frame(n, seed=0):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({
        "measure": rng.normal(100, 15, size=n),
        "count_small": rng.integers(0, 100, size=n),
        "count_mid": rng.integers(0, 40_000, size=n),
        "region": rng.choice(["north", "south", "east", "west"], size=n),
        "uid": [f"id-{i}" for i in range(n)],
    })


# ── Ingest: never change a value to save a byte ───────────────────────────────

def test_dtype_optimization_is_lossless():
    df = _mixed_frame(60_000)
    optimized, changes = ingest.optimize_dtypes(df)

    assert changes, "expected integer downcasting and categorical encoding to apply"
    assert ingest.frame_bytes(optimized) < ingest.frame_bytes(df)
    assert list(optimized.columns) == list(df.columns)
    assert len(optimized) == len(df)
    for column in df.columns:
        original, new = df[column], optimized[column]
        if isinstance(new.dtype, pd.CategoricalDtype):
            assert (original.astype(str) == new.astype(str)).all()
        else:
            assert np.array_equal(original.to_numpy(), new.astype(original.dtype).to_numpy())


def test_floats_are_never_downcast():
    # Halving float64 would save more memory than everything else combined and
    # silently change every mean, CI and coefficient LANA reports.
    df = _mixed_frame(60_000)
    optimized, _ = ingest.optimize_dtypes(df)
    assert optimized["measure"].dtype == np.dtype("float64")


def test_small_frames_keep_the_dtypes_pandas_inferred():
    # Below the threshold the saving is trivial, and unsurprising behaviour on
    # the small files that make up most uploads matters more.
    df = _mixed_frame(100)
    optimized, changes = ingest.optimize_dtypes(df)
    assert changes == {}
    assert optimized is df


def test_free_text_columns_are_not_turned_into_categories():
    # profile.py classifies a mostly-distinct object column as TEXT. Converting
    # it to a categorical would make it look CATEGORICAL instead and change
    # every downstream judgement about it.
    df = pd.DataFrame({"note": [f"unique note number {i}" for i in range(60_000)]})
    optimized, changes = ingest.optimize_dtypes(df)
    assert "note" not in changes
    assert not isinstance(optimized["note"].dtype, pd.CategoricalDtype)


def test_chunked_read_matches_a_direct_read():
    df = _mixed_frame(120_000)
    payload = df.to_csv(index=False).encode()

    reference = pd.read_csv(io.BytesIO(payload))
    got, _ = ingest._read_csv_chunked(io.BytesIO(payload), chunk_rows=25_000)

    assert got.shape == reference.shape
    assert list(got.columns) == list(reference.columns)
    for column in reference.columns:
        left, right = reference[column], got[column]
        if isinstance(right.dtype, pd.CategoricalDtype):
            assert (left.astype(str) == right.astype(str)).all()
        else:
            assert np.array_equal(left.to_numpy(), right.astype(left.dtype).to_numpy())


def test_a_single_chunk_still_gets_its_string_savings():
    # Regression: the chunked reader reverts each chunk's categoricals so that
    # two chunks cannot disagree about a category set. With exactly one chunk
    # there is nothing to disagree with, and an early return skipped the
    # re-apply — so a file just over the chunked-read threshold kept none of
    # the savings a slightly smaller file received.
    df = pd.DataFrame({
        "region": np.resize(["north", "south", "east", "west"], 60_000),
        "measure": np.linspace(0, 1, 60_000),
    })
    payload = df.to_csv(index=False).encode()

    one_chunk, changes = ingest._read_csv_chunked(io.BytesIO(payload), chunk_rows=10 ** 9)
    assert isinstance(one_chunk["region"].dtype, pd.CategoricalDtype)
    assert "region" in changes

    many, _ = ingest._read_csv_chunked(io.BytesIO(payload), chunk_rows=20_000)
    assert isinstance(many["region"].dtype, pd.CategoricalDtype)
    assert ingest.frame_bytes(one_chunk) == ingest.frame_bytes(many)
    assert (one_chunk["region"].astype(str) == many["region"].astype(str)).all()


def test_projection_estimates_the_frame_cost_from_a_sample():
    df = _mixed_frame(80_000)
    payload = df.to_csv(index=False).encode()
    actual, _ = ingest.read_frame(io.BytesIO(payload), ".csv", len(payload))

    projected = ingest.project_csv_frame_bytes(io.BytesIO(payload), len(payload))
    assert projected is not None
    projected_bytes, projected_rows = projected

    # An estimate, not a measurement — it only has to be close enough to make
    # an admit/refuse decision without being wrong by an order of magnitude.
    assert 0.4 < projected_bytes / ingest.frame_bytes(actual) < 2.5
    assert 0.7 < projected_rows / len(df) < 1.4


def test_projection_declines_rather_than_guessing_on_junk():
    assert ingest.project_csv_frame_bytes(io.BytesIO(b""), 0) is None


# ── Correlation: 12x faster, same numbers ─────────────────────────────────────

@pytest.mark.parametrize("method", ["pearson", "spearman"])
def test_vectorized_correlation_matches_scipy_exactly(method):
    rng = np.random.default_rng(7)
    n = 4_000
    df = pd.DataFrame({
        "a": rng.normal(size=n),
        "c": rng.normal(size=n),
        "d": rng.normal(size=n),
    })
    df["b"] = 0.7 * df["a"] + rng.normal(0, 0.5, size=n)   # real relationship
    df["e"] = df["a"] * 2.0                                 # perfect |r| = 1
    # Different null patterns per column, so pairwise deletion gives a
    # different n for every pair — the case a matrix shortcut could get wrong.
    df.loc[rng.choice(n, 300, replace=False), "a"] = np.nan
    df.loc[rng.choice(n, 500, replace=False), "c"] = np.nan

    got = {(p["column_a"], p["column_b"]): p
           for p in compute_correlations(df, method=method)}
    reference = scipy_stats.pearsonr if method == "pearson" else scipy_stats.spearmanr

    assert got, "expected pairs to be returned"
    for col_a, col_b in itertools.combinations(df.columns, 2):
        pair = df[[col_a, col_b]].dropna()
        if len(pair) < 3:
            continue
        key = (col_a, col_b) if (col_a, col_b) in got else (col_b, col_a)
        assert key in got, f"missing pair {col_a}/{col_b}"
        entry = got[key]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            r_ref, p_ref = reference(pair[col_a], pair[col_b])
        assert entry["n"] == len(pair)
        assert entry["correlation"] == round(float(r_ref), 4)
        assert entry["p_value"] == round(float(p_ref), 6)


def test_constant_columns_are_still_skipped():
    df = pd.DataFrame({"flat": [3.0] * 50, "varies": np.arange(50.0)})
    assert compute_correlations(df) == []


# ── Session cache: compute once per version, invalidate on change ─────────────

def test_profiles_are_computed_once_per_version(monkeypatch):
    import backend.session_store as store_mod

    calls = []
    original = store_mod.profile_dataframe

    def counting(df, *args, **kwargs):
        calls.append(len(df))
        return original(df, *args, **kwargs)

    monkeypatch.setattr(store_mod, "profile_dataframe", counting)

    session = Session(session_id="s", filename="f.csv", raw=_mixed_frame(50))
    first = session.profiles()
    second = session.profiles()

    assert first is second
    assert len(calls) == 1, f"profiled {len(calls)} times, expected 1"


def test_recleaning_invalidates_only_the_cleaned_version():
    from app.data.lineage import CleaningLedger

    session = Session(session_id="s", filename="f.csv", raw=_mixed_frame(50))
    original_profiles = session.profiles()

    session.set_cleaned(_mixed_frame(40, seed=1), CleaningLedger())
    cleaned_profiles = session.profiles()
    assert cleaned_profiles is not original_profiles

    # A second clean must not hand back the first clean's profiles.
    session.set_cleaned(_mixed_frame(30, seed=2), CleaningLedger())
    assert session.profiles() is not cleaned_profiles
    assert len(session.profiles()["measure"].name) > 0

    # The original was never touched, so its entry is still valid.
    session.set_version("original")
    assert session.profiles() is original_profiles


def test_context_is_cached_and_rebuilt_after_cleaning():
    from app.data.lineage import CleaningLedger

    session = Session(session_id="s", filename="f.csv", raw=_mixed_frame(60))
    build = lambda df, profiles: build_context(df, profiles=profiles)

    first = session.context(build)
    assert session.context(build) is first

    session.set_cleaned(_mixed_frame(50, seed=3), CleaningLedger())
    assert session.context(build) is not first


# ── LLM context: bounded, and honest about what it dropped ────────────────────

def test_context_fits_the_token_budget_and_says_what_it_dropped():
    rng = np.random.default_rng(0)
    n = 3_000
    wide = pd.DataFrame({f"num{i}": rng.normal(size=n) for i in range(12)})
    for i in range(8):
        wide[f"cat{i}"] = rng.choice(list("abcdefghij"), size=n)

    generous = build_context(wide, token_budget=100_000)
    tight = build_context(wide, token_budget=1_600)

    assert generous.coverage["sections_dropped"] == []
    assert tight.coverage["sections_dropped"], "expected trimming under a tight budget"
    assert estimate_tokens(tight.text) < estimate_tokens(generous.text)
    # Whatever was dropped has to be admitted in the prompt itself, or the
    # model answers confidently from a context it cannot know is incomplete.
    assert "omitted entirely" in tight.text
    assert "LIMITS OF THIS CONTEXT" in tight.text


def test_facts_survive_trimming_so_verification_still_works():
    # The validator checks answers against what LANA computed, not against what
    # the model was shown. Trimming the prompt must not shrink the fact ledger.
    rng = np.random.default_rng(1)
    n = 2_000
    wide = pd.DataFrame({f"num{i}": rng.normal(size=n) for i in range(10)})
    for i in range(6):
        wide[f"cat{i}"] = rng.choice(list("abcde"), size=n)

    generous = build_context(wide, token_budget=100_000)
    tight = build_context(wide, token_budget=1_500)
    assert len(tight.facts) == len(generous.facts)


def test_no_budget_means_no_trimming():
    df = _mixed_frame(500)
    assert build_context(df).coverage["sections_dropped"] == []


def test_token_reserve_covers_the_reply_the_model_is_allowed_to_write():
    # Regression: the reserve was a flat 1200 tokens while the system prompt
    # alone measures ~585 and the model may generate LLM_MAX_TOKENS (2048) —
    # so a full context plus a long answer overflowed an 8192 window by ~1400
    # tokens, causing exactly the silent front-truncation this module exists
    # to prevent.
    from app.llm.base import ANSWER_SYSTEM_PROMPT
    from app.llm.context import context_token_reserve

    system_tokens = estimate_tokens(ANSWER_SYSTEM_PROMPT)
    window, max_answer = 8192, 2048

    reserve = context_token_reserve(max_answer)
    assert reserve >= system_tokens + max_answer, "reserve must cover prompt + full reply"

    allowance = window - reserve
    assert allowance + system_tokens + max_answer <= window, "worst case must fit"


def test_reserve_tracks_a_larger_configured_reply_budget():
    from app.llm.context import context_token_reserve

    assert context_token_reserve(4096) - context_token_reserve(2048) == 2048


def test_context_admits_when_even_the_column_list_overflows():
    # Only the optional sections are droppable. When the per-column detail
    # alone still exceeds the window, the context must say so rather than let
    # the runtime silently cut facts off the front.
    rng = np.random.default_rng(5)
    wide = pd.DataFrame({f"measure_{i}": rng.normal(size=200) for i in range(30)})

    ctx = build_context(wide, token_budget=3_000, max_answer_tokens=2048)

    assert ctx.coverage["over_budget"] is True
    assert "exceeds the space available" in ctx.text


def test_a_comfortable_budget_is_not_reported_as_over_budget():
    df = _mixed_frame(200)
    ctx = build_context(df, token_budget=100_000, max_answer_tokens=2048)
    assert ctx.coverage["over_budget"] is False


# ── Sampling: applied above the limit, and always disclosed ───────────────────

def test_plots_downsample_above_the_point_limit():
    from app.visualization.charts import _downsample

    small = _mixed_frame(1_000)
    kept, was_sampled = _downsample(small)
    assert was_sampled is False and len(kept) == len(small)

    big = _mixed_frame(PLOT_POINT_LIMIT + 5_000)
    kept, was_sampled = _downsample(big)
    assert was_sampled is True
    assert len(kept) == PLOT_POINT_LIMIT
    # Deterministic: the same dataset must always give the same picture.
    again, _ = _downsample(big)
    assert kept.index.equals(again.index)


def test_large_charts_still_render_and_stay_valid_png():
    df = _mixed_frame(PLOT_POINT_LIMIT + 20_000)
    for chart in ("Histogram", "Scatter Plot", "Line Plot", "Box Plot", "Violin Plot"):
        png = create_chart(df, chart, "measure")
        assert png[:8] == b"\x89PNG\r\n\x1a\n", f"{chart} did not produce a PNG"


def test_work_plan_reports_its_own_sampling():
    exact = plan_for(1_000)
    assert not exact.sampled and exact.note() is None

    sampled = plan_for(5_000_000)
    assert sampled.sampled
    assert "sample" in sampled.note()


# ── Streaming export: identical bytes, bounded memory ─────────────────────────

def test_streamed_csv_export_is_byte_identical():
    df = _mixed_frame(120_000)
    streamed = b"".join(_csv_chunks(df))
    assert streamed == df.to_csv(index=False).encode("utf-8")


def test_csv_export_endpoint_round_trips(client):
    csv = b"name,score\nAlice,1\nBob,2\n"
    sid = client.post("/upload", files={"file": ("t.csv", csv, "text/csv")}).json()["session_id"]
    body = client.get(f"/export/csv/{sid}").content
    assert pd.read_csv(io.BytesIO(body)).shape == (2, 2)


# ── Host-adaptive limits ──────────────────────────────────────────────────────

def test_host_budgets_are_coherent():
    assert HOST.total_bytes > 0
    assert 0 < HOST.available_bytes <= HOST.total_bytes
    assert HOST.cpu_count >= 1
    # The upload ceiling has to leave room for the parse to actually happen.
    assert HOST.upload_limit_bytes() < HOST.session_budget_bytes()


def test_admission_refuses_an_impossible_request():
    ok, free = can_admit(HOST.total_bytes * 10)
    assert ok is False
    assert free > 0

    ok, _ = can_admit(1024)
    assert ok is True


def test_health_reports_the_limits_it_derived(client):
    body = client.get("/health").json()
    assert body["ok"] is True
    assert body["host"]["total_mb"] > 0
    assert body["limits"]["max_upload_mb"] > 0
    # The session metric must say what it measures; frame bytes and process RSS
    # differ by several times and conflating them is how a cap stops protecting.
    assert "frame_mb" in body["sessions"]
    assert "RSS" in body["sessions"]["measures"]


def test_upload_reports_what_ingest_did(client):
    csv = _mixed_frame(200).to_csv(index=False).encode()
    body = client.post("/upload", files={"file": ("t.csv", csv, "text/csv")}).json()
    assert body["ingest"]["rows"] == 200
    assert body["ingest"]["columns"] == 5
    assert body["ingest"]["frame_mb"] >= 0
