# Engineering changelog

A running record of substantive changes to LANA, in the order they happened.
Each entry states the problem, what was tried, what actually worked (or
didn't), and the evidence behind that conclusion. The point of writing this
down is to keep the project's history explainable — including the dead ends —
not just to record what shipped.

---

## 2026-08-19 — Build the evaluation harness

**Problem.** LANA's claim to reduce LLM hallucination on numeric questions
(`app/llm/context.py` + `app/llm/validation.py`) had never been measured
against a real model. The existing tests (`tests/test_data_science.py`)
check the validator against hand-picked, hardcoded answer strings — useful,
but they can't tell you how the *actual* grounding-plus-validation pipeline
behaves against a real model's real output.

**Solution.** Added `eval/`, a self-contained harness with no changes to
existing code:

- `eval/datasets.py` — two seeded synthetic datasets (deterministic, not
  hardcoded tables — generated from a fixed numpy seed so the same run
  always produces the same data).
- `eval/ground_truth.py` — resolves each question's correct answer by
  calling LANA's own `compute_statistics`, `analyze_correlations`,
  `perform_linear_regression` on the live dataframe, so "ground truth" means
  "what LANA's own fact ledger says," not a separately hand-computed number
  that could quietly disagree with the app.
- `eval/cases.py` — 40 labeled questions across both datasets: aggregation,
  group-by, statistical, correlation (each with a real relationship *and* a
  negative control), regression, unanswerable-from-data, questions phrased
  to tempt a fabricated answer, rounding/percentage, multi-number, and edge
  cases (an identifier, a discrete rating code, a constant column, an
  entirely empty column).
- `eval/adversarial.py` + `eval/grading.py` — 14 scripted right/wrong
  answers, fed straight into the real `validate_answer()` with no model
  call, to measure the validator in isolation (precision/recall/F1) and to
  measure, honestly, the failure modes it does *not* claim to catch (a real
  number attributed to the wrong column, a wrong-but-plausible in-range
  value, a non-numeric causal claim).
- `eval/harness.py` + `eval/run.py` — runs every case through the real,
  running `phi3:mini` under two conditions: `baseline` (the context LANA
  itself used before grounding existed — `generate_context()` — plus a
  generic system prompt, no validation) and `lana` (the current
  `build_context()` + `ANSWER_SYSTEM_PROMPT` + `validate_answer()`).

**Why this approach.** Reusing the app's own statistics and number-extraction
code for grading (rather than a second, independently-written parser) means
the benchmark can't silently disagree with the product about what a
"correct answer" or "a number claim" even is. Using the project's own prior
context-building code as the baseline, instead of an invented strawman,
means the comparison is against something real LANA actually shipped, not
against a deliberately weak alternative.

**Tradeoff.** The harness pins the model (`phi3:mini`) and generation
parameters explicitly rather than reading the developer's `.env`, so results
stay reproducible even if local config changes — but that also means it
only measures one model unless someone deliberately runs it against another.

**Result.** On the very first smoke test (`--limit 8`), it found a real bug
— see the next entry.

---

## 2026-08-19 — Fix systematic over-refusal in the grounded answer path

**Problem.** The first eval smoke test showed LANA answering *worse* than
the naive baseline on trivial questions the grounded context answers
directly — e.g. asked for the mean revenue (`mean 221.885` stated plainly in
`build_context()`'s output), the model replied *"The provided data does not
include... It has revenue totals by region only"* — a sentence lifted
almost verbatim from the one worked example in `ANSWER_SYSTEM_PROMPT`
(`app/llm/base.py`). 6 of 8 smoke-test questions failed this way.

**First attempt (partial fix).** Reworded rule 2 of `ANSWER_SYSTEM_PROMPT`
to add a "check before you refuse" instruction, a contrasting example of the
present-fact case, and an explicit instruction not to reuse the example's
exact wording. This worked at what it targeted: re-running the same 8 cases
showed the model no longer copying the fixed phrase — every refusal was now
phrased differently per question. **It did not fix the underlying problem**
— the model was still refusing on facts that were provably present in the
context (verified directly: printed `build_context()`'s actual output and
confirmed `mean 221.885`, `std 221.343`, and every group-by mean it denied
having were stated in plain text).

**Root cause, found by isolating the variable.** A three-way manual test —
identical context and question, with (a) the full system prompt, (b) a
one-line minimal system prompt, (c) no system prompt — answered correctly
in all three cases, contradicting the harness's own failure on the same
question. The difference: the harness calls `LLMProvider._build_prompt()`,
which appends a trailing sentence after every question:
*"Answer using only the facts above. If they do not contain what is needed,
say so explicitly and name what is missing."* Reproducing that exact
trailing sentence reproduced the failure on 3/3 repeated tries. The
prompt's own design comment says "models attend most reliably to the end of
a long prompt" — which is exactly why this backfired: the refusal branch
was the literal last thing the model read before generating, on every
question, regardless of whether refusal was the right answer.

**Fix.** Reworded the trailing sentence in `_build_prompt()` (`app/llm/base.py`)
to end on a search-and-answer instruction instead, with refusal explicitly
framed as the last resort rather than modelled as the sentence to produce:
*"Search COLUMNS, CATEGORY BREAKDOWNS, and GROUP AVERAGES above for the
exact fact this question needs, and state it directly when you find it.
Treat 'not available' as a last resort, not a default."*

**Why this approach.** Tested two candidate rewordings head-to-head before
committing to either. The rejected candidate (moving the refusal clause
earlier, positive instruction last) actually made things *worse* on one
case — the model claimed the fact was "not explicitly stated," then
fabricated a nonsensical derived value (`221.885 / 500 = 0.44377`) instead
of just quoting the number that was right there. That's a more dangerous
failure than over-refusal (a wrong number instead of an honest non-answer),
so the search-and-answer wording was chosen specifically because it fixed
retrieval *without* introducing that new failure mode — verified against
both a genuinely unanswerable question (still correctly refused) and every
originally-failing case (all correct).

**Tradeoff.** None identified yet against the cases tested; the honest
caveat is that this was validated on 8 cases plus targeted manual probes
before the fix, not yet the full 40-case suite — see the result below.

**Tests.** `tests/` (82 tests) re-run and pass unchanged after both edits —
neither touches behavior any existing test asserts on. No new unit tests
were added for this specific fix; the eval harness itself is the test that
caught and validated it, and its 8-case before/after transcripts are the
evidence (`eval/results/run_1787103714.json` → `run_1787154444.json`).

**Result.** On the 8-case smoke set: LANA's correct rate went from **1/8 to
8/8**; baseline stayed at 5/8 (unchanged, as expected — it wasn't touched).
Full 40-case run below.

---

## 2026-08-19 — Full 40-case run, a grading bug found in the harness itself, and the honest remainder

**Full-suite result (after the fix above).** `python -m eval.run`, real
`phi3:mini`, 40 questions × 2 conditions:

| | baseline | lana |
|---|---|---|
| correct | 21/40 (52.5%) | 33/40 (82.5%) |
| hallucinated | 5/40 (12.5%) | 1/40 (2.5%) |

**A bug in the grader, caught before trusting the first pass of these
numbers.** The first raw run showed LANA at 30/40 with 4 hallucinations. Three
of those "hallucinated" verdicts were wrong on inspection — the model had
in fact refused correctly (e.g. *"The facts above do not include
per-customer revenue... it is not possible to calculate the exact total
revenue for next quarter"* — a legitimate decline on a forecasting
question). `eval/grading.py`'s refusal detector matched "does not include"
but not "do not include" (correct for a plural subject), and "not possible
to determine" but not "...to calculate." Fixed by replacing the literal
phrase list with a small set of regex patterns covering the verb forms that
actually appeared (`eval/grading.py`), verified against the four exact
strings that had been mis-graded, then **re-graded the already-collected
transcripts with `eval/regrade.py` — no new model calls** — to get the
corrected numbers above. This is also recorded so a future reader doesn't
mistake the harness's own bug for a model or product finding.

**What's still wrong, and why it matters more than the headline number.**
7 of 40 LANA answers remain wrong. The validator's own trust layer caught
exactly 1 of them (the one with numbers wildly outside any observed range —
`survey-11`). The other 6 fall into three categories that are worth naming
precisely, because they are exactly the boundary Priority 3 asked for:

1. **Scope gaps, not hallucinations** (`retail-11`, `survey-11`, `survey-08`)
   — `build_context()` never surfaces a regression coefficient (only Pearson
   `r`), and `_group_summaries()` deliberately excludes discrete-coded
   columns (e.g. a 1–5 rating) from being averaged by group. Questions that
   need either of those facts cannot be answered correctly from LANA's
   context today — not a bug, a genuine scope boundary worth documenting
   (and a real candidate for what to ground next).
2. **Right number, wrong attribution** (`survey-12`) — asked what percentage
   of employees work remotely, the model answered **59.8%**, which is a
   real fact in the context (`remote: False=239 (59.8%)`) attached to the
   wrong label (true answer: 40.2%). This is precisely the blind spot the
   adversarial suite (`adv-10`/`adv-11`) predicted from scripted cases — now
   confirmed happening organically with the real model. `validate_answer`
   does not catch it, by design: it checks whether a *value* matches any
   computed fact, not whether the *label* attached to it is the right one.
3. **Two smaller residual misses**: `retail-13` (a null-count stated inline
   within a column's description line, rather than under its own heading,
   was still missed) and `retail-19` (the model pulled numbers from a
   similarly-worded but semantically different `GROUP AVERAGES` entry —
   mean *customer age* broken down *by* rating — instead of rating's own
   directly stated mean). `retail-20` (averaging the `order_id` identifier
   despite an explicit "not meaningful" annotation) is the one remaining
   `hallucinated` verdict.

**Why this is reported this way.** The honest version of this result is not
"82.5% accurate" alone — it's 82.5% accurate, a validator that only catches
1 of the remaining 7 failures, and three specifically-named reasons why,
one of which (attribution) the system doesn't currently claim to solve at
all. That distinction is the actual deliverable from Priority 1: a
measured, falsifiable claim instead of "the AI is grounded in your data."

**Tests.** 82 existing tests unaffected (no product code touched in this
entry beyond what the previous entry already covered). `eval/regrade.py` is
new harness tooling, not a product change.

**Not yet done.** The scope gaps and the attribution blind spot are findings,
not fixes — deciding whether/how to close them (extend `build_context` to
include regression coefficients; make attribution checkable) is a separate,
larger change than this entry, and hasn't been made yet.
