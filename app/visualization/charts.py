"""Chart generation — returns PNG bytes, called by the FastAPI /chart endpoint.

Uses matplotlib's object-oriented API rather than pyplot. pyplot keeps every
figure in a global registry that only ``plt.close()`` empties, so any code
path that raises between creating a figure and closing it leaks that figure
for the life of the process. It also stores the "current figure" and the
active style in global state, which two chart requests running in FastAPI's
threadpool would race over. Constructing ``Figure`` directly avoids both:
nothing is registered globally, and an abandoned figure is simply garbage
collected.
"""

import io

import matplotlib
matplotlib.use("Agg")  # headless backend — GUI backends break in server threads
import pandas as pd
import seaborn as sns
from matplotlib.figure import Figure

from ..data.profile import profile_dataframe

CHART_TYPES = [
    "Histogram",
    "Line Plot",
    "Bar Chart",
    "Scatter Plot",
    "Box Plot",
    "Heatmap",
    "Violin Plot",
    "Pie Chart",
    "Area Plot",
]

# Applied once at import. Seaborn's set_theme mutates global rcParams, so
# calling it per request would race between concurrent chart threads — and
# calling it after the figure exists (as this used to) styles nothing.
sns.set_theme(style="darkgrid")

# Categorical charts degrade into noise past this many bars/slices.
MAX_CATEGORIES = 20
MAX_PIE_SLICES = 10


def _to_bytes(fig: Figure) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    return buf.getvalue()


def _annotate(ax, message: str) -> None:
    """Render an explanatory message inside the figure instead of raising."""
    ax.text(0.5, 0.5, message, ha="center", va="center",
            transform=ax.transAxes, wrap=True)
    ax.set_axis_off()


def create_chart(
    df: pd.DataFrame,
    chart_type: str,
    column: str,
    secondary_column: str | None = None,
) -> bytes:
    """Render one of the supported chart types and return PNG bytes.

    Parameters
    ----------
    df:               Source DataFrame.
    chart_type:       One of CHART_TYPES.
    column:           Primary column to visualise.
    secondary_column: X-axis for Scatter Plot; ignored for others.
    """
    if column not in df.columns:
        raise ValueError(f"Column '{column}' not found.")
    if chart_type == "Scatter Plot" and secondary_column and secondary_column not in df.columns:
        raise ValueError(f"Column '{secondary_column}' not found.")

    fig = Figure(figsize=(10, 5))
    ax = fig.subplots()
    _draw(df, chart_type, column, secondary_column, ax)
    fig.tight_layout()
    return _to_bytes(fig)


def _draw(
    df: pd.DataFrame,
    chart_type: str,
    column: str,
    secondary_column: str | None,
    ax,
) -> None:
    numeric = pd.api.types.is_numeric_dtype(df[column])

    # Charts that plot raw values need a numeric column. Saying so beats
    # rendering an empty axis or raising an opaque library error.
    if chart_type in ("Histogram", "Line Plot", "Box Plot", "Violin Plot",
                      "Area Plot", "Scatter Plot") and not numeric:
        _annotate(ax, f"'{column}' is not numeric — this chart plots raw values.\n"
                      f"Use a Bar Chart or Pie Chart to show its frequencies.")
        return

    if chart_type == "Line Plot":
        sns.lineplot(data=df, x=df.index, y=column, ax=ax)
        ax.set_title(f"Line Plot — {column}")

    elif chart_type == "Bar Chart":
        counts = df[column].value_counts().head(MAX_CATEGORIES)
        sns.barplot(x=counts.index.astype(str), y=counts.values, ax=ax)
        total = int(df[column].nunique())
        suffix = f" (top {MAX_CATEGORIES} of {total})" if total > MAX_CATEGORIES else ""
        ax.set_title(f"Bar Chart — frequency of {column}{suffix}")
        ax.set_ylabel("Count")
        ax.tick_params(axis="x", rotation=45)

    elif chart_type == "Scatter Plot":
        x = secondary_column if secondary_column else df.index
        sns.scatterplot(data=df, x=x, y=column, ax=ax)
        ax.set_title(f"Scatter Plot — {column}")

    elif chart_type == "Histogram":
        sns.histplot(df[column].dropna(), kde=True, ax=ax)
        ax.set_title(f"Histogram — {column}")

    elif chart_type == "Box Plot":
        sns.boxplot(data=df, y=column, ax=ax)
        ax.set_title(f"Box Plot — {column}")

    elif chart_type == "Heatmap":
        _draw_heatmap(df, ax)

    elif chart_type == "Violin Plot":
        sns.violinplot(data=df, y=column, ax=ax)
        ax.set_title(f"Violin Plot — {column}")

    elif chart_type == "Pie Chart":
        counts = df[column].value_counts().head(MAX_PIE_SLICES)
        ax.pie(counts.values, labels=counts.index.astype(str),
               autopct="%1.1f%%", startangle=140)
        total = int(df[column].nunique())
        suffix = f" (top {MAX_PIE_SLICES} of {total})" if total > MAX_PIE_SLICES else ""
        ax.set_title(f"Pie Chart — {column}{suffix}")

    elif chart_type == "Area Plot":
        df[column].plot(kind="area", ax=ax, alpha=0.6)
        ax.set_title(f"Area Plot — {column}")

    else:
        _annotate(ax, f"Unknown chart type: {chart_type}")


def _draw_heatmap(df: pd.DataFrame, ax) -> None:
    """Correlation matrix over genuine measurements only.

    Identifiers and LANA's own annotation columns are excluded for the same
    reason the /correlation endpoint excludes them: an outlier score
    correlates 1.0 with the column it was derived from by construction, and a
    row ID correlates with whatever the rows were sorted by. Left in, those
    cells are the brightest thing in the plot and mean nothing.
    """
    profiles = profile_dataframe(df)
    usable = [name for name, p in profiles.items() if p.is_numeric_measure]
    excluded = [
        name for name, p in profiles.items()
        if pd.api.types.is_numeric_dtype(df[name]) and not p.is_numeric_measure
    ]

    if len(usable) < 2:
        _annotate(ax, "Need at least 2 numeric measurement columns for a heatmap."
                      + (f"\nExcluded as non-measurements: {', '.join(excluded[:5])}"
                         if excluded else ""))
        return

    sns.heatmap(df[usable].corr(), annot=True, fmt=".2f",
                cmap="coolwarm", ax=ax, linewidths=0.5)
    title = "Correlation Heatmap"
    if excluded:
        title += f"  (excluded {len(excluded)} ID/annotation column(s))"
    ax.set_title(title)
