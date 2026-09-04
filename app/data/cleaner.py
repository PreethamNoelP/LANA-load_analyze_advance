"""Data cleaning — issue detection and explainable, logged transformation.

Two guarantees hold for everything in this module:

1. **Nothing is removed silently.** Every operation returns a
   :class:`~app.data.lineage.TransformRecord` stating what changed, how much,
   why it is defensible, and whether it can be undone. Operations that were
   requested but not applied are recorded as skips rather than ignored.
2. **The destructive option is never the default.** Outliers are flagged, not
   deleted. Missing values get an indicator column so the imputation stays
   visible downstream. Deletion remains available — it just has to be asked
   for explicitly, and it is logged as data loss.
"""

from __future__ import annotations

import math

import pandas as pd

from .lineage import CleaningLedger
from .outliers import (
    annotate_outliers,
    detect_outliers,
    iqr_mask,
    winsorize_column,
)
from .profile import ColumnKind, profile_dataframe, suggest_imputation

# Below this share of missing values, a missingness indicator column adds more
# clutter than information — the imputation is still logged either way.
INDICATOR_MIN_NULL_PCT = 5.0

# Text-variant detection walks unique values; on a high-cardinality column that
# is both slow and meaningless (see ColumnKind.TEXT), so it is capped.
MAX_TEXT_UNIQUE_SCAN = 5000


def _safe(val):
    """Convert NaN/Inf floats to None for JSON serialisation."""
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return None
    return val


def _indicator_name(column: str) -> str:
    return f"{column}__was_missing"


# ── Detection ────────────────────────────────────────────────────────────────

def detect_issues(df: pd.DataFrame, profiles: dict | None = None) -> dict:
    """Scan a DataFrame and return a structured map of data quality issues.

    Read-only. Every finding carries the reasoning behind it, and every
    suggested remedy carries its statistical justification — the user is
    given a recommendation to accept or reject, never an action already taken.

    ``profiles`` may be supplied by a caller that already computed them.
    """
    issues: dict = {}
    if profiles is None:
        profiles = profile_dataframe(df)

    # ── 0. Column types and profiles ─────────────────────────────────────────
    # Not "issues" — always reported so the UI can offer a type override
    # (e.g. a date column pandas read in as plain text) before analysis.
    issues["column_types"] = {col: str(dtype) for col, dtype in df.dtypes.items()}
    issues["profiles"] = {name: p.to_dict() for name, p in profiles.items()}
    issues["quality"] = _quality_block(profiles, len(df))

    # ── 1. Duplicates ────────────────────────────────────────────────────────
    dup_count = int(df.duplicated().sum())
    if dup_count > 0:
        sample_rows = []
        for rec in df[df.duplicated(keep=False)].head(6).to_dict(orient="records"):
            sample_rows.append({k: _safe(v) if isinstance(v, float) else v for k, v in rec.items()})
        issues["duplicates"] = {
            "count": dup_count,
            "pct": round(dup_count / len(df) * 100, 2) if len(df) else 0.0,
            "sample_rows": sample_rows,
            "caveat": (
                "Identical rows are not always errors — repeated transactions, "
                "sensor readings, or survey responses can legitimately match. "
                "Check whether a timestamp or ID column is missing before removing them."
            ),
        }

    # ── 2. Missing values ────────────────────────────────────────────────────
    null_info: dict = {}
    for col, p in profiles.items():
        if p.null_count == 0:
            continue
        suggestion = suggest_imputation(p)
        info: dict = {
            "count": p.null_count,
            "pct": round(p.null_pct, 1),
            "dtype": p.dtype,
            "kind": p.kind.value,
            "suggested": suggestion["method"],
            "rationale": suggestion["rationale"],
            "alternatives": suggestion.get("alternatives", []),
            "refuses_imputation": suggestion.get("refuses_imputation", False),
        }
        if p.is_numeric_measure:
            info["mean"] = p.mean
            info["median"] = p.median
            info["suggested_value"] = p.median if suggestion["method"] == "median" else p.mean
        else:
            mode = p.top_values[0][0] if p.top_values else None
            info["mode"] = mode
            info["suggested_value"] = mode if suggestion["method"] == "mode" else None
        null_info[col] = info
    if null_info:
        issues["nulls"] = null_info

    # ── 3. Outliers — reported as candidates for review, never as defects ────
    outlier_info: dict = {}
    for col, p in profiles.items():
        if not p.supports_outlier_analysis:
            continue
        report = detect_outliers(df[col], profile=p)
        if not report.has_candidates:
            continue
        # `count`, `lower_bound` and `upper_bound` are the IQR figures the UI
        # has always shown; the richer report travels alongside them.
        outlier_info[col] = {
            "count": report.iqr.count if report.iqr.applicable else report.modified_zscore.count,
            "lower_bound": report.iqr.lower_bound,
            "upper_bound": report.iqr.upper_bound,
            "col_min": p.min,
            "col_max": p.max,
            "recommended_action": report.recommended_action,
            "recommended_method": report.recommended_method,
            "interpretation": report.interpretation,
            "caveats": report.caveats,
            "sample_values": report.sample_values,
            "agreement": report.agreement,
            "methods": {
                "iqr": report.iqr.to_dict(),
                "modified_zscore": report.modified_zscore.to_dict(),
            },
        }
    if outlier_info:
        issues["outliers"] = outlier_info

    # ── 4. Text inconsistencies (case/whitespace variants of one value) ──────
    text_issues: dict = {}
    for col, p in profiles.items():
        # Free-text and identifier columns have no meaningful "same value in
        # two forms" — every value is distinct by nature.
        if p.kind not in (ColumnKind.CATEGORICAL, ColumnKind.TEXT):
            continue
        if p.unique > MAX_TEXT_UNIQUE_SCAN:
            continue
        series = df[col].dropna()
        if series.empty or not (
            pd.api.types.is_object_dtype(series)
            or pd.api.types.is_string_dtype(series)
            or isinstance(series.dtype, pd.CategoricalDtype)
        ):
            continue
        series = series.astype(str)
        groups: dict[str, list[str]] = {}
        for val in series.unique():
            groups.setdefault(val.lower().strip(), []).append(val)
        inconsistent = {n: sorted(v) for n, v in groups.items() if len(v) > 1}
        if not inconsistent:
            continue
        affected = {v for variants in inconsistent.values() for v in variants}
        text_issues[col] = {
            "groups": inconsistent,
            "total_affected": int(series.isin(affected).sum()),
        }
    if text_issues:
        issues["text_inconsistencies"] = text_issues

    return issues


