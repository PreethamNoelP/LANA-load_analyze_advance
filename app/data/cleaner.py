"""Data cleaning — issue detection and operation application."""

from __future__ import annotations

import math

import pandas as pd


def _safe(val):
    """Convert NaN/Inf floats to None for JSON serialisation."""
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return None
    return val


def detect_issues(df: pd.DataFrame) -> dict:
    """Scan a DataFrame and return a structured map of data quality issues."""
    issues: dict = {}

    # ── 1. Duplicates ────────────────────────────────────────────────────────
    dup_count = int(df.duplicated().sum())
    if dup_count > 0:
        sample_rows = []
        for rec in df[df.duplicated(keep=False)].head(6).to_dict(orient="records"):
            sample_rows.append({k: _safe(v) if isinstance(v, float) else v for k, v in rec.items()})
        issues["duplicates"] = {"count": dup_count, "sample_rows": sample_rows}

    # ── 2. Null values ───────────────────────────────────────────────────────
    null_info: dict = {}
    for col in df.columns:
        null_count = int(df[col].isna().sum())
        if null_count == 0:
            continue
        info: dict = {
            "count": null_count,
            "pct": round(null_count / len(df) * 100, 1),
            "dtype": str(df[col].dtype),
        }
        if pd.api.types.is_numeric_dtype(df[col]):
            info["mean"]   = _safe(round(float(df[col].mean()), 4))
            info["median"] = _safe(round(float(df[col].median()), 4))
            info["suggested"] = "mean"
            info["suggested_value"] = info["mean"]
        else:
            mode_vals = df[col].mode()
            mode = str(mode_vals.iloc[0]) if len(mode_vals) > 0 else None
            info["mode"] = mode
            info["suggested"] = "mode"
            info["suggested_value"] = mode
        null_info[col] = info
    if null_info:
        issues["nulls"] = null_info

    # ── 3. Outliers (IQR method, numeric columns only) ───────────────────────
    outlier_info: dict = {}
    for col in df.select_dtypes(include="number").columns:
        s = df[col].dropna()
        if len(s) < 4:
            continue
        q1, q3 = float(s.quantile(0.25)), float(s.quantile(0.75))
        iqr = q3 - q1
        if iqr == 0:
            continue
        lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        count = int(((df[col] < lower) | (df[col] > upper)).sum())
        if count == 0:
            continue
        outlier_info[col] = {
            "count": count,
            "lower_bound": round(lower, 4),
            "upper_bound": round(upper, 4),
            "col_min": _safe(round(float(s.min()), 4)),
            "col_max": _safe(round(float(s.max()), 4)),
        }
    if outlier_info:
        issues["outliers"] = outlier_info

    # ── 4. Text inconsistencies (case/whitespace variants of the same value) ──
    text_issues: dict = {}
    for col in df.select_dtypes(include="object").columns:
        series = df[col].dropna().astype(str)
        if series.empty:
            continue
        groups: dict[str, list[str]] = {}
        for val in series.unique():
            norm = val.lower().strip()
            groups.setdefault(norm, []).append(val)
        inconsistent = {
            norm: sorted(variants)
            for norm, variants in groups.items()
            if len(variants) > 1
        }
        if not inconsistent:
            continue
        affected_values = {v for variants in inconsistent.values() for v in variants}
        text_issues[col] = {
            "groups": inconsistent,
            "total_affected": int(series.isin(affected_values).sum()),
        }
    if text_issues:
        issues["text_inconsistencies"] = text_issues

    return issues


def apply_cleaning(df: pd.DataFrame, operations: list[dict]) -> pd.DataFrame:
    """Apply a list of cleaning operations and return the resulting DataFrame."""
    result = df.copy()

    for op in operations:
        op_type = op.get("type")
        col     = op.get("column")
        method  = op.get("method")

        if op_type == "remove_duplicates":
            result = result.drop_duplicates().reset_index(drop=True)

        elif op_type == "fill_nulls" and col and col in result.columns:
            if method == "mean" and pd.api.types.is_numeric_dtype(result[col]):
                result[col] = result[col].fillna(result[col].mean())
            elif method == "median" and pd.api.types.is_numeric_dtype(result[col]):
                result[col] = result[col].fillna(result[col].median())
            elif method == "zero":
                result[col] = result[col].fillna(0)
            elif method == "mode":
                mode_val = result[col].mode()
                if len(mode_val) > 0:
                    result[col] = result[col].fillna(mode_val.iloc[0])
            elif method == "drop":
                result = result.dropna(subset=[col]).reset_index(drop=True)

        elif op_type == "remove_outliers" and col and col in result.columns:
            if pd.api.types.is_numeric_dtype(result[col]):
                q1 = result[col].quantile(0.25)
                q3 = result[col].quantile(0.75)
                iqr = q3 - q1
                if iqr > 0:
                    lower = q1 - 1.5 * iqr
                    upper = q3 + 1.5 * iqr
                    result = result[
                        (result[col] >= lower) & (result[col] <= upper)
                    ].reset_index(drop=True)

        elif op_type == "normalize" and col and col in result.columns:
            if pd.api.types.is_numeric_dtype(result[col]):
                if method == "minmax":
                    mn, mx = result[col].min(), result[col].max()
                    if mx != mn:
                        result[col] = (result[col] - mn) / (mx - mn)
                elif method == "zscore":
                    mean, std = result[col].mean(), result[col].std()
                    if std != 0:
                        result[col] = (result[col] - mean) / std

        elif op_type == "fix_text" and col and col in result.columns:
            mapping = op.get("mapping", {})
            if mapping:
                result[col] = result[col].replace(mapping)

    return result