# Engineering changelog

A running record of substantive changes to LANA, in the order they happened.
Each entry states the problem, what was tried, what actually worked (or
didn't), and the evidence behind that conclusion. The point of writing this
down is to keep the project's history explainable — including the dead ends —
not just to record what shipped.

---

## 2026-09-20 — Grounding isolation, the SSRF guard that wasn't, deployment posture, auditability

A second audit round. Every finding below is a case where the code and the
documentation disagreed, and in each one the documentation was the optimistic
party. That pattern is the thing worth recording: this codebase's comments are
unusually good, and *that is exactly what made these hard to see* — a
well-argued paragraph describing a defence reads like evidence the defence
exists.

---

### 1. Executed-SQL facts leaked across questions — the worst bug found

**Problem.** `build_context()` is expensive (profiling plus a correlation scan,
~2.1 s on a 200k x 30 frame) so its result is cached per session version. The
executed-SQL path needs its query's figures in the fact list for the validator
to credit them, and did it like this:

```python
context = _build_query_context(session)   # the cached object
context.facts.extend(sql_answer.facts)    # mutated in place
```

The comment directly above it said *"Executed facts are appended to a copy of
the ledger's fact list"*. There was no copy. So question 1's query results
stayed in the shared context forever, and question 5 was validated against
question 1's evidence as well as its own.

**Why this is the worst one.** `validate_answer` marks a number verified when
it matches any fact in the context within 2% relative tolerance. With stale
facts accumulating, a figure the current answer had no basis for could collide
with a leftover from an earlier question and be shown to the user with the
green *"verified against the data"* mark. A warning that never fires costs a
user nothing. A verification badge on a fabricated number costs them the
reason they trusted the tool — which is the specific failure the entire
validation layer exists to prevent. Two lesser consequences came with it:
unbounded per-session growth, and two concurrent questions mutating one list.

**Solution.** `_with_sql_facts()` in `backend/main.py` returns
`dataclasses.replace(context, facts=[*context.facts, *sql_answer.facts])` — a
new `GroundedContext` with a fresh list, sharing text, ranges, vocabulary and
coverage by reference because the validator only reads those. One small list
per question instead of a rebuild.

**Evidence.** `tests/test_grounding_isolation.py`. The tests were verified to
fail against the original code before being accepted: reinstating the in-place
extend fails three of them, including the end-to-end one that asks two
questions and inspects the session's cached context afterwards.

---

### 2. The SSRF guard documented four layers and implemented three

**Problem.** `app/sources/security.py` opens with a careful account of four
guards, ending: *"4. The connection is pinned to the address that was
validated, which closes the DNS-rebinding window between the check and the
connect. Guard 4 is the one people skip."* SECURITY.md repeated the claim.

`CheckedUrl.ip` was computed and never read. `check_redirect_chain` was dead
code. `RestSource._get` called `check_url(current)` and then handed
`checked.url` — the *hostname* — to `httpx`, which performed its own DNS
lookup at connect time. Two resolutions: one checked, one used. A hostname
with a one-second TTL answers the first with a public address and the second
with `127.0.0.1`, and every other guard is bypassed.

**Solution.** `CheckedUrl` now carries every validated address and can render
a pinned URL; `RestSource._open` builds the request against an IP literal with
`Host` naming the original site and the `sni_hostname` extension set so TLS
still verifies the certificate against that name. All validated addresses are
tried in turn — they were all checked, and a host whose first record is
unreachable from this network (an AAAA on an IPv4-only host) must still work.

**The tempting wrong fix, stated because it is the obvious one.** Pinning
breaks HTTPS unless SNI is handled, and the quick way to make a pinned HTTPS
request "work" is to disable certificate verification. That trades a
rebinding window for a permanent man-in-the-middle. `sni_hostname` is what
makes pinning and verification coexist, and a test asserts it is set rather
than asserting the fetch merely succeeded.

**Evidence.** `tests/test_ssrf_pinning.py` asserts the destination is an IP
literal (so no second lookup can influence it), that `Host` and SNI still name
the site, that a 302 to `169.254.169.254` is refused before a socket opens,
and that a second address is tried when the first is unreachable.

---

### 3. `docker compose up` published an unauthenticated API on the network

**Problem.** SECURITY.md's first bullet: *"It binds to localhost."* The Compose
file said `- "8000:8000"` and `- "8080:80"`, which publish on every interface,
and `LANA_AUTH_TOKEN` defaults to empty. So the documented posture and the
shipped default were opposites, and the gap was invisible to anyone who read
either one alone.

**Solution.** `127.0.0.1:` prefixes on all three published ports, including
the opt-in Ollama service — which has no authentication of its own and, once
exposed, is a free inference endpoint for anyone who can route to the host.
Reaching LANA from elsewhere is now a deliberate edit with the reasoning
written next to it.

**Also added:** one `security posture` log line per worker at startup, naming
auth mode, persistence, SQL grounding, whether private source URLs are
permitted, and how many file roots are configured. An operator who inherits a
running LANA cannot otherwise tell whether the person who deployed it turned
the SSRF guard off. A specific warning fires for the one combination that is a
live exposure rather than a choice: no token, with CORS opened to a non-local
origin.

---

### 4. The `file` connector was an arbitrary server-side read

**Problem.** Not documented anywhere, and the only finding here that was a gap
rather than a contradiction. `FileSource` reads a caller-supplied path on the
server's disk. On a laptop that is the feature. With `LANA_AUTH_TOKEN` set —
the shared deployment the project explicitly supports — `POST /sources/load`
with `{"kind": "file", "target": "/srv/other/export.csv"}` returns that file as
a dataset. The extension allowlist is no defence: CSV and JSON are exactly what
interesting files are in.

**Solution.** The policy is keyed on the project's own existing signal for
"more than one person can reach this" rather than a second switch that could
disagree with the first. No token: unrestricted, unchanged.
`LANA_FILE_SOURCE_ROOTS` set: confined to those directories.
Token set with no roots: the connector reports itself *unavailable* with the
variable named, so the UI greys it out with a reason.

Refusing to guess is the point. Silently exposing the filesystem and silently
removing a feature someone was using are both worse than saying which
configuration is missing.

Containment is decided on `Path.resolve()`d paths — a string comparison is
defeated by `..` or a symlink — and re-checked on every access rather than
once at construction, because a symlink can be repointed in between.

---

### 5. The product's strongest feature was invisible in the product

**Problem.** The 0.2.0 changelog told users: *"The query and its result travel
with the answer, so you can read exactly where a number came from — and re-run
it yourself."* That was true of the API. `/query/stream` emits a `sql` event
carrying the statement, the columns, the rows and the timing. `App.jsx`
handled `delta` and `validation` and silently ignored `sql` and `grounding`,
so nothing reached the screen.

This is the gap that matters most for the product rather than for security.
The entire argument for LANA over a general chatbot is that its figures are
computed rather than recalled, and the evidence for that was being discarded
one layer from the user.

**Solution.** A `Provenance` component in `AskAI.jsx`: a badge on every answer
(*Computed by query* / *From computed summary*) and, for executed answers, a
collapsed panel holding the exact statement and the result table. Collapsed
because the badge is the everyday signal and the table is for the moment
someone wants to check the work.

The backend now also emits `{"grounding": "sql"}` on the SQL branch. It only
emitted it on the ledger branch before, so a client had to infer the SQL path
from the *absence* of an event.

**One thing the verification found that tests would not have.** Driven in a
browser against real data, `avg_revenue` rendered as `267.5210191082802`.
The table exists so a reader can compare it against the answer's stated
"267.52" — printing sixteen significant figures beside that makes the
comparison harder than not showing the table at all. Values are now formatted,
with small magnitudes (a correlation, a p-value) kept at six decimals so they
do not collapse to `0.00`.

---

### 6. Auditability

**Problem.** The structured log answers *"why was yesterday slow"*. Nothing
answered *"who exported that dataset, and when"* — logs go to stdout and live
as long as the container.

**Solution.** `app/audit.py`: append-only JSONL under the data directory,
rotated at 8 MB. A deliberately small closed set of actions — data loaded,
cleaned, version-switched, questioned, exported, plus refused access and
failed authentication — so the file can be read end to end by a person rather
than grepped by an engineer.

**What it does not record, and why that is the harder decision.** No cell
values, no column names, no secrets, no token. An audit trail that copies the
data it audits is a second, less protected copy of that data, which is how
audit logging becomes the breach. Cleaning records operation *types* and row
counts; which columns were filled and with what stays in the lineage ledger,
which already travels with the data. Every detail string goes through the
connector redaction on the way in, because the thing most likely to arrive
here by accident is a connection label built from a URI with a password in it.

`GET /audit` is scoped to the calling principal, always — reading the trail
must not become a way to observe someone else's activity. It reports
`enabled` explicitly, because "nothing happened" and "nothing is being
recorded" are very different answers.

**A bug the tests found.** A process killed mid-write leaves a line with no
terminator, and appending straight onto it fused the fragment and the next
entry into one unparseable line — so a crash cost *two* records, the second
of which nobody would know was missing. `record()` now closes a torn line
before appending.

**Stated limit.** This is a file under a process lock, not a tamper-evident
ledger. It answers what this instance did for an operator reading it; it is
not evidence against someone with write access to the disk.

---

### What is still missing after this round

- **No real user accounts.** Sessions are owned, but with `LANA_AUTH_TOKEN`
  everyone shares one principal, so two colleagues on one instance still see
  each other's sessions. This is the largest remaining gap for enterprise use
  and it needs actual authentication, not another refinement of the token.
- **Charts are server-rendered PNGs.** No zoom, no hover, no brushing, and a
  chart cannot be exported as data.
- **Exports carry the profile and the lineage but not the conversation.** The
  questions asked and the queries that answered them — now visible in the UI —
  do not reach the PDF or the Word report.
- **Persisted sessions are unencrypted at rest**, as stated in SECURITY.md.
- **Multi-host replicas remain unsupported.** Coordination is filesystem-based
  by deliberate choice.

---

## 2026-09-17 — Executed-SQL grounding, validator recall, connectors, production hardening

A single round addressing an external audit. Its findings are quoted where they
drove a change, because several were right in ways that were uncomfortable to
read.

---

### 1. The fact ledger's coverage ceiling — removed by executing queries

**Problem.** The audit: *"LANA's ledger approach has a hard ceiling: it can only
answer what `build_context()` happened to precompute, capped at
`MAX_CORRELATIONS = 8`, `MAX_REGRESSIONS = 2`, `MAX_DETAIL_COLUMNS = 30`."*
That is what the 2026-08-19 entry below had already measured from the other
direction: three of seven residual failures were "scope gaps, not
hallucinations". No prompt fixes a fact that was never computed.

**Solution.** `app/analysis/sql_engine.py` and `app/llm/sql_answer.py`. A
question is planned as one DuckDB `SELECT`, executed against the session's
frame, and answered from the *result table*. The number is computed from the
rows rather than retrieved from a summary — a strictly stronger form of
grounding than retrieval.

The ledger was **not** replaced. It still builds the context, still answers when
planning fails, and still supplies the column ranges and vocabulary the
validator judges surrounding prose against. `SqlPlanningFailed` is a
first-class outcome: the path is better when it works and must never be worse
than what it replaced when it does not.

**The security problem this creates, and the four layers in front of it.** A
model writing SQL the server executes is a code-execution path reached from a
text box, and DuckDB reads files, speaks HTTP via `httpfs`, and can `ATTACH`
databases. Layers: DuckDB with `enable_external_access=false` and
`lock_configuration=true`; a single-statement check using DuckDB's own parser;
a read-only shape check; a denylist of file-touching functions. Layer 1 is what
actually holds — verified directly: `read_csv_auto`, `COPY … TO`, `ATTACH` and
`INSTALL` all raise `PermissionException`, and re-enabling external access
raises `InvalidInputException`.

**A bug the tests found in the denylist.** The first version matched whole
words, so `\bread_csv\b` matched `read_csv` and **not** `read_csv_auto` — `_`
is a word character, so there is no boundary after the prefix. A local file
read passed validation and was stopped only by the sandbox, which is precisely
the single-layer situation the denylist exists to prevent. Fixing it by
matching prefixes then over-fired on a column legitimately named
`glob_region`. The final form matches a *called function* (identifier, optional
quote, `(`) for readers and a whole word for statement keywords. Both
directions are pinned by tests.

**Tradeoff, stated because it is real.** The SQL path costs two model calls
(plan, then answer) against the ledger's one, plus up to one repair attempt.
For a question the ledger could already answer, that is strictly more latency.
Accepted because the ledger's answer to a question it *cannot* cover is wrong
rather than slow, and `LANA_SQL_GROUNDING=false` turns it off.

---

### 2. Validator recall: 0.571 → 0.810, precision held at 1.000

**Problem.** The audit's sharpest finding: *"the validator — the thing LANA
claims as its differentiator — catches 14.3% of wrong answers"*, and the README
reported the 82.5% accuracy figure while leaving that number in this changelog.
Both halves were fair.

**Root cause.** Fact matching was *global*: a number within 2% of any of the
~100 facts in a context was stamped `verified` regardless of what the sentence
claimed it was. With eight columns spanning different magnitudes, almost
everything matches something.

**Solution.** `_targeted_statistic_check` resolves the claim the way a reader
does — "the average revenue is X" means `mean(revenue)` — and compares against
*that fact alone*. When it resolves, its verdict is authoritative and the global
scan is skipped, because a targeted comparison cannot be laundered by a
collision. Facts gained `statistic` and `provenance`, so a figure can also be
credited to an executed query rather than a precomputed summary.

**Two bugs this surfaced, both found by tests rather than inspection.**

1. *A correct answer flagged.* `"The average revenue is $221.88 per order; the
   average order is 2.3 items"` resolved `$221.88` against `mean revenue for
   rating=3` and reported the true mean as wrong. Cause: the category `"3"` is
   matched by `\b3\b`, which matches the `3` inside `2.3`. A bare-number
   category now requires its grouping column to be named too. This was
   `eval/adversarial.py`'s adv-20, which exists specifically as a precision
   control — it earned its place.
2. *Cue selection read left-to-right.* `"There are 500 orders … of which 31.4%
   came from the north region"` resolved `31.4` against the *count* cue opening
   the sentence and flagged a correct share. Cues are now ranked by distance
   from the number, and a trailing `%` settles it outright.

**Result.** Measured by `eval/validator_bench.py`, which loads any earlier git
revision of `validation.py` and scores it on the same cases, so the delta is
recomputed rather than asserted:

| | recall | precision | f1 |
|---|---|---|---|
| before | 0.571 | 1.000 | 0.727 |
| after | **0.810** | **1.000** | **0.895** |

The adversarial suite grew from 20 to 36 cases to measure this honestly — a
+0.077 delta on the old 20-case suite was one case, which is not evidence of
anything. Every new wrong case is paired with a correct one in identical
phrasing, because a check that gains recall by flagging both is not an
improvement, and only the pairing makes that visible.

**What is still missed, and why it is counted as a miss.** Four of 36: two
causal claims with no number to check, and two *paraphrases* — "the oldest
customer" for `max(customer_age)`, "been at the company for" for
`years_at_company`. The targeted check matches names literally. Those two keep
`should_flag=True` rather than being reclassified out of the metric, so the
published recall reflects the gap instead of defining it away.

---

### 3. A benchmark that only ever saw clean data

**Problem.** The audit: *"No real-world messy dataset. Not evidenced in the
project."* Correct. Both seeded frames had correct dtypes, tidy labels and
missingness placed on purpose — a fair test of statistical correctness and an
unfair test of everything else.

**Solution.** `messy_support_tickets()`: numbers stored as text with currency
symbols and thousands separators, four date formats in one column, category
labels differing only by case and whitespace, five spellings of "missing" that
pandas does not read as NaN, exact and near-duplicate rows, an extreme outlier,
a constant column and an empty one. Ten cases run against it, resolved against
an explicit cleaned reference.

Scored **separately, never pooled**. Averaging clean and messy produces one
number describing neither, and the gap between them is the honest headline.
`--seeds 0,1,2` regenerates every dataset so a figure becomes a spread rather
than one convenient draw.

---

### 4. Concurrency, measured — and two real bugs it found

**Problem.** The audit: *"the memory-budget reasoning is impressive but
untested under contention"*. Also correct.

**Solution.** `eval/load_test.py`: p50/p95/p99 per endpoint under N concurrent
clients, RSS growth, and whether an over-budget upload is refused cleanly rather
than by dying. It prints its mode in every report, because in-process ASGI
numbers exclude the network and reporting them as end-to-end would be the kind
of overclaim this project documents against.

**First run, 16 concurrent clients, 196 requests: zero 5xx.** Admission control
returned 413 with an actionable message and kept serving. Two findings:

1. **`/health` p95 of 7.9s, p99 8.0s** — by far the slowest endpoint. Every call
   probed the LLM, and with Ollama stopped each probe paid a full connection
   timeout. A liveness endpoint that takes eight seconds *precisely when a
   dependency is down* is a healthcheck that fails the container for the wrong
   reason. Now cached for 5s behind a non-blocking lock.
2. **RSS reported 0.0 MB on Windows.** `GetCurrentProcess()` returns the
   pseudo-handle `-1`; passed as a bare Python int, ctypes marshalled it as 32
   bits and the call failed silently. A benchmark reporting zero memory is worse
   than one reporting nothing.

**Remaining, unfixed and stated:** `/chart` p95 is 7.2s under 16 clients.
Server-side matplotlib serialises, and it is the slowest thing LANA does.

---

### 5. Multi-worker: from an undocumented constraint to a supported one

**Problem.** The audit: *"`uvicorn --workers 2` silently breaks session affinity
**and** the concurrency cap. Nothing in the code or Dockerfile prevents this."*
The `threading.Semaphore` gave four workers four independent caps of two; the
in-memory dict gave them four disjoint session stores.

**Solution.** `backend/coordination.py` behind one interface —
`InProcessCoordinator` (unchanged single-user behaviour) and `SqliteCoordinator`
(leases and token buckets in the SQLite file persistence already uses, with
`BEGIN IMMEDIATE` around each read-modify-write). Plus read-through in
`SessionStore.get`: a worker that misses reads the frames another worker
persisted instead of returning 404.

Not Redis — adding a mandatory network service to make a concurrency cap correct
would be a cost paid by every single-user install for a deployment shape that is
not the common one.

**The boundary, stated rather than implied away:** this coordinates workers
*sharing a filesystem*. It is not a distributed lock service, and replicas
across separate hosts are not supported.

---

### 6. Auth that was never access control

**Problem.** `VITE_LANA_AUTH_TOKEN` was compiled into the JavaScript bundle. The
audit: *"anyone who can load the page has the token. It gates `curl`, not
browsers."*

**Solution.** `POST /auth/session` trades the token for an HttpOnly,
SameSite=Strict cookie. Page scripts cannot read it, an XSS payload cannot
exfiltrate it, and rotating it no longer means rebuilding the frontend image —
which the Dockerfile and compose file no longer accept a build arg for. Bearer
headers still work for API clients.

Sessions also gained an owner. A different principal gets **404, not 403** — 403
confirms the id exists and turns the endpoint into an enumeration oracle.
Ownership is read from a request-scoped `ContextVar` rather than a parameter
threaded through eighteen endpoints, because the endpoint someone forgets to
thread it through is the hole.

**A bug the tests found.** `/auth/status` was not exempt from the auth
middleware, so a browser could not ask whether a token was required without
already having one.

---

### 7. Observability, and the metric that double-counted

**Problem.** Five `logger.warning` calls in the whole codebase. No metrics, no
request ids. The audit: *"no way to answer why was yesterday slow"*.

**Solution.** `app/observability.py` — JSON logs with a request id on every
record via `ContextVar` (so it survives `run_in_threadpool`), and a Prometheus
registry. Stdlib only: a JSON formatter is forty lines and a counter is a dict
with a lock, matching the reasoning `app/resources.py` already applies to RAM
probing. Path labels are collapsed to `/session/{id}` so instrumentation cannot
mint one time series per upload.

**A bug the tests found.** `Histogram.observe` incremented every bucket the
value fell into, and `_render_series` accumulated cumulatively as well — so one
0.5s sample appeared three times in `le="10"`, and `le="+Inf"` disagreed with
the largest finite bucket. Storage is now non-cumulative; only rendering
cumulates.

---

### 8. Connectors: one contract, four sources

**Problem.** One way in — file upload — wired directly into `/upload`.

**Solution.** `app/sources/`: a `DataSource` ABC, a registry, and file, SQL
(SQLAlchemy), MongoDB and REST/URL connectors. A connector's only job is to
produce a `DataFrame` and describe itself; everything downstream is unchanged
and unaware. `GET /sources` is generated from the registry and the frontend
picker renders from it, so a new connector appears in the UI with no frontend
change. `tests/test_sources.py` drives every registered connector through one
conformance suite, and `test_a_connector_dataset_gets_the_full_pipeline`
asserts a SQL-backed session reaches profiling, cleaning, charts, statistics,
regression, lineage and export — so "same capabilities regardless of source" is
a fact about the code rather than a claim in a README.

**SSRF is the serious part.** "Analyse the data at this URL" hands an attacker
an HTTP client inside the deployment network; `169.254.169.254` returns cloud
credentials. Defence: scheme allowlist, *every* resolved address must be public
(checking one is not enough — a name with a public A record and a `127.0.0.1` A
record passes a first-match check), every redirect hop re-validated, and the
connection pinned to the checked address to close the DNS-rebinding window.

**Credentials** are supplied out-of-band from the connection string, never
echoed back, and redacted from every human-facing string.

---

### What this round did not fix

* `/chart` p95 of 7.2s under concurrency — matplotlib serialises.
* Paraphrased statistic and column references remain unvalidated (2 of 36).
* Prompt injection via a *column name*: the planner sees the schema, so a
  maliciously named column is still text it reads. The sandbox bounds the
  consequences; it does not remove the influence.
* The 82.5% accuracy figure is still one model, one seed, clean data. The
  infrastructure to widen it now exists and is documented; the runs need a
  machine with the models pulled, and are not in this round.

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

---

## 2026-09-12 — Audit follow-through: a shipping report bug, a budget that was
## simply wrong, and the first enforced tooling

**Problem.** A full-repo review turned up more than thirty findings. Most were
noise or nice-to-haves; a handful were real, and two were actively wrong in
ways no test would have caught.

**The report bug.** The exported PDF/Word "Data Profile" section was built by
`generate_context()`, which classifies columns by pandas dtype alone. So a
report printed `mean order_id = 1150` and full statistics for
`revenue__outlier_score` — LANA's own cleaning annotation — a few lines below
a "Numeric Column Summary" that correctly excluded both. The report is the one
artifact that travels to people who never used LANA and cannot spot that. Fixed
by giving `generate_context()` an optional `profiles` argument and passing it
from the report path. Deliberately *not* by changing its default behaviour:
`eval/harness.py` uses the naive, dtype-only mode as the baseline its published
52.5% figure is measured against, and quietly changing that would make the
recorded comparison incomparable. A test pins both modes.

**The budget that was wrong.** `CONTEXT_TOKEN_RESERVE` was a flat 1200 tokens,
commented as covering "the system prompt, the question, and the model's own
reply". Measured: the system prompt alone is 585 tokens and the model may
generate `LLM_MAX_TOKENS` (2048). The real reserve is ~2900, so a full context
plus a long answer overflowed an 8192 window by 1433 tokens — and Ollama
truncates from the front, discarding the DATASET FACTS while leaving the
question intact. The module exists to prevent exactly that, and a guessed
constant had reintroduced it. Now computed from the measured prompt plus the
configured answer budget, so editing either cannot silently invalidate it.

**Preview and apply disagreeing about outliers.** `detect_outliers` refuses
below `MIN_POINTS` because quantiles from a handful of points mean nothing, but
the apply path had no such guard. `/clean/preview` would report "not
applicable" for a column while `/clean/apply` used those same fences —
including `remove_outliers`, which deletes rows. A 5-row column lost 20% of the
dataset to a rule the detector itself called unusable. All three outlier
operations now refuse through the existing `ledger.skip()` path.

**Attribution, extended.** Column statistics had no fact `family`, so the
sibling check never ran on them — and `eval/adversarial.py`'s adv-10 (revenue's
true mean, labelled "marketing spend") was reported as **verified**. Not merely
missed: the validator was vouching for a wrong answer. Column stats now group
by statistic with the column as the category. Name matching accepts the prose
form of a column ("marketing spend" for `marketing_spend`) because that is how
models write them. The two searches are asymmetric on purpose — the correct
label is looked for in the whole sentence, a wrong one only in a narrow window
— because a false "misattributed" on a correct answer costs more than a missed
flag. Verified against four control phrasings that must not flag.

