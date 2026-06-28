"""Statistical analysis utilities."""

from __future__ import annotations

from typing import Any, Dict

import pandas as pd


def compute_statistics(series: pd.Series) -> Dict[str, Any]:
    """Compute descriptive statistics for a single column."""
    is_numeric = pd.api.types.is_numeric_dtype(series)
    stats: Dict[str, Any] = {
        "Count": int(series.count()),
        "Null Count": int(series.isnull().sum()),
        "Unique Values": int(series.nunique()),
    }

    mode_vals = series.mode()
    stats["Mode"] = mode_vals.iloc[0] if not mode_vals.empty else None

    if is_numeric:
        stats.update(
            {
                "Min": series.min(),
                "Max": series.max(),
                "Mean": round(series.mean(), 4),
                "Median": round(series.median(), 4),
                "Std Dev": round(series.std(), 4),
                "Variance": round(series.var(), 4),
                "25th Pct": round(series.quantile(0.25), 4),
                "75th Pct": round(series.quantile(0.75), 4),
                "IQR": round(series.quantile(0.75) - series.quantile(0.25), 4),
                "Skewness": round(series.skew(), 4),
                "Kurtosis": round(series.kurt(), 4),
            }
        )
    return stats


def generate_context(df: pd.DataFrame) -> str:
    """Build a rich text description of a DataFrame for use as LLM context."""
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
                continue
            lines.append(
                f"- '{col}' (numeric): "
                f"min={s.min():.4g}, max={s.max():.4g}, "
                f"mean={s.mean():.4g}, median={s.median():.4g}, "
                f"std={s.std():.4g}, nulls={df[col].isnull().sum()}"
            )
        else:
            unique = df[col].nunique()
            sample = df[col].dropna().head(5).tolist()
            lines.append(
                f"- '{col}' (categorical): {unique} unique values, "
                f"sample={sample}, nulls={df[col].isnull().sum()}"
            )

    return "\n".join(lines)


def generate_recommendations(df: pd.DataFrame) -> Dict[str, list[str]]:
    """Suggest suitable visualizations and analyses based on column types."""
    numeric_cols = df.select_dtypes(include="number").columns.tolist()
    cat_cols = df.select_dtypes(include=["object", "category"]).columns.tolist()

    viz: list[str] = []
    if numeric_cols:
        viz.append(f"Histogram — distribution of {numeric_cols[:3]}")
        if len(numeric_cols) > 1:
            viz.append(f"Scatter Plot — {numeric_cols[0]} vs {numeric_cols[1]}")
        if "timestamp" in df.columns or "date" in df.columns:
            viz.append(f"Line Plot — {numeric_cols[:3]} over time")
        viz.append(f"Box Plot — spread and outliers in {numeric_cols[:3]}")
        if len(numeric_cols) >= 2:
            viz.append("Heatmap — correlation matrix for numeric columns")
    if cat_cols:
        viz.append(f"Bar Chart — frequency of {cat_cols[0]}")
        viz.append(f"Pie Chart — proportion breakdown of {cat_cols[0]}")

    analysis: list[str] = []
    if len(numeric_cols) > 1:
        analysis.append(f"Linear Regression — {numeric_cols[0]} vs {numeric_cols[1]}")
        analysis.append(f"Correlation Analysis — all numeric columns")
    if numeric_cols:
        analysis.append(f"Statistical Summary — {numeric_cols[:3]}")
    if cat_cols:
        analysis.append(f"Value Counts — {cat_cols[0]}")

    return {"visualization": viz[:5], "analysis": analysis[:5]}