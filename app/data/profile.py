"""Column profiling — the single statistical description LANA reasons from.

Every downstream decision reads a profile instead of recomputing statistics
ad hoc: which imputation to suggest, whether an outlier rule is even
applicable to a column, what the LLM is told about the data, and which
caveats travel with a number. Centralising this is what makes LANA's advice
consistent — the cleaning screen and the AI answer cannot disagree about
whether a column is skewed, because they read the same profile.

Profiling is strictly read-only. Nothing here modifies a DataFrame.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import pandas as pd


class ColumnKind(str, Enum):
    """Semantic role of a column, which is not the same as its storage dtype.

    An integer column of primary keys and an integer column of order counts
    are both ``int64``, but imputing the mean of the first is nonsense. Every
    statistical decision keys off this, not off ``dtype``.
    """

    NUMERIC = "numeric"
    CATEGORICAL = "categorical"
    DATETIME = "datetime"
    BOOLEAN = "boolean"
    TEXT = "text"              # high-cardinality free text — not a category
    IDENTIFIER = "identifier"  # near-unique key; arithmetic on it is meaningless
    CONSTANT = "constant"      # one distinct value — zero information, zero variance
    EMPTY = "empty"            # every value is null


# Identifier detection. Near-uniqueness alone is not enough — a latency or
# revenue column can legitimately have almost no repeated values. A key is
# near-unique AND either named like one or laid out as a near-contiguous run,
# which a measurement essentially never is.
_IDENTIFIER_UNIQUE_RATIO = 0.98
_IDENTIFIER_MIN_ROWS = 20
_IDENTIFIER_CONTIGUITY = 1.05
_ID_NAME_PATTERN = re.compile(
    r"(^|[_\s\-.])(id|ids|uuid|guid|key|index|idx|rowid|pk|sku)([_\s\-.]|$)",
    re.IGNORECASE,
)

# Free-text detection for object columns: mostly-distinct strings are notes,
# addresses, or IDs — grouping or mode-filling them is meaningless.
_TEXT_UNIQUE_RATIO = 0.90
_TEXT_MIN_ROWS = 20

# Integer columns with very few distinct values relative to their length are
# encoded categories (0/1 flags, 1-5 ratings, class labels). They stay NUMERIC
# — a point-biserial correlation or a regression on a binary predictor is
# perfectly valid — but outlier fences and mean-imputation are not meaningful
# on them, so they carry a separate flag rather than a different kind.
_DISCRETE_CODE_MAX_UNIQUE = 12
_DISCRETE_CODE_MIN_ROWS = 30
_DISCRETE_CODE_MAX_RATIO = 0.05

# Cardinality range within which grouping a column produces useful buckets.
_GROUPABLE_MAX_LEVELS = 30

# Columns LANA itself adds while cleaning. They are bookkeeping *about* the
# data, not measurements *of* it — an outlier score correlates 1.0 with the
# column it was derived from by construction, and without this exclusion that
# artifact surfaces as the dataset's strongest finding. They stay visible and
# usable for filtering; they are simply not treated as variables.
ANNOTATION_SUFFIXES = ("__outlier_score", "__outlier", "__was_missing", "__pre_winsorize")


def is_annotation_column(name: object) -> bool:
    """True for a column LANA generated rather than one the user uploaded."""
    return str(name).endswith(ANNOTATION_SUFFIXES)

# Skew thresholds follow the conventional interpretation of the moment
# coefficient of skewness (Bulmer's rule of thumb).
_SKEW_SYMMETRIC = 0.5
_SKEW_MODERATE = 1.0

# Above this fraction of missing values, imputing invents more data than it
# recovers — LANA refuses to recommend it and says so.
_IMPUTATION_REFUSAL_THRESHOLD = 0.40


def _finite(val: Any) -> Any:
    """Return None for NaN/Inf so profiles serialise cleanly to JSON."""
    if val is None:
        return None
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return None
    return val


def _round(val: Any, places: int = 4) -> Any:
    val = _finite(val)
    return round(float(val), places) if isinstance(val, (int, float)) else val


@dataclass
class ColumnProfile:
    """Everything LANA knows about one column, computed once."""

    name: str
    dtype: str
    kind: ColumnKind

    count: int              # non-null values
    null_count: int
    null_pct: float         # 0-100, of total rows
    unique: int
    unique_pct: float       # 0-100, of non-null values

    # Numeric-only — None for every other kind.
    min: float | None = None
    max: float | None = None
    mean: float | None = None
    median: float | None = None
    std: float | None = None
    mad: float | None = None       # median absolute deviation (robust spread)
    q1: float | None = None
    q3: float | None = None
    iqr: float | None = None
    skewness: float | None = None
    kurtosis: float | None = None
    zero_count: int | None = None
    negative_count: int | None = None

    # Categorical-only.
    top_values: list[tuple[str, int]] = field(default_factory=list)

    # Derived judgements — the reason this class exists.
    shape: str = "unknown"          # symmetric | moderately skewed | highly skewed
    robust_center: str = "mean"     # which centre statistic to trust
    discrete_code: bool = False     # numeric, but an encoded category
    caveats: list[str] = field(default_factory=list)

    @property
    def is_annotation(self) -> bool:
        """True for a column LANA added during cleaning, not user data."""
        return is_annotation_column(self.name)

    @property
    def is_numeric_measure(self) -> bool:
        """True when arithmetic (mean, regression, correlation) is meaningful.

        Deliberately excludes identifiers, constants and LANA's own annotation
        columns even though pandas reports them all as numeric dtypes.
        """
        return self.kind is ColumnKind.NUMERIC and not self.is_annotation

    @property
    def supports_outlier_analysis(self) -> bool:
        """True when 'unusually large/small' is a meaningful thing to ask.

        A 1-5 satisfaction rating has no outliers — a 5 is not anomalous, it
        is the top of the scale.
        """
        return self.is_numeric_measure and not self.discrete_code

    @property
    def is_groupable(self) -> bool:
        """True when splitting the data by this column yields useful buckets."""
        if self.is_annotation:
            return False
        if self.kind is ColumnKind.BOOLEAN:
            return True
        if self.kind is ColumnKind.CATEGORICAL:
            return 2 <= self.unique <= _GROUPABLE_MAX_LEVELS
        return self.discrete_code and 2 <= self.unique <= _GROUPABLE_MAX_LEVELS

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe representation for API responses."""
        out: dict[str, Any] = {
            "name": self.name,
            "dtype": self.dtype,
            "kind": self.kind.value,
            "count": self.count,
            "null_count": self.null_count,
            "null_pct": self.null_pct,
            "unique": self.unique,
            "unique_pct": self.unique_pct,
            "shape": self.shape,
            "robust_center": self.robust_center,
            "discrete_code": self.discrete_code,
            "annotation": self.is_annotation,
            "groupable": self.is_groupable,
            "supports_outlier_analysis": self.supports_outlier_analysis,
            "caveats": list(self.caveats),
        }
        for key in ("min", "max", "mean", "median", "std", "mad", "q1", "q3",
                    "iqr", "skewness", "kurtosis", "zero_count", "negative_count"):
            val = getattr(self, key)
            if val is not None:
                out[key] = val
        if self.top_values:
            out["top_values"] = [{"value": v, "count": c} for v, c in self.top_values]
        return out