**A threshold that hid a real signal.** Unknown references only warned above
*two* of them, so an answer inventing a single plausible segment ("Revenue is
highest in the 'enterprise' segment") passed silently — adv-04. Lowered to one,
after checking it caused no false alarms on the suite's correct-answer cases;
the stoplist gained the statistical vocabulary a model routinely quotes, which
is what the threshold had been crudely standing in for.

**Uploaded data as an injection surface.** Category values are quoted into the
text the model reads, and a cell containing newlines could fake a section
heading. Values are now stripped of control characters and length-capped. This
is containment, not a fix, and `KNOWN_BLIND_SPOTS` says so: instruction-shaped
text is still text the model can obey. What holds is that every number LANA
reports is computed, so a figure the data talked the model into still fails
validation.

**Tooling, finally enforced.** A `.ruff_cache/` had been in the repo for months
— ruff run by hand, findings never enforced. The frontend had no linter, no
formatter and no tests at all. Both are now in CI, along with coverage and a
Python 3.11/3.13 matrix. Ruff found 28 real issues on first run (13 autofixed;
the rest mostly exception chaining discarded at ten `raise` sites inside
`except` blocks). ESLint found three errors including the `GRADE_COLORS`
cross-import this review had flagged independently. Two React-Compiler rules
are downgraded to warnings with reasons rather than obeyed: this app fetches in
effects because it has no data-fetching library and does not need one.

**Tradeoff.** Adding Vitest pulled in dev-only advisories (it depends on the
same Vite 5 the project already pins). `npm audit --omit=dev` is clean, so
nothing shipped is affected, and the alternative — no frontend tests at all —
was the larger risk. It does make the Vite major upgrade worth doing
deliberately, since it clears that whole cluster at once.

**Tests.** 156 backend tests (was 126) and 6 frontend tests (was 0). The
deterministic half of `eval/` — the adversarial suite, which makes no model
calls — now runs in pytest as `tests/test_adversarial_suite.py`, so the
precision the docs cite is actually gated. It sits at 11/14, up from 10/14,
with the three remaining misses being the `in_range` and `causal` buckets
`KNOWN_BLIND_SPOTS` names; the test fails if any of them silently starts
passing, because that would mean the capability text now understates what LANA
does.

**Not done, deliberately.** No rate limiting (a dependency, for a documented
localhost tool where size limits cover the real risk), no mypy (high noise on
pandas-heavy code without a large typing investment), no APIRouter split of
`backend/main.py` (a 745-line file worth splitting, but not as a drive-by
alongside correctness fixes), no Docker. The model-driven half of `eval/` was
not re-run: it needs a live Ollama and 40 completions, and none of these
changes alter what the model is asked — only what it is told and what happens
to its answer afterwards.

---

## 2026-09-12 — Closing the last attribution gap, and two things that were
## quietly corrupting the validator

**The gap that was left.** The previous round gave column statistics and
category breakdowns a fact family, so the validator could catch a real number
quoted under a sibling's name. Correlation and regression figures got nothing,
and `KNOWN_BLIND_SPOTS` said so plainly. That bullet is now gone.

The reason it took a second pass is that a correlation is not scoped to a
column, it is scoped to a *pair*, and the existing check compares a single
category name. Naming one half of a pair is not an attribution: "revenue
correlates at 0.72" says nothing about what it correlates with. So `Fact`
gained `category_names` — every name that must appear for the figure to be
correctly attributed — defaulting to `(category,)` so single-level facts are
untouched. Correlations form one family; regression coefficient, intercept and
R² are three, because an R² is not an alternative reading of a slope.

Naming half a pair is treated as too vague to be a mislabelling rather than as
an error, which keeps the asymmetry the rest of this check runs on: generous
about evidence the answer is right, strict about concluding it is wrong.

**A false positive this would have shipped.** Writing the tests surfaced
something the design had missed. Correlation coefficients cluster in a narrow
band, and the validator's 2% *relative* tolerance is tiny in absolute terms
down there — in the eval retail set, two pairs sit at 0.0674 and 0.0667, which
are within tolerance of each other. Matching takes the first fact that fits, so
an answer naming the second pair would have been flagged as misattributed while
quoting a number that legitimately matches it. A sibling whose own value also
matches the quoted number is now excluded from consideration. It applies just
as well to two regions with near-identical means; correlations are only where
it shows up first.

**The scratchpad problem.** Reasoning models (deepseek-r1, qwen3) emit their
chain of thought inline in `<think>` tags, and nothing stripped it. The obvious
cost is that the user reads it. The real cost is that `validate_answer`
extracts *every* number in the text, and a scratchpad is full of figures the
model considered and discarded — precisely the numbers that match no fact. A
test asserts the damage rather than describing it: two discarded figures
produce two unsupported claims and `trustworthy=False` on an answer that is
correct. The validator is only worth having if a warning means something.

Stripped in `LLMProvider.answer_question`/`answer_question_stream` so both
providers get it and a third inherits it. The streaming filter buffers across
chunk boundaries, because `<think>` genuinely arrives as `<thi` + `nk>` and a
per-chunk replace would pass the whole scratchpad through. Honest limit: this
was verified against the tag shape, not against a live reasoning model. There
is no Ollama running here, and newer Ollama versions may return thinking in a
separate field instead. Stripping costs nothing either way.

**A comment that was actively misleading.** `LLM_NUM_CTX` was documented as
"ignored by the openai_compat provider". True of the provider, which never
sends it; false of LANA, which uses it as the token budget in `build_context`
for *every* provider. Someone pointing LANA at a hosted model with a 128k
window and leaving the default at 8192 was having their facts trimmed to 8192
tokens with nothing saying why.

**Walking back a deadline that was too blunt.** The 20s request timeout added
last round applied to everything except upload. That suits a metadata read and
not work that scales with the data: cleaning a large frame, rendering a chart,
scanning every numeric pair, fitting a regression. Cutting those off turns a
slow answer into a wrong error message. They now get 120s — still bounded,
because the deadline exists so that "slow" never becomes "forever".

**And a config entry walked back entirely.** A `UP038` ignore was committed on
the strength of the local ruff (0.9.10), with a commit message asserting CI's
lint gate was red. Checking against the pinned version (0.15.1) showed the rule
had been removed outright and CI had never failed — the ignore bought nothing
and added a warning to every run. Reverted. The mistake was running a gate
against a version the repo does not pin, which is the same class of error as
the stale `.ruff_cache` that started this whole thread.

**Tests.** 168 backend tests, up from 156. `app/llm/reasoning.py` at 100%
coverage, `validation.py` at 92%, overall 87%. The adversarial suite is 13/16
with attribution at 4/4 and `numeric_fabrication` still 9/9; the three misses
remain the `in_range` and `causal` buckets named as out of scope.

---

## 2026-09-14 — Docker packaging, opt-in session persistence, opt-in auth token

**Problem.** LANA had no path from "clone the repo" to "running instance"
that didn't involve a Python venv and a separate Node toolchain — a real
barrier for anyone who just wants to try it. Two gaps sat next to that:
every session lived only in the process's memory (a restart silently lost
every upload), and every endpoint was open to anyone who could reach the
port, which is fine for one person on their own laptop and wrong the moment
LANA runs somewhere more than just that one person can reach.

