"""The two grounding holes the eval harness found, and the precision controls.

Both were cases where the validator *vouched for* a wrong answer rather than
merely missing it, which is the worse failure: a warning that never fires
costs a user nothing, a "verified" badge on a fabricated number costs them
the reason they trusted the tool.
"""

import pandas as pd
import pytest

from app.llm.context import build_context
from app.llm.validation import validate_answer

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture(scope="module")
def df():
    # order_id is near-unique and contiguous, so profile.py classifies it as an
    # identifier; revenue and customer_age span deliberately different
    # magnitudes, which is what lets a wrong figure hide inside the "some
    # column's range" test.
    n = 60
    return pd.DataFrame({
        "order_id": range(5_000, 5_000 + n),
        "region": ["north", "south"] * (n // 2),
        "customer_age": [20 + (i % 40) for i in range(n)],
        "revenue": [100.0 + (i % 50) * 10 for i in range(n)],
    })


@pytest.fixture(scope="module")
def ctx(df):
    return build_context(df)


# ── Identifiers contribute no centre, in the facts or in the prompt ───────────

def test_no_mean_median_or_std_fact_is_emitted_for_an_identifier(ctx):
    labels = {f.label for f in ctx.facts if f.label.startswith("order_id")}
    assert labels == {"order_id min", "order_id max"}


def test_the_prompt_does_not_show_an_identifier_a_mean_to_quote(df, ctx):
    # Printing the mean and then saying not to use it is an instruction
    # competing with a number, and the number tends to win — so the figure
    # itself must not be in the line at all.
    line = next(ln for ln in ctx.text.splitlines() if "'order_id'" in ln)
    assert f"{df['order_id'].mean():,.1f}" not in line
    assert "identifier" in line
    assert "no average" in line


def test_the_identifier_is_declared_centreless_for_the_validator(ctx):
    assert ctx.centreless_columns == frozenset({"order_id"})


def test_an_average_of_an_identifier_is_refused_at_any_value(df, ctx):
    # Refused structurally, not because the number looks wrong: the true mean
    # of the column is the hardest case, since it is both real arithmetic and
    # squarely inside the column's own range.
    answer = f"The average order_id is {df['order_id'].mean():,.1f}."
    result = validate_answer(answer, ctx)

    assert [c.status for c in result.claims] == ["unsupported"]
    assert "identifier" in result.claims[0].note
    assert result.warnings


def test_describing_an_identifier_range_is_still_fine(df, ctx):
    answer = (
        f"order_id values run from {df['order_id'].min():,.0f} "
        f"to {df['order_id'].max():,.0f}."
    )
    result = validate_answer(answer, ctx)
    assert not result.warnings
    assert {c.status for c in result.claims} <= {"verified", "derived"}


# ── A central value its own column cannot produce ─────────────────────────────

def test_an_average_above_its_column_maximum_is_refused(df, ctx):
    # Sits comfortably inside revenue's range, which is exactly why the old
    # "inside any column's range" test let it through.
    answer = "The average customer age is 415.0 years."
    result = validate_answer(answer, ctx)

    assert result.claims[0].status == "unsupported"
    assert "cannot fall outside" in result.claims[0].note


def test_a_correct_average_is_left_alone(df, ctx):
    answer = f"The average revenue per order is about ${df['revenue'].mean():,.2f}."
    result = validate_answer(answer, ctx)
    assert not result.warnings


def test_a_total_may_exceed_the_column_maximum(ctx):
    # A sum is not bound by any single value's range, so the check must not
    # fire near one. Asserted on the claim's own note rather than on the
    # warning list, because an unmatched total can still be reported by the
    # pre-existing unsupported rule.
    answer = "Total revenue across all orders was 9,412.00."
    result = validate_answer(answer, ctx)
    assert all("cannot fall outside" not in (c.note or "") for c in result.claims)


def test_an_ambiguous_attribution_concludes_nothing(ctx):
    # 'revenue' is the nearest column name to 2.3 and 2.3 is far outside its
    # range — but another number sits between them, so the attribution is not
    # clean enough to call anything wrong.
    answer = "The average revenue is $345.00 per order; the average order is 2.3 items."
    result = validate_answer(answer, ctx)
    assert all("cannot fall outside" not in (c.note or "") for c in result.claims)


# ── Matching a fact about a column the answer never mentions ──────────────────

def test_a_number_matching_an_unrelated_columns_fact_is_not_verified(df, ctx):
    # The shape that made "the average order_id is 5,249.5" come back verified:
    # fact matching scans every fact in the context, so a value can collide
    # with a statistic of a column the sentence is not about. Phrased as a
    # total here so the central-value check stays out of it and this isolates
    # the collision rule.
    # 1,070 is 'total customer_age for region=north'. The sentence is about
    # revenue and names no region, so the sibling check — which compares a
    # fact against other *categories of the same statistic* — has nothing to
    # say. Before the collision rule, this was reported as verified.
    total_age_north = float(
        df.loc[df["region"] == "north", "customer_age"].sum()
    )
    answer = f"Revenue reached {total_age_north:,.2f} in that period."
    result = validate_answer(answer, ctx)

    assert result.claims[0].status == "misattributed"
    assert "never mentions" in result.claims[0].note


def test_a_collision_is_not_claimed_when_no_other_column_is_named(df, ctx):
    # Conservative on purpose: with nothing in the sentence pointing at a
    # different column, there is no evidence the match is wrong, so the
    # benefit of the doubt stands.
    answer = f"The figure is {float(df['revenue'].mean()):,.2f} overall."
    result = validate_answer(answer, ctx)
    assert result.claims[0].status == "verified"


def test_a_correctly_attributed_number_survives_the_collision_check(df, ctx):
    answer = f"Revenue averages ${df['revenue'].mean():,.2f} per order."
    result = validate_answer(answer, ctx)
    assert result.claims[0].status == "verified"
    assert not result.warnings
