"""Executed-query grounding: plan SQL, run it, answer from the result.

This is the path that removes the fact-ledger ceiling. ``build_context()``
can only answer what it precomputed; a query answers what was asked, because
the number is produced by running SQL over the real rows rather than looked up
in a summary someone decided to compute in advance.

The flow is deliberately two calls to the model, not one:

1. **Plan.** The model sees the schema and the question, and writes one
   DuckDB ``SELECT``. It never sees the data here, only column names and
   types, so a plan cannot be contaminated by cell contents.
2. **Execute.** ``app.analysis.sql_engine`` validates and runs it in a
   sandbox. A rejection or engine error is fed back once as a repair hint —
   models fix their own SQL from a concrete error far more reliably than they
   avoid the mistake up front, and one retry captures most of that without
   turning a wrong question into an expensive loop.
3. **Answer.** The model sees the question and the *result table*, and is
   told it may use no number that is not in that table.

Every number in the result becomes a :class:`Fact` carrying
``provenance="executed_sql"``, so the existing validator checks the prose
against the query output with the same machinery it already uses for ledger
facts — including the sibling-attribution check, which works unchanged
because a group-by result has exactly the family/category shape it expects.

Failure is a first-class outcome. When planning or execution does not
succeed, this module returns ``None`` and the caller falls back to the ledger
path. Executed grounding is strictly better when it works and must never be
worse than what it replaced when it does not.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from ..analysis.sql_engine import (
    TABLE_NAME,
    SqlExecutionError,
    SqlRejected,
    SqlResult,
    execute_sql,
    schema_for_prompt,
)
from .context import Fact

logger = logging.getLogger("lana.sql_answer")

# One repair attempt. Measured on the eval suite: a second repair recovered no
# additional cases and doubled the worst-case latency of a failing question.
MAX_PLAN_ATTEMPTS = 2

# Rows of the result shown to the answering model. Enough for a per-category
# breakdown to be read in full; beyond this the answer is summarising a table
# the user should be looking at directly.
MAX_ROWS_IN_ANSWER_PROMPT = 25


SQL_PLANNER_SYSTEM_PROMPT = """You translate a question about a dataset into exactly one DuckDB SQL query.

RULES:
1. Output ONLY the SQL. No explanation, no commentary, no markdown fences.
2. Exactly one statement. It must start with SELECT or WITH. Never INSERT, UPDATE, DELETE, CREATE, ATTACH, COPY, INSTALL or SET.
3. Query only the table you are given. Never reference any other table, file, URL or function that reads external data.
4. Quote every column name with double quotes, e.g. "marketing spend". Column names may contain spaces and punctuation.
5. Aggregate rather than listing rows. A question about "the average", "how many", "which category" wants one row or one row per group, not the raw table.
6. Always alias a computed column to a clear, descriptive snake_case name, e.g. AVG("revenue") AS avg_revenue.
7. Exclude NULLs where they would distort an aggregate, and use the column's own type — do not cast a VARCHAR to a number unless the question requires it.
8. If the question cannot be answered from these columns alone, output exactly: CANNOT_ANSWER
9. To count missing/NULL values PER COLUMN (e.g. "which column has the most missing values"), never use a single CASE expression scanning row-by-row — it only finds the first NULL column in each row, not a true count per column. Instead UNION ALL one row per column:
   SELECT * FROM (
     SELECT 'col_a' AS column_name, COUNT(*) - COUNT("col_a") AS missing_count FROM dataset
     UNION ALL
     SELECT 'col_b', COUNT(*) - COUNT("col_b") FROM dataset
   ) AS missing_by_column ORDER BY missing_count DESC

Output the bare SQL and nothing else."""


ANSWER_FROM_SQL_SYSTEM_PROMPT = """You are LANA's data analyst. You are given a question and the result of a SQL query that was actually executed against the user's dataset.

RULES — follow all of them:

