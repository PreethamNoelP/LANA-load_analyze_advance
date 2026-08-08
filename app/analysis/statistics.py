"""Descriptive statistics and correlation analysis.

Two principles distinguish this from calling ``df.describe()``:

* **Every estimate carries its uncertainty.** A mean computed from 12 rows
  and a mean computed from 120,000 rows are not the same claim, and LANA
  reports the confidence interval that separates them.
* **Multiple testing is corrected.** Scanning every column pair for
  correlation runs C(k,2) hypothesis tests at once. With 20 numeric columns
  that is 190 tests, and at alpha = 0.05 roughly 10 of them return
  "significant" from pure noise. Raw p-values ranked by strength are a
  false-discovery machine, so LANA reports Benjamini-Hochberg q-values and
  judges significance on those.
"""

from __future__ import annotations

import warnings
from itertools import combinations
from typing import Any, Literal

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

from ..data.profile import ColumnKind, profile_column, profile_dataframe

# Benjamini-Hochberg false discovery rate. 0.05 keeps the expected share of
# false positives among reported discoveries at 5%.
FDR_ALPHA = 0.05

# The pair count grows quadratically, so a wide frame has to be capped or a
# single request ties up a worker for minutes. 25 columns is 300 pairs.
MAX_CORRELATION_COLUMNS = 25

# Above this row count a deterministic sample gives the same coefficients to
# several decimal places at a fraction of the cost. random_state is fixed so
# repeated requests return identical numbers.
MAX_CORRELATION_ROWS = 200_000
_SAMPLE_SEED = 0

# Correlation strength labels (absolute value of r), following the
# conventional social-science reading of effect size.
_STRENGTH_BANDS = ((0.7, "strong"), (0.4, "moderate"), (0.2, "weak"))


def _round(val, places: int = 4):
    if val is None:
        return None
    val = float(val)
    return None if (np.isnan(val) or np.isinf(val)) else round(val, places)


def compute_statistics(series: pd.Series) -> dict[str, Any]:
    """Descriptive statistics for a single column, with uncertainty.

    Always returns count, null count and unique count. Numeric columns also
    get centre, spread, percentiles, shape, and — the part that makes the
    numbers usable — the standard error and 95% confidence interval of the
    mean, plus robust alternatives that survive outliers.
    """
    profile = profile_column(series)

    stats: dict[str, Any] = {
        "count": profile.count,
        "null_count": profile.null_count,
        "unique": profile.unique,
        # Interpretation metadata: what kind of column this is, and which
        # centre statistic can be trusted for it.
        "kind": profile.kind.value,
        "shape": profile.shape,
        "robust_center": profile.robust_center,
        "caveats": profile.caveats,
    }

    mode_vals = series.mode()
    stats["mode"] = mode_vals.iloc[0] if not mode_vals.empty else None

    if not pd.api.types.is_numeric_dtype(series):
        return stats

    values = series.dropna().astype("float64")
    n = len(values)

    stats.update({
        "min": profile.min,
        "max": profile.max,
        "mean": profile.mean,
        "median": profile.median,
        "std": profile.std,
        "variance": _round(float(values.var())) if n > 1 else None,
        "p25": profile.q1,
        "p75": profile.q3,
        "iqr": profile.iqr,
        "skewness": profile.skewness,
        "kurtosis": profile.kurtosis,
        # Robust spread — unlike std, a single extreme value cannot inflate it.
        "mad": profile.mad,
        "zero_count": profile.zero_count,
        "negative_count": profile.negative_count,
    })

    # ── Uncertainty on the mean ──────────────────────────────────────────────
    # Without this, "mean = 47.3" reads as a fact about the population when it
    # is an estimate with a spread that may be wider than the effect anyone
    # cares about.
    if n >= 2 and profile.std is not None and profile.std > 0:
        sem = float(values.std(ddof=1) / np.sqrt(n))
        # t rather than z: correct for small samples, converges to z for large.
        margin = float(scipy_stats.t.ppf(0.975, df=n - 1) * sem)
        mean = float(values.mean())
        stats["std_error"] = _round(sem)
        stats["ci95_low"] = _round(mean - margin)
        stats["ci95_high"] = _round(mean + margin)
        stats["ci95_interpretation"] = (
            f"95% confident the true mean lies between {mean - margin:.4g} and "
            f"{mean + margin:.4g}, based on {n:,} non-null value(s)."
        )
    else:
        stats["std_error"] = None
        stats["ci95_interpretation"] = (
            "Too few distinct values to estimate the uncertainty of the mean."
            if n >= 2 else
            f"Only {n} non-null value(s) — no uncertainty estimate is possible."
        )

    # ── Distribution shape test ──────────────────────────────────────────────
    # Reported as evidence, not as a gate. Note that with large n, normality
    # tests reject on trivial departures, so the verdict is phrased carefully.
    if 8 <= n <= 5000:
        try:
            _, p_normal = scipy_stats.shapiro(values)
            stats["normality_p"] = _round(p_normal, 6)
            stats["normality"] = (
                "consistent with normal" if p_normal > 0.05
                else "significantly non-normal"
            )
        except Exception:
            stats["normality"] = "not testable"
    elif n > 5000:
        stats["normality"] = (
            "not tested — at this sample size normality tests reject on "
            "differences too small to matter; read the skewness and kurtosis instead"
        )

    return stats


