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

from .context import GroundedContext

# ── What this layer verifies, and what it does not ──────────────────────────
# Stated once, here, so the API and the "what LANA checks" panel in the UI
# quote this text directly instead of independently re-describing what the
# code below does — and drifting from it the first time either one changes.

VERIFIED_CLAIM_TYPES = (
    "A number in the answer matches a fact LANA computed, within a small "
    "rounding tolerance (2% relative).",
    "A quoted or backticked column or category name that does not exist in "
    "this dataset is caught as an unknown reference.",
)

KNOWN_BLIND_SPOTS = (
    "A real, correctly-computed number attached to the wrong label — for "
    "example, quoting the right figure but naming the wrong column or "
    "category. This checks whether a VALUE matches any fact, not whether "
    "the LABEL attached to it is the one that fact actually belongs to.",
    "A wrong-but-plausible value that happens to fall inside a column's "
    "observed range. It is marked 'derived' rather than flagged, because a "
    "legitimate calculation can land anywhere in that range too.",
    "A non-numeric claim — a causal statement, a comparison, a "
    "recommendation — with no number in it to extract and check at all.",
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


@dataclass
class NumericClaim:
    """One number the model asserted, and LANA's verdict on it."""

    text: str
    value: float
    status: str                       # verified | derived | unsupported
    matched_fact: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out = {"text": self.text, "value": self.value, "status": self.status}
        if self.matched_fact:
            out["matched_fact"] = self.matched_fact
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
    def trustworthy(self) -> bool:
        return not self.warnings

    def to_dict(self) -> dict[str, Any]:
        return {
            "trustworthy": self.trustworthy,
            "numbers_checked": len(self.claims),
            "verified": self.verified_count,
            "unsupported": self.unsupported_count,
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
            result.claims.append(
                NumericClaim(raw, value, "verified", matched_fact=matched.label)
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
