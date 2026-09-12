"""Outlier detection — annotate first, delete only on explicit instruction.

LANA's position on outliers is that they are *findings*, not defects. A
$4.2M order, a 300-year-old customer, and a sensor reading of -273°C are all
"outliers" by the same arithmetic, but one is the most valuable row in the
dataset, one is a data entry bug, and one is a physical impossibility.
Arithmetic cannot tell them apart, so this module never decides — it
measures, disagrees with itself out loud when the methods disagree, and
hands the judgement to the user.

Two independent rules are always computed:

* **Tukey IQR fences** — what most people mean by "outlier". Assumes a
  roughly symmetric distribution; over-flags the long tail of skewed data.
* **Modified z-score (MAD)** — Iglewicz & Hoaglin's robust rule. Uses the
  median and median absolute deviation, so a handful of extreme values
  cannot inflate the spread and hide themselves.

Where the two disagree, that disagreement is itself reported, because it is
the strongest available signal that a column is skewed rather than corrupt.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

import pandas as pd

from .profile import ColumnProfile, profile_column

Method = Literal["iqr", "modified_zscore"]

# Tukey's fence multiplier. 1.5 is the conventional "outlier" boundary;
# 3.0 marks "far out" points.
IQR_MULTIPLIER = 1.5
IQR_EXTREME_MULTIPLIER = 3.0

# Iglewicz & Hoaglin (1993) recommend 3.5 on the modified z-score.
MODIFIED_Z_THRESHOLD = 3.5

# 0.6745 is the 0.75 quantile of the standard normal — it rescales the MAD so
# that for normally distributed data the modified z-score matches an ordinary
# z-score.
_MAD_TO_SIGMA = 0.6745

# Flagging more than this share of a column means the rule is describing the
# distribution's shape, not finding anomalies within it.
_OVER_FLAG_FRACTION = 0.10

# Below this many points, quantiles are too unstable for either rule to say
# anything meaningful.
MIN_POINTS = 8


@dataclass
class OutlierMethodResult:
    """One detection rule's verdict on one column."""

    method: Method
    applicable: bool
    count: int = 0
    fraction: float = 0.0            # of non-null values
    lower_bound: float | None = None
    upper_bound: float | None = None
    threshold: float | None = None   # modified z-score cutoff, when applicable
    extreme_count: int = 0           # beyond the "far out" fence
    reason_unavailable: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "method": self.method,
            "applicable": self.applicable,
            "count": self.count,
            "fraction": round(self.fraction, 4),
            "extreme_count": self.extreme_count,
        }
        for key in ("lower_bound", "upper_bound", "threshold"):
            val = getattr(self, key)
            if val is not None:
                out[key] = val
        if self.reason_unavailable:
            out["reason_unavailable"] = self.reason_unavailable
        return out


@dataclass
class OutlierReport:
    """Both rules' verdicts plus LANA's recommendation for one column."""

    column: str
    n: int
    iqr: OutlierMethodResult
    modified_zscore: OutlierMethodResult
    recommended_method: Method | None
    recommended_action: str          # annotate | investigate | none | not_applicable
    interpretation: str
    caveats: list[str] = field(default_factory=list)
    sample_values: list[float] = field(default_factory=list)
    agreement: dict[str, int] = field(default_factory=dict)

    @property
    def has_candidates(self) -> bool:
        return max(self.iqr.count, self.modified_zscore.count) > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "column": self.column,
            "n": self.n,
            "iqr": self.iqr.to_dict(),
            "modified_zscore": self.modified_zscore.to_dict(),
            "recommended_method": self.recommended_method,
            "recommended_action": self.recommended_action,
            "interpretation": self.interpretation,
            "caveats": list(self.caveats),
            "sample_values": list(self.sample_values),
            "agreement": dict(self.agreement),
        }


def _numeric_values(series: pd.Series) -> pd.Series:
    return series.dropna().astype("float64")


def too_few_points(series: pd.Series) -> str | None:
    """Why this column is too small for outlier analysis, or None if it's fine.

    :func:`detect_outliers` — the preview path — already refuses below
    ``MIN_POINTS``. Exposing the same check lets the *apply* path refuse for
    the identical reason, so the preview cannot report "not applicable" for a
    column that cleaning then goes ahead and deletes rows from anyway. The
    threshold lives here, with the rules it protects, rather than being
    restated by each caller.
    """
    n = int(_numeric_values(series).size)
    if n < MIN_POINTS:
        return (
            f"Only {n} non-null value(s). Below {MIN_POINTS} points, quantiles "
            "are too unstable for outlier detection to mean anything."
        )
    return None


