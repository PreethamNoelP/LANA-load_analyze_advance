"""The sandboxed SQL path: what it computes, and what it refuses.

A model writing SQL that the server executes is a code-execution path reached
from a text box. The security half of this file is therefore not decoration —
it is the evidence that the sandbox described in ``app/analysis/sql_engine``'s
module docstring actually holds, on this machine, in this DuckDB build. Every
escape attempt below was chosen because it is the *realistic* one: local file
read, exfiltration over HTTP, statement stacking, and turning the sandbox off.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.analysis.sql_engine import (
    DUCKDB_AVAILABLE,
    MAX_RESULT_ROWS,
    SqlExecutionError,
    SqlRejected,
    execute_sql,
    schema_for_prompt,
    validate_sql,
)

pytestmark = pytest.mark.skipif(
    not DUCKDB_AVAILABLE, reason="duckdb is not installed"
)


@pytest.fixture
def orders():
    rng = np.random.default_rng(1234)
    n = 300
    return pd.DataFrame({
        "order_id": np.arange(1000, 1000 + n),
        "region": rng.choice(["north", "south", "east"], size=n),
        "revenue": rng.exponential(200.0, size=n).round(2),
        "is_repeat": rng.choice([True, False], size=n),
    })


# ── What it computes ────────────────────────────────────────────────────────

def test_aggregate_matches_pandas(orders):
    result = execute_sql(orders, 'SELECT AVG("revenue") AS avg_revenue FROM dataset')
    assert result.row_count == 1
    assert result.rows[0][0] == pytest.approx(orders["revenue"].mean())


def test_group_by_matches_pandas(orders):
    result = execute_sql(
        orders,
        'SELECT "region", AVG("revenue") AS avg_revenue FROM dataset '
        'GROUP BY "region" ORDER BY avg_revenue DESC',
    )
    expected = orders.groupby("region")["revenue"].mean().sort_values(ascending=False)
    assert [r[0] for r in result.rows] == list(expected.index)
    for row, value in zip(result.rows, expected.to_list(), strict=True):
        assert row[1] == pytest.approx(value)


def test_filtered_count_matches_pandas(orders):
    result = execute_sql(
        orders,
        "SELECT COUNT(*) AS n FROM dataset WHERE \"region\" = 'north' "
        'AND "revenue" > 100',
    )
    expected = len(orders[(orders.region == "north") & (orders.revenue > 100)])
    assert result.rows[0][0] == expected


def test_a_question_the_fact_ledger_cannot_answer(orders):
    """The whole point of this path: a filtered, conditional aggregate.

    ``build_context`` precomputes column moments, category counts and group
    means. It does not precompute "average revenue for repeat customers in
    the north", and no amount of prompt engineering lets a model derive it
    from what the ledger holds. Executing SQL does.
    """
    result = execute_sql(
        orders,
        'SELECT AVG("revenue") AS avg_revenue FROM dataset '
        "WHERE \"region\" = 'north' AND \"is_repeat\" = true",
    )
    expected = orders[(orders.region == "north") & (orders.is_repeat)]["revenue"].mean()
    assert result.rows[0][0] == pytest.approx(expected)


def test_empty_result_is_reported_not_faked(orders):
    result = execute_sql(
        orders, "SELECT * FROM dataset WHERE \"region\" = 'antarctica'"
    )
    assert result.is_empty
    assert "no rows" in result.to_markdown()


def test_results_are_bounded_and_say_so(orders):
    result = execute_sql(orders, "SELECT * FROM dataset", max_rows=10)
    assert result.row_count == 10
    assert result.truncated is True


def test_default_row_cap_applies(orders):
    result = execute_sql(orders, "SELECT * FROM dataset")
    assert result.row_count <= MAX_RESULT_ROWS


def test_nan_becomes_null_not_invalid_json(orders):
    frame = orders.copy()
    frame.loc[0, "revenue"] = np.nan
    result = execute_sql(
        frame, 'SELECT "revenue" FROM dataset ORDER BY "order_id" LIMIT 1'
    )
    assert result.rows[0][0] is None


def test_frame_is_not_mutated_by_a_query(orders):
    before = orders.copy(deep=True)
    execute_sql(orders, 'SELECT COUNT(*) AS n FROM dataset')
    pd.testing.assert_frame_equal(orders, before)


# ── What it refuses: statement shape ────────────────────────────────────────

@pytest.mark.parametrize("statement", [
    "DROP TABLE dataset",
    "DELETE FROM dataset",
    "UPDATE dataset SET revenue = 0",
    "INSERT INTO dataset VALUES (1)",
    "CREATE TABLE evil (a INT)",
    "ALTER TABLE dataset ADD COLUMN x INT",
])
def test_write_statements_are_refused(statement):
    with pytest.raises(SqlRejected):
        validate_sql(statement)


def test_stacked_statements_are_refused():
    with pytest.raises(SqlRejected, match="one statement"):
        validate_sql("SELECT 1; DROP TABLE dataset")


def test_a_semicolon_inside_a_string_is_not_a_second_statement():
    # The reason DuckDB's parser counts statements rather than a split on ';'.
    assert validate_sql("SELECT * FROM dataset WHERE region = 'a;b'")


def test_comments_cannot_hide_a_write():
    with pytest.raises(SqlRejected):
        validate_sql("/* SELECT */ DROP TABLE dataset")
    with pytest.raises(SqlRejected):
        validate_sql("--ignore\nDELETE FROM dataset")


def test_a_comment_only_statement_is_refused():
    with pytest.raises(SqlRejected, match="only comments"):
        validate_sql("-- just a comment")


def test_oversized_sql_is_refused():
    with pytest.raises(SqlRejected, match="characters"):
        validate_sql("SELECT " + "1," * 5000 + "1 FROM dataset")


def test_trailing_semicolon_is_accepted_and_stripped():
    assert validate_sql("SELECT 1 FROM dataset;") == "SELECT 1 FROM dataset"


# ── What it refuses: reaching outside the dataset ───────────────────────────

@pytest.mark.parametrize("statement", [
    "SELECT * FROM read_csv_auto('/etc/passwd')",
    "SELECT * FROM read_parquet('secrets.parquet')",
    "SELECT * FROM read_json_auto('http://169.254.169.254/latest/meta-data/')",
    "SELECT * FROM glob('*')",
    "INSTALL httpfs",
    "LOAD httpfs",
    "ATTACH 'other.db' AS other",
    "COPY (SELECT 1) TO '/tmp/out.csv'",
    "SET enable_external_access=true",
])
def test_external_access_is_refused_by_validation(statement):
    with pytest.raises(SqlRejected):
        validate_sql(statement)


@pytest.mark.parametrize("statement", [
    "SELECT * FROM read_csv_auto('/etc/passwd')",
    "SELECT * FROM glob('*')",
])
def test_external_access_is_refused_by_the_engine_too(orders, statement):
    """Defence in depth: the sandbox must hold even with validation bypassed.

    ``validate=False`` is not a production path — it exists so this test can
    prove the second layer independently. If this ever passes, the denylist
    is the only thing standing between a model and the filesystem.
    """
    with pytest.raises(SqlExecutionError):
        execute_sql(orders, statement, validate=False)


def test_the_engine_cannot_re_enable_external_access(orders):
    with pytest.raises(SqlExecutionError):
        execute_sql(orders, "SET enable_external_access=true", validate=False)


def test_a_column_named_like_a_denied_token_still_works(orders):
    """The denylist matches whole words, so real data is not collateral."""
    frame = orders.rename(columns={"region": "glob_region"})
    result = execute_sql(frame, 'SELECT COUNT("glob_region") AS n FROM dataset')
    assert result.rows[0][0] == len(frame)


# ── Failure reporting ───────────────────────────────────────────────────────

def test_an_unknown_column_is_an_actionable_error(orders):
    with pytest.raises(SqlExecutionError) as exc:
        execute_sql(orders, 'SELECT "not_a_column" FROM dataset')
    assert "could not be run" in str(exc.value)


def test_engine_errors_do_not_leak_filesystem_paths(orders):
    with pytest.raises(SqlExecutionError) as exc:
        execute_sql(orders, 'SELECT "nope" FROM dataset')
    message = str(exc.value)
    assert "C:\\Users" not in message and "/home/" not in message


# ── Schema rendering ────────────────────────────────────────────────────────

def test_schema_uses_sql_types_not_pandas_dtypes(orders):
    schema = schema_for_prompt(orders)
    assert "BIGINT" in schema and "DOUBLE" in schema and "VARCHAR" in schema
    assert "object" not in schema


def test_schema_quotes_awkward_column_names():
    frame = pd.DataFrame({'total ($)': [1.0], 'a"b': ["x"]})
    schema = schema_for_prompt(frame)
    assert '"total ($)"' in schema
    assert '"a""b"' in schema


def test_awkward_column_names_are_actually_queryable():
    frame = pd.DataFrame({"total ($)": [1.0, 2.0, 3.0]})
    result = execute_sql(frame, 'SELECT SUM("total ($)") AS total FROM dataset')
    assert result.rows[0][0] == pytest.approx(6.0)
