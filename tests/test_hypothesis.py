"""Tests for the deterministic half of Investigate: one driver, one test.

No model runs here — see tests/test_investigate.py for the LLM-facing half.
These assert the statistics themselves: the right test is chosen for the
right column shape, a real relationship is detected, and an untestable
hypothesis says why rather than returning a misleading number.
"""

import numpy as np
import pandas as pd
import pytest

from app.analysis import hypothesis as hyp
from app.data.profile import profile_dataframe

# hyp.test_driver is called via the module below rather than imported by
# name: pytest collects any module-level name starting with `test_` as a test
# function, and would otherwise try to run the imported function itself as a
# fixture-less test and fail at collection.
MAX_GROUPS_TESTED = hyp.MAX_GROUPS_TESTED


def rng(seed: int = 20260923) -> np.random.Generator:
    return np.random.default_rng(seed)


@pytest.fixture()
def driven_df():
    """'region' genuinely explains 'revenue'; 'noise' does not."""
    r, n = rng(), 300
    region = r.choice(["north", "south"], size=n)
    base = np.where(region == "north", 300.0, 100.0)
    return pd.DataFrame({
        "revenue": base + r.normal(0, 20.0, size=n),
        "region": region,
        "noise": r.choice(["a", "b", "c"], size=n),
        "customer_id": range(n),
    })


def test_categorical_driver_with_a_real_effect_is_detected(driven_df):
    profiles = profile_dataframe(driven_df)
    result = hyp.test_driver(driven_df, "revenue", "region", profiles, rationale="plausible")

    assert result.testable
    assert result.test == "kruskal"
    assert result.driver_kind == "categorical"
    assert result.p_value < 0.01
    assert result.effect_label in ("moderate", "strong")
    assert result.rationale == "plausible"
    # Group means are sorted descending, and the true ordering is recovered.
    assert result.group_summary[0].category == "north"
    assert result.group_summary[-1].category == "south"
    assert "north" in result.direction


def test_categorical_driver_with_no_real_effect_is_not_confidently_wrong(driven_df):
    profiles = profile_dataframe(driven_df)
    result = hyp.test_driver(driven_df, "revenue", "noise", profiles)

    assert result.testable
    # Not asserting p > 0.05 (that would be flaky by construction); asserting
    # the effect size is small is a stabler claim about pure noise.
    assert result.effect_size < 0.06


def test_numeric_driver_correlation(driven_df):
    r = rng(1)
    n = 200
    x = r.normal(size=n)
    df = pd.DataFrame({"x": x, "y": 2.0 * x + r.normal(scale=0.3, size=n)})
    profiles = profile_dataframe(df)

    result = hyp.test_driver(df, "y", "x", profiles)
    assert result.testable
    assert result.test == "pearson"
    assert result.driver_kind == "numeric"
    assert result.direction == "positive"
    assert result.effect_size > 0.8


def test_a_column_cannot_be_tested_against_itself(driven_df):
    profiles = profile_dataframe(driven_df)
    result = hyp.test_driver(driven_df, "revenue", "revenue", profiles)
    assert not result.testable
    assert "itself" in result.reason


def test_an_identifier_column_is_refused_as_a_driver(driven_df):
    profiles = profile_dataframe(driven_df)
    result = hyp.test_driver(driven_df, "revenue", "customer_id", profiles)
    assert not result.testable
    assert "neither groupable" in result.reason


def test_a_missing_column_is_refused(driven_df):
    profiles = profile_dataframe(driven_df)
    result = hyp.test_driver(driven_df, "revenue", "does_not_exist", profiles)
    assert not result.testable


def test_too_many_groups_are_capped_not_silently_all_used():
    r, n = rng(2), 400
    # 15 distinct groups — more than MAX_GROUPS_TESTED — each with enough rows.
    group = r.integers(0, 15, size=n).astype(str)
    df = pd.DataFrame({"metric": r.normal(size=n), "group": group})
    profiles = profile_dataframe(df)

    result = hyp.test_driver(df, "metric", "group", profiles)
    assert result.testable
    assert result.groups_truncated
    assert len(result.group_summary) <= MAX_GROUPS_TESTED
