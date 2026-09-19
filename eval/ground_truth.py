"""Resolve a case's ground-truth spec against the live dataframe.

Originally every op here called the *same* production functions LANA uses to
build its own fact ledger, on the stated reasoning that a separately-written
statistics routine might round or define things differently and make a
grading-harness bug look like a model failure. That reasoning is sound, and
it is also not sufficient on its own: a benchmark whose answer key is
produced by the code under test cannot detect an error in that code. If
``compute_statistics`` computed a standard deviation with the wrong ddof,
every case would still be graded "correct" against it, and the eval would
report a clean run.

So both are computed now. The answer key is an **independent** implementation
— numpy and scipy called directly, deliberately not through LANA's wrappers —
and the production function is evaluated alongside it and compared. Grading
uses the independent value; any disagreement is recorded on the result and
surfaced, because a divergence between the two is a finding in its own right
and belongs in the report rather than silently resolved in either direction.

``tests/test_ground_truth_agreement.py`` runs that comparison over every case
with no model involved, so the two implementations are held to each other on
every commit rather than only when someone runs the full eval.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

from app.analysis.regression import perform_linear_regression
from app.analysis.statistics import analyze_correlations, compute_statistics
from app.data.profile import profile_column

# How far the independent and production figures may differ before the
# disagreement is reported. Tight: these should agree to floating-point noise,
# and anything larger is a definitional difference worth knowing about.
_AGREEMENT_REL_TOLERANCE = 1e-6
_AGREEMENT_ABS_TOLERANCE = 1e-9


@dataclass(frozen=True)
class CrossCheck:
    """An independent figure, LANA's own figure, and whether they agree."""

    statistic: str
    independent: float
    production: float
    agrees: bool

    @property
    def detail(self) -> str:
        verdict = "agree" if self.agrees else "DISAGREE"
        return (
            f"{self.statistic}: independent={self.independent!r} "
            f"production={self.production!r} ({verdict})"
        )


def _cross_check(statistic: str, independent: float, production: float) -> CrossCheck:
    agrees = bool(np.isclose(
        independent, production,
        rtol=_AGREEMENT_REL_TOLERANCE, atol=_AGREEMENT_ABS_TOLERANCE,
        equal_nan=True,
    ))
    return CrossCheck(statistic, float(independent), float(production), agrees)


def _independent_column_stat(series: pd.Series, stat: str) -> float | None:
    """Compute one column statistic with numpy directly.

    Deliberately not routed through ``compute_statistics``: this exists to
    disagree with it if it is wrong. ``ddof=1`` for the standard deviation and
    the variance because a sample standard deviation is what a per-column
    summary means; a mismatch there is precisely the kind of bug this catches.
    """
    values = pd.to_numeric(series, errors="coerce").dropna().to_numpy(dtype="float64")
    if values.size == 0:
        return None
    match stat:
        case "mean":
            return float(np.mean(values))
        case "median":
            return float(np.median(values))
        case "std":
            return float(np.std(values, ddof=1)) if values.size > 1 else 0.0
        case "variance":
            return float(np.var(values, ddof=1)) if values.size > 1 else 0.0
        case "min":
            return float(np.min(values))
        case "max":
            return float(np.max(values))
        case "sum":
            return float(np.sum(values))
        case "count":
            return float(values.size)
        case "range":
            return float(np.max(values) - np.min(values))
        case "q1":
            return float(np.percentile(values, 25))
        case "q3":
            return float(np.percentile(values, 75))
        case "iqr":
            return float(np.percentile(values, 75) - np.percentile(values, 25))
        case _:
            return None


@dataclass
class GroundTruth:
    kind: str                              # "numeric" | "categorical" | "multi" | "unanswerable"
    value: Any = None                      # float | str | list[GroundTruth] | None
    candidates: list[str] | None = None    # all candidate labels, for "categorical"
    tolerance: float = 0.02
    tolerance_kind: str = "relative"       # "relative" | "absolute"
    note: str = ""                         # human-readable, carried into the report
    cross_check: CrossCheck | None = None  # independent vs production, where both exist

    @property
    def disagreement(self) -> str | None:
        """Set when the independent and production figures differ."""
        if self.kind == "multi":
            details = [p.disagreement for p in self.value or [] if p.disagreement]
            return "; ".join(details) if details else None
        if self.cross_check is None or self.cross_check.agrees:
            return None
        return self.cross_check.detail


