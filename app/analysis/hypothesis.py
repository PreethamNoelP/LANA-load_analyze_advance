"""Deterministic testing of one candidate "driver" against a target metric.

This is the half of the Investigate feature that never touches the LLM. Given
a target numeric column and a candidate driver column, it runs the test that
column pair actually supports — Kruskal-Wallis for a categorical/boolean
driver, Pearson correlation for a numeric one — and reports an effect size
alongside the p-value, the same "don't just say significant, say how much"
principle :mod:`app.analysis.statistics` already applies to the correlation
scan.

Multiple-comparison correction across every hypothesis tested in one
investigation is applied by the caller (:mod:`app.llm.investigate`), reusing
the same Benjamini-Hochberg routine the correlation scan uses — testing six
candidate drivers at once has exactly the same false-discovery problem as
testing six column pairs at once, and the fix is the same fix.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from scipy import stats as scipy_stats

from ..data.profile import ColumnProfile

# A driver with more groups than this is capped to its largest groups before
# testing. Kruskal-Wallis across 30 sparse groups is not a meaningful test of
# anything, and the dataset's own grouping rule already allows up to 30
# levels (app/data/profile.py's _GROUPABLE_MAX_LEVELS) — a bound suited to
# "is this worth a bar chart" is not the same bound as "is this worth a
# hypothesis test".
MAX_GROUPS_TESTED = 10

# Below this many members, a group's mean is nearly meaningless and inflates
# the test's degrees of freedom without adding real information.
MIN_GROUP_SIZE = 2

# Effect-size bands, in the variance-explained scale both tests report on
# (eta-squared / epsilon-squared for Kruskal-Wallis, r-squared for Pearson).
# Cohen's conventional thresholds for this scale — the same style of banding
# app/analysis/statistics.py already uses for correlation strength, applied to
# the "how much variance" reading rather than the "how strong an r" reading.
_EFFECT_BANDS = ((0.14, "strong"), (0.06, "moderate"), (0.01, "weak"))


def _effect_label(value: float) -> str:
    for threshold, label in _EFFECT_BANDS:
        if value >= threshold:
            return label
    return "negligible"


@dataclass
class GroupStat:
    """One category's summary, for the group-comparison case."""

    category: str
    n: int
    mean: float

    def to_dict(self) -> dict[str, Any]:
        return {"category": self.category, "n": self.n, "mean": round(self.mean, 4)}


@dataclass
class DriverTest:
    """The result of testing one candidate column against the target metric.

    ``testable`` separates "this hypothesis was tested and found weak" from
    "this hypothesis could not be tested at all" (too few groups, no
    overlapping data) — collapsing the two would make a data-quality problem
    look like a negative finding, which is exactly the kind of honesty gap
    the rest of LANA's statistics module refuses to leave in place.

    ``q_value`` and ``significant`` are left ``None`` until the caller applies
    correction across every hypothesis tested in the same investigation; a
    single ``DriverTest`` in isolation has no multiple-comparison problem to
    correct for.
    """

    driver_column: str
    rationale: str = ""
    testable: bool = True
    reason: str | None = None

    driver_kind: str | None = None      # "categorical" | "numeric"
    test: str | None = None             # "kruskal" | "pearson"
    statistic: float | None = None
    p_value: float | None = None
    n: int | None = None
    effect_size: float | None = None
    effect_label: str | None = None
    direction: str | None = None
    group_summary: list[GroupStat] = field(default_factory=list)
    groups_truncated: bool = False

    q_value: float | None = None
    significant: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "driver_column": self.driver_column,
            "rationale": self.rationale,
            "testable": self.testable,
        }
        if not self.testable:
            out["reason"] = self.reason
            return out
        out.update({
            "driver_kind": self.driver_kind,
            "test": self.test,
            "statistic": round(self.statistic, 4) if self.statistic is not None else None,
            "p_value": round(self.p_value, 6) if self.p_value is not None else None,
            "n": self.n,
            "effect_size": round(self.effect_size, 4) if self.effect_size is not None else None,
            "effect_label": self.effect_label,
            "direction": self.direction,
            "groups_truncated": self.groups_truncated,
            "q_value": round(self.q_value, 6) if self.q_value is not None else None,
            "significant": self.significant,
        })
        if self.group_summary:
            out["group_summary"] = [g.to_dict() for g in self.group_summary]
        return out


def _untestable(driver_column: str, rationale: str, reason: str) -> DriverTest:
    return DriverTest(
        driver_column=driver_column, rationale=rationale,
        testable=False, reason=reason,
    )