**Solution — packaging.** `Dockerfile` (backend) and `frontend/Dockerfile`
(multi-stage: `vite build`, served by nginx) plus `docker-compose.yml`.
nginx performs the same `/api/*` prefix-stripping the Vite dev proxy already
did (`frontend/nginx.conf`), so `frontend/src/api.js` needed no
production-specific branch. `docker compose up --build` now gets a working
instance talking to Ollama on the host (`host.docker.internal`, with the
Linux `extra_hosts` shim Docker Desktop doesn't need but doesn't mind
either). An `ollama` service exists behind a `with-ollama` profile for
anyone who'd rather containerize that too, off by default because pulling a
model inside it is a multi-GB first run most people don't want.

**Solution — persistence.** `backend/persistence.py`: a SQLite index
(session_id, filename, active_version, last_used, has_cleaned) plus one
Parquet file per stored frame — Parquet, not CSV, because it round-trips
dtypes exactly, and a column LANA parsed as datetime or category must not
come back as a string after a restart. `SessionStore` gained an optional
`persist_dir`; when set, every create/clean-apply mirrors to disk and every
eviction/expiry deletes the mirror too, so disk usage tracks the in-memory
LRU rather than growing unbounded. `CleaningLedger` gained
`to_persisted_dict`/`from_persisted_dict`, deliberately separate from the
existing `to_dict()` — that one is the lossy API-facing summary, this one
round-trips every field of every `TransformRecord` exactly.