def iqr_mask(series: pd.Series, multiplier: float = IQR_MULTIPLIER) -> tuple[pd.Series, float, float]:
    """Boolean mask of Tukey-fence outliers, aligned to ``series``' index.

    Nulls are never flagged — a missing value is a separate concern with its
    own handling, and conflating the two hides both.
    """
    values = _numeric_values(series)
    q1, q3 = float(values.quantile(0.25)), float(values.quantile(0.75))
    iqr = q3 - q1
    lower, upper = q1 - multiplier * iqr, q3 + multiplier * iqr
    numeric = pd.to_numeric(series, errors="coerce")
    mask = ((numeric < lower) | (numeric > upper)).fillna(False)
    return mask.astype(bool), lower, upper


def modified_zscore(series: pd.Series) -> tuple[pd.Series, float | None]:
    """Iglewicz-Hoaglin modified z-scores, aligned to ``series``' index.

    Returns ``(scores, scale)``. ``scale`` is None when the column has no
    robust spread at all (more than half the values identical *and* the mean
    absolute deviation is zero), in which case the rule cannot be applied and
    scores are all NaN.
    """
    numeric = pd.to_numeric(series, errors="coerce")
    values = numeric.dropna()
    if values.empty:
        return pd.Series(float("nan"), index=series.index), None

    median = float(values.median())
    mad = float((values - median).abs().median())

    if mad > 0:
        scale = mad / _MAD_TO_SIGMA
    else:
        # MAD collapses to zero when >50% of values are identical. Iglewicz &
        # Hoaglin's documented fallback is the mean absolute deviation, which
        # survives that case.
        mean_ad = float((values - median).abs().mean())
        if mean_ad <= 0:
            return pd.Series(float("nan"), index=series.index), None
        scale = mean_ad / 0.7979  # E|X-mu|/sigma for a normal distribution

    return (numeric - median) / scale, scale


def detect_outliers(series: pd.Series, profile: ColumnProfile | None = None) -> OutlierReport:
    """Run both rules over one column and interpret the result.

    Passing a precomputed ``profile`` avoids re-deriving statistics; when
    omitted it is computed here.
    """
    p = profile or profile_column(series)
    values = _numeric_values(series)
    n = len(values)

    def unavailable(reason: str) -> OutlierReport:
        return OutlierReport(
            column=p.name,
            n=n,
            iqr=OutlierMethodResult("iqr", applicable=False, reason_unavailable=reason),
            modified_zscore=OutlierMethodResult(
                "modified_zscore", applicable=False, reason_unavailable=reason
            ),
            recommended_method=None,
            recommended_action="not_applicable",
            interpretation=reason,
        )

    if not p.is_numeric_measure:
        return unavailable(
            f"'{p.name}' is {p.kind.value}, not a numeric measurement — "
            "outlier fences do not apply to it."
        )
    if p.discrete_code:
        return unavailable(
            f"'{p.name}' holds only {p.unique} distinct integer codes — it is a "
            "rating or flag, so its extremes are the ends of a scale rather "
            "than anomalies."
        )
    if n < MIN_POINTS:
        return unavailable(
            f"Only {n} non-null value(s). Below {MIN_POINTS} points, quantiles "
            "are too unstable for outlier detection to mean anything."
        )

    # ── Rule 1: Tukey IQR fences ─────────────────────────────────────────────
    mask_iqr, lower, upper = iqr_mask(series)
    iqr_width = float(values.quantile(0.75) - values.quantile(0.25))
    if iqr_width <= 0:
        iqr_result = OutlierMethodResult(
            "iqr", applicable=False,
            reason_unavailable="The middle 50% of values are identical (IQR = 0), "
                               "so Tukey fences collapse to a single point.",
        )
        mask_iqr = pd.Series(False, index=series.index)
    else:
        _, ext_lower, ext_upper = iqr_mask(series, IQR_EXTREME_MULTIPLIER)
        numeric = pd.to_numeric(series, errors="coerce")
        extreme = int(((numeric < ext_lower) | (numeric > ext_upper)).fillna(False).sum())
        count = int(mask_iqr.sum())
        iqr_result = OutlierMethodResult(
            "iqr", applicable=True, count=count, fraction=count / n,
            lower_bound=round(lower, 4), upper_bound=round(upper, 4),
            extreme_count=extreme,
        )

    # ── Rule 2: modified z-score (MAD) ───────────────────────────────────────
    scores, scale = modified_zscore(series)
    if scale is None:
        mz_result = OutlierMethodResult(
            "modified_zscore", applicable=False,
            reason_unavailable="No robust spread — the column has effectively "
                               "one repeated value.",
        )
        mask_mz = pd.Series(False, index=series.index)
    else:
        mask_mz = (scores.abs() > MODIFIED_Z_THRESHOLD).fillna(False).astype(bool)
        count = int(mask_mz.sum())
        mz_result = OutlierMethodResult(
            "modified_zscore", applicable=True, count=count, fraction=count / n,
            threshold=MODIFIED_Z_THRESHOLD,
            extreme_count=int((scores.abs() > MODIFIED_Z_THRESHOLD * 2).fillna(False).sum()),
        )

    report = OutlierReport(
        column=p.name, n=n, iqr=iqr_result, modified_zscore=mz_result,
        recommended_method=None, recommended_action="none", interpretation="",
    )
    report.agreement = {
        "both": int((mask_iqr & mask_mz).sum()),
        "iqr_only": int((mask_iqr & ~mask_mz).sum()),
        "mad_only": int((mask_mz & ~mask_iqr).sum()),
    }

    _interpret(report, p, values, mask_iqr, mask_mz)
    return report


