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

from ..analysis.regression import perform_linear_regression
from ..analysis.statistics import compute_correlations
from ..data.profile import ColumnKind, ColumnProfile, profile_dataframe
from .base import ANSWER_SYSTEM_PROMPT

# Bounds — chosen so a wide dataset degrades gracefully rather than truncating
# mid-fact inside the model's context window.
MAX_DETAIL_COLUMNS = 30
MAX_CATEGORIES_PER_COLUMN = 12
MAX_GROUPBY_CATEGORICALS = 3
MAX_GROUPBY_NUMERICS = 2
# A discrete-coded column (e.g. a 1-5 rating) averaged by group is a
# genuinely common, meaningful question ("average rating by region") even
# though the column isn't a continuous measurement — this was previously
# excluded entirely (see docs/engineering-changelog.md, 2026-08-19 entry).
# Capped lower than MAX_GROUPBY_NUMERICS and added on top of it, not instead,
# since it's the supplementary case, not the common one.
MAX_GROUPBY_DISCRETE_NUMERICS = 1
MAX_GROUPBY_LEVELS = 15
MAX_CORRELATIONS = 8
# Regression is heavier than a correlation coefficient (a full OLS fit per
# pair), so only the strongest, already-significant relationships get one -
# enough to answer "what's the regression coefficient", not an exhaustive scan.
MAX_REGRESSIONS = 2
# Pairs grow quadratically and the prompt has a fixed budget; 12 columns is
# 66 pairs, of which only the strongest are rendered.
MAX_CORRELATION_COLUMNS = 12

# Token budget for the whole context block. The column and category caps above
# bound the *shape* of the context but not its length: 30 columns each with 12
# categories, plus group averages and correlations, can exceed a model's window
# on its own. When it does, Ollama truncates from the front — silently
# discarding the DATASET FACTS the answer depends on while leaving the question
# intact, which produces a confident answer grounded in nothing.
#
# So the length is measured and trimmed here instead, from the least
# answer-critical section first, and whatever was dropped is *stated* in the
# LIMITS block. A context that admits it is partial is usable; one that was
# quietly cut is not.
#
# Characters per token. English prose with numbers runs ~4; 3.5 is deliberately
# pessimistic so the estimate errs toward trimming early rather than
# overflowing. Avoids a tokenizer dependency for a budget check.
CHARS_PER_TOKEN = 3.5

# Room left for the question itself, which cannot be measured here: the context
# is built before the question is read (and cached per data version), so the
# budget has to assume a generous one rather than look at it.
QUESTION_TOKEN_MARGIN = 300

# Fallback for callers that don't state how many tokens the model may generate.
# Matches app.config's LLM_MAX_TOKENS default so the two cannot drift apart
# silently for the default configuration.
DEFAULT_MAX_ANSWER_TOKENS = 2048


def context_token_reserve(max_answer_tokens: int = DEFAULT_MAX_ANSWER_TOKENS) -> int:
    """Tokens of the window that are NOT available to the context block.

    This used to be a flat 1200, which was simply wrong: the system prompt
    alone measures ~585 tokens and the model is configured to generate up to
    ``LLM_MAX_TOKENS`` (2048 by default). The real reserve for a default setup
    is ~2900, so a full-sized context plus a long answer could exceed an 8192
    window by well over a thousand tokens — and the resulting front-truncation
    is precisely the silent failure this module exists to prevent.

    Measured from the actual system prompt rather than estimated, so editing
    that prompt can never quietly invalidate the budget again.
    """
    return (
        estimate_tokens(ANSWER_SYSTEM_PROMPT)
        + QUESTION_TOKEN_MARGIN
        + max(0, max_answer_tokens)
    )

# Trimmed in this order. Column descriptions are load-bearing for almost every
# question and are given up last; correlations are the most specialised and go
# first.
_TRIM_ORDER = ("relationships", "groups", "breakdowns")


def estimate_tokens(text: str) -> int:
    """Approximate token count for a budget check, without a tokenizer."""
    return int(len(text) / CHARS_PER_TOKEN) + 1


