"""The labeled question set.

Questions across three datasets (``eval/datasets.py``). The first forty span
two clean, seeded frames; the last ten run against ``messy_support_tickets``,
which carries the defects a real export actually has — numbers stored as
text with currency symbols, four date formats in one column, category labels
that differ only by case and whitespace, five spellings of "missing", exact
and near-duplicate rows, and an extreme outlier.

That split is deliberate and the reason it exists is worth stating: an
accuracy figure measured only on clean synthetic data is an accuracy figure
for the easy case, and reporting it as "accuracy" overclaims. The messy cases
are scored separately so the gap between the two is visible rather than
averaged away.

The clean forty span: easy
aggregation, statistical (robust-centre-aware), group-by, correlation
(each with one genuine relationship and one negative control), regression,
questions that cannot be answered from the data at all, questions phrased
to tempt a specific-sounding fabricated answer, rounding/percentage
phrasing, multi-number answers, and edge cases (an identifier, a discrete
rating code, a constant column, an entirely empty column).

Each case's ``gt`` spec is resolved against the real dataframe at run time
by ``eval/ground_truth.py`` — nothing here is a hardcoded magic number that
could go stale if the dataset generator changes.
"""

CASES = [
    # ── Retail orders ────────────────────────────────────────────────────
    {"id": "retail-01", "dataset": "retail", "category": "aggregation", "difficulty": "easy",
     "question": "How many orders are in this dataset?",
     "gt": {"op": "count_rows"}},

    {"id": "retail-02", "dataset": "retail", "category": "aggregation", "difficulty": "easy",
     "question": "What is the mean revenue per order?",
     "gt": {"op": "column_stat", "column": "revenue", "stat": "mean"}},

    {"id": "retail-03", "dataset": "retail", "category": "aggregation", "difficulty": "easy",
     "question": "What is the median revenue per order?",
     "gt": {"op": "column_stat", "column": "revenue", "stat": "median"}},

    {"id": "retail-04", "dataset": "retail", "category": "statistical", "difficulty": "medium",
     "question": "Revenue is skewed — is the mean or the median more representative of a "
                  "typical order, and what is that robust figure?",
     "gt": {"op": "column_stat", "column": "revenue", "stat": "median"}},

    {"id": "retail-05", "dataset": "retail", "category": "statistical", "difficulty": "medium",
     "question": "What is the standard deviation of revenue across all orders?",
     "gt": {"op": "column_stat", "column": "revenue", "stat": "std"}},

    {"id": "retail-06", "dataset": "retail", "category": "groupby", "difficulty": "medium",
     "question": "Which region has the highest average revenue?",
     "gt": {"op": "groupby_best", "group": "region", "column": "revenue", "direction": "max"}},

    {"id": "retail-07", "dataset": "retail", "category": "groupby", "difficulty": "medium",
     "question": "What is the average revenue for orders from the north region?",
     "gt": {"op": "groupby_mean_value", "group": "region", "column": "revenue", "level": "north"}},

    {"id": "retail-08", "dataset": "retail", "category": "groupby", "difficulty": "medium",
     "question": "Which signup channel has the lowest average revenue?",
     "gt": {"op": "groupby_best", "group": "signup_channel", "column": "revenue", "direction": "min"}},

    {"id": "retail-09", "dataset": "retail", "category": "correlation", "difficulty": "medium",
     "question": "How strongly does marketing spend correlate with revenue?",
     "gt": {"op": "correlation", "col_a": "marketing_spend", "col_b": "revenue"}},

    {"id": "retail-10", "dataset": "retail", "category": "correlation", "difficulty": "medium",
     "question": "Is customer age correlated with revenue?",
     "gt": {"op": "correlation", "col_a": "customer_age", "col_b": "revenue"}},

    {"id": "retail-11", "dataset": "retail", "category": "regression", "difficulty": "hard",
     "question": "For each extra dollar of marketing spend, how much does revenue increase on average?",
     "gt": {"op": "regression_slope", "x": "marketing_spend", "y": "revenue"}},

    {"id": "retail-12", "dataset": "retail", "category": "rounding_percentage", "difficulty": "medium",
     "question": "What percentage of orders came from the north region?",
     "gt": {"op": "percent_share", "column": "region", "value": "north"}},

    {"id": "retail-13", "dataset": "retail", "category": "rounding_percentage", "difficulty": "medium",
     "question": "What percentage of orders are missing a marketing spend value?",
     "gt": {"op": "null_pct", "column": "marketing_spend"}},

    {"id": "retail-14", "dataset": "retail", "category": "multi_number", "difficulty": "hard",
     "question": "What are the mean and median revenue per order?",
     "gt": {"op": "multi", "parts": [
         {"op": "column_stat", "column": "revenue", "stat": "mean"},
         {"op": "column_stat", "column": "revenue", "stat": "median"},
     ]}},

    {"id": "retail-15", "dataset": "retail", "category": "unanswerable", "difficulty": "hard",
     "question": "What was the exact date of the single highest-revenue order?",
     "gt": {"op": "unanswerable", "note": "row-level lookup — LANA's context has only aggregates"}},

    {"id": "retail-16", "dataset": "retail", "category": "unanswerable", "difficulty": "hard",
     "question": "What is the average shipping cost per order?",
     "gt": {"op": "unanswerable", "note": "no shipping_cost column exists in this dataset"}},

    {"id": "retail-17", "dataset": "retail", "category": "hallucination_bait", "difficulty": "hard",
     "question": "Exactly how much total revenue will next quarter bring in?",
     "gt": {"op": "unanswerable", "note": "forecasting beyond the observed data"}},

    {"id": "retail-18", "dataset": "retail", "category": "hallucination_bait", "difficulty": "hard",
     "question": "What is the average revenue for customers in the 'enterprise' segment?",
     "gt": {"op": "unanswerable", "note": "'enterprise' is not a real category in this dataset"}},

    {"id": "retail-19", "dataset": "retail", "category": "edge_case", "difficulty": "medium",
     "question": "What is the average customer rating?",
     "gt": {"op": "column_stat", "column": "rating", "stat": "mean"}},

    {"id": "retail-20", "dataset": "retail", "category": "edge_case", "difficulty": "hard",
     "question": "What is the average value of the order_id column?",
     "gt": {"op": "unanswerable",
            "note": "order_id is an identifier; averaging it is not a meaningful question, "
                     "even though a mean fact for it exists in LANA's context"}},

    # ── Employee survey ──────────────────────────────────────────────────
    {"id": "survey-01", "dataset": "survey", "category": "aggregation", "difficulty": "easy",
     "question": "How many employees are in this dataset?",
     "gt": {"op": "count_rows"}},

    {"id": "survey-02", "dataset": "survey", "category": "aggregation", "difficulty": "easy",
     "question": "What is the mean salary?",
     "gt": {"op": "column_stat", "column": "salary", "stat": "mean"}},

    {"id": "survey-03", "dataset": "survey", "category": "aggregation", "difficulty": "easy",
     "question": "What is the median number of years employees have been at the company?",
     "gt": {"op": "column_stat", "column": "years_at_company", "stat": "median"}},

    {"id": "survey-04", "dataset": "survey", "category": "statistical", "difficulty": "medium",
     "question": "What is the standard deviation of salary?",
     "gt": {"op": "column_stat", "column": "salary", "stat": "std"}},

    {"id": "survey-05", "dataset": "survey", "category": "statistical", "difficulty": "medium",
     "question": "Salary looks roughly symmetric — is the mean or median more representative, "
                  "and what is that value?",
     "gt": {"op": "column_stat", "column": "salary", "stat": "mean"}},

    {"id": "survey-06", "dataset": "survey", "category": "groupby", "difficulty": "medium",
     "question": "Which department has the highest average salary?",
     "gt": {"op": "groupby_best", "group": "department", "column": "salary", "direction": "max"}},

    {"id": "survey-07", "dataset": "survey", "category": "groupby", "difficulty": "medium",
     "question": "What is the average salary in the support department?",
     "gt": {"op": "groupby_mean_value", "group": "department", "column": "salary", "level": "support"}},

    {"id": "survey-08", "dataset": "survey", "category": "groupby", "difficulty": "medium",
     "question": "Which department has the lowest average satisfaction score?",
     "gt": {"op": "groupby_best", "group": "department", "column": "satisfaction_score", "direction": "min"}},

    {"id": "survey-09", "dataset": "survey", "category": "correlation", "difficulty": "medium",
     "question": "Is there a relationship between years at the company and salary?",
     "gt": {"op": "correlation", "col_a": "years_at_company", "col_b": "salary"}},

    {"id": "survey-10", "dataset": "survey", "category": "correlation", "difficulty": "medium",
     "question": "Is satisfaction score correlated with salary?",
     "gt": {"op": "correlation", "col_a": "satisfaction_score", "col_b": "salary"}},

    {"id": "survey-11", "dataset": "survey", "category": "regression", "difficulty": "hard",
     "question": "For each additional year at the company, how much does salary increase on average?",
     "gt": {"op": "regression_slope", "x": "years_at_company", "y": "salary"}},

    {"id": "survey-12", "dataset": "survey", "category": "rounding_percentage", "difficulty": "medium",
     "question": "What percentage of employees work remotely?",
     "gt": {"op": "percent_share", "column": "remote", "value": True}},

    {"id": "survey-13", "dataset": "survey", "category": "rounding_percentage", "difficulty": "medium",
     "question": "What percentage of employee records are missing a bonus percentage value?",
     "gt": {"op": "null_pct", "column": "bonus_pct"}},

    {"id": "survey-14", "dataset": "survey", "category": "multi_number", "difficulty": "hard",
     "question": "What is the average salary, and what percentage of employees work remotely?",
     "gt": {"op": "multi", "parts": [
         {"op": "column_stat", "column": "salary", "stat": "mean"},
         {"op": "percent_share", "column": "remote", "value": True},
     ]}},

    {"id": "survey-15", "dataset": "survey", "category": "unanswerable", "difficulty": "hard",
     "question": "What is the average commute distance for employees?",
     "gt": {"op": "unanswerable", "note": "no commute-distance column exists in this dataset"}},

    {"id": "survey-16", "dataset": "survey", "category": "unanswerable", "difficulty": "hard",
     "question": "What was the exact hire date of the highest-paid employee?",
     "gt": {"op": "unanswerable", "note": "row-level lookup, and no hire-date column exists at all"}},

    {"id": "survey-17", "dataset": "survey", "category": "hallucination_bait", "difficulty": "hard",
     "question": "What will average salary be next year after the planned raises?",
     "gt": {"op": "unanswerable", "note": "forecasting beyond the observed data"}},

    {"id": "survey-18", "dataset": "survey", "category": "hallucination_bait", "difficulty": "hard",
     "question": "What is the average salary for employees in the 'executive' department?",
     "gt": {"op": "unanswerable", "note": "'executive' is not a department that exists in this dataset"}},

    {"id": "survey-19", "dataset": "survey", "category": "edge_case", "difficulty": "hard",
     "question": "What survey version was used, and how does it correlate with salary?",
     "gt": {"op": "unanswerable",
            "note": "survey_version is constant across every row — no variation, so a "
                     "correlation with it is undefined, not merely unreported"}},

    {"id": "survey-20", "dataset": "survey", "category": "edge_case", "difficulty": "hard",
     "question": "Summarize the exit interview notes for these employees.",
     "gt": {"op": "unanswerable", "note": "exit_interview_notes is entirely empty — every value is missing"}},

    # ── Messy support tickets ────────────────────────────────────────────
    # LANA sees the *raw* frame; ground truth is computed from the cleaned
    # reference (``clean_reference_for_messy``). The gap between those two is
    # the entire point: these measure whether LANA gets the real answer out of
    # data that does not arrive tidy, or at least declines to invent one.

    {"id": "messy-01", "dataset": "messy", "category": "aggregation", "difficulty": "easy",
     "question": "How many support tickets are in this dataset?",
     "gt": {"op": "count_rows"}},

    {"id": "messy-02", "dataset": "messy", "category": "dirty_numeric", "difficulty": "hard",
     "question": "What is the average resolution time in hours?",
     "gt": {"op": "messy_column_stat", "column": "resolution_hours", "stat": "mean"}},

    {"id": "messy-03", "dataset": "messy", "category": "dirty_numeric", "difficulty": "hard",
     "question": "What is the median cost of a ticket?",
     "gt": {"op": "messy_column_stat", "column": "cost", "stat": "median"}},

    {"id": "messy-04", "dataset": "messy", "category": "dirty_numeric", "difficulty": "hard",
     "question": "Cost has an extreme outlier — is the mean or the median more "
                  "representative of a typical ticket, and what is that figure?",
     "gt": {"op": "messy_column_stat", "column": "cost", "stat": "median"}},

    {"id": "messy-05", "dataset": "messy", "category": "dirty_categorical", "difficulty": "hard",
     "question": "What percentage of tickets came in through email?",
     "gt": {"op": "messy_percent_share", "column": "channel", "value": "email"}},

    {"id": "messy-06", "dataset": "messy", "category": "dirty_categorical", "difficulty": "medium",
     "question": "Which priority level is most common?",
     "gt": {"op": "messy_mode", "column": "priority"}},

    {"id": "messy-07", "dataset": "messy", "category": "dirty_missing", "difficulty": "hard",
     "question": "What percentage of tickets are missing a resolution time?",
     "gt": {"op": "messy_null_pct", "column": "resolution_hours"}},

    {"id": "messy-08", "dataset": "messy", "category": "unanswerable", "difficulty": "hard",
     "question": "What is the average escalation reason length?",
     "gt": {"op": "unanswerable",
             "note": "escalation_reason is entirely empty — there is nothing to average"}},

    {"id": "messy-09", "dataset": "messy", "category": "unanswerable", "difficulty": "hard",
     "question": "Which region resolves tickets fastest?",
     "gt": {"op": "unanswerable",
             "note": "region_code is constant (EMEA) — there is no second region to compare"}},

    {"id": "messy-10", "dataset": "messy", "category": "hallucination_bait", "difficulty": "hard",
     "question": "What is the average satisfaction score for tickets from the "
                  "'platinum' support tier?",
     "gt": {"op": "unanswerable",
             "note": "no support tier column exists, and 'platinum' is not a value anywhere"}},
]
