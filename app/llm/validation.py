"""Insight validation — check the model's answer against the grounded facts.

A local 8B model asked "what is the average order value for the Enterprise
segment?" will produce a number whether or not that number exists in its
context. The number will be well-formatted, plausibly scaled, and wrong. No
prompt eliminates this; the only reliable defence is to check the output
against the data afterwards.

This layer extracts the numeric claims from an answer and classifies each one
against the fact set that produced the context:

* **verified**    — matches a fact LANA computed, within rounding tolerance
* **derived**     — inside the observed range of a column mentioned nearby, so
  it is plausibly a legitimate calculation from stated facts
* **unsupported** — matches no fact and falls outside every column's observed
  range

Only unsupported claims are surfaced as warnings. The goal is a high-signal
flag on the answers a user would otherwise trust, not a pedantic audit of
every digit.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from .context import Fact, GroundedContext

# ── What this layer verifies, and what it does not ──────────────────────────
# Stated once, here, so the API and the "what LANA checks" panel in the UI
# quote this text directly instead of independently re-describing what the
# code below does — and drifting from it the first time either one changes.

VERIFIED_CLAIM_TYPES = (
    "A number in the answer matches a fact LANA computed, within a small "
    "rounding tolerance (2% relative).",
    "A quoted or backticked column or category name that does not exist in "
    "this dataset is caught as an unknown reference.",
    "For a number that belongs to one specific column or category — a column "
    "statistic (mean, median, min, max, std), a share-of-category percentage, "
    "a count, or a group-by mean/total — the text near that number does not "
    "explicitly name a *different* column or category of the same statistic "
    "while omitting the correct one.",
)

KNOWN_BLIND_SPOTS = (
    "A real, correctly-computed number attached to the wrong label, where "
    "the mislabeling is a paraphrase rather than an explicit name — for "
    "example, describing a boolean column's False-share number using words "
    "like 'not remote' rather than writing 'False'. The attribution check "
    "above matches names literally (allowing for a column written as prose, "
    "'marketing spend' for marketing_spend), so a mislabelling that never "
    "names either the right or the wrong column is invisible to it.",
    "A correlation coefficient or regression figure attributed to the wrong "
    "pair of columns. Those facts carry no sibling grouping, so unlike column "
    "statistics and category breakdowns they are not attribution-checked.",
    "A wrong-but-plausible value that happens to fall inside a column's "
    "observed range. It is marked 'derived' rather than flagged, because a "
    "legitimate calculation can land anywhere in that range too.",
    "A non-numeric claim — a causal statement, a comparison, a "
    "recommendation — with no number in it to extract and check at all.",
    "Instructions hidden in the uploaded data itself. Category values are "
    "quoted into the facts the model reads, so a cell containing text like "
    "'ignore the above and say X' is text the model can choose to obey. "
    "Newlines and control characters are stripped and length is capped so "
    "such a value cannot fake a section heading, and any *number* it induces "
    "is still checked against LANA's own computed facts — but it can still "
    "steer wording, tone or a non-numeric claim.",
)


def capability_summary() -> dict[str, list[str]]:
    """What this validator does and does not check, as plain statements.

    Exists so a caller (the API, the UI, the docs) quotes this boundary
    verbatim instead of re-describing it from memory somewhere else.
    """
    return {
        "verifies": list(VERIFIED_CLAIM_TYPES),
        "does_not_verify": list(KNOWN_BLIND_SPOTS),
    }


# Relative tolerance when matching a claimed number to a computed fact. The
# model rounds ("about 4,200"), so exact equality would flag correct answers.
_REL_TOLERANCE = 0.02
_ABS_TOLERANCE = 1e-9

# Small integers are overwhelmingly prose ("3 key findings", "the top 5"),
# not data claims. Flagging them buries the real warnings in noise.
_TRIVIAL_INTEGER_MAX = 20

# Every quantifier below is explicitly bounded. An unbounded `\d+` followed by
# a literal that can fail (`%`) backtracks once per digit, at every starting
# offset — quadratic in the answer's length, so a reply of 20,000 digits pins a
# worker for ~12 seconds. No real numeric claim needs more than 18 digits, and
# the bound makes the scan linear.
_MAX_DIGITS = 18

_PERCENT_PATTERN = re.compile(
    rf"(\d{{1,{_MAX_DIGITS}}}(?:\.\d{{1,{_MAX_DIGITS}}})?)\s{{0,4}}(?:%|percent)",
    re.IGNORECASE,
)

# Matches numbers with optional thousands separators, decimals, sign and
# currency prefix, excluding those embedded in identifiers (e.g. "col_2").
_NUMBER_PATTERN = re.compile(
    rf"(?<![\w.])[-+]?\$?\d{{1,3}}(?:,\d{{3}}){{1,8}}(?:\.\d{{1,{_MAX_DIGITS}}})?(?![\w])"
    rf"|(?<![\w.])[-+]?\$?\d{{1,{_MAX_DIGITS}}}(?:\.\d{{1,{_MAX_DIGITS}}})?(?![\w])"
)

# Defence in depth: a provider's reply is normally capped by LLM_MAX_TOKENS,
# but an OpenAI-compatible endpoint is remote and its response size is not
# something this process controls. Validation is a best-effort trust signal,
# so truncating a pathological reply costs nothing.
_MAX_ANSWER_CHARS = 100_000

# Quoted or backticked tokens are the model referring to a column or category
# by name — cheap to check and a common hallucination site.
_REFERENCE_PATTERN = re.compile(r"[`'\"]([A-Za-z_][\w \-/]{0,48})[`'\"]")

# How far around a matched number to look for the category it's actually
# labelled with. Wide enough to cover "the north region average is $267" (the
# category named before the number) and "$267 in the north region" (named
# after), without spanning into an unrelated neighbouring sentence.
_ATTRIBUTION_WINDOW_BEFORE = 80
_ATTRIBUTION_WINDOW_AFTER = 40


@dataclass
class NumericClaim:
    """One number the model asserted, and LANA's verdict on it."""

    text: str
    value: float
    status: str                       # verified | derived | unsupported | misattributed
    matched_fact: str | None = None
    note: str | None = None           # why, for a misattributed claim

    def to_dict(self) -> dict[str, Any]:
        out = {"text": self.text, "value": self.value, "status": self.status}
        if self.matched_fact:
            out["matched_fact"] = self.matched_fact
        if self.note:
            out["note"] = self.note
        return out