Off by default (`LANA_PERSIST_SESSIONS=false`): a plain `uvicorn --reload`
dev run should not start writing a `data/` directory into a contributor's
checkout with no announcement. The Docker image sets it to `true` with a
named volume, because there a restart silently losing every session is the
worse default. A subtlety that would have been a real bug: `Session.last_used`
is `time.monotonic()`, whose reference point is undefined across process
boundaries — reusing a persisted reading after a restart would compare it
against a new process's clock and could make a just-restored session look
arbitrarily stale (or immune to TTL expiry) for the wrong reason. Every
restored session is instead stamped `last_used = time.monotonic()` (now) at
load time, discarding the old reading rather than trusting it.

**Solution — auth.** An `LANA_AUTH_TOKEN` env var, checked by ASGI
middleware in `backend/main.py` via `hmac.compare_digest` against the
`Authorization: Bearer` header, with `/health` exempt so a container
healthcheck doesn't need it wired in separately. This is a single shared
secret, not a login system — anyone holding it has full access, and there
is no per-user concept. Off by default, same reasoning as persistence: it
changes nothing for the single-local-user case LANA has always assumed.
The frontend reads the token from `VITE_LANA_AUTH_TOKEN`, baked in at Vite
build time (`import.meta.env` only exposes what existed when `vite build`
ran) — which meant the three export buttons, previously plain `<a href
download>` links, had to become fetch-then-blob downloads
(`downloadCsv`/`downloadPdf`/`downloadDocx` in `frontend/src/api.js`): a
bare anchor click cannot carry a custom `Authorization` header, so enabling
the token would otherwise have silently broken every export.

