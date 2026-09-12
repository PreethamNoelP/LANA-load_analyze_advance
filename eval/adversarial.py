"""Controlled adversarial cases for the validator, independent of any model.

These answer strings are scripted, not sampled from a live model — that is
the point. It isolates one question: given a known-correct or known-wrong
answer, does ``app.llm.validation.validate_answer`` classify it correctly?
Measuring the validator this way, in addition to watching it run against a
real model's actual output in the main comparison, is what makes the
precision/recall numbers reproducible independent of model variance.

Two disjoint buckets, by design:

* ``capability == "numeric_fabrication"`` — invented figures and invented
  column/category references. This is what ``app/llm/validation.py``
  explicitly claims to catch, and it's the only bucket the headline
  precision/recall/F1 is computed over.
* everything else (``attribution`` / ``in_range`` / ``causal``) — failure
  modes included so the boundary is measured and reported rather than
  asserted.

  ``attribution`` is no longer in that "expect a low catch rate" group. Both
  of its cases are caught as of 2026-09-12: the validator now groups facts
  into families (same statistic, different column or category) and flags a
  number quoted under a sibling's name. That covers adv-10 (revenue's mean
  called "marketing spend") and adv-11 (engineering's mean called support's).
  What it still cannot see is a mislabelling that never names either side
  literally — the paraphrase case named in KNOWN_BLIND_SPOTS.

  ``in_range`` and ``causal`` remain genuinely out of scope: a wrong-but-
  plausible value inside a column's range is indistinguishable from a real
  calculation, and a non-numeric causal claim has nothing to extract. Their
  catch rate should stay at zero, and ``tests/test_adversarial_suite.py``
  fails if that silently changes in either direction.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class AdversarialCase:
    id: str
    dataset: str
    capability: str      # "numeric_fabrication" | "attribution" | "in_range" | "causal"
    should_flag: bool     # True: a correctly-behaving validator flags this as unsupported
    text: str
    note: str


def build_cases(retail_df: pd.DataFrame, survey_df: pd.DataFrame) -> list[AdversarialCase]:
    r_mean = float(retail_df["revenue"].mean())
    r_max = float(retail_df["revenue"].max())
    r_north_mean = float(retail_df.groupby("region", observed=True)["revenue"].mean()["north"])
    r_marketing_mean = float(retail_df["marketing_spend"].mean())
    s_mean = float(survey_df["salary"].mean())
    s_eng_mean = float(survey_df.groupby("department", observed=True)["salary"].mean()["engineering"])
    s_support_mean = float(survey_df.groupby("department", observed=True)["salary"].mean()["support"])

    return [
        # ── In scope: fabricated numbers — a working validator flags these ──
        AdversarialCase("adv-01", "retail", "numeric_fabrication", True,
            f"The average revenue per order was ${r_mean * 47:,.2f}.",
            "~47x the true mean; far outside the observed range."),
        AdversarialCase("adv-02", "retail", "numeric_fabrication", True,
            "Average revenue was 9,812,345.50 across all orders.",
            "Round, confident-looking figure with no basis in any computed fact."),
        AdversarialCase("adv-03", "survey", "numeric_fabrication", True,
            f"The average salary is ${s_mean + 250_000:,.2f}.",
            "True mean plus an arbitrary large offset."),

        # ── In scope: invented column/category values — a working validator flags these ──
        AdversarialCase("adv-04", "retail", "numeric_fabrication", True,
            "Revenue is highest in the 'enterprise' segment, averaging $612.40.",
            "'enterprise' is not a value of any column in this dataset."),
        AdversarialCase("adv-05", "survey", "numeric_fabrication", True,
            "The 'executive' department has the highest average salary at $210,500.",
            "'executive' is not a department that exists in this dataset."),

        # ── In scope: correct, well-supported answers — should NOT be flagged ──
        AdversarialCase("adv-06", "retail", "numeric_fabrication", False,
            f"The average revenue per order is about ${r_mean:,.2f}.",
            "Matches the true computed mean, phrased naturally."),
        AdversarialCase("adv-07", "retail", "numeric_fabrication", False,
            f"Orders from the north region average ${r_north_mean:,.0f} in revenue.",
            "Matches the true group-mean fact LANA computes, rounded to whole dollars."),
        AdversarialCase("adv-08", "survey", "numeric_fabrication", False,
            f"The engineering department's average salary is roughly ${s_eng_mean:,.0f}.",
            "Matches the true group-mean fact, rounded."),
        AdversarialCase("adv-09", "retail", "numeric_fabrication", False,
            f"Average marketing spend is about ${r_marketing_mean:,.2f}.",
            "Matches the true computed mean."),

        # ── Attribution: right number, wrong label (in scope since 2026-09-12) ──
        AdversarialCase("adv-10", "retail", "attribution", True,
            f"The average marketing spend per order is ${r_mean:,.2f}.",
            "The dollar figure is real, but it's revenue's true mean, not marketing "
            "spend's. Caught since column statistics gained a fact family: the "
            "sibling check sees 'marketing spend' named next to revenue's number."),
        AdversarialCase("adv-11", "survey", "attribution", True,
            f"The support department's average salary is ${s_eng_mean:,.0f}.",
            f"That figure (${s_eng_mean:,.0f}) is engineering's true mean "
            f"(support's real mean is ${s_support_mean:,.0f}), misattributed to support."),

        # ── Out of scope: wrong but in-range ("derived" bucket by design) ──
        AdversarialCase("adv-12", "retail", "in_range", True,
            f"The average revenue per order is about ${(r_mean + r_max) / 2:,.2f}.",
            "Plausible-looking and inside the column's observed range, but not the "
            "true mean — lands in the validator's 'derived' bucket, not 'unsupported'."),

        # ── Out of scope: non-numeric causal claims ──
        AdversarialCase("adv-13", "retail", "causal", True,
            "Higher marketing spend causes higher revenue.",
            "Causal language with no extractable number — nothing for a numeric "
            "validator to check at all."),
        AdversarialCase("adv-14", "survey", "causal", True,
            "Working remotely causes lower job satisfaction at this company.",
            "Same failure mode: a false causal claim with nothing numeric to verify."),
    ]
