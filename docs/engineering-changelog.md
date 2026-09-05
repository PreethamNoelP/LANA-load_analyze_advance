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

---

## 2026-08-21 — Make validator boundaries explicit, and document the provenance trace

**Problem.** The previous entry's findings (the attribution blind spot, the
scope gaps) lived only in this file. `validate_answer()`'s actual coverage
was real but implicit — a reader had to go through the source to learn what
it does and doesn't catch, and a user of the app had no way to see it at
all.

**Solution.**
- Added `VERIFIED_CLAIM_TYPES` and `KNOWN_BLIND_SPOTS` — two plain-language
  constant tuples in `app/llm/validation.py` — plus `capability_summary()`,
  which returns them as a dict. This is the single source of truth: the API
  and the UI both read it directly rather than each independently
  describing the boundary in their own words.
- Added `GET /validator/capabilities` (`backend/main.py`), serving that
  summary.
- Added a collapsed-by-default "What LANA checks in every answer" panel to
  the Ask AI screen (`frontend/src/components/AskAI.jsx`), fetching from
  the new endpoint. Deliberately not a persistent badge on every message —
  the warning itself is the signal that matters on every answer; this is
  for whoever wants to know exactly what "verified" does and doesn't mean,
  once.
- Added `docs/provenance.md`: a five-step, code-and-line-referenced trace
  from question to displayed verdict, with a real worked example and the
  real organic attribution failure from the eval run as the concrete
  illustration of the boundary, rather than a hypothetical one.

**Why this approach.** The risk in documenting "what this verifies" in
prose is that the prose and the code drift apart the first time either one
changes. Making the API and the UI both read the same two constants the
validator itself defines closes that gap structurally — there is nothing
to keep in sync by hand.

**Tradeoff.** None identified — this is additive (new constants, new
endpoint, new UI panel, new doc) and touches no existing validation logic.

**Tests.** Added `test_capability_summary_names_both_the_scope_and_the_boundary`
(`tests/test_data_science.py`) and `test_validator_capabilities_endpoint`
(`tests/test_smoke.py`). Full suite: 84 passed (was 82). Frontend build
verified clean (`npm run build`). The live endpoint was hit directly with
`curl` against a real running server to confirm the actual JSON shape, not
just that the code imports without error. The new UI panel's rendering was
not visually verified in a browser — no browser tooling was available in
this session; worth opening the Ask AI screen once to confirm the toggle
looks and behaves as intended.

**Result.** The boundary from the previous entry is now something a caller
can point to — in the API response, in the UI, and in a document that
quotes the exact same source instead of restating it.

---

## 2026-09-05 — Close the two named context scope gaps

**Problem.** The 2026-08-19 eval entry named two specific, non-hallucination
failures as scope boundaries rather than bugs: `build_context()` never
surfaced a regression coefficient (only Pearson `r`), and `_group_summaries()`
deliberately excluded discrete-coded columns (a 1-5 rating, say) from being
averaged by group — so "average rating by region" and "what's the
regression coefficient" had no fact to answer either question from, no
matter how the model was prompted.

**Solution.**
- `_group_summaries()` (`app/llm/context.py`) now also averages
  discrete-coded numeric columns by group, in addition to continuous ones —
  capped at `MAX_GROUPBY_DISCRETE_NUMERICS = 1` and added on top of the
  existing continuous-numeric budget, not instead of it, since the encoded-
  scale case is supplementary. Each such line is explicitly labelled
  `[encoded scale — average is illustrative, not a continuous measurement]`
  rather than presented with the same implied precision as a real
  measurement — the exclusion from *outlier* analysis
  (`supports_outlier_analysis`) was always correct and is untouched; this
  only lifts the exclusion from *group averaging*, which is a different
  question with a different answer. Also guards against grouping a column
  by itself, now that a discrete column can appear on both sides.
- A new `_regression_lines()` fits `perform_linear_regression()` (the
  existing, already-tested regression module — no new statistics were
  written) for the top `MAX_REGRESSIONS = 2` already-significant correlated
  pairs, and adds the coefficient, intercept, R² and CI95 as both prompt
  text and `Fact`s. Bounded to 2 pairs deliberately: an OLS fit costs more
  than a correlation coefficient, and this only needs to answer "what's the
  slope for the relationship that already matters," not run an exhaustive
  regression scan.

**Why this approach.** Both gaps were already precisely named in the
2026-08-19 entry, which is what made this a scoped fix rather than open-
ended: no new statistical method was invented, no new validator logic was
touched, and both additions reuse functions (`perform_linear_regression`,
the existing `discrete_code`/`is_numeric_measure` profile flags) that were
already in the codebase and already tested elsewhere.

