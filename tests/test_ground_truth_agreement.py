"""Hold the eval's answer key and LANA's own statistics to each other.

The benchmark used to compute its ground truth by calling the same production
functions it was grading. That cannot detect an error in those functions: if
``compute_statistics`` used the wrong ddof, every case would still grade
"correct" against it and the run would look clean.

The answer key is now computed independently (numpy/scipy directly), with
LANA's figure evaluated alongside it. This test runs that comparison across
every case in the suite — no model, no Ollama, so it gates every commit
rather than only a full eval run.

A failure here means one of two things, and both are worth stopping for: a
real defect in LANA's statistics, or a definitional difference between the
two implementations that nobody has written down.
"""

import pytest

from eval.cases import CASES
from eval.datasets import employee_survey, messy_support_tickets, retail_orders
from eval.ground_truth import resolve


@pytest.fixture(scope="module")
def dfs():
    return {
        "retail": retail_orders(),
        "survey": employee_survey(),
        "messy": messy_support_tickets(),
    }


@pytest.fixture(scope="module")
def resolved(dfs):
    return [(case, resolve(dfs[case["dataset"]], case["gt"])) for case in CASES]


def test_every_case_resolves(resolved):
    assert len(resolved) == len(CASES)
    for case, gt in resolved:
        assert gt.kind in {"numeric", "categorical", "multi", "unanswerable"}, case["id"]


def test_independent_and_production_statistics_agree(resolved):
    disagreements = [
        f"{case['id']}: {gt.disagreement}"
        for case, gt in resolved
        if gt.disagreement
    ]
    assert not disagreements, (
        "the eval's independent answer key and LANA's own statistics disagree:\n  "
        + "\n  ".join(disagreements)
    )


#

# Ops resolved by a production function, and therefore the ones that must be
# independently checked. The others (count_rows, percent_share, groupby_*) are
# already plain pandas in the harness, with no LANA code path to disagree with.
CROSS_CHECKED_OPS = {"column_stat", "null_pct", "correlation", "regression_slope"}


def test_every_case_using_a_production_statistic_is_cross_checked(resolved):
    # Guards the guard: if resolve() stopped populating cross_check, the
    # agreement test above would pass by vacuously finding no disagreements.
    missing = [
        case["id"] for case, gt in resolved
        if case["gt"]["op"] in CROSS_CHECKED_OPS and gt.cross_check is None
    ]
    assert not missing, f"cases resolved by a production function with no cross-check: {missing}"

    checked = [case["id"] for case, gt in resolved if gt.cross_check is not None]
    assert checked, "no case carries a cross-check at all"


def test_a_planted_disagreement_is_detected(dfs, monkeypatch):
    # Proves the comparison has teeth: break the production statistic and the
    # cross-check must notice. Without this, a silently-disabled comparison
    # looks exactly like a passing one.
    import eval.ground_truth as ground_truth

    monkeypatch.setattr(
        ground_truth, "compute_statistics",
        lambda series: {"mean": 123456.0, "median": 1.0, "std": 1.0},
    )
    gt = resolve(dfs["retail"], {"op": "column_stat", "column": "revenue", "stat": "mean"})

    assert gt.disagreement is not None
    assert "DISAGREE" in gt.disagreement
    # Grading still uses the independent figure, not the corrupted one.
    assert gt.value != 123456.0
