"""Synthetic, seeded evaluation datasets.

Two datasets, deliberately different in shape — one skewed with an
identifier and a discrete rating code, one closer to symmetric with a
constant column and a column that is entirely empty — so the benchmark
exercises more than one profile of "real" data, not just one convenient
fixture reused everywhere.

Every value is derived from a fixed numpy seed, so a ground-truth figure
computed once (see ``eval/ground_truth.py``) stays correct forever: read a
case's spec in ``eval/cases.py``, open this file, and check the number by
hand if you want to. There is no separate "answer key" to keep in sync.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def retail_orders(seed: int = 20260101, n: int = 500) -> pd.DataFrame:
    """E-commerce orders: skewed revenue, a real region effect, a genuine
    correlation (marketing spend), and a genuinely unrelated column
    (customer age) to serve as a negative control."""
    rng = np.random.default_rng(seed)

    region = rng.choice(["north", "south", "east", "west"], size=n, p=[0.30, 0.20, 0.25, 0.25])
    region_multiplier = {"north": 1.35, "south": 0.75, "east": 1.00, "west": 1.05}
    base_revenue = rng.exponential(scale=220.0, size=n)
    revenue = np.round(base_revenue * np.array([region_multiplier[r] for r in region]), 2)

    # Real, moderate linear relationship — the case R11 regression slope and
    # R9 correlation are both computed against this.
    marketing_spend = np.round(35 + 0.18 * revenue + rng.normal(0, 25, size=n), 2)
    marketing_spend = np.clip(marketing_spend, 5, None)

    customer_age = rng.integers(18, 71, size=n)          # independent of revenue by construction
    rating = rng.integers(1, 6, size=n)                   # discrete code, not a continuous measure
    signup_channel = rng.choice(["organic", "paid_ad", "referral"], size=n)
    order_date = pd.to_datetime("2025-01-01") + pd.to_timedelta(rng.integers(0, 365, size=n), unit="D")

    df = pd.DataFrame({
        "order_id": np.arange(5000, 5000 + n),
        "order_date": order_date,
        "region": region,
        "signup_channel": signup_channel,
        "rating": rating,
        "customer_age": customer_age,
        "revenue": revenue,
        "marketing_spend": marketing_spend,
    })

    # Realistic partial missingness: ~4% of orders never recorded a spend figure.
    missing_idx = rng.choice(n, size=int(n * 0.04), replace=False)
    df.loc[missing_idx, "marketing_spend"] = np.nan
    return df


def employee_survey(seed: int = 20260202, n: int = 400) -> pd.DataFrame:
    """HR survey: roughly symmetric salary, a real years-of-service effect,
    a constant column, and a column that is entirely empty — the two edge
    cases a naive wrapper is most likely to mishandle."""
    rng = np.random.default_rng(seed)

    department = rng.choice(["engineering", "sales", "support", "hr"], size=n, p=[0.40, 0.25, 0.25, 0.10])
    dept_base = {"engineering": 95_000, "sales": 78_000, "support": 62_000, "hr": 68_000}
    years = np.round(np.clip(rng.exponential(scale=3.2, size=n), 0, 25), 1)

    salary = np.array([dept_base[d] for d in department]) + rng.normal(0, 9_000, size=n)
    salary = np.round(salary + years * 850, 2)  # real, moderate positive relationship with tenure

    satisfaction = rng.integers(1, 6, size=n)             # discrete code
    remote = rng.choice([True, False], size=n, p=[0.35, 0.65])

    df = pd.DataFrame({
        "employee_id": [f"E{100000 + i}" for i in range(n)],
        "department": department,
        "salary": salary,
        "years_at_company": years,
        "satisfaction_score": satisfaction,
        "remote": remote,
        "survey_version": ["v3"] * n,          # constant column — edge case
        "exit_interview_notes": [None] * n,    # entirely empty — edge case
    })

    # bonus_pct: >40% missing on purpose — exercises the imputation-refusal
    # threshold in app/data/profile.py and the "how reliable is this" framing.
    bonus_pct = np.round(rng.uniform(0, 15, size=n), 1)
    bonus_missing = rng.choice(n, size=int(n * 0.46), replace=False)
    bonus_pct[bonus_missing] = np.nan
    df["bonus_pct"] = bonus_pct

    # years_at_company: modest additional missingness — partial records happen.
    years_missing = rng.choice(n, size=int(n * 0.07), replace=False)
    df.loc[years_missing, "years_at_company"] = np.nan
    return df


def messy_support_tickets(seed: int = 20260303, n: int = 600) -> pd.DataFrame:
    """A deliberately dirty dataset, shaped like data people actually have.

    ``retail_orders`` and ``employee_survey`` are clean: correct dtypes, tidy
    category labels, missingness only where it was put on purpose. That makes
    them a fair test of statistical correctness and an unfair test of
    everything else, and the audit said so — the benchmark had "no real-world
    messy dataset", so a measured accuracy figure was an accuracy figure on
    the easy case.

    Every defect below is one that shows up in a real export, and each one
    breaks a different part of the pipeline if it is not handled:

    * **Numbers stored as text**, with currency symbols, thousands separators
      and stray whitespace (``" $1,234.50 "``). A profiler that trusts dtypes
      calls this a text column and computes nothing; a SQL planner that casts
      blindly gets NULLs.
    * **Mixed date formats** in one column — ISO, US, European and a bare
      year. Real exports concatenate systems, and pandas silently produces
      ``object`` rather than failing.
    * **Inconsistent category casing and whitespace** (``"Email"``,
      ``"email"``, ``" EMAIL "``), which splits one category into four and
      makes every share and group-by wrong rather than merely imprecise.
    * **Several spellings of missing** — empty string, ``"N/A"``, ``"null"``,
      ``"-"``, ``"unknown"`` — none of which pandas reads as NaN, so the null
      rate looks like zero while half the column is absent.
    * **Duplicated rows**, including near-duplicates differing only in
      whitespace.
    * **A high-cardinality free-text column** that must not be grouped.
    * **An extreme outlier** two orders of magnitude out, which drags a mean
      somewhere no median goes.
    * **A column that is entirely one value**, and one that is entirely empty.

    Seeded, so the defects land in the same rows on every run and a
    ground-truth figure computed once stays correct.
    """
    rng = np.random.default_rng(seed)

    priority = rng.choice(["low", "medium", "high", "critical"], size=n,
                          p=[0.35, 0.35, 0.22, 0.08])

    # Channel, with the casing/whitespace inconsistency intact.
    base_channel = rng.choice(["email", "phone", "chat"], size=n, p=[0.5, 0.3, 0.2])
    channel_variants = {
        "email": ["email", "Email", "EMAIL", " email "],
        "phone": ["phone", "Phone", "phone "],
        "chat": ["chat", "Chat", " Chat"],
    }
    channel = [
        channel_variants[c][rng.integers(0, len(channel_variants[c]))]
        for c in base_channel
    ]

    # Resolution hours as text with currency-style noise, plus several
    # spellings of "missing" that pandas will not recognise.
    true_hours = np.round(rng.exponential(6.0, size=n) + 0.5, 1)
    missing_tokens = ["", "N/A", "null", "-", "unknown", "NULL", "n/a"]
    hours_text = []
    for value in true_hours:
        if rng.random() < 0.12:
            hours_text.append(missing_tokens[rng.integers(0, len(missing_tokens))])
        elif rng.random() < 0.25:
            hours_text.append(f" {value:,.1f} ")
        else:
            hours_text.append(f"{value:.1f}")

    # Cost as text with a currency symbol and thousands separators.
    true_cost = np.round(rng.exponential(180.0, size=n) + 5, 2)
    true_cost[rng.integers(0, n)] = 98_500.00          # one extreme outlier
    cost_text = [f"${v:,.2f}" for v in true_cost]

    # Four date formats in one column, as an export from four systems gives.
    days = rng.integers(0, 400, size=n)
    starts = pd.to_datetime("2025-01-01") + pd.to_timedelta(days, unit="D")
    formats = ["%Y-%m-%d", "%m/%d/%Y", "%d.%m.%Y", "%Y"]
    opened = [
        stamp.strftime(formats[rng.integers(0, len(formats))]) for stamp in starts
    ]

    satisfaction = rng.integers(1, 6, size=n).astype(float)
    satisfaction[rng.choice(n, size=int(n * 0.18), replace=False)] = np.nan

    frame = pd.DataFrame({
        "ticket_id": [f"TK-{100000 + i}" for i in range(n)],
        "opened_at": opened,
        "channel": channel,
        "priority": priority,
        "resolution_hours": hours_text,
        "cost": cost_text,
        "satisfaction": satisfaction,
        "agent_notes": [
            f"Customer reported issue {rng.integers(1000, 9999)} and it was handled."
            for _ in range(n)
        ],
        "region_code": ["EMEA"] * n,              # constant
        "escalation_reason": [None] * n,          # entirely empty
    })

    # Exact and whitespace-only-different duplicates, appended at the end so
    # the first occurrence's index is stable.
    duplicates = frame.iloc[rng.choice(n, size=25, replace=False)].copy()
    near = frame.iloc[rng.choice(n, size=10, replace=False)].copy()
    near["channel"] = near["channel"].astype(str) + " "
    return pd.concat([frame, duplicates, near], ignore_index=True)


def clean_reference_for_messy(df: pd.DataFrame) -> pd.DataFrame:
    """The messy frame with its defects resolved, for computing ground truth.

    Kept beside the generator rather than inside it so a case's expected
    answer is derived by an explicit, readable transformation that a reader
    can check by hand — the same reasoning ``eval/ground_truth.py`` gives for
    computing its answer key independently of the code under test.
    """
    out = df.copy()
    missing = {"", "n/a", "null", "-", "unknown", "nan", "none"}

    hours = out["resolution_hours"].astype(str).str.strip()
    hours = hours.where(~hours.str.lower().isin(missing))
    out["resolution_hours"] = pd.to_numeric(
        hours.str.replace(",", "", regex=False), errors="coerce"
    )

    out["cost"] = pd.to_numeric(
        out["cost"].astype(str).str.replace(r"[$,\s]", "", regex=True),
        errors="coerce",
    )
    out["channel"] = out["channel"].astype(str).str.strip().str.lower()
    out["priority"] = out["priority"].astype(str).str.strip().str.lower()
    return out