def _bh_qvalues(p_values: list[float]) -> list[float]:
    """Benjamini-Hochberg adjusted p-values (q-values), input order preserved.

    q_(i) = min over j >= i of ( n / j * p_(j) ), enforced monotone from the
    largest p-value downward.
    """
    n = len(p_values)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: p_values[i])
    q = [0.0] * n
    running_min = 1.0
    for rank in range(n, 0, -1):          # walk from the largest p-value down
        idx = order[rank - 1]
        running_min = min(running_min, p_values[idx] * n / rank)
        q[idx] = min(running_min, 1.0)
    return q


def _strength_label(r: float) -> str:
    magnitude = abs(r)
    for threshold, label in _STRENGTH_BANDS:
        if magnitude >= threshold:
            return label
    return "negligible"


def compute_correlations(
    df: pd.DataFrame,
    method: Literal["pearson", "spearman"] = "pearson",
    alpha: float = FDR_ALPHA,
) -> list[dict[str, Any]]:
    """Pairwise correlation between numeric column pairs, ranked by strength.

    Rows with a null in either column of a pair are dropped before computing
    that pair's correlation (pairwise deletion), so ``n`` differs per pair.
    Pairs with fewer than 3 overlapping values, or a constant column (zero
    variance, undefined correlation), are skipped.

    Every returned pair carries a Benjamini-Hochberg q-value alongside its raw
    p-value, and ``significant`` is judged on the q-value. Columns that are
    identifiers or encoded categories are excluded: correlating a row ID with
    a measurement produces a real-looking coefficient that means nothing.
    """
    profiles = profile_dataframe(df)
    numeric_cols = [
        name for name, p in profiles.items()
        if p.is_numeric_measure and p.count >= 3
    ]
    excluded = [
        {"column": name,
         "reason": "LANA annotation, derived from another column" if p.is_annotation
                   else p.kind.value}
        for name, p in profiles.items()
        if pd.api.types.is_numeric_dtype(df[name]) and not p.is_numeric_measure
    ]

    truncated_columns = numeric_cols[MAX_CORRELATION_COLUMNS:]
    numeric_cols = numeric_cols[:MAX_CORRELATION_COLUMNS]

    # Sampling is deterministic, so the same upload always yields the same
    # coefficients — a correlation that changes between refreshes is worse
    # than one computed slightly more cheaply.
    sampled = len(df) > MAX_CORRELATION_ROWS
    source = df.sample(MAX_CORRELATION_ROWS, random_state=_SAMPLE_SEED) if sampled else df

    corr_fn = scipy_stats.spearmanr if method == "spearman" else scipy_stats.pearsonr

    raw: list[dict[str, Any]] = []
    for col_a, col_b in combinations(numeric_cols, 2):
        pair = source[[col_a, col_b]].dropna()
        if len(pair) < 3:
            continue
        try:
            # A constant column has undefined correlation — scipy warns and
            # returns nan, which is filtered out below anyway.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=scipy_stats.ConstantInputWarning)
                correlation, p_value = corr_fn(pair[col_a], pair[col_b])
        except Exception:
            continue
        if pd.isna(correlation):
            continue
        raw.append({
            "column_a": col_a,
            "column_b": col_b,
            "correlation": round(float(correlation), 4),
            "p_value": round(float(p_value), 6),
            "n": int(len(pair)),
        })

    if not raw:
        return []

    q_values = _bh_qvalues([r["p_value"] for r in raw])
    tests = len(raw)

    for entry, q in zip(raw, q_values):
        r = entry["correlation"]
        entry["q_value"] = round(float(q), 6)
        entry["significant"] = bool(q < alpha)
        entry["strength"] = _strength_label(r)
        entry["direction"] = "positive" if r > 0 else "negative" if r < 0 else "none"
        # r^2 is the honest effect size: how much of one column's variance the
        # other accounts for. An r of 0.3 sounds meaningful until it is
        # restated as "explains 9% of the variation".
        entry["r_squared"] = round(r * r, 4)
        entry["interpretation"] = _describe_correlation(entry, method, tests)

    raw.sort(key=lambda e: abs(e["correlation"]), reverse=True)

    notes = list(excluded)
    if truncated_columns:
        notes.append({
            "column": f"+{len(truncated_columns)} more numeric columns",
            "reason": f"only the first {MAX_CORRELATION_COLUMNS} numeric columns were tested",
        })
    if sampled:
        notes.append({
            "column": "(all)",
            "reason": f"computed on a fixed random sample of {MAX_CORRELATION_ROWS:,} "
                      f"rows out of {len(df):,}",
        })
    if notes:
        # Attached to the first row so the caller can surface it without a
        # separate response shape. Consumers that ignore it lose nothing.
        raw[0]["_excluded_columns"] = notes
    return raw