def _looks_like_identifier(series: pd.Series, non_null: int, unique: int) -> bool:
    """Distinguish a key column from a near-unique measurement.

    Near-uniqueness on its own is not evidence: latency, revenue and duration
    columns are routinely 100% distinct. What a key adds is either a name that
    says so, or a near-contiguous run of integers, which a measured quantity
    essentially never produces.
    """
    if non_null < _IDENTIFIER_MIN_ROWS or unique / non_null < _IDENTIFIER_UNIQUE_RATIO:
        return False
    if _ID_NAME_PATTERN.search(str(series.name or "")):
        return True
    values = series.dropna()
    try:
        span = float(values.max()) - float(values.min()) + 1
    except (TypeError, ValueError):
        return False
    return 0 < span <= unique * _IDENTIFIER_CONTIGUITY


def _is_discrete_code(series: pd.Series, non_null: int, unique: int) -> bool:
    """True for integer columns that encode categories rather than measure."""
    if not pd.api.types.is_integer_dtype(series):
        return False
    return (
        unique <= _DISCRETE_CODE_MAX_UNIQUE
        and non_null >= _DISCRETE_CODE_MIN_ROWS
        and unique / non_null <= _DISCRETE_CODE_MAX_RATIO
    )


def _classify(series: pd.Series, non_null: int, unique: int) -> ColumnKind:
    """Assign a semantic kind from dtype plus cardinality evidence."""
    if non_null == 0:
        return ColumnKind.EMPTY
    if unique <= 1:
        return ColumnKind.CONSTANT

    if pd.api.types.is_bool_dtype(series):
        return ColumnKind.BOOLEAN
    if pd.api.types.is_datetime64_any_dtype(series):
        return ColumnKind.DATETIME

    if pd.api.types.is_numeric_dtype(series):
        if (pd.api.types.is_integer_dtype(series)
                and _looks_like_identifier(series, non_null, unique)):
            return ColumnKind.IDENTIFIER
        return ColumnKind.NUMERIC

    if isinstance(series.dtype, pd.CategoricalDtype):
        return ColumnKind.CATEGORICAL

    # Object / string.
    if non_null >= _TEXT_MIN_ROWS and unique / non_null >= _TEXT_UNIQUE_RATIO:
        return ColumnKind.TEXT
    return ColumnKind.CATEGORICAL