def _quality_block(profiles, total_rows: int) -> dict:
    from .profile import dataset_quality
    return dataset_quality(profiles, total_rows)


# ── Type coercion ────────────────────────────────────────────────────────────

def _to_numeric_lenient(series: pd.Series) -> pd.Series:
    """Coerce to numeric, first stripping currency/percent formatting.

    Handles common real-world number formats pandas' own numeric coercion
    can't: "$1,234.56" -> 1234.56, "45%" -> 45, "(1,234)" (accounting-style
    negatives) -> -1234.
    """
    if not (pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series)):
        return pd.to_numeric(series, errors="coerce")
    cleaned = (
        series.astype(str)
        .str.strip()
        .str.replace(r"[,$%]", "", regex=True)
        .str.replace(r"^\((.+)\)$", r"-\1", regex=True)
    )
    return pd.to_numeric(cleaned, errors="coerce")


def _fill_value_for_categorical(series: pd.Series, value) -> pd.Series:
    """Make a categorical column accept a fill value outside its categories."""
    if isinstance(series.dtype, pd.CategoricalDtype) and value not in series.cat.categories:
        return series.cat.add_categories([value])
    return series


# ── Application ──────────────────────────────────────────────────────────────

def apply_cleaning(
    df: pd.DataFrame,
    operations: list[dict],
) -> tuple[pd.DataFrame, CleaningLedger]:
    """Apply cleaning operations, returning the result and a full audit ledger.

    The ledger is the point: it records the row/cell impact, rationale and
    reversibility of every step, and every operation that was requested but
    could not be applied.
    """
    result = df.copy()
    ledger = CleaningLedger()

    for op in operations:
        op_type = op.get("type")
        col = op.get("column")
        method = op.get("method")

        if op_type != "remove_duplicates":
            if not col:
                ledger.skip(op_type or "unknown", None, "No column specified.")
                continue
            if col not in result.columns:
                ledger.skip(op_type, col, f"Column '{col}' is not present in the data.")
                continue

        before = result

        if op_type == "remove_duplicates":
            result = _op_remove_duplicates(before, ledger)
        elif op_type == "fill_nulls":
            result = _op_fill_nulls(before, ledger, col, method, op)
        elif op_type == "flag_outliers":
            result = _op_flag_outliers(before, ledger, col, op)
        elif op_type == "winsorize":
            result = _op_winsorize(before, ledger, col)
        elif op_type == "remove_outliers":
            result = _op_remove_outliers(before, ledger, col)
        elif op_type == "normalize":
            result = _op_normalize(before, ledger, col, method)
        elif op_type == "fix_text":
            result = _op_fix_text(before, ledger, col, op.get("mapping") or {})
        elif op_type == "cast_type":
            result = _op_cast_type(before, ledger, col, op.get("dtype"))
        else:
            ledger.skip(str(op_type), col, "Unknown operation.")

    return result, ledger


