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