**Tests.** `tests/test_persistence.py`, 13 new cases: `PersistenceBackend`
save/load round-trips (including a cleaned version with a ledger, and a
metadata-only save that must not rewrite frames), a simulated restart via
two `SessionStore` instances pointed at the same directory, eviction
deleting the on-disk mirror, a corrupted/missing raw frame being skipped
and cleaned up rather than failing startup, and the auth middleware's four
states (exempt path, no token, wrong token, correct token) plus confirming
the default stays fully open. 181 backend tests total, up from 168; the
existing 168 pass unchanged since persistence defaults to off and no route
signature changed.

**Not done here.** No CI job builds or pushes the Docker images yet — this
round only verified the Dockerfiles by inspection and ran the app directly
via the existing dev servers, since Docker was not available in the
environment this was built in. Rotating `LANA_AUTH_TOKEN` requires
rebuilding the frontend image (the token is baked in, not read at
container start). Multi-worker/multi-replica deployment still isn't
supported — the session store is still one process's in-memory dict with a
disk mirror, not a shared external store.

---

## 2026-09-16 — Two holes an outside reader would find first, and a stated threat model

**Problem.** An audit of the repo turned up two defects that had been sitting
in public, both of the kind a security-minded reader checks within the first
ten minutes: CSV export wrote cell values verbatim, and the pre-parse memory
gate only covered CSV. Neither was exotic. Both were worse *after* the Docker
packaging landed, because that work exists precisely to get more people
running and reading this.