1. The query result is the ONLY source of numbers. Every figure you state must appear in the result table. Never estimate, round beyond what is shown, or add a number from general knowledge.
2. Lead with the direct answer. Then, briefly, the evidence.
3. Quote exact column names and category labels as they appear in the result.
4. NO CAUSAL LANGUAGE. Write "is associated with" or "moves together with", never "causes", "drives" or "leads to".
5. If the result is empty, or does not actually answer the question, say so plainly instead of filling the gap.
6. Do not describe the SQL, the query, or the fact that a query was run. Answer as an analyst reporting a finding.
7. Be concise. No preamble, no restating the question."""


@dataclass
class SqlGroundedAnswer:
    """An answer produced by executing a query, with its full provenance."""

    answer: str
    result: SqlResult
    facts: list[Fact] = field(default_factory=list)
    attempts: int = 1
    repairs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sql": self.result.sql,
            "columns": list(self.result.columns),
            "rows": [list(r) for r in self.result.rows],
            "row_count": self.result.row_count,
            "truncated": self.result.truncated,
            "elapsed_ms": round(self.result.elapsed_ms, 2),
            "attempts": self.attempts,
            "repairs": list(self.repairs),
        }


class SqlPlanningFailed(Exception):
    """Planning or execution did not produce a usable result. Caller falls back."""


# Models wrap SQL in fences despite being told not to, and some prefix it with
# "SQL:" or a stray sentence. Extracting rather than rejecting costs nothing
# and recovers a large share of otherwise-valid plans.
_FENCE_RE = re.compile(r"```(?:sql)?\s*(.+?)```", re.DOTALL | re.IGNORECASE)
_LEADING_LABEL_RE = re.compile(r"^\s*(?:sql|query|answer)\s*:\s*", re.IGNORECASE)


def extract_sql(raw: str) -> str:
    """Pull the SQL out of a model reply that may be wrapped or prefixed."""
    if not raw:
        return ""
    text = raw.strip()
    fenced = _FENCE_RE.search(text)
    if fenced:
        text = fenced.group(1)
    text = _LEADING_LABEL_RE.sub("", text.strip())
    # A model that adds a trailing explanation usually does so after a blank
    # line following the statement's semicolon.
    if ";" in text:
        text = text[: text.index(";") + 1]
    return text.strip()


def _planner_prompt(question: str, schema: str, repair: str | None) -> str:
    parts = [schema, "", "=== QUESTION ===", question]
    if repair:
        parts += [
            "",
            "=== YOUR PREVIOUS ATTEMPT FAILED ===",
            repair,
            "",
            "Write a corrected query that avoids this problem. Output only the SQL.",
        ]
    return "\n".join(parts)


def plan_and_execute(
    provider: Any,
    df: pd.DataFrame,
    question: str,
    *,
    table_name: str = TABLE_NAME,
    max_attempts: int = MAX_PLAN_ATTEMPTS,
) -> tuple[SqlResult, int, list[str]]:
    """Ask the model for SQL, run it, repairing once on a stated error.

    Raises :class:`SqlPlanningFailed` when no attempt produced a result, which
    is the signal for the caller to use the ledger path instead.
    """
    schema = schema_for_prompt(df, table_name)
    repair: str | None = None
    repairs: list[str] = []

    for attempt in range(1, max_attempts + 1):
        raw = provider.generate(
            _planner_prompt(question, schema, repair),
            system_prompt=SQL_PLANNER_SYSTEM_PROMPT,
        )
        sql = extract_sql(raw)

        if not sql or sql.upper().startswith("CANNOT_ANSWER"):
            # The model's own judgement that the schema cannot answer this.
            # Not an error to repair — retrying just invites it to invent a
            # column, which is the failure mode this whole path exists to
            # avoid.
            raise SqlPlanningFailed(
                "The planner reported that this question cannot be answered "
                "from the dataset's columns."
            )

        try:
            result = execute_sql(df, sql, table_name=table_name)
        except (SqlRejected, SqlExecutionError) as exc:
            repair = f"{sql}\n\nError: {exc}"
            repairs.append(str(exc))
            logger.info(
                "SQL plan attempt %d/%d failed: %s", attempt, max_attempts, exc
            )
            continue

        if result.is_empty:
            # An empty result is a legitimate answer ("no orders match") but is
            # far more often a wrong filter. One repair, then it is reported
            # honestly as empty rather than retried forever.
            if attempt < max_attempts:
                repair = (
                    f"{sql}\n\nError: the query ran but returned no rows. "
                    f"Check the filter values against the column types; "
                    f"category values are case-sensitive."
                )
                repairs.append("query returned no rows")
                continue

        return result, attempt, repairs

    raise SqlPlanningFailed(
        "No valid query could be produced for this question. "
        + (f"Last error: {repairs[-1]}" if repairs else "")
    )


# ── Turning an executed result into checkable facts ──────────────────────────

# A result column whose name looks like a key rather than a measure. Used only
# to choose which columns label a row and which carry its numbers; a wrong
# guess costs attribution precision, not correctness.
def _split_result_columns(result: SqlResult) -> tuple[list[int], list[int]]:
    """Indices of (label columns, numeric columns) in a query result."""
    labels: list[int] = []
    numbers: list[int] = []
    for i, _name in enumerate(result.columns):
        column_values = [row[i] for row in result.rows if row[i] is not None]
        if column_values and all(
            isinstance(v, (int, float)) and not isinstance(v, bool)
            for v in column_values
        ):
            numbers.append(i)
        else:
            labels.append(i)
    return labels, numbers


def facts_from_result(result: SqlResult) -> list[Fact]:
    """Every number the query returned, as a fact the validator can check.

    The shape produced here is the same one ``build_context`` produces for a
    group-by, which is what lets the existing sibling-attribution check work
    on executed results without modification: facts sharing a ``family``
    (one measure) with different ``category`` values (one per group) are
    exactly the "same statistic, different label" relationship it looks for.
    """
    if result.is_empty or not result.columns:
        return []

    label_idx, number_idx = _split_result_columns(result)
    facts: list[Fact] = []

    for row in result.rows:
        label_parts = [
            f"{result.columns[i]}={row[i]}" for i in label_idx if row[i] is not None
        ]
        category = (
            " / ".join(str(row[i]) for i in label_idx if row[i] is not None) or None
        )
        category_column = result.columns[label_idx[0]] if label_idx else None

        for i in number_idx:
            value = row[i]
            if value is None:
                continue
            measure = str(result.columns[i])
            label = (
                f"{measure} where {', '.join(label_parts)} (executed SQL)"
                if label_parts
                else f"{measure} (executed SQL)"
            )
            facts.append(
                Fact(
                    label=label,
                    value=float(value),
                    column=measure,
                    category=category,
                    category_column=category_column,
                    # Only a multi-row result has siblings worth comparing; a
                    # single-row result has nothing to be confused with, and
                    # giving it a family would invite a spurious mismatch.
                    family=f"sql:{measure}" if len(result.rows) > 1 and label_idx else None,
                    category_names=(category,) if category else (),
                    provenance="executed_sql",
                )
            )
    return facts


def _answer_prompt(question: str, result: SqlResult) -> str:
    return (
        "=== QUERY RESULT (computed from the user's actual data) ===\n"
        f"{result.to_markdown(MAX_ROWS_IN_ANSWER_PROMPT)}\n"
        + (
            "\nNOTE: this result was truncated. Say so if you summarise it.\n"
            if result.truncated
            else ""
        )
        + "\n=== QUESTION ===\n"
        f"{question}\n\n"
        "Answer using only the figures in the result above."
    )


def answer_with_sql(
    provider: Any,
    df: pd.DataFrame,
    question: str,
    *,
    table_name: str = TABLE_NAME,
) -> SqlGroundedAnswer:
    """Full executed-grounding path. Raises :class:`SqlPlanningFailed` to fall back."""
    result, attempts, repairs = plan_and_execute(
        provider, df, question, table_name=table_name
    )
    from .reasoning import strip_reasoning

    answer = strip_reasoning(
        provider.generate(
            _answer_prompt(question, result),
            system_prompt=ANSWER_FROM_SQL_SYSTEM_PROMPT,
        )
    )
    return SqlGroundedAnswer(
        answer=answer,
        result=result,
        facts=facts_from_result(result),
        attempts=attempts,
        repairs=repairs,
    )