def _describe_correlation(entry: dict[str, Any], method: str, tests: int) -> str:
    r = entry["correlation"]
    label = f"{entry['strength']} {entry['direction']}" if entry["direction"] != "none" else "no"
    base = (
        f"{label} {method} correlation (r = {r:.3f}) between "
        f"'{entry['column_a']}' and '{entry['column_b']}' over {entry['n']:,} "
        f"paired values; it accounts for {entry['r_squared'] * 100:.1f}% of the "
        f"variation in either column."
    )
    if entry["significant"]:
        verdict = (
            f" This survives false-discovery correction across all {tests} pairs "
            f"tested (q = {entry['q_value']:.4f})."
        )
    else:
        verdict = (
            f" This does NOT survive false-discovery correction across all {tests} "
            f"pairs tested (q = {entry['q_value']:.4f}), so it is consistent with "
            f"chance even though its raw p-value is {entry['p_value']:.4f}."
        )
    return base + verdict + " Correlation does not establish causation."


MAX_CONTEXT_COLUMNS = 40


def generate_context(df: pd.DataFrame) -> str:
    """Build a text description of a DataFrame to pass as LLM context.

    Retained for report exports and backward compatibility. The AI question
    path uses :func:`app.llm.context.build_context`, which grounds answers in
    a verifiable fact set rather than summary statistics alone.
    """
    lines = [
        f"Dataset: {len(df):,} rows x {len(df.columns)} columns.",
        f"Columns: {', '.join(str(c) for c in df.columns)}.",
        "",
    ]

    detail_cols = df.columns[:MAX_CONTEXT_COLUMNS]
    omitted = len(df.columns) - len(detail_cols)

    for col in detail_cols:
        if pd.api.types.is_numeric_dtype(df[col]):
            s = df[col].dropna()
            if s.empty:
                lines.append(f"- '{col}' (numeric): all values are null.")
            else:
                lines.append(
                    f"- '{col}' (numeric): "
                    f"min={s.min():.4g}, max={s.max():.4g}, "
                    f"mean={s.mean():.4g}, median={s.median():.4g}, "
                    f"std={s.std():.4g}, nulls={df[col].isnull().sum()}"
                )
        else:
            sample = df[col].dropna().head(5).tolist()
            lines.append(
                f"- '{col}' (categorical): {df[col].nunique()} unique values, "
                f"sample={sample}, nulls={df[col].isnull().sum()}"
            )

    if omitted > 0:
        lines.append(f"\n(+{omitted} more columns not detailed here — ask about a specific one by name.)")

    return "\n".join(lines)