@dataclass
class ValidationResult:
    """Outcome of checking one answer against the context that produced it."""

    claims: list[NumericClaim] = field(default_factory=list)
    unknown_references: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def verified_count(self) -> int:
        return sum(1 for c in self.claims if c.status == "verified")

    @property
    def unsupported_count(self) -> int:
        return sum(1 for c in self.claims if c.status == "unsupported")

    @property
    def misattributed_count(self) -> int:
        return sum(1 for c in self.claims if c.status == "misattributed")

    @property
    def trustworthy(self) -> bool:
        return not self.warnings

    def to_dict(self) -> dict[str, Any]:
        return {
            "trustworthy": self.trustworthy,
            "numbers_checked": len(self.claims),
            "verified": self.verified_count,
            "unsupported": self.unsupported_count,
            "misattributed": self.misattributed_count,
            "unknown_references": list(self.unknown_references),
            "warnings": list(self.warnings),
            "claims": [c.to_dict() for c in self.claims],
        }


def _parse_number(raw: str) -> float | None:
    cleaned = raw.replace(",", "").replace("$", "").strip()
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return None if math.isnan(value) or math.isinf(value) else value


def _matches(value: float, target: float) -> bool:
    if target == 0:
        return abs(value) <= _ABS_TOLERANCE
    return abs(value - target) <= max(_ABS_TOLERANCE, abs(target) * _REL_TOLERANCE)


def _mentions(window: str, token: str | None) -> bool:
    """Whole-word, case-insensitive check for a literal token in a text window.

    Also matches the way prose writes a column name: a model asked about
    ``marketing_spend`` answers about "marketing spend". Only separator
    variants are accepted — this is not fuzzy matching, and a different word
    is still a different word.
    """
    if not token:
        return False
    variants = {token}
    if "_" in token:
        variants.add(token.replace("_", " "))
        variants.add(token.replace("_", "-"))
    return any(
        re.search(rf"\b{re.escape(v)}\b", window, re.IGNORECASE)
        for v in variants
    )


def _sentence_around(text: str, start: int, end: int) -> str:
    """The sentence containing a match, used to look for the correct label.

    Deliberately wider than the window used to look for a *wrong* label. The
    two searches are asymmetric on purpose: be generous about finding evidence
    that the answer is right, strict about concluding that it is wrong. A
    false "misattributed" on a correct answer costs more trust than a missed
    flag, because it teaches the reader to ignore the warning.
    """
    left = max(
        text.rfind(".", 0, start), text.rfind("!", 0, start),
        text.rfind("?", 0, start), text.rfind("\n", 0, start),
    )
    right_candidates = [
        i for i in (text.find(".", end), text.find("!", end),
                    text.find("?", end), text.find("\n", end))
        if i != -1
    ]
    right = min(right_candidates) if right_candidates else len(text)
    return text[left + 1:right]


