"""Grounded context construction for the AI question path.

The old context gave the model min/max/mean/median/std per column. Ask "which
region has the highest average revenue?" against that and the model has no
grounding whatsoever for the answer — so it invents one, fluently and
confidently. Summary statistics are not a substitute for the facts a question
actually needs.

This module builds context as a **fact set** first and prompt text second:

* every fact is computed from the DataFrame and recorded with a label and a
  numeric value, so the same structure can later verify the model's answer
  (see :mod:`app.llm.validation`);
* the facts that answer real questions are included — category breakdowns and
  group-by aggregates, not just column-level moments;
* the prompt states explicitly what is *absent* from the context, which is
  what lets the model say "I cannot answer that from the data provided"
  instead of guessing.

Everything is bounded so a 500-column upload cannot silently overflow the
model's context window and push the earlier facts out.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from ..analysis.statistics import compute_correlations
from ..data.profile import ColumnKind, ColumnProfile, profile_dataframe

# Bounds — chosen so a wide dataset degrades gracefully rather than truncating
# mid-fact inside the model's context window.
MAX_DETAIL_COLUMNS = 30
MAX_CATEGORIES_PER_COLUMN = 12
MAX_GROUPBY_CATEGORICALS = 3
MAX_GROUPBY_NUMERICS = 2
MAX_GROUPBY_LEVELS = 15
MAX_CORRELATIONS = 8
# Pairs grow quadratically and the prompt has a fixed budget; 12 columns is
# 66 pairs, of which only the strongest are rendered.
MAX_CORRELATION_COLUMNS = 12


@dataclass
class Fact:
    """One verifiable numeric claim derived from the data."""

    label: str
    value: float
    column: str | None = None


@dataclass
class GroundedContext:
    """Prompt text plus the machine-checkable facts behind it."""

    text: str
    facts: list[Fact] = field(default_factory=list)
    column_ranges: dict[str, tuple[float, float]] = field(default_factory=dict)
    vocabulary: set[str] = field(default_factory=set)   # column names + category values
    coverage: dict[str, Any] = field(default_factory=dict)


def _fmt(value: float) -> str:
    """Format a number for the prompt without losing precision to rounding."""
    if value is None:
        return "n/a"
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return f"{int(value):,}"
    return f"{value:,.6g}"


def build_context(
    df: pd.DataFrame,
    lineage_narrative: str | None = None,
    version: str = "original",
) -> GroundedContext:
    """Assemble the grounded context for a DataFrame.

    ``lineage_narrative`` is the cleaning ledger's account of how this version
    was produced. Including it is what stops the model describing imputed
    values as if they were measured.
    """
    profiles = profile_dataframe(df)
    facts: list[Fact] = []
    ranges: dict[str, tuple[float, float]] = {}
    vocabulary: set[str] = {str(c) for c in df.columns}

    lines: list[str] = [
        "=== DATASET FACTS ===",
        f"This is the '{version}' version of the uploaded dataset.",
        f"Shape: {len(df):,} rows x {len(df.columns)} columns.",
        f"Columns: {', '.join(str(c) for c in df.columns)}.",
    ]
    facts.append(Fact("row count", float(len(df))))
    facts.append(Fact("column count", float(len(df.columns))))

    # ── Provenance ───────────────────────────────────────────────────────────
    if lineage_narrative:
        lines += ["", "--- HOW THIS VERSION WAS PRODUCED ---", lineage_narrative]

    # ── Column-level facts ───────────────────────────────────────────────────
    detail = list(profiles.items())[:MAX_DETAIL_COLUMNS]
    omitted = [name for name, _ in list(profiles.items())[MAX_DETAIL_COLUMNS:]]

    lines += ["", "--- COLUMNS ---"]
    for name, p in detail:
        lines.append(_describe_column(name, p, facts, ranges, vocabulary))

    # ── Category breakdowns: what "which X has the most Y" needs ─────────────
    breakdown_lines = _category_breakdowns(df, profiles, facts, vocabulary)
    if breakdown_lines:
        lines += ["", "--- CATEGORY BREAKDOWNS (exact counts) ---", *breakdown_lines]

    # ── Group-by aggregates: the other half of comparative questions ─────────
    group_lines = _group_summaries(df, profiles, facts)
    if group_lines:
        lines += ["", "--- GROUP AVERAGES (exact) ---", *group_lines]

    # ── Relationships, corrected for multiple testing ────────────────────────
    corr_lines = _correlation_summary(df, profiles, facts)
    if corr_lines:
        lines += ["", "--- RELATIONSHIPS BETWEEN NUMERIC COLUMNS ---", *corr_lines]

    # ── Explicit statement of what is missing from this context ──────────────
    lines += ["", "--- LIMITS OF THIS CONTEXT ---"]
    limits = [
        "You do NOT have the individual rows. You cannot look up, count or filter "
        "records beyond the aggregates stated above.",
        "Any number not stated above is unavailable. Do not estimate, interpolate "
        "or infer one — say that the data provided does not contain it.",
    ]
    if omitted:
        limits.append(
            f"{len(omitted)} column(s) are not detailed above and you know nothing "
            f"about their values: {', '.join(omitted[:10])}"
            + ("…" if len(omitted) > 10 else "")
        )
    truncated = [
        name for name, p in detail
        if p.kind is ColumnKind.CATEGORICAL and p.unique > MAX_CATEGORIES_PER_COLUMN
    ]
    if truncated:
        limits.append(
            "Category lists are truncated to the most frequent values for: "
            f"{', '.join(truncated[:8])}. Lower-frequency categories exist but are not shown."
        )
    lines += [f"- {limit}" for limit in limits]

    coverage = {
        "columns_total": len(df.columns),
        "columns_detailed": len(detail),
        "columns_omitted": omitted,
        "facts": len(facts),
        "version": version,
    }

    return GroundedContext(
        text="\n".join(lines),
        facts=facts,
        column_ranges=ranges,
        vocabulary=vocabulary,
        coverage=coverage,
    )


def _describe_column(
    name: str,
    p: ColumnProfile,
    facts: list[Fact],
    ranges: dict[str, tuple[float, float]],
    vocabulary: set[str],
) -> str:
    missing = f"{p.null_count:,} missing ({p.null_pct}%)" if p.null_count else "no missing values"

    if p.kind is ColumnKind.EMPTY:
        return f"- '{name}': every value is missing. Nothing can be said about it."

    # LANA's own bookkeeping. Described so the model can reason about *which*
    # rows were flagged or imputed, but never presented as a measured variable.
    if p.is_annotation:
        flagged = next((c for v, c in p.top_values if v in ("True", "true")), None)
        detail = f"{flagged:,} rows marked True" if flagged is not None else f"{p.count:,} values"
        return (
            f"- '{name}': added by LANA's cleaning step, not part of the uploaded "
            f"data ({detail}). Use it to say which rows were affected; do not "
            f"treat it as a measurement or correlate it with the column it came from."
        )

    if p.is_numeric_measure or p.kind is ColumnKind.IDENTIFIER:
        if p.min is not None and p.max is not None:
            ranges[name] = (p.min, p.max)
        for label, value in (("min", p.min), ("max", p.max), ("mean", p.mean),
                             ("median", p.median), ("std", p.std)):
            if value is not None:
                facts.append(Fact(f"{name} {label}", float(value), column=name))
        detail = (
            f"range {_fmt(p.min)} to {_fmt(p.max)}, mean {_fmt(p.mean)}, "
            f"median {_fmt(p.median)}, std {_fmt(p.std)}"
        )
        note = ""
        if p.kind is ColumnKind.IDENTIFIER:
            note = " [identifier — arithmetic on it is not meaningful]"
        elif p.discrete_code:
            vocabulary.update(str(v) for v, _ in p.top_values)
            for value, count in p.top_values:
                facts.append(Fact(f"count of {name}={value}", float(count), column=name))
            levels = ", ".join(f"{v} ({c:,})" for v, c in p.top_values)
            note = (
                f" [encoded category, not a measurement — value counts: {levels}]"
            )
        elif p.shape in ("moderately skewed", "highly skewed"):
            note = f" [{p.shape}; median is the reliable centre, not the mean]"
        return f"- '{name}' (numeric): {detail}, {missing}.{note}"

    if p.kind is ColumnKind.DATETIME:
        return f"- '{name}' (date/time): {p.unique:,} distinct timestamps, {missing}."

    if p.kind is ColumnKind.CONSTANT:
        return f"- '{name}': a single repeated value across every row. Carries no information."

    if p.kind is ColumnKind.TEXT:
        return (
            f"- '{name}' (free text): {p.unique:,} distinct values across {p.count:,} "
            f"rows — too varied to group, {missing}."
        )

    # Categorical / boolean.
    shown = p.top_values[:MAX_CATEGORIES_PER_COLUMN]
    vocabulary.update(str(v) for v, _ in shown)
    for value, count in shown:
        facts.append(Fact(f"count of {name}={value}", float(count), column=name))
    listed = ", ".join(f"{value} ({count:,})" for value, count in shown)
    more = f", +{p.unique - len(shown)} rarer" if p.unique > len(shown) else ""
    return f"- '{name}' (categorical): {p.unique:,} distinct — {listed}{more}. {missing}."


def _category_breakdowns(
    df: pd.DataFrame,
    profiles: dict[str, ColumnProfile],
    facts: list[Fact],
    vocabulary: set[str],
) -> list[str]:
    """Exact value counts with percentages for low-cardinality columns."""
    lines: list[str] = []
    total = len(df)
    for name, p in list(profiles.items())[:MAX_DETAIL_COLUMNS]:
        if not p.is_groupable or p.unique > MAX_CATEGORIES_PER_COLUMN:
            continue
        counts = df[name].value_counts()
        parts = []
        for value, count in counts.items():
            label = str(value)
            vocabulary.add(label)
            pct = count / total * 100 if total else 0
            parts.append(f"{label}={count:,} ({pct:.1f}%)")
            facts.append(Fact(f"share of {name}={label} in percent", round(pct, 1), column=name))
        lines.append(f"- '{name}': " + "; ".join(parts))
    return lines


def _group_summaries(
    df: pd.DataFrame,
    profiles: dict[str, ColumnProfile],
    facts: list[Fact],
) -> list[str]:
    """Mean of each numeric column within each level of each categorical column.

    This is the single highest-value addition to the context: comparative
    questions ("which segment performs best?") are the most common thing asked
    of an analyst, and without these aggregates the model can only guess.
    """
    categoricals = [
        name for name, p in profiles.items()
        if p.is_groupable and p.unique <= MAX_GROUPBY_LEVELS
    ][:MAX_GROUPBY_CATEGORICALS]
    numerics = [
        name for name, p in profiles.items()
        if p.is_numeric_measure and not p.discrete_code
    ][:MAX_GROUPBY_NUMERICS]

    if not categoricals or not numerics:
        return []

    lines: list[str] = []
    for cat in categoricals:
        for num in numerics:
            try:
                grouped = df.groupby(cat, observed=True)[num].agg(["mean", "count", "sum"])
            except (TypeError, ValueError):
                continue
            grouped = grouped[grouped["count"] > 0].sort_values("mean", ascending=False)
            if grouped.empty:
                continue
            parts = []
            for level, row in grouped.head(MAX_GROUPBY_LEVELS).iterrows():
                label = str(level)
                mean_val = float(row["mean"])
                parts.append(f"{label}: mean {_fmt(mean_val)} (n={int(row['count']):,})")
                facts.append(Fact(f"mean {num} for {cat}={label}", round(mean_val, 6), column=num))
                facts.append(Fact(f"total {num} for {cat}={label}",
                                  round(float(row["sum"]), 6), column=num))
            best, worst = grouped.index[0], grouped.index[-1]
            lines.append(
                f"- '{num}' by '{cat}' (highest mean first): " + "; ".join(parts)
                + f". Highest: {best}. Lowest: {worst}."
            )
    return lines


def _correlation_summary(
    df: pd.DataFrame,
    profiles: dict[str, ColumnProfile],
    facts: list[Fact],
) -> list[str]:
    numeric = [name for name, p in profiles.items()
               if p.is_numeric_measure][:MAX_CORRELATION_COLUMNS]
    if len(numeric) < 2:
        return []

    try:
        pairs = compute_correlations(df[numeric])
    except Exception:
        return []
    if not pairs:
        return []

    lines: list[str] = []
    significant = [p for p in pairs if p.get("significant")]
    for pair in pairs[:MAX_CORRELATIONS]:
        verdict = "survives" if pair["significant"] else "does NOT survive"
        lines.append(
            f"- '{pair['column_a']}' vs '{pair['column_b']}': r = {pair['correlation']} "
            f"({pair['strength']} {pair['direction']}, n = {pair['n']:,}); "
            f"{verdict} false-discovery correction (q = {pair['q_value']})."
        )
        facts.append(Fact(
            f"correlation between {pair['column_a']} and {pair['column_b']}",
            float(pair["correlation"]),
        ))

    if not significant:
        lines.append(
            "- None of these relationships survive correction for the number of "
            "pairs tested. State that clearly rather than reporting the strongest "
            "one as a finding."
        )
    lines.append("- Correlation is association only. Never describe it as one column causing another.")
    return lines
