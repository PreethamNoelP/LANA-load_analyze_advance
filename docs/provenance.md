# Provenance: how an answer traces back to the data

Every answer LANA gives to a question follows the same path, and every step
of it is one specific, inspectable place in the code — not a black box.
This is written to be read alongside the source, not instead of it. File
and function names below are exact.

## The trace

1. **The question arrives.** `POST /query` (`backend/main.py`, `query()`).
   The question is a plain string; nothing is inferred from it yet.

2. **The context is built from the data, before the question is even
   looked at.** `build_context()` (`app/llm/context.py`). This runs first
   and produces a `GroundedContext`: the prompt text the model will read,
   plus a parallel list of `Fact` objects — each one a label and a number
   that was actually computed by `app/data/profile.py` or
   `app/analysis/statistics.py`, not written by hand. If the active data
   version was cleaned, the cleaning ledger's own narrative
   (`CleaningLedger.narrative()`, `app/data/lineage.py`) is folded into the
   same context, so a fact is never quoted without also disclosing that it
   rests on transformed data.

3. **The model answers using only that context.**
   `LLMProvider.answer_question()` (`app/llm/base.py`), with
   `ANSWER_SYSTEM_PROMPT` and the context text from step 2. The model sees
   the *facts* computed about the data — never the raw rows.

4. **The claim is extracted and checked against the same facts.**
   `validate_answer()` (`app/llm/validation.py`). Every number in the
   model's raw answer is pulled out with the product's own number-detection
   regex (not a second, separately-written parser), then compared against
   the `Fact` list from step 2, within a 2% tolerance. Each number lands in
   exactly one of four states:
   - **verified** — matches a fact within tolerance, and (for a fact scoped
     to one category of a column) the text doesn't explicitly name a
     *different* category of that same statistic near the number.
   - **misattributed** — matches a fact within tolerance, but the text
     nearby explicitly names a different, sibling category instead of the
     one this fact actually belongs to (e.g. quoting the `region=north`
     figure while the sentence says `south`). Added 2026-09-05; see the
     example below for exactly what it does and doesn't catch.
   - **derived** — inside the observed range of a column mentioned nearby,
     so it's plausibly a real calculation, just not an exact fact match.
   - **unsupported** — matches nothing, and falls outside every column's
     observed range.

5. **The verdict is shown, not silently absorbed.**
   `frontend/src/components/AskAI.jsx`. Unsupported claims, misattributed
   claims, and unknown references all produce a visible warning; verified
   figures are noted quietly (a small "N figures verified" line) — the
   signal a user actually needs is the exception, not a wall of
   confirmations.

## A real example

Question: *"What is the average revenue for orders from the north region?"*

- Step 2 produces the fact `mean revenue for region=north = 267.521`,
  among many others, before the question is read.
- Step 3: the model answers *"Orders from the north region average $267.52
  in revenue."*
- Step 4: `267.52` is extracted and matched against `267.521` within 2%
  tolerance → **verified**.
- Step 5: the UI shows the answer with a quiet "1 figure verified against
  the data" line. No warning, because none is needed.

Now contrast that with a real failure this exact pipeline produced during
evaluation (full account in `docs/engineering-changelog.md`): asked what
percentage of employees work remotely, the model answered `59.8%` — which
*is* a real fact (`share of remote=False in percent = 59.8`), attached to
the wrong label. At the time, step 4 verified the number only, not which
label it was attached to, so this passed as verified even though the
answer was wrong.

As of 2026-09-05, step 4 also checks this — but only for a literal,
explicit mislabeling. If the model instead wrote *"59.8% of records have
`remote=True`"* (naming the wrong side of the split by its literal value),
that would now be caught and marked **misattributed**, because `False` —
the category `59.8` actually belongs to — is never named nearby while
`True` is. The organic failure above would still pass as verified today,
because *"work remotely"* is a paraphrase, not the literal value `False`
or `True` — the check matches an explicit category name in the text, not
what the model's prose implies. That paraphrase gap is the trace's
honestly-documented remaining limit, and the reason the section below
still names it.

## What this verifies, and what it does not

Sourced directly from `VERIFIED_CLAIM_TYPES` and `KNOWN_BLIND_SPOTS` in
`app/llm/validation.py`. The `/validator/capabilities` endpoint and the
"What LANA checks" panel in the Ask AI screen both read the exact same two
lists — this section is not an independent description that could quietly
drift from what the code does.

**Verifies:**
- A number in the answer matches a fact LANA computed, within a small
  rounding tolerance (2% relative).
- A quoted or backticked column or category name that does not exist in
  this dataset is caught as an unknown reference.
- For a number scoped to one category of a column (a share-of-category
  percentage, a count, or a group-by mean/total): the text near that
  number does not explicitly name a *different* category of the same
  statistic while omitting the correct one.

**Does not verify:**
- A real, correctly-computed number attached to the wrong label, where the
  mislabeling is a paraphrase rather than an explicit category name — for
  example, describing a boolean column's False-share number using words
  like "not remote" rather than literally writing "False". The check
  above only catches an explicit, literal wrong category name near the
  number; it cannot recognise a paraphrase, and a correctly-computed
  number can still be discussed using the wrong column or category
  entirely (not just the wrong level of the right one) without ever
  naming either literally.
- A wrong-but-plausible value that happens to fall inside a column's
  observed range (marked "derived", not flagged, because a legitimate
  calculation can land anywhere in that range too).
- A non-numeric claim — a causal statement, a comparison, a
  recommendation — with no number in it to extract and check at all.

A validation layer that quietly promises more than this list would be the
actual risk. This one names its edge on purpose.
