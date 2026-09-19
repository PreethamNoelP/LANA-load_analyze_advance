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
    "Where the sentence names both a statistic and a column — 'the average "
    "revenue is X' — the number is compared against that column's own mean "
    "specifically, not against every fact in the context. A wrong value for a "
    "correctly-named statistic is therefore caught even when it happens to "
    "collide with an unrelated figure, and even when it falls inside the "
    "column's range. This applies to mean, median, standard deviation, "
    "minimum, maximum, sum, count and share, including the group-scoped form "
    "('the average revenue in the north region').",
    "Where the answer was produced by running a SQL query against the actual "
    "rows, every figure is checked against that query's result, and the claim "
    "records that its provenance is an executed query rather than a "
    "precomputed summary.",
    "A figure stated as a count of rows or records that exceeds the "
    "dataset's own row count is refused as arithmetically impossible.",
    "A quoted or backticked column or category name that does not exist in "
    "this dataset is caught as an unknown reference.",
    "For a number that belongs to one specific column or category — a column "
    "statistic (mean, median, min, max, std), a share-of-category percentage, "
    "a count, or a group-by mean/total — the text near that number does not "
    "explicitly name a *different* column or category of the same statistic "
    "while omitting the correct one.",
    "For a figure that belongs to a *pair* of columns — a correlation "
    "coefficient, or a regression coefficient, intercept or R^2 — the text "
    "near it does not name a different pair LANA also computed while leaving "
    "out one of the two columns the figure actually came from. Naming half a "
    "pair is treated as too vague to be a mislabelling, not as an error.",
    "A number is not accepted as verified purely because it lands within "
    "tolerance of some fact elsewhere in the context. Where the sentence "
    "explicitly names a different column from the one the matched statistic "
    "belongs to, and never names that statistic's own column, the match is "
    "reported as a collision rather than a verification.",
    "An average or median presented for a column it could not have come "
    "from is refused outright: min <= mean <= max holds for every column, so "
    "a central value outside its own column's observed range is impossible "
    "rather than merely unlikely. The same applies to any central value "
    "claimed for an identifier column, for which LANA computes no mean, "
    "median or standard deviation at all.",
)

