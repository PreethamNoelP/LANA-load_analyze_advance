"""Chart generation — returns PNG bytes, called by the FastAPI /chart endpoint."""

import io

import matplotlib
matplotlib.use("Agg")  # headless backend — GUI backends break in server threads
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

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


def _to_bytes(fig: plt.Figure) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    buf.seek(0)
    data = buf.read()
    plt.close(fig)
    return data


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
    fig, ax = plt.subplots(figsize=(10, 5))
    sns.set_theme(style="darkgrid")

    if chart_type == "Line Plot":
        sns.lineplot(data=df, x=df.index, y=column, ax=ax)
        ax.set_title(f"Line Plot — {column}")

    elif chart_type == "Bar Chart":
        plot_df = df[[column]].head(50)
        sns.barplot(data=plot_df, x=plot_df.index, y=column, ax=ax)
        ax.set_title(f"Bar Chart — {column}")
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
        numeric_df = df.select_dtypes(include="number")
        if numeric_df.shape[1] < 2:
            ax.text(0.5, 0.5, "Need ≥ 2 numeric columns for heatmap",
                    ha="center", va="center", transform=ax.transAxes)
        else:
            sns.heatmap(numeric_df.corr(), annot=True, fmt=".2f",
                        cmap="coolwarm", ax=ax, linewidths=0.5)
        ax.set_title("Correlation Heatmap")

    elif chart_type == "Violin Plot":
        if pd.api.types.is_numeric_dtype(df[column]):
            sns.violinplot(data=df, y=column, ax=ax)
        else:
            ax.text(0.5, 0.5, f"'{column}' is not numeric",
                    ha="center", va="center", transform=ax.transAxes)
        ax.set_title(f"Violin Plot — {column}")

    elif chart_type == "Pie Chart":
        counts = df[column].value_counts().head(10)
        ax.pie(counts.values, labels=counts.index, autopct="%1.1f%%", startangle=140)
        ax.set_title(f"Pie Chart — {column}")

    elif chart_type == "Area Plot":
        if pd.api.types.is_numeric_dtype(df[column]):
            df[column].plot(kind="area", ax=ax, alpha=0.6)
        ax.set_title(f"Area Plot — {column}")

    else:
        ax.text(0.5, 0.5, f"Unknown chart type: {chart_type}",
                ha="center", va="center", transform=ax.transAxes)

    plt.tight_layout()
    return _to_bytes(fig)