def _describe_shape(skew: float | None) -> str:
    if skew is None:
        return "unknown"
    magnitude = abs(skew)
    if magnitude < _SKEW_SYMMETRIC:
        return "symmetric"
    if magnitude < _SKEW_MODERATE:
        return "moderately skewed"
    return "highly skewed"


def profile_column(series: pd.Series, total_rows: int | None = None) -> ColumnProfile:
    """Compute the full profile for a single column.

    ``total_rows`` defaults to the series length; pass it explicitly when
    profiling a subset so null percentages stay relative to the full frame.
    """
    total = total_rows if total_rows is not None else len(series)
    non_null_series = series.dropna()
    count = int(len(non_null_series))
    null_count = int(total - count)
    unique = int(non_null_series.nunique())

    kind = _classify(series, count, unique)
    discrete_code = kind is ColumnKind.NUMERIC and _is_discrete_code(series, count, unique)

    profile = ColumnProfile(
        name=str(series.name),
        dtype=str(series.dtype),
        kind=kind,
        count=count,
        null_count=null_count,
        null_pct=round(null_count / total * 100, 2) if total else 0.0,
        unique=unique,
        unique_pct=round(unique / count * 100, 2) if count else 0.0,
        discrete_code=discrete_code,
    )

    # ── Numeric detail ───────────────────────────────────────────────────────
    # Computed for identifiers too (min/max of an ID is legitimately useful),
    # but `is_numeric_measure` still gates the statistical machinery.
    if pd.api.types.is_numeric_dtype(series) and count > 0 and kind is not ColumnKind.EMPTY:
        s = non_null_series.astype("float64")
        q1, q3 = float(s.quantile(0.25)), float(s.quantile(0.75))
        median = float(s.median())
        profile.min = _round(float(s.min()))
        profile.max = _round(float(s.max()))
        profile.mean = _round(float(s.mean()))
        profile.median = _round(median)
        profile.std = _round(float(s.std())) if count > 1 else None
        profile.mad = _round(float((s - median).abs().median()))
        profile.q1 = _round(q1)
        profile.q3 = _round(q3)
        profile.iqr = _round(q3 - q1)
        profile.zero_count = int((s == 0).sum())
        profile.negative_count = int((s < 0).sum())
        # Skew/kurtosis are undefined below 3/4 points and explode on tiny
        # samples — reporting them there would be noise dressed as signal.
        if count >= 8:
            profile.skewness = _round(float(s.skew()))
            profile.kurtosis = _round(float(s.kurt()))

    # ── Categorical detail ───────────────────────────────────────────────────
    # Discrete codes get value counts too: "how many rows scored 5" is the
    # question people actually ask of a rating column.
    if count > 0 and (kind in (ColumnKind.CATEGORICAL, ColumnKind.BOOLEAN) or discrete_code):
        counts = non_null_series.value_counts().head(10)
        profile.top_values = [(str(idx), int(val)) for idx, val in counts.items()]

    # ── Derived judgements ───────────────────────────────────────────────────
    profile.shape = _describe_shape(profile.skewness)
    profile.robust_center = (
        "median" if profile.shape in ("moderately skewed", "highly skewed") else "mean"
    )
    profile.caveats = _build_caveats(profile)

    return profile


