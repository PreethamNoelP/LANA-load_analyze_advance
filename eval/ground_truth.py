"""Resolve a case's ground-truth spec against the live dataframe.

Every op below calls the *same* production functions LANA uses to build its
own fact ledger — ``compute_statistics``, ``analyze_correlations``,
``perform_linear_regression``, ``profile_column`` — rather than an
independently hand-rolled computation. "Ground truth" here means "what
LANA's own statistics code says", which is exactly the claim under test: if
grading used a separately-written statistics routine that happened to
round or define things slightly differently, a disagreement would look like
a model failure when it was actually a grading-harness bug.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from app.analysis.regression import perform_linear_regression
from app.analysis.statistics import analyze_correlations, compute_statistics
from app.data.profile import profile_column


@dataclass
class GroundTruth:
    kind: str                              # "numeric" | "categorical" | "multi" | "unanswerable"
    value: Any = None                      # float | str | list[GroundTruth] | None
    candidates: list[str] | None = None    # all candidate labels, for "categorical"
    tolerance: float = 0.02
    tolerance_kind: str = "relative"       # "relative" | "absolute"
    note: str = ""                         # human-readable, carried into the report


def resolve(df: pd.DataFrame, spec: dict) -> GroundTruth:
    op = spec["op"]

    if op == "unanswerable":
        return GroundTruth(kind="unanswerable", note=spec.get("note", ""))

    if op == "count_rows":
        return GroundTruth(kind="numeric", value=float(len(df)), tolerance=0.0,
                            note="len(df)")

    if op == "column_stat":
        stats = compute_statistics(df[spec["column"]])
        value = stats.get(spec["stat"])
        if value is None:
            raise ValueError(f"compute_statistics('{spec['column']}') has no '{spec['stat']}'")
        return GroundTruth(kind="numeric", value=float(value),
                            tolerance=spec.get("tolerance", 0.02),
                            note=f"{spec['stat']} of '{spec['column']}' via compute_statistics()")

    if op == "null_pct":
        p = profile_column(df[spec["column"]])
        return GroundTruth(kind="numeric", value=float(p.null_pct),
                            tolerance=spec.get("tolerance", 0.5), tolerance_kind="absolute",
                            note=f"null_pct of '{spec['column']}' via profile_column()")

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
        return GroundTruth(kind="numeric", value=float(pair["correlation"]),
                            tolerance=spec.get("tolerance", 0.07), tolerance_kind="absolute",
                            note=f"pearson r via analyze_correlations(); "
                                 f"significant={pair['significant']}, q={pair['q_value']}")

    if op == "regression_slope":
        result = perform_linear_regression(df, spec["x"], spec["y"])
        return GroundTruth(kind="numeric", value=float(result.coefficient),
                            tolerance=spec.get("tolerance", 0.10),
                            note=f"OLS slope of '{spec['y']}' on '{spec['x']}' via "
                                 f"perform_linear_regression(); r2={result.r2:.3f}")

    if op == "multi":
        parts = [resolve(df, sub) for sub in spec["parts"]]
        return GroundTruth(kind="multi", value=parts,
                            note="; ".join(p.note for p in parts))

    raise ValueError(f"Unknown ground-truth op '{op}'")