def generate_recommendations(df: pd.DataFrame) -> dict[str, list[str]]:
    """Suggest charts and analyses that suit this dataset's actual properties.

    Driven by column profiles rather than column position: a scatter plot is
    only proposed for a pair that actually correlates, a time series only when
    a real datetime column exists, and a log-scale histogram only for columns
    whose skew warrants it.
    """
    profiles = profile_dataframe(df)
    numeric = [n for n, p in profiles.items() if p.is_numeric_measure and not p.discrete_code]
    categorical = [n for n, p in profiles.items() if p.is_groupable]
    datetime_cols = [n for n, p in profiles.items() if p.kind is ColumnKind.DATETIME]
    skewed = [n for n in numeric if profiles[n].shape == "highly skewed"]
    missing = [n for n, p in profiles.items() if p.null_pct >= 10]

    viz: list[str] = []
    analysis: list[str] = []

    if datetime_cols and numeric:
        viz.append(f"Line Plot — {numeric[0]} over '{datetime_cols[0]}'")
    if skewed:
        viz.append(f"Histogram — '{skewed[0]}' is highly skewed; check its long tail")
    elif numeric:
        viz.append(f"Histogram — distribution of '{numeric[0]}'")

    # Only propose a scatter plot for a pair with a real relationship.
    if len(numeric) >= 2:
        pairs = compute_correlations(df[numeric])
        strong = [p for p in pairs if p["significant"] and abs(p["correlation"]) >= 0.4]
        if strong:
            best = strong[0]
            viz.append(
                f"Scatter Plot — '{best['column_a']}' vs '{best['column_b']}' "
                f"(r = {best['correlation']})"
            )
            analysis.append(
                f"Linear Regression — '{best['column_b']}' on '{best['column_a']}' "
                f"({best['strength']} {best['direction']} relationship)"
            )
        else:
            analysis.append(
                "Correlation scan — no pair passed false-discovery correction, "
                "so treat any apparent relationship as unproven"
            )
        viz.append("Heatmap — correlation matrix across all numeric columns")

    if numeric:
        viz.append(f"Box Plot — spread and flagged extremes in '{numeric[0]}'")
        analysis.append(f"Statistical Summary — '{numeric[0]}' with 95% confidence interval")
    if categorical:
        viz.append(f"Bar Chart — frequency of '{categorical[0]}'")
        analysis.append(f"Value Counts — '{categorical[0]}'")
        if numeric:
            analysis.append(f"Group comparison — '{numeric[0]}' broken down by '{categorical[0]}'")
    if missing:
        analysis.append(
            f"Missingness review — '{missing[0]}' is {profiles[missing[0]].null_pct}% empty; "
            "check whether it is missing at random before imputing"
        )

    return {"visualization": viz[:5], "analysis": analysis[:5]}