def _build_caveats(p: ColumnProfile) -> list[str]:
    """Plain-English warnings that must travel with this column's numbers."""
    caveats: list[str] = []

    if p.kind is ColumnKind.EMPTY:
        caveats.append("Every value is missing — this column carries no information.")
        return caveats
    if p.kind is ColumnKind.CONSTANT:
        caveats.append(
            "Only one distinct value — zero variance, so correlation and "
            "regression against it are undefined."
        )
        return caveats
    if p.kind is ColumnKind.IDENTIFIER:
        caveats.append(
            "Values are near-unique, so this looks like an ID rather than a "
            "measurement. Averaging it or treating its extremes as outliers "
            "is not meaningful."
        )
    if p.kind is ColumnKind.TEXT:
        caveats.append(
            "High-cardinality free text — grouping or mode-filling it will "
            "not produce meaningful categories."
        )
    if p.discrete_code:
        caveats.append(
            f"Only {p.unique} distinct integer values across {p.count:,} rows — this "
            "looks like an encoded category (a flag, rating or class label). "
            "Correlating or regressing on it is valid, but its extremes are the "
            "ends of a scale, not anomalies, and its mean is only meaningful if "
            "the codes are genuinely ordered."
        )

    if p.null_pct >= _IMPUTATION_REFUSAL_THRESHOLD * 100:
        caveats.append(
            f"{p.null_pct}% of values are missing. Imputing would invent more "
            "data than it recovers — prefer flagging the gap, or analyse only "
            "the rows that have this value."
        )
    elif p.null_pct >= 10:
        caveats.append(
            f"{p.null_pct}% missing — whatever fill you choose will shrink this "
            "column's real variance. Keep the missingness flag."
        )

    if p.supports_outlier_analysis and p.shape == "highly skewed":
        caveats.append(
            f"Highly skewed (skewness {p.skewness}). The mean is pulled toward "
            "the tail; the median is the more honest centre, and IQR outlier "
            "fences will over-flag the long tail as anomalies."
        )
    if p.supports_outlier_analysis and p.kurtosis is not None and p.kurtosis > 3:
        caveats.append(
            f"Heavy-tailed (excess kurtosis {p.kurtosis}). Extreme values are "
            "expected here, not necessarily errors."
        )
    if p.is_numeric_measure and p.zero_count and p.count and p.zero_count / p.count > 0.5:
        caveats.append(
            f"{p.zero_count} of {p.count} values are exactly zero — this may be "
            "a sparse or 'not applicable' encoding rather than a true measurement."
        )

    return caveats


def profile_dataframe(
    df: pd.DataFrame,
    max_columns: int | None = None,
) -> dict[str, ColumnProfile]:
    """Profile every column, preserving order.

    ``max_columns`` caps the work on very wide frames; the cap is a
    performance guard for the callers that render profiles into a bounded
    LLM prompt, not a correctness limit.
    """
    columns = df.columns if max_columns is None else df.columns[:max_columns]
    total = len(df)
    return {str(col): profile_column(df[col], total_rows=total) for col in columns}