KNOWN_BLIND_SPOTS = (
    "A real, correctly-computed number attached to the wrong label, where "
    "the mislabeling is a paraphrase rather than an explicit name — for "
    "example, describing a boolean column's False-share number using words "
    "like 'not remote' rather than writing 'False'. The attribution check "
    "above matches names literally (allowing for a column written as prose, "
    "'marketing spend' for marketing_spend), so a mislabelling that never "
    "names either the right or the wrong column is invisible to it.",
    "A correlation or regression figure pinned to a pair of columns LANA "
    "never actually scanned. The attribution check compares a figure against "
    "the other pairs in the same context, so a pairing that was never "
    "computed has no sibling to contradict it — and if both column names are "
    "real, the unknown-reference check has nothing to say either.",
    "Which of two near-identical figures an answer meant. Where a sibling's "
    "own value also falls within the 2% tolerance of the quoted number — "
    "common for correlation coefficients, which cluster — naming that sibling "
    "is accepted rather than flagged, because it is a defensible reading of "
    "the same number.",
    "A wrong-but-plausible value that falls inside its column's range *and* "
    "is not tied to a named statistic. Where the sentence names the statistic "
    "as well as the column, the targeted check above compares against that "
    "exact figure and catches the error; where it names only the column, or "
    "phrases the quantity loosely ('revenue is around 400'), the number is "
    "marked 'derived' rather than flagged, because a legitimate calculation "
    "can land anywhere in that range too. Attribution must also be "
    "unambiguous — a mention with another number between it and this one is "
    "treated as too vague to conclude anything from.",
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
    # How the matched fact was obtained: "ledger" (LANA precomputed it while
    # building the context) or "executed_sql" (it is a cell of a result
    # returned by a query run against the real rows). These are not equally
    # strong claims and the UI must not render them as though they were.
    provenance: str | None = None
    # True when the verdict came from resolving *which* statistic of *which*
    # column the sentence was talking about and comparing against that fact
    # specifically, rather than from scanning every fact for a value within
    # tolerance. A targeted verdict is the one worth trusting; see
    # _targeted_statistic_check.
    targeted: bool = False

    def to_dict(self) -> dict[str, Any]:
        out = {"text": self.text, "value": self.value, "status": self.status}
        if self.matched_fact:
            out["matched_fact"] = self.matched_fact
        if self.note:
            out["note"] = self.note
        if self.provenance:
            out["provenance"] = self.provenance
        if self.targeted:
            out["targeted"] = True
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
    def executed_count(self) -> int:
        """Claims verified against a cell of an actually-executed query result."""
        return sum(
            1 for c in self.claims
            if c.status == "verified" and c.provenance == "executed_sql"
        )

    @property
    def targeted_count(self) -> int:
        """Claims whose verdict came from a resolved (statistic, column) lookup."""
        return sum(1 for c in self.claims if c.targeted)

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
            "executed": self.executed_count,
            "targeted": self.targeted_count,
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


def _name_variants(token: str) -> set[str]:
    """A column name as prose might write it: ``marketing_spend`` or "marketing spend".

    Only separator variants. This is not fuzzy matching — a different word is
    still a different word.
    """
    variants = {token}
    if "_" in token:
        variants.add(token.replace("_", " "))
        variants.add(token.replace("_", "-"))
    return variants


def _mentions(window: str, token: str | None) -> bool:
    """Whole-word, case-insensitive check for a literal token in a text window."""
    if not token:
        return False
    return any(
        re.search(rf"\b{re.escape(v)}\b", window, re.IGNORECASE)
        for v in _name_variants(token)
    )


def _nearest_named(answer: str, start: int, end: int, names: set[str]) -> str | None:
    """Which of ``names`` this number is actually presented as a statistic of.

    Nearest mention wins, and a mention only counts when no *other* number sits
    between it and this one. Without that second rule, "average revenue is $600
    per order; the average order is 2.3 items" attributes 2.3 to revenue purely
    because the word appears in the window — and 2.3 then looks impossible for
    a column it was never about. Being wrong in that direction costs more than
    a missed flag, so the attribution has to be unambiguous before anything is
    concluded from it.
    """
    best: tuple[int, str] | None = None
    for name in sorted(names):
        for variant in _name_variants(name):
            for m in re.finditer(rf"\b{re.escape(variant)}\b", answer, re.IGNORECASE):
                if m.end() <= start:
                    gap = answer[m.end():start]
                elif m.start() >= end:
                    gap = answer[end:m.start()]
                else:
                    continue
                if len(gap) > _ATTRIBUTION_WINDOW_BEFORE or _NUMBER_PATTERN.search(gap):
                    continue
                if best is None or len(gap) < best[0]:
                    best = (len(gap), name)
    return best[1] if best else None


def _attribution_names(fact: Fact) -> tuple[str, ...]:
    """Every name that must appear for this fact to be correctly attributed.

    One name for a fact scoped to a single category; both column names for a
    fact that belongs to a pair, such as a correlation or a regression.
    """
    if fact.category_names:
        return fact.category_names
    return (fact.category,) if fact.category else ()


def _mentions_all(window: str, names: tuple[str, ...]) -> bool:
    return bool(names) and all(_mentions(window, name) for name in names)


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
    value: float,
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
    own_names = _attribution_names(fact)
    if not own_names:
        return None
    siblings = [
        f for f in context.facts
        if f.family == fact.family
        and _attribution_names(f) != own_names
        # A sibling whose own value also matches the quoted number is not a
        # wrong label: naming it is a legitimate reading of that number. This
        # matters most for correlations, where coefficients cluster in a
        # narrow band and a 2% relative tolerance covers several of them, but
        # it applies equally to two regions with near-identical means.
        and not _matches(value, f.value)
    ]
    if not siblings:
        return None

    window = answer[max(0, start - _ATTRIBUTION_WINDOW_BEFORE):
                     min(len(answer), end + _ATTRIBUTION_WINDOW_AFTER)]
    # The correct label is looked for in the whole sentence as well as the
    # window: if the sentence making the claim names the right column or
    # category anywhere, this is not a clean mismatch and is left alone.
    context_for_own = _sentence_around(answer, start, end) + " " + window
    if _mentions_all(context_for_own, own_names):
        return None

    wrong = next(
        (s for s in siblings if _mentions_all(window, _attribution_names(s))), None
    )
    if wrong is None:
        return None
    return (
        f"the text names '{wrong.category}' near this number, but it matches "
        f"{fact.label} (category '{fact.category}')"
    )


def _known_columns(context: GroundedContext) -> set[str]:
    return {f.column for f in context.facts if f.column}


def _naming_a_fact(fact: Fact, context: GroundedContext) -> set[str]:
    """Every name a sentence could correctly use for this fact's measure.

    For a ledger fact that is just the column. For an executed-SQL fact the
    ``column`` is a query *alias* — ``avg_revenue``, ``total_spend`` — and an
    answer describing it says "revenue", not "avg_revenue". Treating the alias
    as the only acceptable name made every correct executed answer look
    misattributed: the sentence named `revenue`, the fact was called
    `avg_revenue`, and the column check reported a collision.

    So an alias is also named by any real dataset column it was derived from,
    detected by the column appearing as a word inside the alias. That is
    deliberately narrow — ``avg_revenue`` is named by ``revenue``, but not by
    ``marketing_spend`` — so the check keeps its teeth on genuinely wrong
    attributions.
    """
    names = {fact.column} if fact.column else set()
    if fact.provenance == "executed_sql" and fact.column:
        alias_parts = set(re.split(r"[^A-Za-z0-9]+", fact.column.lower()))
        for candidate in _known_columns(context):
            if candidate == fact.column:
                continue
            candidate_parts = set(re.split(r"[^A-Za-z0-9]+", candidate.lower()))
            if candidate_parts and candidate_parts <= alias_parts:
                names.add(candidate)
    return {n for n in names if n}


def _column_attribution_check(
    fact: Fact,
    context: GroundedContext,
    answer: str,
    start: int,
    end: int,
) -> str | None:
    """Catch a number matched to a fact about a column the answer never mentions.

    Fact matching is global and label-blind: a number within 2% of *any* of the
    hundred-odd facts in a context is stamped verified, whatever the sentence
    around it is actually about. Measured case — "the average order_id is
    5,249.5" came back verified because it landed within tolerance of "total
    customer_age for region=west" (5,354). Nothing about that is a
    verification; it is a collision.

    ``_attribution_check`` above does not see it, because that one compares a
    fact against its *siblings* — same statistic, different category — and an
    unrelated column's total is not a sibling of anything the answer named.

    Fires only when the text explicitly names a known column that is not the
    matched fact's, and does not name the matched fact's own column anywhere in
    the sentence. Same asymmetry as the sibling check: generous about finding
    evidence the answer is right, strict about concluding it is wrong.
    """
    own = fact.column
    if not own:
        return None

    # Every name that would correctly identify this fact's measure — the
    # column itself, plus the dataset columns behind a SQL alias.
    acceptable = _naming_a_fact(fact, context)

    window = answer[max(0, start - _ATTRIBUTION_WINDOW_BEFORE):
                     min(len(answer), end + _ATTRIBUTION_WINDOW_AFTER)]
    sentence = _sentence_around(answer, start, end) + " " + window
    if any(_mentions(sentence, name) for name in acceptable):
        return None

    named_other = next(
        (c for c in sorted(_known_columns(context))
         if c not in acceptable and _mentions(window, c)),
        None,
    )
    if named_other is None:
        return None
    return (
        f"the text is about '{named_other}' here, but this number matches "
        f"{fact.label} — a statistic of '{own}', which the sentence never mentions"
    )


# Words that mark a number as a claim about a column's centre. A mean or a
# median is mathematically bound by its own column's min and max, which is what
# makes the check below arithmetic rather than a heuristic.
_CENTRAL_TENDENCY_CUES = ("average", "mean", "median", "typical", "midpoint")

# ...and words that mark it as an aggregate instead, which legitimately exceeds
# any single value's range. A total of a column is not bound by its max, so the
# check must not fire near one.
_AGGREGATE_CUES = (
    "total", "sum", "combined", "altogether", "overall", "count", "number of",
    "across all", "cumulative", "aggregate",
)


def _impossible_central_value(
    answer: str,
    start: int,
    end: int,
    context: GroundedContext,
) -> str | None:
    """Flag a claimed average/median that its own column cannot produce.

    ``min <= mean <= max`` holds for every column, always — so an answer saying
    "the average revenue is $45,000" when revenue tops out at $2,000 is not
    implausible, it is impossible. That makes this one of the few checks here
    that can conclude "wrong" from arithmetic rather than from a guess.

    Previously such a claim was waved through as "derived", because the
    plausibility test asked whether the value fell inside *any* column's range.
    With eight columns spanning different orders of magnitude, almost every
    number falls inside one of them — which is most of why the validator caught
    so little.
    """
    window = answer[max(0, start - _ATTRIBUTION_WINDOW_BEFORE):
                     min(len(answer), end + _ATTRIBUTION_WINDOW_AFTER)].lower()
    if not any(cue in window for cue in _CENTRAL_TENDENCY_CUES):
        return None
    if any(cue in window for cue in _AGGREGATE_CUES):
        return None

    name = _nearest_named(
        answer, start, end,
        set(context.centreless_columns) | set(context.column_ranges),
    )
    if name is None:
        return None

    # A column LANA computes no centre for cannot have one quoted back. This
    # does not depend on the value at all — the average of an identifier is
    # meaningless whatever number is attached to it, so there is no version of
    # the claim that could be supported.
    if name in context.centreless_columns:
        return (
            f"presented as a central value of '{name}', which is an identifier — "
            f"LANA computes no average, median or standard deviation for it, "
            f"because arithmetic on a key is not meaningful"
        )

    value = _parse_number(answer[start:end])
    if value is None or name not in context.column_ranges:
        return None
    low, high = context.column_ranges[name]
    if low <= value <= high:
        return None
    return (
        f"presented as a central value of '{name}', which ranges from {low:,.4g} "
        f"to {high:,.4g} — an average or median cannot fall outside that"
    )


# ── Targeted (statistic, column) resolution ─────────────────────────────────
# The single largest source of missed errors in the measured run was that fact
# matching is *global*: a number within 2% of any of the ~100 facts in a
# context was stamped "verified" regardless of what the sentence claimed it
# was. `_column_attribution_check` catches the subset where the sentence names
# a different known column, but says nothing when the sentence names the right
# column and simply states the wrong value for it — the most common real
# failure, and one the old code reported as a clean verification whenever the
# wrong value happened to collide with some unrelated fact.
#
# This resolves the claim the way a reader does — "the average revenue is X"
# means mean(revenue) — and compares against *that* fact alone. When it
# resolves, its verdict is authoritative and the global scan is skipped
# entirely, because a targeted comparison cannot be laundered by a collision.

_STATISTIC_CUES: tuple[tuple[str, tuple[str, ...]], ...] = (
    # Ordered: the first cue found in the window wins, so more specific
    # multi-word phrases must precede the single words they contain.
    ("std", ("standard deviation", "std dev", "stdev", "std.")),
    ("correlation", ("correlation", "correlated", "correlates", "corr.")),
    ("r2", ("r-squared", "r squared", "r2", "r²")),
    ("coefficient", ("coefficient", "slope")),
    ("intercept", ("intercept",)),
    ("median", ("median",)),
    ("mean", ("average", "mean", "avg.", "typical")),
    ("max", ("maximum", "highest", "largest", "greatest", "peak", "max ")),
    ("min", ("minimum", "lowest", "smallest", "min ")),
    ("share", ("percentage", "percent", "share of", "proportion", "%")),
    ("sum", ("total", "sum of", "combined", "altogether")),
    ("count", ("count of", "number of", "how many", "there are")),
)

# A statistic named for a *pair* of columns cannot be resolved by finding one
# column name near the number, so the targeted check stays out of their way
# and leaves them to the existing pair-attribution logic.
_PAIRWISE_STATISTICS = frozenset({"correlation", "r2", "coefficient", "intercept"})


def _statistic_in_window(window: str, number_at: int) -> str | None:
    """Which summary the text around a number says it is, if it says at all.

    ``number_at`` is the number's own offset inside ``window``, and cues are
    ranked by distance from it rather than by position in the string. Reading
    left-to-right instead got this wrong on a sentence with two claims:
    "There are 500 orders, of which 31.4% came from the north region" resolved
    31.4 against the *count* cue ("there are") that opens the sentence, and
    flagged a correct share as a wrong count (eval/adversarial.py adv-36).
    The nearest cue is the one the number is actually governed by.
    """
    lowered = window.lower()
    best: tuple[int, str] | None = None
    for statistic, cues in _STATISTIC_CUES:
        for cue in cues:
            start = 0
            while (position := lowered.find(cue, start)) != -1:
                # Distance to the nearer edge of the cue, so a long phrase is
                # not penalised against a short one sitting the same distance
                # away.
                distance = min(
                    abs(position - number_at),
                    abs(position + len(cue) - number_at),
                )
                if best is None or distance < best[0]:
                    best = (distance, statistic)
                start = position + 1
    return best[1] if best else None


# A number written with a trailing percent sign is a share, whatever else the
# sentence says around it. Checked before the cue scan so no amount of nearby
# prose can reinterpret it as a count or a total.
_TRAILING_PERCENT_RE = re.compile(r"\s{0,4}(?:%|percent)", re.IGNORECASE)


def _facts_by_statistic(
    context: GroundedContext, statistic: str, column: str
) -> list[Fact]:
    return [
        f for f in context.facts
        if f.statistic == statistic and f.column and f.column.lower() == column.lower()
    ]


# A category label that is just a number — the levels of a 1-5 rating code, a
# year, a boolean stored as 0/1 — cannot be located by a word-boundary search,
# because `\b3\b` matches the "3" inside "2.3". That is not a hypothetical:
# it made "the average revenue is $221.88 per order; the average order is 2.3
# items" resolve to `mean revenue for rating=3` and flag a correct answer
# (eval/adversarial.py adv-20). For these, the grouping column has to be named
# too — "rating 3", not a stray digit somewhere in the sentence.
_BARE_NUMBER_RE = re.compile(r"^[-+]?\d+(?:\.\d+)?$")


def _mentions_category(window: str, fact: Fact) -> bool:
    """Whether this window genuinely names the group a fact belongs to."""
    if not fact.category:
        return False
    if _BARE_NUMBER_RE.match(fact.category.strip()):
        return bool(
            fact.category_column
            and _mentions(window, fact.category_column)
            and _mentions(window, fact.category)
        )
    return _mentions(window, fact.category)


def _targeted_statistic_check(
    answer: str,
    start: int,
    end: int,
    value: float,
    context: GroundedContext,
) -> NumericClaim | None:
    """Resolve the claim to one fact and rule on it, or return None.

    Returns None whenever the sentence is not specific enough to resolve —
    no statistic cue, no unambiguously-nearest column, or no such fact
    computed. The same asymmetry the rest of this module uses applies: be
    strict about concluding "wrong", generous about declining to conclude.
    """
    window_start = max(0, start - _ATTRIBUTION_WINDOW_BEFORE)
    window = answer[window_start:min(len(answer), end + _ATTRIBUTION_WINDOW_AFTER)]
    if _TRAILING_PERCENT_RE.match(answer[end:end + 10]):
        statistic = "share"
    else:
        statistic = _statistic_in_window(window, start - window_start)
    if statistic is None or statistic in _PAIRWISE_STATISTICS:
        return None

    known = _known_columns(context)
    if not known:
        return None
    column = _nearest_named(answer, start, end, known)
    if column is None:
        return None

    candidates = _facts_by_statistic(context, statistic, column)
    if not candidates:
        return None

    # A group-scoped claim ("average revenue in the north region") must be
    # checked against that group's own figure, not the column-wide one. Where
    # a category is named in the window, narrow to facts carrying it; where
    # none is, prefer the column-wide fact (the one with no category column).
    scoped = [
        f for f in candidates
        if f.category and f.category_column and _mentions_category(window, f)
    ]
    if scoped:
        candidates = scoped
    else:
        whole_column = [f for f in candidates if not f.category_column]
        if whole_column:
            candidates = whole_column
        elif len(candidates) > 1:
            # Several group-level facts and no category named: the sentence
            # does not say which group it means, so nothing can be concluded.
            return None

    hit = next((f for f in candidates if _matches(value, f.value)), None)
    if hit is not None:
        return NumericClaim(
            answer[start:end], value, "verified",
            matched_fact=hit.label, provenance=hit.provenance, targeted=True,
        )

    # Resolved to a specific fact whose value this is not. This is the case
    # the old global scan reported as verified whenever the wrong number
    # happened to land within 2% of anything else in the context.
    expected = candidates[0]
    note = (
        f"presented as the {statistic} of '{column}', which LANA computed "
        f"as {expected.value:,.6g}, not {value:,.6g}"
    )

    # Where the quoted number is a real figure borrowed from somewhere else,
    # say so. Knowing the answer reported the *other* side of a split, or
    # another column's mean, is a more actionable diagnosis than "that is not
    # the right number", and it is the distinction the misattributed /
    # unsupported split exists to carry: a mislabelled real value is a
    # different defect from an invented one.
    #
    # "Somewhere else" has to include a sibling category of the same column,
    # not just a different column. The original organic failure this whole
    # check descends from is exactly that shape — remote=True's share quoted
    # under the label remote=False — and restricting the search to other
    # columns silently reclassified it as a plain fabrication.
    borrowed = next(
        (f for f in context.facts
         if f.statistic == statistic
         and _matches(value, f.value)
         and (f.column, f.category) != (expected.column, expected.category)),
        None,
    )
    if borrowed is not None:
        if borrowed.column and borrowed.column.lower() != column.lower():
            whose = f"'{borrowed.column}'"
        elif borrowed.category:
            whose = f"'{borrowed.category}'"
        else:
            whose = "another group"
        return NumericClaim(
            answer[start:end], value, "misattributed",
            matched_fact=borrowed.label,
            provenance=borrowed.provenance,
            targeted=True,
            note=f"{note} — {value:,.6g} is the {statistic} of {whose}",
        )

    return NumericClaim(
        answer[start:end], value, "unsupported",
        matched_fact=expected.label,
        provenance=expected.provenance,
        targeted=True,
        note=note,
    )


def _impossible_count(
    answer: str, start: int, end: int, value: float, context: GroundedContext
) -> str | None:
    """A row/record count larger than the dataset itself cannot be right.

    Arithmetic, not a heuristic: no subset of N rows has more than N members,
    so a count cued as rows/records/orders above the stated row count is
    impossible regardless of which column it claims to be about.
    """
    window = answer[max(0, start - _ATTRIBUTION_WINDOW_BEFORE):
                     min(len(answer), end + _ATTRIBUTION_WINDOW_AFTER)].lower()
    if not any(cue in window for cue in ("number of", "count", "how many", "there are")):
        return None
    if any(cue in window for cue in ("average", "mean", "median", "percent", "%", "total of")):
        return None
    rows = next((f.value for f in context.facts if f.label == "row count"), None)
    if rows is None or value <= rows:
        return None
    return (
        f"stated as a count, but it exceeds the dataset's {int(rows):,} total "
        f"rows — no subset can be larger than the whole"
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

        # Checked before the fact scan, not after it. A claim like "the average
        # order_id is 5,249.5" is refused because no such statistic exists —
        # and running this first means a chance collision with an unrelated
        # fact (measured: it matched "total customer_age for region=west")
        # cannot launder it into a verification.
        impossible = _impossible_central_value(
            answer, match.start(), match.end(), context
        )
        if impossible is None:
            impossible = _impossible_count(
                answer, match.start(), match.end(), value, context
            )
        if impossible is not None:
            result.claims.append(
                NumericClaim(raw, value, "unsupported", note=impossible)
            )
            continue

        # Authoritative when it resolves: a comparison against *the* fact the
        # sentence names cannot be laundered by an unrelated collision, which
        # is exactly what the global scan below is vulnerable to. Skipping
        # that scan on a targeted verdict is the point, not an optimisation.
        targeted = _targeted_statistic_check(
            answer, match.start(), match.end(), value, context
        )
        if targeted is not None:
            result.claims.append(targeted)
            continue

        # Executed-SQL facts are consulted first. A number produced by running
        # a query against the real rows is a stronger match than one that
        # merely equals a precomputed summary, so when both would match, the
        # claim is credited to the stronger evidence.
        matched = next(
            (f for f in context.facts
             if f.provenance == "executed_sql" and _matches(value, f.value)),
            None,
        ) or next(
            (fact for fact in context.facts if _matches(value, fact.value)), None
        )
        if matched is not None:
            note = _attribution_check(
                matched, context, answer, match.start(), match.end(), value
            ) or _column_attribution_check(
                matched, context, answer, match.start(), match.end()
            )
            status = "misattributed" if note else "verified"
            result.claims.append(
                NumericClaim(raw, value, status, matched_fact=matched.label,
                             note=note, provenance=matched.provenance)
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
    # A claim carrying a note was refused for a specific, stateable reason —
    # say that reason rather than folding it into the generic count, which
    # would throw away the most useful thing the check produced.
    for claim in [c for c in unsupported if c.note][:4]:
        result.warnings.append(f"'{claim.text}' cannot be right: {claim.note}.")

    generic = [c for c in unsupported if not c.note]
    if generic:
        preview = ", ".join(c.text for c in generic[:4])
        result.warnings.append(
            f"{len(generic)} number(s) in this answer ({preview}) do not match "
            "any statistic LANA computed and fall outside every column's observed "
            "range. Verify them against the data before using them."
        )
    # Any explicitly quoted name that isn't in the data is worth surfacing.
    # This used to require more than two before it said anything, so an answer
    # inventing a single plausible-sounding segment ("Revenue is highest in the
    # 'enterprise' segment") passed silently — the exact case eval/adversarial
    # adv-04 describes. The threshold was protecting against noise from the
    # model quoting ordinary words, which the stoplist below handles directly.
    if result.unknown_references:
        preview = ", ".join(f"'{r}'" for r in result.unknown_references[:4])
        plural = "do" if len(result.unknown_references) > 1 else "does"
        result.warnings.append(
            f"The answer refers to {preview}, which {plural} not appear among "
            "this dataset's column names or category values."
        )
    if result.claims and result.verified_count == 0 and len(result.claims) >= 3:
        result.warnings.append(
            "None of the figures in this answer match a statistic LANA computed. "
            "Treat the whole answer as unverified."
        )

    return result


# Words a model quotes while describing its own analysis rather than naming
# something in the data. Now that a single unknown reference warns, this list
# is what keeps that from being noisy, so it covers the statistical vocabulary
# an answer routinely puts in quotes.
_GENERIC_TERMS = {
    "yes", "no", "n/a", "na", "none", "null", "true", "false", "mean", "median",
    "average", "sum", "total", "count", "min", "max", "std", "the", "and", "or",
    "data", "dataset", "column", "columns", "row", "rows", "value", "values",
    "high", "low", "top", "bottom", "note", "summary", "insight", "insights",
    "mode", "range", "iqr", "variance", "skew", "skewness", "kurtosis",
    "outlier", "outliers", "correlation", "regression", "significant",
    "p-value", "q-value", "confidence interval", "standard deviation",
    "missing", "nulls", "unknown", "other", "overall", "group", "groups",
}