def _op_remove_duplicates(df: pd.DataFrame, ledger: CleaningLedger) -> pd.DataFrame:
    dup_count = int(df.duplicated().sum())
    if dup_count == 0:
        ledger.skip("remove_duplicates", None, "No duplicate rows found.")
        return df

    result = df.drop_duplicates().reset_index(drop=True)
    ledger.record(
        "remove_duplicates", df, result,
        params={"keep": "first"},
        rationale=(
            f"Removed {dup_count} row(s) that were byte-for-byte identical to an "
            "earlier row across every column, keeping the first occurrence."
        ),
        caveats=[
            f"{dup_count} row(s) were discarded and cannot be recovered from this "
            "version. Switch to the original version to see them. Repeated "
            "measurements can be legitimate — if a timestamp or ID column is "
            "absent from this file, genuine distinct events may look identical."
        ],
        reversible=False,
    )
    return result


def _op_fill_nulls(
    df: pd.DataFrame,
    ledger: CleaningLedger,
    col: str,
    method: str | None,
    op: dict,
) -> pd.DataFrame:
    from .profile import profile_column

    p = profile_column(df[col], total_rows=len(df))
    if p.null_count == 0:
        ledger.skip("fill_nulls", col, f"'{col}' has no missing values.")
        return df

    is_numeric = p.is_numeric_measure
    if method in ("mean", "median") and not pd.api.types.is_numeric_dtype(df[col]):
        ledger.skip("fill_nulls", col,
                    f"Method '{method}' needs a numeric column; '{col}' is {p.kind.value}.")
        return df

    result = df.copy()
    missing_mask = result[col].isna()

    # ── The non-destructive option: mark the gap, do not invent a value ──────
    if method == "flag":
        indicator = _indicator_name(col)
        result[indicator] = missing_mask.to_numpy()
        ledger.record(
            "flag_nulls", df, result, column=col,
            params={"indicator_column": indicator},
            rationale=(
                f"Recorded which {p.null_count} row(s) are missing '{col}' without "
                "substituting a value. Nothing was invented, so downstream "
                "analysis can decide per question whether to exclude them."
            ),
            caveats=list(p.caveats[:1]),
            cells_changed=0,
        )
        return result

    if method == "drop":
        result = result.dropna(subset=[col]).reset_index(drop=True)
        pct = p.null_count / len(df) * 100 if len(df) else 0
        caveats = [
            f"{p.null_count} row(s) ({pct:.1f}%) were discarded because '{col}' was "
            "empty. Those rows may hold valid data in their other columns — this "
            "loses all of it."
        ]
        if pct > 20:
            caveats.append(
                f"Dropping {pct:.1f}% of the dataset for one column is a large "
                "loss. If the missingness is not random, the remaining rows are "
                "a biased sample and every downstream statistic inherits that bias."
            )
        ledger.record(
            "drop_null_rows", df, result, column=col,
            params={"column": col},
            rationale=f"Removed rows where '{col}' had no value.",
            caveats=caveats,
            reversible=False,
        )
        return result

    # ── Imputation: compute the fill value and state its cost ────────────────
    if method == "mean":
        fill_value = float(result[col].mean())
        basis = f"the column mean ({fill_value:.4g})"
    elif method == "median":
        fill_value = float(result[col].median())
        basis = f"the column median ({fill_value:.4g})"
    elif method == "zero":
        fill_value = 0
        basis = "zero"
    elif method == "mode":
        mode_vals = result[col].mode()
        if len(mode_vals) == 0:
            ledger.skip("fill_nulls", col, f"'{col}' has no mode — every value is missing.")
            return df
        fill_value = mode_vals.iloc[0]
        basis = f"the most frequent value ('{fill_value}')"
    else:
        ledger.skip("fill_nulls", col, f"Unsupported fill method '{method}'.")
        return df

    # A missingness indicator keeps the imputation visible: without it, a
    # filled value is indistinguishable from a measured one forever after.
    indicator_requested = op.get("add_indicator")
    add_indicator = (
        indicator_requested
        if indicator_requested is not None
        else p.null_pct >= INDICATOR_MIN_NULL_PCT
    )
    if add_indicator:
        result[_indicator_name(col)] = missing_mask.to_numpy()

    result[col] = _fill_value_for_categorical(result[col], fill_value)
    result[col] = result[col].fillna(fill_value)

    caveats = [
        f"{p.null_count} value(s) in '{col}' are now imputed, not measured. "
        "Any variance, standard deviation or correlation computed on this "
        "column is now artificially tightened."
    ]
    if method == "mean" and p.shape in ("moderately skewed", "highly skewed"):
        caveats.append(
            f"'{col}' is {p.shape} (skewness {p.skewness}), so the mean is pulled "
            f"toward the tail. The median ({p.median}) would have been the more "
            "robust fill."
        )
    if method == "zero" and is_numeric and (p.min is None or p.min > 0):
        caveats.append(
            f"Zero lies outside the observed range of '{col}' "
            f"(min {p.min}). Filling with it creates values the data never contained."
        )
    if p.null_pct >= 40:
        caveats.append(
            f"{p.null_pct}% of '{col}' was missing — the imputed value now "
            "dominates the column and largely determines its statistics."
        )
    caveats.extend(p.caveats[:1])

    ledger.record(
        "fill_nulls", df, result, column=col,
        params={
            "method": method,
            "fill_value": _safe(fill_value) if isinstance(fill_value, float) else fill_value,
            "indicator_column": _indicator_name(col) if add_indicator else None,
        },
        rationale=(
            f"Filled {p.null_count} missing value(s) in '{col}' with {basis}."
            + (f" A '{_indicator_name(col)}' column marks which rows were imputed."
               if add_indicator else "")
        ),
        caveats=caveats,
        # Reversible in principle: the indicator identifies exactly which cells
        # to reset, but only when the indicator was written.
        reversible=bool(add_indicator),
        inverse={"restore_nulls_where": _indicator_name(col)} if add_indicator else None,
    )
    return result


