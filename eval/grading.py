"""Grading utilities shared by the main comparison and the adversarial suite.

Number extraction is imported directly from ``app.llm.validation`` rather
than reimplemented here, so grading and the shipped product share one
definition of "a number claim in this text." A second, hand-rolled parser
that quietly disagreed with the real one would make the benchmark measure
its own parsing bugs instead of the system under test.
"""
from __future__ import annotations

import re

from app.llm.validation import _NUMBER_PATTERN, _PERCENT_PATTERN, _parse_number

# Regex, not a literal-phrase list: the first version of this matched "does
# not include" but not "do not include" ("the facts do not include..." is
# grammatically correct for a plural subject), which caused real, correct
# refusals to be graded as hallucinations in the first full eval run — a bug
# in the grader, not the model. Verb-form classes below cover the
# conjugations that actually showed up in transcripts, without trying to be
# an exhaustive NLU refusal detector.
_REFUSAL_PATTERNS = [
    re.compile(r"\b(?:do|does)\s+not\s+(?:include|contain|provide|have|list|state)\b", re.I),
    re.compile(r"\b(?:don't|doesn't)\s+(?:include|contain|provide|have|list|state)\b", re.I),
    re.compile(r"\b(?:is|are|was|were)\s+not\s+(?:available|provided|possible|stated|given|specified)\b", re.I),
    re.compile(r"\bcannot\s+(?:be\s+)?(?:determin\w*|calculat\w*|answer\w*|comput\w*|deriv\w*)\b", re.I),
    re.compile(r"\bcan'?t\s+(?:be\s+)?(?:determin\w*|calculat\w*|answer\w*)\b", re.I),
    re.compile(r"\bnot\s+possible\s+to\s+(?:determin\w*|calculat\w*|comput\w*|answer\w*)\b", re.I),
    re.compile(r"\bno\s+(?:data|information)\s+(?:is\s+)?(?:available|provided)\b", re.I),
    re.compile(r"\bnot\s+(?:enough|sufficient)\s+(?:data|information)\b", re.I),
    re.compile(r"\b(?:is|are)\s+not\s+(?:a\s+)?meaningful\b", re.I),
    re.compile(r"\bnot\s+provided\s+in\s+the\s+(?:dataset|data|facts)\b", re.I),
    re.compile(r"\bmissing\s+from\s+the\s+(?:dataset|data|facts)\b", re.I),
    re.compile(r"\bdo\s+not\s+have\s+(?:access\s+to|the\s+data)\b", re.I),
    re.compile(r"\bunable\s+to\s+(?:determin\w*|calculat\w*|answer\w*|provide)\b", re.I),
    re.compile(r"\bi\s+don'?t\s+know\b|\bi\s+do\s+not\s+know\b", re.I),
    re.compile(r"\bno\s+way\s+to\s+know\b", re.I),
    re.compile(r"\bnot\s+clear\s+from\s+the\s+data\b", re.I),
    re.compile(r"\bnot\s+specified\b", re.I),
    re.compile(r"\bcannot\s+be\s+answered\b", re.I),
    re.compile(r"\boutside\s+the\s+scope\b", re.I),
    re.compile(r"\bnot\s+in\s+the\s+data\b", re.I),
    re.compile(r"\bnot\s+part\s+of\s+the\s+data\b", re.I),
    re.compile(r"\bno\s+such\b", re.I),
    re.compile(r"\bundefined\b", re.I),
    re.compile(r"\bdoes\s+not\s+appear\b", re.I),
]


def looks_like_refusal(text: str) -> bool:
    """True if the answer contains explicit decline/limitation language."""
    return any(p.search(text) for p in _REFUSAL_PATTERNS)


def extract_numbers(text: str) -> list[float]:
    """Every numeric claim in ``text``, using the product's own extraction."""
    values: list[float] = []
    for match in _PERCENT_PATTERN.finditer(text):
        value = _parse_number(match.group(1))
        if value is not None:
            values.append(value)
    for match in _NUMBER_PATTERN.finditer(text):
        value = _parse_number(match.group(0))
        if value is not None:
            values.append(value)
    return values


def numeric_matches(value: float, candidates: list[float], tolerance: float,
                     kind: str = "relative") -> bool:
    for c in candidates:
        if kind == "absolute":
            if abs(c - value) <= tolerance:
                return True
        else:
            if abs(c - value) <= max(1e-9, abs(value) * tolerance):
                return True
    return False


def grade_categorical(expected: str, candidates: list[str], text: str) -> str:
    """'correct' | 'hedged' | 'incorrect' for a which-X-is-highest style answer.

    'hedged' means the expected label is present but so is at least one
    competing candidate — the answer didn't commit to a single claim, which
    is graded as not-correct rather than guessed at.
    """
    lowered = text.lower()
    others = [c for c in candidates if c != expected]
    expected_hit = expected.lower() in lowered
    other_hits = [c for c in others if c.lower() in lowered]
    if expected_hit and not other_hits:
        return "correct"
    if expected_hit and other_hits:
        return "hedged"
    return "incorrect"