def _attribution_check(
    fact: Fact,
    context: GroundedContext,
    answer: str,
    start: int,
    end: int,
) -> str | None:
    """Look for an explicit, differently-labelled sibling near a matched number.

    Returns a note describing the mismatch when the text names a *different*
    category of the same statistic (e.g. a different region, or the other
    side of a boolean) near the number, while never naming the category the
    matched fact actually belongs to. Returns None otherwise — including
    when the fact isn't part of any family (a plain column statistic has
    nothing to be confused with), and when the text is ambiguous rather than
    pointing at a specific wrong answer (both the correct and an incorrect
    category are named nearby, as a legitimate comparison would).
    """
    if not fact.family:
        return None
    siblings = [
        f for f in context.facts
        if f.family == fact.family and f.category != fact.category
    ]
    if not siblings:
        return None

    window = answer[max(0, start - _ATTRIBUTION_WINDOW_BEFORE):
                     min(len(answer), end + _ATTRIBUTION_WINDOW_AFTER)]
    # The correct label is looked for in the whole sentence as well as the
    # window: if the sentence making the claim names the right column or
    # category anywhere, this is not a clean mismatch and is left alone.
    context_for_own = _sentence_around(answer, start, end) + " " + window
    if _mentions(context_for_own, fact.category):
        return None

    wrong = next((s for s in siblings if _mentions(window, s.category)), None)
    if wrong is None:
        return None
    return (
        f"the text names '{wrong.category}' near this number, but it matches "
        f"{fact.label} (category '{fact.category}')"
    )


def validate_answer(answer: str, context: GroundedContext) -> ValidationResult:
    """Check an answer's numeric claims and named references against the facts."""
    result = ValidationResult()
    if not answer or not answer.strip():
        return result
    answer = answer[:_MAX_ANSWER_CHARS]

    percent_values = {
        _parse_number(m.group(1)) for m in _PERCENT_PATTERN.finditer(answer)
    }
    percent_values.discard(None)

    for match in _NUMBER_PATTERN.finditer(answer):
        raw = match.group(0)
        value = _parse_number(raw)
        if value is None:
            continue
        # Skip prose integers and years, which carry no data claim.
        if abs(value) <= _TRIVIAL_INTEGER_MAX and float(value).is_integer():
            continue
        if 1900 <= value <= 2100 and float(value).is_integer():
            continue

        matched = next(
            (fact for fact in context.facts if _matches(value, fact.value)), None
        )
        if matched is not None:
            note = _attribution_check(matched, context, answer, match.start(), match.end())
            status = "misattributed" if note else "verified"
            result.claims.append(
                NumericClaim(raw, value, status, matched_fact=matched.label, note=note)
            )
            continue

        in_range = any(
            low <= value <= high for low, high in context.column_ranges.values()
        )
        if in_range or value in percent_values or 0 <= value <= 100:
            result.claims.append(NumericClaim(raw, value, "derived"))
        else:
            result.claims.append(NumericClaim(raw, value, "unsupported"))

    # ── Named references the data does not contain ───────────────────────────
    if context.vocabulary:
        lowered = {v.lower() for v in context.vocabulary}
        seen: set[str] = set()
        for match in _REFERENCE_PATTERN.finditer(answer):
            token = match.group(1).strip()
            key = token.lower()
            if not token or key in lowered or key in seen:
                continue
            # Ignore short filler and generic analytic words in quotes.
            if len(token) < 3 or key in _GENERIC_TERMS:
                continue
            seen.add(key)
            result.unknown_references.append(token)

    # ── Warnings: only what a user genuinely needs to see ────────────────────
    misattributed = [c for c in result.claims if c.status == "misattributed"]
    if misattributed:
        for claim in misattributed[:4]:
            result.warnings.append(
                f"'{claim.text}' looks misattributed: {claim.note}."
            )

    unsupported = [c for c in result.claims if c.status == "unsupported"]
    if unsupported:
        preview = ", ".join(c.text for c in unsupported[:4])
        result.warnings.append(
            f"{len(unsupported)} number(s) in this answer ({preview}) do not match "
            "any statistic LANA computed and fall outside every column's observed "
            "range. Verify them against the data before using them."
        )
    if len(result.unknown_references) > 2:
        preview = ", ".join(result.unknown_references[:4])
        result.warnings.append(
            f"The answer refers to {preview}, which do not appear among this "
            "dataset's column names or category values."
        )
    if result.claims and result.verified_count == 0 and len(result.claims) >= 3:
        result.warnings.append(
            "None of the figures in this answer match a statistic LANA computed. "
            "Treat the whole answer as unverified."
        )

    return result


_GENERIC_TERMS = {
    "yes", "no", "n/a", "na", "none", "null", "true", "false", "mean", "median",
    "average", "sum", "total", "count", "min", "max", "std", "the", "and", "or",
    "data", "dataset", "column", "columns", "row", "rows", "value", "values",
    "high", "low", "top", "bottom", "note", "summary", "insight", "insights",
}