def _op_flag_outliers(
    df: pd.DataFrame,
    ledger: CleaningLedger,
    col: str,
    op: dict,
) -> pd.DataFrame:
    method = op.get("outlier_method") or "modified_zscore"
    try:
        result, details = annotate_outliers(df, col, method=method)
    except (ValueError, KeyError) as exc:
        ledger.skip("flag_outliers", col, str(exc))
        return df

    report = detect_outliers(df[col])
    ledger.record(
        "flag_outliers", df, result, column=col,
        params=details,
        rationale=(
            f"Flagged {details['flagged']} value(s) in '{col}' using "
            f"{'the MAD rule' if method == 'modified_zscore' else 'IQR fences'}. "
            f"Every row is retained; '{details['flag_column']}' marks the "
            f"candidates and '{details['score_column']}' records how far out each "
            "value sits, so severity can be ranked instead of treated as binary."
        ),
        caveats=report.caveats,
        cells_changed=0,
    )
    return result


def _op_winsorize(df: pd.DataFrame, ledger: CleaningLedger, col: str) -> pd.DataFrame:
    try:
        result, details = winsorize_column(df, col)
    except (ValueError, KeyError) as exc:
        ledger.skip("winsorize", col, str(exc))
        return df

    if details["values_clipped"] == 0:
        ledger.skip("winsorize", col, f"No values in '{col}' fall outside the IQR fences.")
        return df

    ledger.record(
        "winsorize", df, result, column=col,
        params=details,
        rationale=(
            f"Capped {details['values_clipped']} extreme value(s) in '{col}' to the "
            f"range [{details['lower_bound']}, {details['upper_bound']}]. No rows "
            f"were removed, and the original values are preserved in "
            f"'{details['backup_column']}'."
        ),
        caveats=[
            f"Capping compresses the true range of '{col}'. The maximum reported "
            "from this version is a fence, not an observation — say so in any "
            "report that quotes it."
        ],
        reversible=True,
        inverse={"restore_from_column": details["backup_column"]},
    )
    return result