def resolve(df: pd.DataFrame, spec: dict) -> GroundTruth:
    op = spec["op"]

    if op == "unanswerable":
        return GroundTruth(kind="unanswerable", note=spec.get("note", ""))

    if op == "count_rows":
        return GroundTruth(kind="numeric", value=float(len(df)), tolerance=0.0,
                            note="len(df)")

    if op == "column_stat":
        column, stat = spec["column"], spec["stat"]
        production = compute_statistics(df[column]).get(stat)
        if production is None:
            raise ValueError(f"compute_statistics('{column}') has no '{stat}'")
        independent = _independent_column_stat(df[column], stat)
        if independent is None:
            raise ValueError(f"no independent implementation of '{stat}' to check against")
        return GroundTruth(
            kind="numeric", value=independent,
            tolerance=spec.get("tolerance", 0.02),
            note=f"{stat} of '{column}', computed with numpy",
            cross_check=_cross_check(f"{stat}('{column}')", independent, float(production)),
        )

    if op == "null_pct":
        column = spec["column"]
        production = float(profile_column(df[column]).null_pct)
        independent = float(df[column].isna().mean() * 100)
        return GroundTruth(
            kind="numeric", value=independent,
            tolerance=spec.get("tolerance", 0.5), tolerance_kind="absolute",
            note=f"null share of '{column}', computed directly",
            # profile_column rounds for display; compare on the same footing
            # rather than reporting a rounding difference as a disagreement.
            cross_check=_cross_check(
                f"null_pct('{column}')", round(independent, 2), production
            ),
        )

    if op == "percent_share":
        col, val = spec["column"], spec["value"]
        pct = float((df[col] == val).mean() * 100)
        return GroundTruth(kind="numeric", value=pct,
                            tolerance=spec.get("tolerance", 0.5), tolerance_kind="absolute",
                            note=f"share of rows where {col} == {val!r}, in percent")

    if op == "groupby_mean_value":
        g = df.groupby(spec["group"], observed=True)[spec["column"]].mean()
        value = float(g.loc[spec["level"]])
        return GroundTruth(kind="numeric", value=value, tolerance=spec.get("tolerance", 0.02),
                            note=f"mean '{spec['column']}' where {spec['group']} == {spec['level']!r}")

    if op == "groupby_best":
        g = df.groupby(spec["group"], observed=True)[spec["column"]].mean()
        direction = spec.get("direction", "max")
        best = g.idxmax() if direction == "max" else g.idxmin()
        return GroundTruth(kind="categorical", value=str(best),
                            candidates=[str(x) for x in g.index.tolist()],
                            note=f"{direction} mean '{spec['column']}' grouped by '{spec['group']}'")

    if op == "correlation":
        result = analyze_correlations(df, method=spec.get("method", "pearson"))
        pair = next((p for p in result["pairs"]
                     if {p["column_a"], p["column_b"]} == {spec["col_a"], spec["col_b"]}), None)
        if pair is None:
            # The pair was excluded or had too few overlapping values — true
            # value is undefined, so no numeric answer can be correct.
            return GroundTruth(kind="unanswerable",
                                note=f"'{spec['col_a']}'/'{spec['col_b']}' pair not returned by "
                                     "analyze_correlations() — excluded or insufficient overlap")
        method = spec.get("method", "pearson")
        # scipy on the pairwise-complete values, which is what LANA's own
        # correlation scan reduces to — computed here without going through it.
        paired = df[[spec["col_a"], spec["col_b"]]].apply(
            pd.to_numeric, errors="coerce"
        ).dropna()
        correlate = scipy_stats.pearsonr if method == "pearson" else scipy_stats.spearmanr
        independent = float(correlate(paired[spec["col_a"]], paired[spec["col_b"]])[0])
        return GroundTruth(
            kind="numeric", value=independent,
            tolerance=spec.get("tolerance", 0.07), tolerance_kind="absolute",
            note=f"{method} r of '{spec['col_a']}'/'{spec['col_b']}', computed with scipy; "
                 f"significant={pair['significant']}, q={pair['q_value']}",
            # analyze_correlations rounds its coefficients to 4dp for display,
            # so the independent figure is rounded to match before comparing.
            # Found by this check on its first run: without it, every
            # correlation case reported a disagreement that was really just
            # presentation. Grading still uses the full-precision value above.
            cross_check=_cross_check(
                f"{method}_r('{spec['col_a']}','{spec['col_b']}')",
                round(independent, 4), float(pair["correlation"]),
            ),
        )

    if op == "regression_slope":
        x, y = spec["x"], spec["y"]
        production = perform_linear_regression(df, x, y)
        # scipy's least squares rather than LANA's scikit-learn path.
        paired = df[[x, y]].apply(pd.to_numeric, errors="coerce").dropna()
        independent = float(scipy_stats.linregress(paired[x], paired[y]).slope)
        return GroundTruth(
            kind="numeric", value=independent,
            tolerance=spec.get("tolerance", 0.10),
            note=f"OLS slope of '{y}' on '{x}', computed with scipy; "
                 f"r2={production.r2:.3f}",
            cross_check=_cross_check(
                f"slope('{y}'~'{x}')", independent, float(production.coefficient)
            ),
        )

    if op == "multi":
        parts = [resolve(df, sub) for sub in spec["parts"]]
        return GroundTruth(kind="multi", value=parts,
                            note="; ".join(p.note for p in parts))

    # ── Messy-dataset ops ────────────────────────────────────────────────────
    # These resolve against the *cleaned reference* of the messy frame, while
    # the model is shown the raw one. That asymmetry is the measurement: the
    # true answer is what the data says once its defects are resolved, and the
    # question is whether LANA reaches it (or declines) from the mess.
    #
    # No cross-check is attached. The production statistics functions cannot
    # be run on a column pandas has typed as text, which is precisely the
    # condition under test — asserting agreement between two implementations
    # of "nothing" would be theatre.

    if op == "messy_column_stat":
        from .datasets import clean_reference_for_messy

        clean = clean_reference_for_messy(df)
        values = pd.to_numeric(clean[spec["column"]], errors="coerce").dropna()
        if values.empty:
            return GroundTruth(kind="unanswerable",
                                note=f"'{spec['column']}' has no parseable values")
        stat = spec["stat"]
        value = float(getattr(values, stat)())
        return GroundTruth(
            kind="numeric", value=value, tolerance=spec.get("tolerance", 0.02),
            note=f"{stat} of '{spec['column']}' after parsing currency/whitespace "
                 f"and treating N/A-style tokens as missing",
        )

    if op == "messy_percent_share":
        from .datasets import clean_reference_for_messy

        clean = clean_reference_for_messy(df)
        pct = float((clean[spec["column"]] == spec["value"]).mean() * 100)
        return GroundTruth(
            kind="numeric", value=pct, tolerance=spec.get("tolerance", 1.0),
            tolerance_kind="absolute",
            note=f"share of '{spec['column']}' == {spec['value']!r} after "
                 f"normalising case and whitespace (the raw column splits this "
                 f"category across several spellings)",
        )

    if op == "messy_null_pct":
        from .datasets import clean_reference_for_messy

        clean = clean_reference_for_messy(df)
        pct = float(clean[spec["column"]].isna().mean() * 100)
        return GroundTruth(
            kind="numeric", value=pct, tolerance=spec.get("tolerance", 1.0),
            tolerance_kind="absolute",
            note=f"missing share of '{spec['column']}' counting '', 'N/A', "
                 f"'null', '-' and 'unknown' as missing",
        )

    if op == "messy_mode":
        from .datasets import clean_reference_for_messy

        clean = clean_reference_for_messy(df)
        counts = clean[spec["column"]].value_counts()
        return GroundTruth(
            kind="categorical", value=str(counts.index[0]),
            candidates=[str(x) for x in counts.index.tolist()],
            note=f"most common '{spec['column']}' after normalisation",
        )

    raise ValueError(f"Unknown ground-truth op '{op}'")