**CSV formula injection.** `_csv_chunks` handed every value straight to
`to_csv`. A cell reading `=HYPERLINK(...)` — arriving in an upload, from a
file LANA did not write — became a live formula when the export was opened in
Excel, LibreOffice or Sheets. That is a code-execution path that runs on the
machine of whoever opens the report, who may not be the person who uploaded
the data.

The fix escapes `=`, `+`, `@`, tab and CR with a leading apostrophe. The part
worth writing down is what it deliberately does *not* do, because the obvious
implementation corrupts real data:

- **Only text columns are touched.** OWASP's list includes `-`, and a numeric
  column is full of negative numbers. Escaping those would put an apostrophe
  in front of every negative value LANA exports — in a tool whose entire
  claim is reporting numbers faithfully.
- **Inside a text column, a leading `-` is judged on what follows.** `-5.2`
  parses as a number and is left byte-identical; `-1+1+cmd|' /C calc'!A0`
  does not and is escaped. The test suite pins both directions, because the
  failure mode here is silent data corruption rather than a crash.
- **Only flagged cells are substituted**, into the original column rather
  than a stringified copy of it, so every untouched value stays exactly as it
  was uploaded. The escape runs per 50,000-row block, preserving the bounded
  memory the streaming export was built for, and copies a block only when
  that block actually contains something to escape.

