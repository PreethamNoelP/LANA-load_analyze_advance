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

from ..data.profile import ColumnProfile, profile_dataframe
from ..resources import PLOT_POINT_LIMIT, SAMPLE_SEED

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


def _note_sample(ax, shown: int, total: int) -> None:
    """State on the figure itself that it was drawn from a sample.

    A plot is a claim about the data. One drawn from 50,000 of 4,000,000 rows
    is a different claim from one drawn from all of them, and the difference
    has to be visible on the artifact that gets screenshotted into a report —
    not only in an API field the image leaves behind.
    """
    ax.text(
        0.995, -0.14,
        f"drawn from a fixed random sample of {shown:,} of {total:,} rows",
        transform=ax.transAxes, ha="right", va="top", fontsize=7, alpha=0.75,
    )


def _downsample(frame: pd.DataFrame, limit: int = PLOT_POINT_LIMIT):
    """Reduce a frame to at most ``limit`` rows, deterministically.

    Returns ``(frame, was_sampled)``. A figure is about 1,000 px across, so
    beyond a few tens of thousands of points a scatter or line plot is drawing
    on top of itself: the marks that land on an occupied pixel cost CPU and
    add nothing a viewer can see. Sampling is seeded so the same dataset
    always yields the same picture.
    """
    if len(frame) <= limit:
        return frame, False
    return frame.sample(limit, random_state=SAMPLE_SEED).sort_index(), True


def create_chart(
    df: pd.DataFrame,
    chart_type: str,
    column: str,
    secondary_column: str | None = None,
    profiles: dict[str, ColumnProfile] | None = None,
) -> bytes:
    """Render one of the supported chart types and return PNG bytes.

    Parameters
    ----------
    df:               Source DataFrame.
    chart_type:       One of CHART_TYPES.
    column:           Primary column to visualise.
    secondary_column: X-axis for Scatter Plot; ignored for others.
    profiles:         Precomputed column profiles, if the caller has them.
                      Only the Heatmap needs them.
    """
    if column not in df.columns:
        raise ValueError(f"Column '{column}' not found.")
    if chart_type == "Scatter Plot" and secondary_column and secondary_column not in df.columns:
        raise ValueError(f"Column '{secondary_column}' not found.")

    fig = Figure(figsize=(10, 5))
    ax = fig.subplots()
    _draw(df, chart_type, column, secondary_column, ax, profiles)
    fig.tight_layout()
    return _to_bytes(fig)