def _interpret(
    report: OutlierReport,
    p: ColumnProfile,
    values: pd.Series,
    mask_iqr: pd.Series,
    mask_mz: pd.Series,
) -> None:
    """Fill in recommendation, interpretation, caveats and sample values."""
    iqr_res, mz_res = report.iqr, report.modified_zscore

    # Prefer the robust rule on skewed data — that is precisely the case where
    # IQR fences mistake the distribution's own tail for anomalies.
    if mz_res.applicable and p.shape in ("moderately skewed", "highly skewed"):
        report.recommended_method = "modified_zscore"
    elif iqr_res.applicable:
        report.recommended_method = "iqr"
    elif mz_res.applicable:
        report.recommended_method = "modified_zscore"

    chosen = iqr_res if report.recommended_method == "iqr" else mz_res
    combined_mask = mask_iqr | mask_mz
    total_candidates = int(combined_mask.sum())

    if total_candidates == 0:
        report.recommended_action = "none"
        report.interpretation = (
            f"No values in '{p.name}' fall outside either outlier rule. "
            "The spread looks internally consistent."
        )
        return

    # Show the most extreme actual values so the user judges data, not a count.
    flagged = values[combined_mask.reindex(values.index, fill_value=False)]
    if not flagged.empty:
        center = p.median if p.median is not None else float(values.median())
        extremes = flagged.reindex(
            (flagged - center).abs().sort_values(ascending=False).index
        ).head(5)
        report.sample_values = [
            round(float(v), 4) for v in extremes if not math.isnan(float(v))
        ]

    report.recommended_action = "annotate"
    method_label = "IQR fences" if report.recommended_method == "iqr" else "the MAD rule"
    report.interpretation = (
        f"{chosen.count} of {report.n} values in '{p.name}' "
        f"({chosen.fraction * 100:.1f}%) sit outside {method_label}. "
        f"LANA flags them for review and keeps every row — none of this is "
        f"evidence that the values are wrong."
    )

    # ── Caveats: the part that stops a user deleting good data ───────────────
    if p.shape == "highly skewed":
        report.caveats.append(
            f"'{p.name}' is highly skewed (skewness {p.skewness}). A long right "
            "tail is the expected shape for quantities like revenue, latency or "
            "population — those points are the distribution, not errors in it."
        )
    if iqr_res.applicable and iqr_res.fraction > _OVER_FLAG_FRACTION:
        report.caveats.append(
            f"IQR flags {iqr_res.fraction * 100:.1f}% of the column. Genuine "
            "anomalies are rare by definition; a share this large means the "
            "rule is describing the distribution's shape, not finding faults."
        )
    if iqr_res.applicable and mz_res.applicable:
        disagreement = report.agreement["iqr_only"] + report.agreement["mad_only"]
        if disagreement > 0:
            report.caveats.append(
                f"The two rules disagree on {disagreement} value(s) "
                f"({report.agreement['both']} flagged by both). Treat only the "
                "values both rules agree on as strong candidates."
            )
    if p.negative_count == 0 and p.min is not None and p.min >= 0 and p.shape != "symmetric":
        report.caveats.append(
            "All values are non-negative and the distribution is asymmetric — "
            "consider a log transform before judging extremes, rather than "
            "removing rows."
        )
    if report.agreement["both"] > 0 and report.agreement["both"] <= max(3, report.n // 100):
        report.recommended_action = "investigate"
        report.caveats.append(
            f"{report.agreement['both']} value(s) are flagged by both rules and "
            "are few enough to inspect individually — worth checking against "
            "the source system before any transformation."
        )


# These suffixes must stay in sync with profile.ANNOTATION_SUFFIXES — that is
# what keeps the generated columns out of correlation scans and group-bys.
def flag_column_name(column: str) -> str:
    return f"{column}__outlier"


def score_column_name(column: str) -> str:
    return f"{column}__outlier_score"


def winsorize_backup_name(column: str) -> str:
    return f"{column}__pre_winsorize"


def annotate_outliers(
    df: pd.DataFrame,
    column: str,
    method: Method = "modified_zscore",
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Add outlier flag and score columns without removing a single row.

    This is LANA's default answer to outliers. The returned frame gains:

    * ``<column>__outlier``       — boolean, True for flagged values
    * ``<column>__outlier_score`` — signed modified z-score (how far out, and
      in which direction), so downstream analysis can rank severity rather
      than treating every flag as equal

    Returns the annotated frame and a details dict for the transformation log.
    """
    if column not in df.columns:
        raise KeyError(f"Column '{column}' not found.")

    result = df.copy()
    series = result[column]
    profile = profile_column(series)

    if not profile.supports_outlier_analysis:
        raise ValueError(
            f"Cannot flag outliers in '{column}' — "
            + (f"it holds only {profile.unique} distinct codes, so its extremes "
               "are scale endpoints rather than anomalies."
               if profile.discrete_code else
               f"it is {profile.kind.value}, not a numeric measurement.")
        )

    scores, scale = modified_zscore(series)
    if method == "iqr":
        mask, lower, upper = iqr_mask(series)
        bounds: dict[str, Any] = {"lower_bound": round(lower, 4), "upper_bound": round(upper, 4)}
    else:
        if scale is None:
            raise ValueError(
                f"Cannot apply the MAD rule to '{column}' — it has no robust "
                "spread (effectively one repeated value)."
            )
        mask = (scores.abs() > MODIFIED_Z_THRESHOLD).fillna(False).astype(bool)
        bounds = {"threshold": MODIFIED_Z_THRESHOLD, "scale": round(scale, 6)}

    flag_col, score_col = flag_column_name(column), score_column_name(column)
    result[flag_col] = mask.to_numpy()
    result[score_col] = scores.round(4).to_numpy()

    return result, {
        "method": method,
        "flagged": int(mask.sum()),
        "flag_column": flag_col,
        "score_column": score_col,
        **bounds,
    }


def winsorize_column(
    df: pd.DataFrame,
    column: str,
    multiplier: float = IQR_MULTIPLIER,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Clip extreme values to the Tukey fences instead of dropping their rows.

    The middle ground between ignoring outliers and deleting them: the row
    survives with every other field intact, and only the extreme value is
    capped. The original value is preserved in ``<column>__pre_winsorize`` so
    the transform stays reversible.
    """
    if column not in df.columns:
        raise KeyError(f"Column '{column}' not found.")

    result = df.copy()
    profile = profile_column(result[column])
    if not profile.supports_outlier_analysis:
        raise ValueError(
            f"Cannot winsorize '{column}' — it is not a continuous numeric "
            f"measurement ({profile.kind.value}"
            + (", encoded category)" if profile.discrete_code else ")")
        )

    values = _numeric_values(result[column])
    q1, q3 = float(values.quantile(0.25)), float(values.quantile(0.75))
    iqr = q3 - q1
    if iqr <= 0:
        raise ValueError(
            f"Cannot winsorize '{column}' — the middle 50% of values are "
            "identical, so the fences collapse to a single point."
        )

    lower, upper = q1 - multiplier * iqr, q3 + multiplier * iqr
    numeric = pd.to_numeric(result[column], errors="coerce")
    changed = int(((numeric < lower) | (numeric > upper)).fillna(False).sum())

    backup_col = winsorize_backup_name(column)
    result[backup_col] = result[column]
    result[column] = numeric.clip(lower=lower, upper=upper)

    return result, {
        "lower_bound": round(lower, 4),
        "upper_bound": round(upper, 4),
        "values_clipped": changed,
        "backup_column": backup_col,
    }