**The asymmetric admission gate.** CSV uploads were costed from a sampled
projection before parsing; `.xlsx` and `.json` skipped the check entirely and
were bounded only by the compressed upload limit. An `.xlsx` is a zip, so that
is the wrong bound by roughly an order of magnitude: measured here, an
ordinary workbook compresses 8.2x, and a deliberately crafted one compresses
arbitrarily.

Excel and JSON cannot be sampled the way CSV can — both parsers must see the
whole document before producing anything — so the projection uses what is
knowable without parsing. For `.xlsx` that is the uncompressed size the
archive's own central directory declares, read with one seek and no
inflation, which is what makes a decompression bomb cheap to refuse instead
of expensive to discover. The multipliers converting that into a frame
estimate were measured on a 60,000-row mixed frame (uncompressed XML ran 4.1x
the resulting frame; a JSON document 1.3x) and then set deliberately above
what those ratios imply. Refusing a file that would have fit is an error the
user can see and override; being OOM-killed partway through materialising one
is not.

Worth noting what this is not: the bomb defence falls out of the ordinary
budget check rather than being special-cased. A file declaring 50 GB of sheet
XML projects a frame far past any budget and is refused by the same code path
that refuses an honestly large CSV — one gate, one meaning.

**SECURITY.md.** Added, and deliberately not a disclaimer. It states the scope
(single-user, local-first, no auth by default, and why that is a decision
rather than an omission), what to do when that stops being true
(`LANA_AUTH_TOKEN` plus TLS), what LANA does protect against, and — the part
that matters — what it knowingly does not: no per-session ownership,
unencrypted persisted data, prompt injection through cell contents only
partly mitigated, no rate limiting. A reader should not have to derive that
list from the source, and "no auth?!" is a better conversation to have in a
document than in an issue thread.

**Tests.** 204 backend tests, up from 181. `tests/test_export_safety.py` (13)
covers five payload shapes plus the four ways the fix could mangle legitimate
data; `tests/test_upload_admission.py` (10) covers the archive probe, a real
zip bomb, the over-estimate direction against an actual parse, and end-to-end
413s for Excel and JSON. Verified against a running server as well as under
pytest: a formula payload came back escaped and `-5.2` came back unchanged.

**Not done here.** `.xls` is still accepted by the extension allowlist but
cannot actually be parsed — `xlrd` is not in requirements.txt, so those
uploads fail at the parser with a dependency error rather than being refused
up front. Separate bug, found while working on this, left alone rather than
folded in silently.

---

## 2026-09-16 — The validator was vouching for wrong answers, and the answer key was grading itself

**Problem.** The project's headline claim is measured, disclosed hallucination
rates. Two things underneath it did not hold up. First, the validator did not
merely *miss* some wrong answers — on a known case it reported one as
**verified**, which is the worse failure by some distance: a warning that never
fires costs a reader nothing, a "verified" badge on a fabricated number costs
them the reason they trusted the tool. Second, the eval's answer key was
computed by calling the same production functions it was grading, so no run
could ever detect an error in them.

**The identifier hole, and what it exposed.** `eval/cases.py`'s retail-20 asks
for the average of `order_id`. The prose in the context said "[identifier —
arithmetic on it is not meaningful]" while the fact ledger cheerfully carried
`order_id mean`, so the model's answer matched a computed fact and came back
verified. Telling a model not to do arithmetic while handing it the result of
that arithmetic was never going to work; identifiers now contribute only the
facts that mean something for a key — their range — and the mean, median and
std are absent from the prompt text as well as the ledger.

