"""Investigate: the model proposes drivers, the engine decides which hold up.

Every other question path in LANA answers what was asked. This one answers a
question nobody phrased precisely — "what explains this metric" — by letting
the model do what it is actually good at (generating plausible, dataset-aware
candidate explanations) and refusing to let it do what it is bad at (judging
whether any of them are real).

The flow, deliberately two calls to the model with deterministic work between
them:

1. **Propose.** The model sees the target column and a list of candidate
   driver columns (name + kind only, never cell values) and picks a handful
   worth testing, each with a one-line rationale.
2. **Test.** Each candidate is tested by :mod:`app.analysis.hypothesis` —
   Kruskal-Wallis for a categorical driver, Pearson correlation for a numeric
   one — against the real rows. The model never sees this step; it cannot
   talk its way past a p-value.
3. **Correct.** Testing several hypotheses at once is exactly the
   multiple-comparisons problem :mod:`app.analysis.statistics` already solves
   for the correlation scan, so the same Benjamini-Hochberg routine is reused
   here rather than re-derived.
4. **Narrate.** The model is shown only the corrected results — numbers, not
   raw data — and asked to summarise them in plain English, honestly
   reporting when nothing survived correction rather than reaching for the
   least-bad option.

If the model's proposal cannot be parsed (a small local model wrapping its
answer in prose, or refusing the JSON instruction outright), a deterministic
fallback selects candidate columns instead of failing the investigation —
the same "never worse than what it replaced" guarantee
:mod:`app.llm.sql_answer` makes about its own repair path.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import pandas as pd

from ..analysis.hypothesis import DriverTest, test_driver

# Reused rather than re-derived: testing several candidate drivers at once is
# the same false-discovery problem the correlation scan already solves, and
# tests/test_data_science.py already reaches into this same private name —
# this is an established internal-reuse point, not a new one.
from ..analysis.statistics import _bh_qvalues
from ..data.profile import ColumnProfile
from .reasoning import strip_reasoning

logger = logging.getLogger("lana.investigate")

# How many candidate drivers the model may propose. Enough to make the
# ranked list feel like a real investigation; few enough that the narrative
# step's prompt and the demo itself stay readable.
MAX_HYPOTHESES = 6

# Candidate columns shown to the proposing model. A wide dataset is trimmed
# here rather than left to overflow the model's context window, the same
# reasoning app.analysis.sql_engine.schema_for_prompt applies to its own cap.
MAX_CANDIDATES_SHOWN = 40

FDR_ALPHA = 0.05

HYPOTHESIS_SYSTEM_PROMPT = """You are LANA's data analyst. You are given a target column from a dataset and a list of other columns that exist in the same dataset. Your job is to pick which of those columns are most worth testing as possible drivers of variation in the target.