**Tradeoff.** None identified against the two gaps named. A real, adjacent
limitation this does *not* address: which of the two correlated columns
becomes `x` (predictor) vs `y` (outcome) in the regression line is an
arbitrary convention (`column_a` as `x`, `column_b` as `y`, matching
`generate_recommendations()`'s existing convention) — correlation itself
carries no directionality, and neither does this fit. The prose says so
("an association in this data, not a causal effect"), consistent with the
regression module's own existing caveat language.

**Tests.** Added 4 tests to `tests/test_data_science.py`'s LLM-grounding
section: discrete-coded group averages appear and are labelled, a column is
never grouped by itself, a real relationship produces a regression fact
with the right coefficient sign and magnitude, and pure noise produces none.
Full suite: 121 passed (was 117).

---

## 2026-09-05 — Catch an explicit, literal mislabeling — not the paraphrase kind

**Problem.** The attribution blind spot named in the 2026-08-19 entry and
`docs/provenance.md` is real: `validate_answer()` checks whether a *value*
matches any fact, never whether the *label* attached to it is the one that
fact actually belongs to. The organic failure that surfaced it — a model
answering "59.8% work remotely" using the `remote=False` share instead of
`remote=True` — is a natural-language attribution problem in the general
case: the model's sentence never wrote the literal category value at all,
it paraphrased it ("work remotely" vs. the boolean's actual `True`/`False`).
Solving that in general needs real language understanding, which is exactly
the kind of thing this project has deliberately avoided bolting on as a
fragile heuristic or an extra LLM call dressed up as ground truth.

**What was actually tractable.** A narrower, honestly-scoped version of the
same check *is* deterministic: when the model's answer explicitly names a
*different, literal* category value near the number than the one the
matched fact belongs to — not a paraphrase, the actual value — that's a
real, checkable signal.

**Solution.**
- `Fact` (`app/llm/context.py`) gained three optional fields: `category`
  (the literal level value a fact is scoped to), `category_column` (the
  column that level belongs to — for a group-by fact this differs from
  `column`, which is the value being averaged), and `family` (groups every
  fact that is a direct alternative to this one: same statistic, same
  columns, different category). Populated at all four places a category-
  scoped fact is created: per-column category/discrete-code counts,
  category-breakdown percentages, and group-by means/totals.
- `validate_answer()` (`app/llm/validation.py`) now runs `_attribution_check`
  on every value-matched fact that has a `family`: it looks in an 80-
  characters-before / 40-after window around the matched number for a
  *sibling* fact's category named literally, while the matched fact's own
  category is never named in that same window. Only flags when the correct
  label is absent and a wrong one is present — a sentence that names both
  (a legitimate comparison, "north is $267 vs south's $190") is left alone
  on purpose, since that's exactly the case where flagging would be wrong.
  A new `misattributed` status sits alongside verified/derived/unsupported
  and does not count toward `verified_count` — it produces its own warning
  naming exactly which sibling was implicated.

**Why this approach.** Tested against the actual documented failure first:
a literal-value version of it ("...records have remote=False" for what is
actually the True share) is caught; the real paraphrased original is not,
and is not claimed to be. `VERIFIED_CLAIM_TYPES`/`KNOWN_BLIND_SPOTS`
(`app/llm/validation.py` — the single source the API, the UI panel, and
`docs/provenance.md` all quote) were rewritten to say precisely that:
catches an explicit wrong category name, not a paraphrase, and a
correctly-computed number can still be discussed under the wrong column or
category entirely without ever naming either literally. Overstating this
check would be a worse outcome than the honest gap it replaces.

**Tradeoff.** A wider text window would catch more explicit mislabelings
that sit further from the number, at the cost of more false positives from
unrelated mentions in a long paragraph. 80/40 characters was chosen to
comfortably cover "the north region average is $267" and "$267 in the
north region" without reaching into a neighbouring sentence; not tuned
against a labelled corpus, since none exists for this specific pattern yet.

**Tests.** Added 5 tests to `tests/test_data_science.py`: an explicit wrong
boolean label is caught, the correctly-labelled side is accepted, a
same-sentence two-sided comparison is correctly left alone, the same check
works for a group-by mean (not just a category share), and — the one that
matters most for honesty — a paraphrased mislabel ("work remotely") is
confirmed to still pass as verified, demonstrating the documented boundary
rather than asserting it. Full suite: 126 passed (was 121).

**Not yet done.** This was not re-run against the live `eval/` harness (it
would need a fresh Ollama session and 40 real model calls); the deterministic
tests above are the same tier of evidence the validator's original
adversarial suite (`eval/adversarial.py`) uses to measure precision/recall
in isolation, which is the right tier for a change entirely inside
`validate_answer()`'s own logic rather than model behaviour. Worth a live
`python -m eval.run` at some point to see whether real model answers ever
produce an explicit literal mislabeling in practice, or whether the
paraphrase case dominates in the wild.