def dataset_quality(profiles: dict[str, ColumnProfile], total_rows: int) -> dict[str, Any]:
    """Aggregate profiles into an at-a-glance data quality summary.

    The score is a transparent deduction from a clean baseline of 100 — every
    penalty is itemised in ``issues`` so it can be defended, never a black-box
    number. It ranks datasets; it does not certify them.
    """
    if not profiles:
        return {"score": 0, "grade": "unknown", "issues": [], "total_cells": 0}

    total_cells = total_rows * len(profiles)
    missing_cells = sum(p.null_count for p in profiles.values())
    missing_pct = (missing_cells / total_cells * 100) if total_cells else 0.0

    empty_cols = [p.name for p in profiles.values() if p.kind is ColumnKind.EMPTY]
    constant_cols = [p.name for p in profiles.values() if p.kind is ColumnKind.CONSTANT]
    high_missing = [p.name for p in profiles.values()
                    if p.null_pct >= _IMPUTATION_REFUSAL_THRESHOLD * 100]
    skewed = [p.name for p in profiles.values()
              if p.is_numeric_measure and p.shape == "highly skewed"]

    issues: list[dict[str, Any]] = []
    score = 100.0

    if missing_pct > 0:
        penalty = min(30.0, missing_pct * 1.5)
        score -= penalty
        issues.append({
            "issue": "missing_values",
            "detail": f"{missing_cells:,} of {total_cells:,} cells ({missing_pct:.1f}%) are empty.",
            "penalty": round(penalty, 1),
        })
    for name, cols, per_col, label in (
        ("empty_columns", empty_cols, 8.0, "carry no data at all"),
        ("constant_columns", constant_cols, 3.0, "have a single repeated value"),
        ("high_missingness_columns", high_missing, 5.0,
         "are missing more than 40% of their values"),
    ):
        if cols:
            penalty = min(20.0, per_col * len(cols))
            score -= penalty
            issues.append({
                "issue": name,
                "detail": f"{len(cols)} column(s) {label}: {', '.join(cols[:5])}"
                          + ("…" if len(cols) > 5 else ""),
                "penalty": round(penalty, 1),
            })

    score = max(0.0, min(100.0, score))
    grade = ("excellent" if score >= 90 else "good" if score >= 75
             else "fair" if score >= 55 else "poor")

    return {
        "score": round(score, 1),
        "grade": grade,
        "total_rows": total_rows,
        "total_columns": len(profiles),
        "total_cells": total_cells,
        "missing_cells": missing_cells,
        "missing_pct": round(missing_pct, 2),
        "issues": issues,
        # Surfaced separately: skew is a modelling caveat, not a quality defect,
        # so it never costs the dataset points.
        "skewed_columns": skewed,
    }


def suggest_imputation(profile: ColumnProfile) -> dict[str, Any]:
    """Recommend how to handle a column's missing values, with reasoning.

    Returns the recommended method plus the rationale LANA must show the
    user. Deliberately refuses to recommend imputation when missingness is
    high enough that filling would fabricate rather than recover.
    """
    if profile.null_count == 0:
        return {"method": None, "rationale": "No missing values in this column."}

    if profile.null_pct >= _IMPUTATION_REFUSAL_THRESHOLD * 100:
        return {
            "method": "flag",
            "rationale": (
                f"{profile.null_pct}% of this column is missing. Any fill value "
                "would dominate the column's real signal, so LANA recommends "
                "keeping the nulls and flagging them instead of imputing."
            ),
            "alternatives": ["drop", "mode" if not profile.is_numeric_measure else "median"],
            "refuses_imputation": True,
        }

    if profile.kind is ColumnKind.IDENTIFIER:
        return {
            "method": "flag",
            "rationale": (
                "This column looks like an identifier. A missing ID cannot be "
                "recovered by imputation — filling it would create false "
                "duplicate keys."
            ),
            "alternatives": ["drop"],
            "refuses_imputation": True,
        }

    if profile.kind is ColumnKind.TEXT:
        return {
            "method": "flag",
            "rationale": (
                "High-cardinality free text — the mode is arbitrary here, so "
                "filling adds a fake value rather than recovering the real one."
            ),
            "alternatives": ["drop"],
            "refuses_imputation": True,
        }

    if not profile.is_numeric_measure or profile.discrete_code:
        label = "Encoded category" if profile.discrete_code else "Categorical column"
        return {
            "method": "mode",
            "rationale": (
                f"{label} — the most frequent value is the only defensible fill; "
                f"an average of category codes is not a real category. Note this "
                f"inflates that value's share by {profile.null_count} row(s)."
            ),
            "alternatives": ["drop", "flag"],
        }

    if profile.shape in ("moderately skewed", "highly skewed"):
        return {
            "method": "median",
            "rationale": (
                f"Distribution is {profile.shape} (skewness {profile.skewness}), "
                f"so the mean ({profile.mean}) is pulled toward the tail. The "
                f"median ({profile.median}) is the robust centre and the safer fill."
            ),
            "alternatives": ["mean", "drop", "flag"],
        }

    return {
        "method": "median",
        "rationale": (
            f"Distribution is roughly symmetric, so mean ({profile.mean}) and "
            f"median ({profile.median}) agree closely. LANA defaults to the "
            f"median because it stays correct if the distribution turns out to "
            f"be skewed. Either fill shrinks the column's variance."
        ),
        "alternatives": ["mean", "drop", "flag"],
    }