RULES:
1. Pick between 3 and 6 columns from the CANDIDATE COLUMNS list only. Never invent a column name that is not in that list.
2. For each, give a one-sentence rationale for why it might explain differences in the target — a plausible mechanism or a common real-world pattern, not a guess at the actual numbers (you cannot see the data's values).
3. Prefer columns whose rationale is specific to this target and these column names, not a generic restatement.
4. Output ONLY a JSON array, nothing before or after it. Each element: {"column": "<exact column name from the list>", "rationale": "<one sentence>"}.
5. Do not wrap the JSON in markdown fences or add commentary."""

NARRATIVE_SYSTEM_PROMPT = """You are LANA's data analyst reporting the results of an automated investigation. Several candidate explanations for a target metric were tested with real statistical tests (Kruskal-Wallis for group comparisons, Pearson correlation for numeric relationships), and corrected for testing multiple hypotheses at once.

RULES:
1. The RESULTS section is the ONLY source of numbers and verdicts. Never state a figure, column name, or verdict that is not in it.
2. Lead with what survived correction, if anything. If nothing did, say so plainly — do not soften a null result into a finding.
3. NO CAUSAL LANGUAGE. Write "is associated with" or "differs by", never "causes", "drives", "leads to", or "because of".
4. Be concise: 2-4 sentences. No preamble, no restating the question, no describing the method (the reader can already see the test names and p-values)."""


@dataclass
class InvestigationReport:
    """Everything one 'what explains this metric' run produced."""

    target_column: str
    hypotheses: list[DriverTest]
    tests_run: int
    significant_count: int
    fdr_alpha: float
    narrative: str
    candidate_note: str | None = None
    proposal_note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_column": self.target_column,
            "hypotheses": [h.to_dict() for h in self.hypotheses],
            "tests_run": self.tests_run,
            "significant_count": self.significant_count,
            "fdr_alpha": self.fdr_alpha,
            "narrative": self.narrative,
            "candidate_note": self.candidate_note,
            "proposal_note": self.proposal_note,
        }


class InvestigationFailed(Exception):
    """Raised when an investigation cannot even be attempted. Maps to a 400."""


# ── Candidate selection ───────────────────────────────────────────────────────

def _candidate_columns(
    target_col: str, profiles: dict[str, ColumnProfile],
) -> list[ColumnProfile]:
    """Columns worth offering the model as a possible driver.

    A driver has to be either groupable (so Kruskal-Wallis has groups to
    compare) or a numeric measurement (so Pearson has something to correlate)
    — the same two shapes :func:`app.analysis.hypothesis.test_driver`
    actually knows how to test. Offering anything else would let the model
    propose a hypothesis that is guaranteed to come back untestable.
    """
    candidates = [
        p for name, p in profiles.items()
        if name != target_col and (p.is_groupable or p.is_numeric_measure)
    ]
    return candidates[:MAX_CANDIDATES_SHOWN]


def _describe_candidate(p: ColumnProfile) -> str:
    if p.is_groupable:
        return f"{p.name} (categorical, {p.unique} groups)"
    return f"{p.name} (numeric)"


def _hypothesis_prompt(target_col: str, candidates: list[ColumnProfile]) -> str:
    lines = [
        f"TARGET COLUMN: {target_col}",
        "",
        "CANDIDATE COLUMNS (choose only from this list):",
        *[f"- {_describe_candidate(p)}" for p in candidates],
    ]
    return "\n".join(lines)


# ── Parsing the model's proposal ──────────────────────────────────────────────

_FENCE_RE = re.compile(r"```(?:json)?\s*(.+?)```", re.DOTALL | re.IGNORECASE)
_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


def _extract_json_array(raw: str) -> list[Any]:
    """Pull a JSON array out of a model reply that may be fenced or padded.

    Mirrors app.llm.sql_answer.extract_sql's approach to the same problem —
    a small local model reliably produces the right content wrapped in the
    wrong envelope, so extracting is worth far more than rejecting.
    """
    if not raw or not raw.strip():
        return []
    text = raw.strip()
    fenced = _FENCE_RE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    match = _ARRAY_RE.search(text)
    if match:
        text = match.group(0)
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _fallback_hypotheses(candidates: list[ColumnProfile]) -> list[dict[str, str]]:
    """Deterministic candidate selection when the model's proposal is unusable.

    Investigate must never fail outright just because a small local model
    ignored the JSON instruction — the same resilience principle
    app.llm.sql_answer applies by falling back to the ledger path rather than
    surfacing a planner failure to the user.
    """
    return [
        {"column": p.name, "rationale": "Selected automatically — the model's own proposal could not be read."}
        for p in candidates[:MAX_HYPOTHESES]
    ]


def propose_hypotheses(
    provider: Any, target_col: str, candidates: list[ColumnProfile],
) -> tuple[list[dict[str, str]], str | None]:
    """Ask the model which candidates are worth testing. Returns (items, note).

    ``note`` is set only when the fallback selection was used, so the caller
    can tell the user their hypotheses came from a heuristic rather than the
    model's own reasoning.
    """
    valid_names = {p.name for p in candidates}
    raw = provider.generate(
        _hypothesis_prompt(target_col, candidates),
        system_prompt=HYPOTHESIS_SYSTEM_PROMPT,
    )
    items = _extract_json_array(raw)

    seen: set[str] = set()
    parsed: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        column = str(item.get("column", "")).strip()
        if column not in valid_names or column in seen:
            continue
        seen.add(column)
        rationale = str(item.get("rationale", "")).strip()[:300]
        parsed.append({"column": column, "rationale": rationale})
        if len(parsed) >= MAX_HYPOTHESES:
            break

    if parsed:
        return parsed, None

    logger.info("Investigate: model proposal unusable, using fallback selection")
    return _fallback_hypotheses(candidates), (
        "The model's proposal could not be read, so candidate columns were "
        "selected automatically instead of by the model's own reasoning."
    )


# ── Narrative ──────────────────────────────────────────────────────────────────

def _results_for_prompt(hypotheses: list[DriverTest]) -> str:
    lines = []
    for h in hypotheses:
        if not h.testable:
            lines.append(f"- {h.driver_column}: not testable ({h.reason})")
            continue
        verdict = "SURVIVES correction" if h.significant else "does NOT survive correction"
        lines.append(
            f"- {h.driver_column}: {h.test} test, effect size {h.effect_size:.3f} "
            f"({h.effect_label}), p={h.p_value:.4g}, q={h.q_value:.4g} — {verdict}. "
            f"{h.direction or ''}"
        )
    return "\n".join(lines)


def _narrative_prompt(target_col: str, hypotheses: list[DriverTest], alpha: float) -> str:
    return (
        f"TARGET: {target_col}\n"
        f"FDR ALPHA: {alpha}\n\n"
        "=== RESULTS (each hypothesis tested independently, then corrected "
        "for testing multiple at once) ===\n"
        f"{_results_for_prompt(hypotheses)}\n\n"
        "Summarise what this investigation found."
    )


def _fallback_narrative(target_col: str, hypotheses: list[DriverTest]) -> str:
    """Deterministic summary used when the narrative model call itself fails.

    Built from the same corrected results the model would have read, so a
    down LLM degrades the investigation to "numbers without prose", never to
    "no answer at all" — the numbers are the part that was actually verified.
    """
    supported = [h for h in hypotheses if h.testable and h.significant]
    if not supported:
        return (
            f"No candidate driver of '{target_col}' survived false-discovery "
            f"correction across {sum(1 for h in hypotheses if h.testable)} "
            f"hypotheses tested."
        )
    top = supported[0]
    return (
        f"'{top.driver_column}' is associated with '{target_col}' and survives "
        f"false-discovery correction (q={top.q_value:.4g}, effect size "
        f"{top.effect_label}). {top.direction or ''}"
    )


def _generate_narrative(
    provider: Any, target_col: str, hypotheses: list[DriverTest], alpha: float,
) -> str:
    try:
        return strip_reasoning(
            provider.generate(
                _narrative_prompt(target_col, hypotheses, alpha),
                system_prompt=NARRATIVE_SYSTEM_PROMPT,
            )
        )
    except Exception:
        logger.warning("Investigate: narrative generation failed; using fallback", exc_info=True)
        return _fallback_narrative(target_col, hypotheses)


# ── Orchestration ──────────────────────────────────────────────────────────────

def investigate(
    provider: Any,
    df: pd.DataFrame,
    target_col: str,
    profiles: dict[str, ColumnProfile],
    *,
    alpha: float = FDR_ALPHA,
) -> InvestigationReport:
    """Full propose → test → correct → narrate pipeline for one target column.

    Raises :class:`InvestigationFailed` when the request cannot even be
    attempted — an unknown or non-numeric target, or a dataset with no other
    column shaped like a testable driver.
    """
    profile = profiles.get(target_col)
    if profile is None:
        raise InvestigationFailed(f"Column '{target_col}' is not in this dataset.")
    if not profile.is_numeric_measure:
        raise InvestigationFailed(
            f"'{target_col}' is not a numeric measurement (kind: {profile.kind.value}), "
            "so there is no metric here to investigate drivers of."
        )

    candidates = _candidate_columns(target_col, profiles)
    if not candidates:
        raise InvestigationFailed(
            f"No other column in this dataset is groupable or numeric, so "
            f"nothing can be tested as a driver of '{target_col}'."
        )

    proposed, proposal_note = propose_hypotheses(provider, target_col, candidates)

    hypotheses = [
        test_driver(df, target_col, item["column"], profiles, rationale=item["rationale"])
        for item in proposed
    ]

    testable = [h for h in hypotheses if h.testable]
    if testable:
        q_values = _bh_qvalues([h.p_value for h in testable])
        for h, q in zip(testable, q_values, strict=True):
            h.q_value = q
            h.significant = q < alpha

    # Supported hypotheses first (strongest effect first), then tested-but-
    # unsupported, then untestable last — a ranking a user can read top to
    # bottom without having to sort it themselves.
    def _sort_key(h: DriverTest) -> tuple[int, float]:
        if not h.testable:
            return (2, 0.0)
        if h.significant:
            return (0, -(h.effect_size or 0.0))
        return (1, h.q_value if h.q_value is not None else 1.0)

    hypotheses.sort(key=_sort_key)

    candidate_note = None
    if len(candidates) > len(proposed):
        candidate_note = (
            f"{len(candidates)} columns in this dataset could be tested; "
            f"{len(proposed)} were selected for this investigation."
        )

    narrative = _generate_narrative(provider, target_col, hypotheses, alpha)

    return InvestigationReport(
        target_column=target_col,
        hypotheses=hypotheses,
        tests_run=len(testable),
        significant_count=sum(1 for h in testable if h.significant),
        fdr_alpha=alpha,
        narrative=narrative,
        candidate_note=candidate_note,
        proposal_note=proposal_note,
    )