def _op_remove_outliers(df: pd.DataFrame, ledger: CleaningLedger, col: str) -> pd.DataFrame:
    from .profile import profile_column

    p = profile_column(df[col], total_rows=len(df))
    if not p.supports_outlier_analysis:
        ledger.skip("remove_outliers", col,
                    f"'{col}' is not a continuous numeric measurement "
                    f"({'encoded category' if p.discrete_code else p.kind.value}).")
        return df

    mask, lower, upper = iqr_mask(df[col])
    removed = int(mask.sum())
    if removed == 0:
        ledger.skip("remove_outliers", col, f"No values in '{col}' fall outside the IQR fences.")
        return df

    report = detect_outliers(df[col], profile=p)
    result = df[~mask].reset_index(drop=True)

    caveats = [
        f"{removed} complete row(s) were deleted because '{col}' fell outside "
        f"[{lower:.4g}, {upper:.4g}]. Every other field in those rows was "
        "discarded with them, and they are gone from this version — switch to "
        "the original version to recover them.",
        "Flagging or capping these values would have preserved the rows. "
        "Deletion is only appropriate when the values are known to be invalid, "
        "not merely extreme.",
    ]
    caveats.extend(report.caveats)
    if removed / len(df) > 0.05:
        caveats.append(
            f"This removed {removed / len(df) * 100:.1f}% of the dataset. "
            "Losing that share to one column's fences usually means the "
            "distribution is skewed rather than contaminated."
        )

    ledger.record(
        "remove_outliers", df, result, column=col,
        params={"method": "iqr", "lower_bound": round(lower, 4), "upper_bound": round(upper, 4),
                "rows_deleted": removed},
        rationale=(
            f"Deleted {removed} row(s) whose '{col}' value fell outside the Tukey "
            "IQR fences, as explicitly requested."
        ),
        caveats=caveats,
        reversible=False,
    )
    return result


def _op_normalize(
    df: pd.DataFrame,
    ledger: CleaningLedger,
    col: str,
    method: str | None,
) -> pd.DataFrame:
    if not pd.api.types.is_numeric_dtype(df[col]):
        ledger.skip("normalize", col, f"'{col}' is not numeric.")
        return df

    result = df.copy()
    series = result[col]

    if method == "minmax":
        mn, mx = float(series.min()), float(series.max())
        if mx == mn:
            ledger.skip("normalize", col, f"'{col}' is constant — min-max scaling is undefined.")
            return df
        result[col] = (series - mn) / (mx - mn)
        params = {"method": "minmax", "min": round(mn, 6), "max": round(mx, 6)}
        inverse = {"formula": "value * (max - min) + min", **params}
        rationale = (
            f"Rescaled '{col}' from [{mn:.4g}, {mx:.4g}] to [0, 1]. The original "
            "scale is recorded and the transform is exactly invertible."
        )
        caveats = [
            f"Min-max scaling is anchored to this dataset's extremes. If '{col}' "
            "contains outliers, they now define the endpoints and compress every "
            "other value into a narrow band."
        ]
    elif method == "zscore":
        mean, std = float(series.mean()), float(series.std())
        if std == 0:
            ledger.skip("normalize", col, f"'{col}' has zero variance — z-scoring is undefined.")
            return df
        result[col] = (series - mean) / std
        params = {"method": "zscore", "mean": round(mean, 6), "std": round(std, 6), "ddof": 1}
        inverse = {"formula": "value * std + mean", **params}
        rationale = (
            f"Standardised '{col}' to mean 0, standard deviation 1 (sample std, "
            f"ddof=1). Original centre {mean:.4g} and spread {std:.4g} are recorded "
            "so the transform is exactly invertible."
        )
        caveats = [
            f"'{col}' is now in standard-deviation units, not its original units. "
            "Any figure quoted from this version needs converting back before it "
            "means anything to a reader."
        ]
    else:
        ledger.skip("normalize", col, f"Unsupported normalisation method '{method}'.")
        return df

    ledger.record(
        "normalize", df, result, column=col,
        params=params, rationale=rationale, caveats=caveats,
        reversible=True, inverse=inverse,
    )
    return result