Fixing that surfaced something bigger. The claim was *still* verified
afterwards, now by matching `total customer_age for region=west` (5,354 against
5,249.5, inside the 2% tolerance). Fact matching is global and label-blind: any
number within tolerance of any of the ~100 facts in a context gets stamped
verified, whatever the sentence is actually about. The existing attribution
check could not see it, because that one compares a fact against its
*siblings* — same statistic, different category — and an unrelated column's
total is nobody's sibling.

**Two checks, both narrow on purpose.** A collision rule: when the sentence
explicitly names a known column that is not the matched statistic's, and never
names that statistic's own column, the match is reported as a collision rather
than a verification. And a central-value rule: `min <= mean <= max` holds for
every column, always, so an average outside its own column's observed range is
impossible rather than unlikely — one of the few places here where "wrong" can
be concluded from arithmetic instead of a guess. Previously such a claim was
waved through as "derived", because plausibility was tested against *any*
column's range, and with eight columns of differing magnitude nearly everything
falls inside one of them. That is most of why the measured catch rate was 10%.

The precision work is the part worth reading. Both rules fire only on an
unambiguous attribution, and "unambiguous" had to be defined: a column name
counts only if no *other* number sits between it and this one. Without that,
"the average revenue is $345 per order; the average order is 2.3 items" flags
2.3 as impossible for revenue — a false alarm on a correct answer, which the
project has held since the beginning costs more trust than a missed flag.
adv-19 and adv-20 exist to hold that line; without them this change would be
indistinguishable from a validator that simply flags more things.

**The answer key now grades independently.** `eval/ground_truth.py` computes
each expected value with numpy and scipy directly, and evaluates LANA's own
function alongside it. Grading uses the independent figure; a divergence is
recorded on the result, printed by the runner, and rendered under the
comparison table, because a disagreement between the two is a finding about
the product or the harness and not something to resolve silently in either
direction. It earned its keep on the first run by flagging every correlation
case — `analyze_correlations` rounds to 4dp for display and scipy does not.
Presentation, not a defect, but nobody had written it down.

`tests/test_ground_truth_agreement.py` runs that comparison over all 40 cases
with no model involved, and plants a deliberate disagreement to prove the
check has teeth — a silently-disabled comparison looks exactly like a passing
one otherwise.

**Multi-model runs.** `eval/run.py --models a,b,c` runs the suite against each
in turn and writes a comparison table; rendering lives in `eval/compare.py` as
a pure function over saved run summaries, so it is covered by the normal test
suite and can be re-run over results saved weeks apart without touching a
model. One model is an anecdote — a reader cannot tell whether the grounding
effect is a property of the pipeline or of phi3:mini's particular failure
modes.

**Enforcing the doc that claims to mirror the code.** `docs/provenance.md`
says of itself that it is "not an independent description that could quietly
drift from what the code does". Nothing checked that, and it had drifted
twice. `tests/test_docs_match_capabilities.py` now fails when a capability
statement is missing from it.

**Tests.** 239 backend tests, up from 204. Adversarial suite: 17/20 behaving
as documented, `numeric_fabrication` now 13/13 at precision 1.00 / recall
1.00 (was 9/9 over a smaller, easier set), and the `in_range` and `causal`
blind spots still measured at zero, deliberately.

**Not done here.** The multi-model numbers themselves. No Ollama was reachable
from the machine this was written on, so the table has tooling and tests but
no rows — running it needs 3-4 pulled models and roughly ten to forty minutes
each. The 10% catch rate quoted above is still the last *measured* figure;
these changes should raise it, and that claim stays unmade until a real run
says so. Publishing an improved number inferred from the adversarial suite
would be exactly the kind of unearned claim this round exists to prevent.

---

## 2026-09-17 — A way in for other people, and two things that were quietly untrue

**Problem.** The repo had no stated way to contribute, no release history a
user could read, and two small claims that did not hold: an upload format
advertised but never supported, and CI warning on every run.

**`.xls` removed rather than fixed.** The extension allowlist accepted it and
the parser could never read it — the legacy binary format needs `xlrd`, which
is not a dependency, so those uploads died with "Missing optional dependency".
Two ways out: add the dependency, or stop claiming the format.

Adding it looked like the friendlier option and was rejected on testability.
Modern pandas cannot *write* `.xls` (xlwt has been gone since pandas 2.0), so
there is no way to generate a fixture, which means shipping a code path that
claims to work and is never exercised — in a project whose whole argument is
that claims should be measured. Dropping it is testable in one line, removes a
legacy binary parser from the attack surface, and costs a user five seconds in
Excel. The refusal names the fix ("save it as .xlsx") rather than the missing
dependency, which is not the user's problem.

**CI actions bumped to v7.** All five jobs were warning that
`actions/checkout@v4` and friends target the deprecated Node 20 runtime.
Checked the current majors rather than guessing: checkout, setup-python and
setup-node are all on v7. The `node-version: 20` used to *build the frontend*
is unrelated and left alone — that is the project's own toolchain, and moving
it is a separate decision with its own blast radius.

**A way in.** `CONTRIBUTING.md`, issue forms, and a PR template. Written
against what this codebase actually pushes back on rather than generic
boilerplate: comments record why and what it cost, constants in comments are
measured, tests assert values rather than status codes, and a validator change
needs a precision control — an adversarial case it should catch *and* a nearby
one it must leave alone. The bug form asks for the shape of the data rather
than the data, since the people most likely to hit a bug here are working with
something they cannot attach.

**`CHANGELOG.md` and v0.1.0.** The engineering log is thorough and is not for
users — it is problem/attempt/tradeoff prose aimed at whoever maintains this.
The user-facing changelog is separate and short, and links here for depth.
v0.1.0 marks the point where the thing is packaged, documented and measured
well enough to hand to someone else, which is a different milestone from
"it works".

**Not done here.** The good-first-issue tickets themselves: `gh` is not
installed on this machine, so they are drafted and waiting to be posted rather
than created. Still no published multi-model eval numbers — that remains the
one outstanding piece of the previous round, and it needs a machine with
Ollama and the models pulled.