@dataclass
class Fact:
    """One verifiable numeric claim derived from the data.

    ``category``/``category_column``/``family`` are set only for a fact that
    is scoped to one specific level of a column (e.g. "share of remote=False"
    or "mean revenue for region=north") — ``category`` is the literal level
    value, ``category_column`` is the column it's a level of (which may
    differ from ``column``: a group-by fact's ``column`` is the value being
    averaged, its ``category_column`` is the column it was grouped by), and
    ``family`` groups every fact that is a direct alternative to this one —
    same statistic, same columns, different category — so the validator can
    tell whether a matched value has siblings a mislabeled answer could have
    confused it with (see validation.py's attribution check).
    """

    label: str
    value: float
    column: str | None = None
    category: str | None = None
    category_column: str | None = None
    family: str | None = None


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
    profiles: dict[str, ColumnProfile] | None = None,
    token_budget: int | None = None,
    max_answer_tokens: int = DEFAULT_MAX_ANSWER_TOKENS,
) -> GroundedContext:
    """Assemble the grounded context for a DataFrame.

    ``lineage_narrative`` is the cleaning ledger's account of how this version
    was produced. Including it is what stops the model describing imputed
    values as if they were measured.

    ``profiles`` lets a caller that already holds them supply them instead of
    paying to re-derive them. A stored frame never changes, so profiling it
    twice can only produce the same answer more slowly.

    ``token_budget`` is the model's context window. Sections are dropped from
    the least answer-critical end until the block fits, and every drop is
    disclosed in LIMITS OF THIS CONTEXT.
    """
    if profiles is None:
        profiles = profile_dataframe(df)
    facts: list[Fact] = []
    ranges: dict[str, tuple[float, float]] = {}
    vocabulary: set[str] = {str(c) for c in df.columns}

    header: list[str] = [
        "=== DATASET FACTS ===",
        f"This is the '{version}' version of the uploaded dataset.",
        f"Shape: {len(df):,} rows x {len(df.columns)} columns.",
        f"Columns: {', '.join(str(c) for c in df.columns)}.",
    ]
    facts.append(Fact("row count", float(len(df))))
    facts.append(Fact("column count", float(len(df.columns))))

    # ── Provenance ───────────────────────────────────────────────────────────
    if lineage_narrative:
        header += ["", "--- HOW THIS VERSION WAS PRODUCED ---", lineage_narrative]

    # ── Column-level facts ───────────────────────────────────────────────────
    detail = list(profiles.items())[:MAX_DETAIL_COLUMNS]
    omitted = [name for name, _ in list(profiles.items())[MAX_DETAIL_COLUMNS:]]

    header += ["", "--- COLUMNS ---"]
    for name, p in detail:
        header.append(_describe_column(name, p, facts, ranges, vocabulary))

    # Sections are kept separate rather than appended to one list so the
    # budget check below can drop a whole section cleanly. Facts stay in the
    # ledger even if their section is dropped from the prompt — the validator
    # verifies against what LANA computed, not against what the model was
    # shown, so a number the model produced from general knowledge that
    # happens to match a dropped fact is still correctly marked verified.
    sections: dict[str, list[str]] = {}

    breakdown_lines = _category_breakdowns(df, profiles, facts, vocabulary)
    if breakdown_lines:
        sections["breakdowns"] = [
            "", "--- CATEGORY BREAKDOWNS (exact counts) ---", *breakdown_lines
        ]

    group_lines = _group_summaries(df, profiles, facts)
    if group_lines:
        sections["groups"] = ["", "--- GROUP AVERAGES (exact) ---", *group_lines]

    corr_lines = _correlation_summary(df, profiles, facts)
    if corr_lines:
        sections["relationships"] = [
            "", "--- RELATIONSHIPS BETWEEN NUMERIC COLUMNS ---", *corr_lines
        ]

    # ── Fit to the model's window, dropping the least critical first ──────────
    dropped: list[str] = []
    allowance: int | None = None
    if token_budget:
        allowance = max(0, token_budget - context_token_reserve(max_answer_tokens))
        for name in _TRIM_ORDER:
            if name not in sections:
                continue
            current = estimate_tokens(
                "\n".join(header + [ln for k in sections for ln in sections[k]])
            )
            if current <= allowance:
                break
            sections.pop(name)
            dropped.append(name)

    lines = list(header)
    for name in ("breakdowns", "groups", "relationships"):
        if name in sections:
            lines += sections[name]

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
    if dropped:
        readable = {
            "breakdowns": "exact category counts",
            "groups": "group averages",
            "relationships": "correlations between numeric columns",
        }
        limits.append(
            "This dataset is too wide to describe fully within the model's context "
            "window, so the following were omitted entirely and you know nothing "
            f"about them: {', '.join(readable[d] for d in dropped)}. Say so if a "
            "question needs them."
        )
    # Only the optional sections can be dropped; the per-column detail is
    # load-bearing for almost every question and is kept even when that means
    # exceeding the budget. Saying so is the difference between a context the
    # model knows is partial and one the runtime quietly cuts from the front.
    over_budget = False
    if allowance is not None:
        projected = estimate_tokens("\n".join(lines))
        over_budget = projected > allowance
        if over_budget:
            limits.append(
                "This dataset's column list alone exceeds the space available in "
                "the model's context window. Earlier facts may have been cut off "
                "before you saw them, so treat any column you cannot actually see "
                "described above as unknown rather than absent."
            )

    lines += [f"- {limit}" for limit in limits]

    text = "\n".join(lines)
    coverage = {
        "columns_total": len(df.columns),
        "columns_detailed": len(detail),
        "columns_omitted": omitted,
        "facts": len(facts),
        "version": version,
        "estimated_tokens": estimate_tokens(text),
        "sections_dropped": dropped,
        "over_budget": over_budget,
    }

    return GroundedContext(
        text=text,
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
                facts.append(Fact(
                    f"count of {name}={value}", float(count), column=name,
                    category=str(value), category_column=name, family=f"count::{name}",
                ))
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
        facts.append(Fact(
            f"count of {name}={value}", float(count), column=name,
            category=str(value), category_column=name, family=f"count::{name}",
        ))
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
            facts.append(Fact(
                f"share of {name}={label} in percent", round(pct, 1), column=name,
                category=label, category_column=name, family=f"share_pct::{name}",
            ))
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
    # An encoded scale (e.g. a 1-5 rating) averaged by group is a real,
    # commonly-asked question ("average rating by region") even though the
    # column isn't a continuous measurement. Kept separate from `numerics`
    # so it's labelled distinctly rather than presented as if it carried the
    # same precision as an actual measurement.
    discrete_numerics = [
        name for name, p in profiles.items()
        if p.is_numeric_measure and p.discrete_code
    ][:MAX_GROUPBY_DISCRETE_NUMERICS]

    if not categoricals or (not numerics and not discrete_numerics):
        return []

    targets = [(n, False) for n in numerics] + [(n, True) for n in discrete_numerics]

    lines: list[str] = []
    for cat in categoricals:
        for num, is_discrete in targets:
            if num == cat:
                continue  # a column can't be meaningfully grouped by itself
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
                facts.append(Fact(
                    f"mean {num} for {cat}={label}", round(mean_val, 6), column=num,
                    category=label, category_column=cat, family=f"mean::{num}_by::{cat}",
                ))
                facts.append(Fact(
                    f"total {num} for {cat}={label}", round(float(row["sum"]), 6), column=num,
                    category=label, category_column=cat, family=f"total::{num}_by::{cat}",
                ))
            best, worst = grouped.index[0], grouped.index[-1]
            note = " [encoded scale — average is illustrative, not a continuous measurement]" if is_discrete else ""
            lines.append(
                f"- '{num}' by '{cat}' (highest mean first){note}: " + "; ".join(parts)
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
        # Hand down the profiles for exactly these columns. A per-column
        # profile does not depend on which other columns are present, so the
        # subset is identical to what the scan would compute for itself — and
        # this is the difference between profiling the frame once per question
        # and profiling it twice.
        pairs = compute_correlations(
            df[numeric], profiles={name: profiles[name] for name in numeric}
        )
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
    lines += _regression_lines(df, significant, facts)
    return lines


def _regression_lines(
    df: pd.DataFrame,
    significant_pairs: list[dict[str, Any]],
    facts: list[Fact],
) -> list[str]:
    """OLS slope/intercept/R^2 for the strongest significant pairs.

    Correlation alone cannot answer "what's the regression coefficient" or
    "how much does Y change per unit of X" — a real question a user asks
    once a relationship is established (docs/engineering-changelog.md,
    2026-08-19 entry names this exact gap). Bounded to the top few pairs
    because a full OLS fit costs more than a correlation coefficient.
    """
    lines: list[str] = []
    for pair in significant_pairs[:MAX_REGRESSIONS]:
        x_col, y_col = pair["column_a"], pair["column_b"]
        try:
            reg = perform_linear_regression(df, x_col, y_col)
        except Exception:
            # Same columns the correlation scan just fit successfully; a
            # regression-specific refusal (e.g. a post-hoc constant check)
            # is rare enough to skip quietly rather than break the section.
            continue
        lines.append(
            f"- Regression of '{y_col}' on '{x_col}': coefficient {_fmt(reg.coefficient)} "
            f"(95% CI {_fmt(reg.ci95_low)} to {_fmt(reg.ci95_high)}), "
            f"intercept {_fmt(reg.intercept)}, R^2 = {reg.r2:.3f} (n = {reg.n:,}). "
            f"Each one-unit increase in '{x_col}' is associated with a "
            f"{_fmt(reg.coefficient)} change in '{y_col}' — an association in this "
            "data, not a causal effect."
        )
        facts.append(Fact(f"regression coefficient of {y_col} on {x_col}", round(reg.coefficient, 6)))
        facts.append(Fact(f"regression intercept of {y_col} on {x_col}", round(reg.intercept, 6)))
        facts.append(Fact(f"regression R2 of {y_col} on {x_col}", round(reg.r2, 6)))
    return lines