def _op_fix_text(
    df: pd.DataFrame,
    ledger: CleaningLedger,
    col: str,
    mapping: dict,
) -> pd.DataFrame:
    if not mapping:
        ledger.skip("fix_text", col, "No replacement mapping supplied.")
        return df

    result = df.copy()
    if isinstance(result[col].dtype, pd.CategoricalDtype):
        # `replace` on a categorical can produce values outside the category
        # set; going through object dtype keeps the operation well-defined.
        result[col] = result[col].astype(object).replace(mapping)
    else:
        result[col] = result[col].replace(mapping)

    affected = int(df[col].isin(list(mapping.keys())).sum())
    if affected == 0:
        ledger.skip("fix_text", col, "None of the mapped values appear in the column.")
        return df

    ledger.record(
        "fix_text", df, result, column=col,
        params={"mapping": mapping, "variants_merged": len(mapping)},
        rationale=(
            f"Merged {len(mapping)} spelling/casing variant(s) in '{col}' into their "
            f"canonical form, affecting {affected} row(s). This raises the counts of "
            "the surviving categories rather than changing any row's meaning."
        ),
        caveats=[
            "Variants were treated as the same underlying value. If any of them "
            "were genuinely distinct categories, this merge has hidden that distinction."
        ],
        reversible=False,
    )
    return result


def _op_cast_type(
    df: pd.DataFrame,
    ledger: CleaningLedger,
    col: str,
    dtype: str | None,
) -> pd.DataFrame:
    result = df.copy()
    before_non_null = int(result[col].notna().sum())
    original_dtype = str(result[col].dtype)

    if dtype == "numeric":
        result[col] = _to_numeric_lenient(result[col])
    elif dtype == "datetime":
        result[col] = pd.to_datetime(result[col], errors="coerce")
    elif dtype == "category":
        result[col] = result[col].astype("category")
    elif dtype == "text":
        # .astype(str) alone would turn real nulls into the literal string
        # "nan"/"NaT" — restore them to actual nulls afterward. (Assigning the
        # whole column, not a `.loc[mask]` slice, is required for the dtype
        # change itself to take effect at all — partial assignment tries to fit
        # the new values back into the column's *existing* dtype.)
        notna = result[col].notna()
        result[col] = result[col].astype(str).where(notna, None)
    else:
        ledger.skip("cast_type", col, f"Unsupported target type '{dtype}'.")
        return df

    lost = before_non_null - int(result[col].notna().sum())
    caveats = []
    if lost > 0:
        caveats.append(
            f"{lost} value(s) in '{col}' could not be parsed as {dtype} and are now "
            "null. Those values are still visible in the original version — check "
            "them before relying on this column, as the parse failure may point to "
            "a real formatting problem in the source."
        )
        if lost == before_non_null:
            caveats.append(
                f"Every value failed to parse. '{col}' is now entirely null in this "
                "version, which almost certainly means the wrong target type was chosen."
            )

    ledger.record(
        "cast_type", df, result, column=col,
        params={"from": original_dtype, "to": dtype, "values_nulled": lost},
        rationale=(
            f"Reinterpreted '{col}' from {original_dtype} as {dtype}"
            + (f"; {lost} unparseable value(s) became null." if lost else " with no data loss.")
        ),
        caveats=caveats,
        reversible=lost == 0,
    )
    return result