def _draw(
    df: pd.DataFrame,
    chart_type: str,
    column: str,
    secondary_column: str | None,
    ax,
    profiles: dict[str, ColumnProfile] | None = None,
) -> None:
    numeric = pd.api.types.is_numeric_dtype(df[column])

    # Charts that plot raw values need a numeric column. Saying so beats
    # rendering an empty axis or raising an opaque library error.
    if chart_type in ("Histogram", "Line Plot", "Box Plot", "Violin Plot",
                      "Area Plot", "Scatter Plot") and not numeric:
        _annotate(ax, f"'{column}' is not numeric — this chart plots raw values.\n"
                      f"Use a Bar Chart or Pie Chart to show its frequencies.")
        return

    # Only the columns being drawn are pulled out. Handing seaborn the whole
    # frame makes it process every column to plot one, which on a wide upload
    # is the dominant cost of rendering a single-series chart.
    if chart_type == "Line Plot":
        data, sampled = _downsample(df[[column]].dropna())
        sns.lineplot(x=data.index, y=data[column], ax=ax)
        ax.set_title(f"Line Plot — {column}")
        ax.set_ylabel(column)
        if sampled:
            _note_sample(ax, len(data), len(df))

    elif chart_type == "Bar Chart":
        # value_counts already aggregates, so there is nothing to downsample:
        # the cost is one pass and the output is bounded by MAX_CATEGORIES.
        counts = df[column].value_counts().head(MAX_CATEGORIES)
        sns.barplot(x=counts.index.astype(str), y=counts.values, ax=ax)
        total = int(df[column].nunique())
        suffix = f" (top {MAX_CATEGORIES} of {total})" if total > MAX_CATEGORIES else ""
        ax.set_title(f"Bar Chart — frequency of {column}{suffix}")
        ax.set_ylabel("Count")
        ax.tick_params(axis="x", rotation=45)

    elif chart_type == "Scatter Plot":
        if secondary_column:
            data, sampled = _downsample(df[[secondary_column, column]].dropna())
            sns.scatterplot(x=data[secondary_column], y=data[column], ax=ax)
            ax.set_xlabel(secondary_column)
        else:
            data, sampled = _downsample(df[[column]].dropna())
            sns.scatterplot(x=data.index, y=data[column], ax=ax)
        ax.set_ylabel(column)
        ax.set_title(f"Scatter Plot — {column}")
        if sampled:
            _note_sample(ax, len(data), len(df))

    elif chart_type == "Histogram":
        values = df[column].dropna()
        # The bars are binned counts and cost one pass at any size, but the KDE
        # overlay fits a kernel per observation, which is what makes a large
        # histogram slow. Above the point limit the curve is estimated from a
        # sample; the bars stay exact, computed from every row.
        if len(values) > PLOT_POINT_LIMIT:
            sns.histplot(values, kde=False, ax=ax)
            curve = values.sample(PLOT_POINT_LIMIT, random_state=SAMPLE_SEED)
            twin = ax.twinx()
            sns.kdeplot(curve, ax=twin, color="tab:orange", linewidth=1.4)
            twin.set_ylabel("")
            twin.set_yticks([])
            ax.set_title(f"Histogram — {column} (bars exact; curve from a sample)")
            _note_sample(ax, len(curve), len(values))
        else:
            sns.histplot(values, kde=True, ax=ax)
            ax.set_title(f"Histogram — {column}")

    elif chart_type == "Box Plot":
        # A box plot is five quantiles plus the points outside the whiskers;
        # quantiles are cheap on any size, so this stays exact.
        sns.boxplot(y=df[column], ax=ax)
        ax.set_ylabel(column)
        ax.set_title(f"Box Plot — {column}")

    elif chart_type == "Heatmap":
        _draw_heatmap(df, ax, profiles)

    elif chart_type == "Violin Plot":
        # Unlike the box plot, a violin is a kernel density estimate, so its
        # cost grows with the row count and it does need a bounded input.
        data, sampled = _downsample(df[[column]].dropna())
        sns.violinplot(y=data[column], ax=ax)
        ax.set_ylabel(column)
        ax.set_title(f"Violin Plot — {column}")
        if sampled:
            _note_sample(ax, len(data), len(df))

    elif chart_type == "Pie Chart":
        counts = df[column].value_counts().head(MAX_PIE_SLICES)
        ax.pie(counts.values, labels=counts.index.astype(str),
               autopct="%1.1f%%", startangle=140)
        total = int(df[column].nunique())
        suffix = f" (top {MAX_PIE_SLICES} of {total})" if total > MAX_PIE_SLICES else ""
        ax.set_title(f"Pie Chart — {column}{suffix}")

    elif chart_type == "Area Plot":
        data, sampled = _downsample(df[[column]].dropna())
        data[column].plot(kind="area", ax=ax, alpha=0.6)
        ax.set_ylabel(column)
        ax.set_title(f"Area Plot — {column}")
        if sampled:
            _note_sample(ax, len(data), len(df))

    else:
        _annotate(ax, f"Unknown chart type: {chart_type}")


# A correlation matrix is annotated cell by cell; past this many columns the
# numbers are unreadable and the render cost grows with the square of the count.
MAX_HEATMAP_COLUMNS = 25


def _draw_heatmap(
    df: pd.DataFrame,
    ax,
    profiles: dict[str, ColumnProfile] | None = None,
) -> None:
    """Correlation matrix over genuine measurements only.

    Identifiers and LANA's own annotation columns are excluded for the same
    reason the /correlation endpoint excludes them: an outlier score
    correlates 1.0 with the column it was derived from by construction, and a
    row ID correlates with whatever the rows were sorted by. Left in, those
    cells are the brightest thing in the plot and mean nothing.
    """
    if profiles is None:
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

    truncated = len(usable) - MAX_HEATMAP_COLUMNS
    usable = usable[:MAX_HEATMAP_COLUMNS]

    # Sampled above the point limit: a coefficient over 50,000 rows already
    # agrees with the full-data figure to more decimal places than a two-digit
    # cell label can show, and the matrix is the most expensive chart here.
    source, sampled = _downsample(df[usable])

    sns.heatmap(source.corr(), annot=True, fmt=".2f",
                cmap="coolwarm", ax=ax, linewidths=0.5)
    title = "Correlation Heatmap"
    notes = []
    if excluded:
        notes.append(f"excluded {len(excluded)} ID/annotation column(s)")
    if truncated > 0:
        notes.append(f"first {MAX_HEATMAP_COLUMNS} of {MAX_HEATMAP_COLUMNS + truncated}")
    if notes:
        title += "  (" + "; ".join(notes) + ")"
    ax.set_title(title)
    if sampled:
        _note_sample(ax, len(source), len(df))