def _test_categorical_driver(
    df: pd.DataFrame, target_col: str, driver_col: str, rationale: str,
) -> DriverTest:
    pair = df[[driver_col, target_col]].dropna()
    counts = pair[driver_col].value_counts()
    usable = counts[counts >= MIN_GROUP_SIZE]
    truncated = len(usable) > MAX_GROUPS_TESTED
    if truncated:
        usable = usable.iloc[:MAX_GROUPS_TESTED]

    if len(usable) < 2:
        return _untestable(
            driver_col, rationale,
            f"fewer than 2 groups of '{driver_col}' have at least "
            f"{MIN_GROUP_SIZE} non-null '{target_col}' values to compare.",
        )

    groups = [
        pair.loc[pair[driver_col] == category, target_col].to_numpy()
        for category in usable.index
    ]
    try:
        statistic, p_value = scipy_stats.kruskal(*groups)
    except ValueError as exc:
        return _untestable(driver_col, rationale, f"the test could not run: {exc}")

    n_total = sum(len(g) for g in groups)
    k = len(groups)
    # Epsilon-squared: the Kruskal-Wallis analogue of eta-squared. Clamped at
    # zero because a weak H relative to (k - 1) produces a small negative
    # value that has no meaning as a share of variance explained.
    epsilon_sq = max(0.0, (float(statistic) - k + 1) / (n_total - k)) if n_total > k else 0.0

    summary = sorted(
        (GroupStat(category=str(cat), n=len(g), mean=float(g.mean()))
         for cat, g in zip(usable.index, groups, strict=True)),
        key=lambda g: g.mean, reverse=True,
    )
    direction = (
        f"'{summary[0].category}' averages highest ({summary[0].mean:.4g}); "
        f"'{summary[-1].category}' averages lowest ({summary[-1].mean:.4g})"
    )

    return DriverTest(
        driver_column=driver_col, rationale=rationale, testable=True,
        driver_kind="categorical", test="kruskal",
        statistic=float(statistic), p_value=float(p_value), n=n_total,
        effect_size=epsilon_sq, effect_label=_effect_label(epsilon_sq),
        direction=direction, group_summary=summary, groups_truncated=truncated,
    )


def _test_numeric_driver(
    df: pd.DataFrame, target_col: str, driver_col: str, rationale: str,
) -> DriverTest:
    pair = df[[driver_col, target_col]].dropna()
    n = len(pair)
    if n < 3:
        return _untestable(
            driver_col, rationale,
            f"fewer than 3 rows have both '{driver_col}' and '{target_col}'.",
        )
    if pair[driver_col].nunique() < 2 or pair[target_col].nunique() < 2:
        return _untestable(
            driver_col, rationale,
            "one of the two columns is constant over the overlapping rows.",
        )

    r, p_value = scipy_stats.pearsonr(pair[driver_col], pair[target_col])
    r = float(r)
    r_squared = r * r
    direction = "positive" if r > 0 else "negative" if r < 0 else "none"

    return DriverTest(
        driver_column=driver_col, rationale=rationale, testable=True,
        driver_kind="numeric", test="pearson",
        statistic=r, p_value=float(p_value), n=n,
        effect_size=r_squared, effect_label=_effect_label(r_squared),
        direction=direction,
    )


def test_driver(
    df: pd.DataFrame,
    target_col: str,
    driver_col: str,
    profiles: dict[str, ColumnProfile],
    rationale: str = "",
) -> DriverTest:
    """Test whether ``driver_col`` explains variation in ``target_col``.

    Picks the test the driver's type actually supports rather than forcing
    one shape on every column: Kruskal-Wallis for a groupable (categorical or
    boolean) driver, Pearson correlation for a numeric one. Anything else —
    the driver is missing, is the target itself, or has no profile suited to
    either test — comes back ``testable=False`` with a plain-English reason
    rather than a misleading numeric result.
    """
    if driver_col == target_col:
        return _untestable(driver_col, rationale, "a column cannot be tested against itself.")
    if driver_col not in df.columns or driver_col not in profiles:
        return _untestable(driver_col, rationale, "this column is not in the dataset.")

    profile = profiles[driver_col]
    if profile.is_groupable:
        return _test_categorical_driver(df, target_col, driver_col, rationale)
    if profile.is_numeric_measure:
        return _test_numeric_driver(df, target_col, driver_col, rationale)
    return _untestable(
        driver_col, rationale,
        f"'{driver_col}' is neither groupable nor a numeric measurement "
        f"(kind: {profile.kind.value}), so no test applies to it.",
    )
