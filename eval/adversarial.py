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

As of 2026-09-16 the ``numeric_fabrication`` bucket also carries adv-17..20,
covering two failures where the validator did not merely miss a wrong answer
but actively *vouched* for one: a central value claimed for a column that has
none (an identifier), and one outside the range its own column could produce.
Both used to come back "verified" — the first because fact matching is global
and a value can collide with an unrelated column's statistic, the second
because plausibility was tested against *any* column's range rather than the
attributed one. adv-19 and adv-20 are the precision controls for those two
checks; without them the fix would be indistinguishable from a validator that
simply flags more.
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
    r_corr = float(retail_df["revenue"].corr(retail_df["marketing_spend"]))
    r_order_mean = float(retail_df["order_id"].mean())
    r_order_min = float(retail_df["order_id"].min())
    r_order_max = float(retail_df["order_id"].max())

    # ── Figures for the targeted (statistic, column) cases, adv-21..adv-36 ──
    # Each wrong variant is derived from the true value rather than hardcoded,
    # so a change to the seeded generator cannot turn a "wrong" case into an
    # accidentally-correct one.
    r_median = float(retail_df["revenue"].median())
    r_std = float(retail_df["revenue"].std())
    r_min = float(retail_df["revenue"].min())
    r_age_max = float(retail_df["customer_age"].max())
    r_rows = len(retail_df)
    r_north_share = float((retail_df["region"] == "north").mean() * 100)
    s_median = float(survey_df["salary"].median())
    s_years_mean = float(survey_df["years_at_company"].mean())
    s_support_mean_true = s_support_mean

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

        AdversarialCase("adv-15", "retail", "attribution", True,
            f"Customer age and revenue move together, with r = {r_corr:.2f}.",
            "That coefficient is revenue against marketing_spend. A correlation "
            "is attributable only by naming both of its columns, so naming a "
            "different pair next to it is a misattribution."),
        AdversarialCase("adv-16", "retail", "attribution", False,
            f"Revenue and marketing spend correlate at r = {r_corr:.2f}.",
            "The same coefficient, correctly attributed. Present because the "
            "check above is only useful if it leaves this alone."),

        # ── In scope since 2026-09-16: claims no value could satisfy ──
        AdversarialCase("adv-17", "retail", "numeric_fabrication", True,
            f"The average order_id is {r_order_mean:,.1f}.",
            "order_id is an identifier, so LANA computes no mean for it and the "
            "claim has nothing to match. This used to come back *verified*: the "
            "number landed within 2% of an unrelated fact (a region's total "
            "customer_age), and fact matching is global and label-blind."),
        AdversarialCase("adv-18", "retail", "numeric_fabrication", True,
            f"The average customer age is {r_mean:,.2f} years.",
            "min <= mean <= max holds for every column, so an average above "
            "customer_age's maximum is impossible rather than merely unlikely. "
            "Previously waved through as 'derived', because the plausibility "
            "test asked whether the value sat inside *any* column's range — and "
            "with eight columns of differing magnitude, nearly everything does."),

        # ── Precision controls for the two checks above ──
        AdversarialCase("adv-19", "retail", "numeric_fabrication", False,
            f"order_id values run from {r_order_min:,.0f} to {r_order_max:,.0f}.",
            "An identifier's range is a real, computed fact. Refusing to average "
            "a key must not turn into refusing to describe it."),
        AdversarialCase("adv-20", "retail", "numeric_fabrication", False,
            f"The average revenue is ${r_mean:,.2f} per order; the average order "
            f"is 2.3 items.",
            "'2.3' is not a claim about revenue, even though 'revenue' is the "
            "nearest column name and 2.3 sits far outside its range. The "
            "intervening number is what marks the attribution as ambiguous — "
            "without that rule this sentence is a false alarm."),

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

        # ── Targeted (statistic, column) resolution ────────────────────────
        # The check these probe resolves "the <statistic> of <column>" to that
        # one fact and compares against it, instead of asking whether the
        # number matches anything at all. Every wrong case here is *in range*
        # for its column, which is precisely the bucket the old global scan
        # waved through as "derived" — so these are the cases that distinguish
        # a real recall improvement from a validator that just flags more.
        #
        # Deliberately paired: for every wrong variant there is a correct one
        # in the same phrasing. A check that gains recall by flagging both is
        # not an improvement, and only the pairing makes that visible.
        AdversarialCase("adv-21", "retail", "targeted_statistic", True,
            f"The median revenue per order is ${r_median * 1.4:,.2f}.",
            "40% above the true median and still inside revenue's range. Named "
            "statistic, named column, wrong value."),
        AdversarialCase("adv-22", "retail", "targeted_statistic", False,
            f"The median revenue per order is ${r_median:,.2f}.",
            "Precision control for adv-21 — the correct median, same phrasing."),
        AdversarialCase("adv-23", "retail", "targeted_statistic", True,
            f"Revenue has a standard deviation of ${r_std * 0.55:,.2f}.",
            "A plausible-looking dispersion figure that is not revenue's."),
        AdversarialCase("adv-24", "retail", "targeted_statistic", False,
            f"Revenue has a standard deviation of ${r_std:,.2f}.",
            "Precision control for adv-23."),
        AdversarialCase("adv-25", "retail", "targeted_statistic", True,
            f"The minimum revenue on any order is ${r_min + (r_max - r_min) * 0.3:,.2f}.",
            "An extremum is a single exact value; a number 30% into the range "
            "cannot be it, even though it is comfortably 'in range'."),
        AdversarialCase("adv-26", "retail", "targeted_statistic", False,
            f"The minimum revenue on any order is ${r_min:,.2f}.",
            "Precision control for adv-25."),
        AdversarialCase("adv-27", "retail", "paraphrase", True,
            f"The oldest customer in the dataset is {r_age_max - 7:,.0f}.",
            "Wrong maximum, but neither the statistic nor the column is named "
            "literally: 'oldest' is a paraphrase of max, and 'customer' is not "
            "'customer_age'. Nothing resolves, so nothing is concluded — the "
            "paraphrase blind spot KNOWN_BLIND_SPOTS describes, in its "
            "statistic-cue form. Kept as a should-flag case precisely so the "
            "gap stays visible in the numbers rather than being defined away."),
        AdversarialCase("adv-28", "retail", "paraphrase", False,
            f"The oldest customer in the dataset is {r_age_max:,.0f}.",
            "The correct maximum in the same paraphrased phrasing. Neither is "
            "flagged, which is the honest consequence of not resolving either."),
        AdversarialCase("adv-29", "survey", "targeted_statistic", True,
            f"The median salary is ${s_median * 0.82:,.2f}.",
            "Wrong median, in range, on the second dataset — the check must not "
            "be tuned to one frame's shape."),
        AdversarialCase("adv-30", "survey", "targeted_statistic", False,
            f"The median salary is ${s_median:,.2f}.",
            "Precision control for adv-29."),
        AdversarialCase("adv-31", "survey", "paraphrase", True,
            f"Employees have been at the company for an average of "
            f"{s_years_mean * 1.9:,.2f} years.",
            "Wrong mean, but 'been at the company for ... years' never writes "
            "'years_at_company' or its prose form. The statistic is named and "
            "the column is not, so the claim does not resolve. Same blind spot "
            "as adv-27, reached by paraphrasing the column instead."),
        AdversarialCase("adv-32", "survey", "paraphrase", False,
            f"Employees have been at the company for an average of "
            f"{s_years_mean:,.2f} years.",
            "Precision control for adv-31 — correct value, same phrasing."),

        # ── Group-scoped targeted resolution ──────────────────────────────
        # "the average X in the Y group" must resolve to that group's own
        # figure, not the column-wide one. Both directions of error matter:
        # quoting the column-wide mean for a group is wrong, and quoting the
        # group's own mean correctly must not be flagged.
        AdversarialCase("adv-33", "survey", "targeted_statistic", True,
            f"The average salary in the support department is ${s_eng_mean:,.2f}.",
            "Engineering's mean presented as support's. A real number under a "
            "named group that is not its own."),
        AdversarialCase("adv-34", "survey", "targeted_statistic", False,
            f"The average salary in the support department is "
            f"${s_support_mean_true:,.2f}.",
            "Precision control for adv-33 — the group's own correct mean."),

        # ── Count and share sanity ────────────────────────────────────────
        AdversarialCase("adv-35", "retail", "targeted_statistic", True,
            f"There are {r_rows * 3:,} orders in the dataset.",
            "A row count three times the dataset's own size. Arithmetically "
            "impossible, not merely unlikely."),
        AdversarialCase("adv-36", "retail", "targeted_statistic", False,
            f"There are {r_rows:,} orders in the dataset, of which "
            f"{r_north_share:.1f}% came from the north region.",
            "Precision control: the true row count and the true share, in the "
            "phrasing most likely to trip a count check."),
    ]
