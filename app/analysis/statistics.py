import warnings
from itertools import combinations
from typing import Any, Literal

import pandas as pd
from scipy import stats as scipy_stats


def compute_statistics(series: pd.Series) -> dict[str, Any]:
    """Descriptive statistics for a single numeric column.

    Always returns count, null count, and unique count.
    Numeric columns also get min/max/mean/median/std/variance/percentiles/skew/kurtosis.
    """
    stats: dict[str, Any] = {
        "count": int(series.count()),
        "null_count": int(series.isnull().sum()),
        "unique": int(series.nunique()),
    }

    mode_vals = series.mode()
    stats["mode"] = mode_vals.iloc[0] if not mode_vals.empty else None

    if pd.api.types.is_numeric_dtype(series):
        q25 = series.quantile(0.25)
        q75 = series.quantile(0.75)
        stats.update({
            "min": series.min(),
            "max": series.max(),
            "mean": round(series.mean(), 4),
            "median": round(series.median(), 4),
            "std": round(series.std(), 4),
            "variance": round(series.var(), 4),
            "p25": round(q25, 4),
            "p75": round(q75, 4),
            "iqr": round(q75 - q25, 4),
            "skewness": round(series.skew(), 4),
            "kurtosis": round(series.kurt(), 4),
        })

    return stats


def compute_correlations(
    df: pd.DataFrame,
    method: Literal["pearson", "spearman"] = "pearson",
) -> list[dict[str, Any]]:
    """Pairwise correlation between every numeric column pair, ranked by strength.

    Rows with a null in either column of a pair are dropped before computing
    that pair's correlation (pairwise deletion), so `n` can differ per pair.
    Pairs with fewer than 2 overlapping values, or a constant column (zero
    variance, undefined correlation), are skipped.
    """
    numeric_cols = df.select_dtypes("number").columns.tolist()
    corr_fn = scipy_stats.spearmanr if method == "spearman" else scipy_stats.pearsonr

    results: list[dict[str, Any]] = []
    for col_a, col_b in combinations(numeric_cols, 2):
        pair = df[[col_a, col_b]].dropna()
        if len(pair) < 2:
            continue
        try:
            # A constant column has undefined correlation — scipy warns and
            # returns nan, which we filter out below anyway.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", category=scipy_stats.ConstantInputWarning)
                correlation, p_value = corr_fn(pair[col_a], pair[col_b])
        except Exception:
            continue
        if pd.isna(correlation):
            continue
        results.append({
            "column_a": col_a,
            "column_b": col_b,
            "correlation": round(float(correlation), 4),
            "p_value": round(float(p_value), 6),
            "n": int(len(pair)),
        })

    results.sort(key=lambda r: abs(r["correlation"]), reverse=True)
    return results


def generate_context(df: pd.DataFrame) -> str:
    """Build a text description of a DataFrame to pass as LLM context."""
    lines = [
        f"Dataset: {len(df):,} rows × {len(df.columns)} columns.",
        f"Columns: {', '.join(df.columns)}.",
        "",
    ]

    for col in df.columns:
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

    return "\n".join(lines)


def generate_recommendations(df: pd.DataFrame) -> dict[str, list[str]]:
    """Suggest chart types and analyses suited to the uploaded dataset."""
    numeric_cols = df.select_dtypes("number").columns.tolist()
    cat_cols = df.select_dtypes(["object", "str", "category"]).columns.tolist()

    viz: list[str] = []
    if numeric_cols:
        viz.append(f"Histogram — distribution of {numeric_cols[:3]}")
        if len(numeric_cols) > 1:
            viz.append(f"Scatter Plot — {numeric_cols[0]} vs {numeric_cols[1]}")
        if any(c in df.columns for c in ("timestamp", "date", "Date", "time")):
            viz.append(f"Line Plot — {numeric_cols[:3]} over time")
        viz.append(f"Box Plot — outliers in {numeric_cols[:3]}")
        if len(numeric_cols) >= 2:
            viz.append("Heatmap — correlation matrix")
    if cat_cols:
        viz.append(f"Bar Chart — frequency of '{cat_cols[0]}'")
        viz.append(f"Pie Chart — proportion of '{cat_cols[0]}'")

    analysis: list[str] = []
    if len(numeric_cols) > 1:
        analysis.append(f"Linear Regression — {numeric_cols[0]} vs {numeric_cols[1]}")
    if numeric_cols:
        analysis.append(f"Statistical Summary — {numeric_cols[:3]}")
    if cat_cols:
        analysis.append(f"Value Counts — '{cat_cols[0]}'")

    return {"visualization": viz[:5], "analysis": analysis[:5]}