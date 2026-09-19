# Changelog

What changed, for people who use LANA rather than work on it. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[semantic versioning](https://semver.org/spec/v2.0.0.html).

For the engineering account — what was tried, what it cost, what is still
missing — see [`docs/engineering-changelog.md`](docs/engineering-changelog.md).

## [Unreleased]

### Fixed

- **An answer could be marked "verified" against a previous question's
  evidence.** When a question was answered by running a query, that query's
  figures were added to the dataset's shared fact list instead of to a
  per-question copy — so from the second question onward, every answer was
  checked against every earlier query's results as well as its own. A number
  the current answer had no basis for could land within tolerance of a
  leftover figure and be shown with the green "verified against the data"
  mark, which is the one failure this tool exists to prevent. Each question
  now sees only its own evidence. If you have been relying on the verification
  badge across a long session, this is the change to know about.
- **URL sources are now actually pinned to the address that was checked.**
  LANA validated every address a hostname resolved to and then handed the
  hostname to its HTTP client, which resolved it again — so a name that
  answered the two lookups differently could pass the check and connect
  somewhere else. The documentation said this window was closed; now it is.

### Added

- **See the query behind an answer.** When LANA answers by running a query
  against your rows, the answer now carries a "Computed by query" badge and an
  expandable panel with the exact statement and the result table every figure
  came from. An answer from the precomputed summary says so instead. The
  backend has sent this since 0.2.0; the interface was dropping it, so the
  claim that you can check where a number came from was not something you
  could act on.
- **An audit trail.** Data loaded, cleaned, switched between versions,
  questioned and exported — plus refused access and failed sign-ins — are
  recorded durably when session persistence is on, or wherever
  `LANA_AUDIT_LOG` points. `GET /audit` returns your own entries. It records
  what happened to which dataset, never the data itself.

### Changed

- **`docker compose up` no longer publishes LANA on your network.** Ports are
  bound to `127.0.0.1`. The stack has no authentication by default, so the
  previous behaviour put an open upload-and-analyse API on whatever network
  the machine was attached to. To reach it from elsewhere, set
  `LANA_AUTH_TOKEN`, put it behind a TLS proxy, and change the port binding
  deliberately.
- **Reading files from the server's disk is scoped to the deployment.** On a
  single-user install nothing changes. With `LANA_AUTH_TOKEN` set, the `file`
  connector is disabled until `LANA_FILE_SOURCE_ROOTS` names the directories
  it may read — otherwise anyone who could reach LANA could read any CSV or
  JSON on the host. Uploading a file is unaffected.

## [0.2.0] — 2026-09-17

Answers are now computed by running a query against your data rather than
looked up in a precomputed summary, LANA reads from databases and APIs as well
as files, and the parts that were only safe for one local user are now safe for
a small shared deployment.

### Added

- **Connect to databases, APIs and URLs, not just files.** SQL databases
  (PostgreSQL, MySQL, SQLite — anything SQLAlchemy drives), MongoDB
  collections, and REST/URL endpoints returning JSON, NDJSON or CSV. Pick a
  source, test the connection, preview the real schema and eight rows, then
  load. Everything after that is identical to an uploaded file: the same
  profiling, cleaning, questions, charts, statistics, validation and export.
- **Answers grounded in an executed query.** A question is now planned as a SQL
  query and run against your rows in a sandboxed engine, so the figure is
  computed from the data rather than retrieved from a summary. The query and
  its result travel with the answer, so you can read exactly where a number
  came from — and re-run it yourself. Questions that cannot be expressed as a
  query fall back to the previous behaviour automatically.
  Turn it off with `LANA_SQL_GROUNDING=false`.
- **A better trust signal.** The check on each answer now works out *which
  statistic of which column* a sentence is claiming and compares against that
  figure specifically, instead of accepting any number that happens to match
  something. Measured recall on the adversarial suite went from 0.571 to
  **0.810**, with precision unchanged at 1.000. Each claim also records whether
  it was verified against an executed query or a precomputed fact.
- **Metrics and structured logs.** Prometheus metrics at `GET /metrics` —
  request rates, latency histograms, LLM outcomes, validation verdicts, live
  sessions. Set `LANA_LOG_FORMAT=json` for one JSON object per line, each
  carrying a request id that ties every line of a request together. No column
  names, values or filenames appear in either.
- **Rate limiting**, per caller, with a separate tighter budget for questions.
  Defaults are far above what one person generates.
- **Session ownership.** With `LANA_AUTH_TOKEN` set, a session can only be read
  by the principal that created it.
- **Multi-worker support.** `uvicorn --workers N` with `LANA_PERSIST_SESSIONS=true`
  now works: workers share one concurrency cap, one rate limit, and one session
  store. Workers must share a filesystem; replicas across separate hosts are
  still not supported.
- **A messy benchmark dataset**, with numbers stored as text, four date formats
  in one column, category labels differing only by case, five spellings of
  "missing", duplicates and an extreme outlier. Scored separately from the
  clean datasets rather than averaged in.
- **Multi-seed evaluation.** `python -m eval.run --seeds 0,1,2` regenerates the
  datasets per run so results are a spread rather than one draw.
- **Two new benchmarks you can run.** `python -m eval.validator_bench` scores
  the answer checker against any earlier version of itself;
  `python -m eval.load_test` reports p50/p95/p99 latency, memory growth and
  refusal behaviour under concurrency.

### Changed

- **The auth token is no longer built into the web page.** It used to be
  compiled into the JavaScript, where anyone loading the page could read it and
  changing it meant rebuilding the frontend image. The browser now exchanges it
  once for a cookie it cannot read. Existing `Authorization: Bearer` API
  clients are unaffected; if you set `VITE_LANA_AUTH_TOKEN` as a Docker build
  argument, you can stop — it is ignored and the image no longer accepts it.
- **`/health` is fast when the model is down.** It used to wait for a full
  connection timeout on every call, which under load meant a p95 of nearly
  eight seconds precisely when something was wrong. The result is cached
  briefly.
- **The Clean tab was split into modules.** No behaviour change.

### Fixed

- A correct answer could be flagged as wrong when a sentence contained a
  decimal and the dataset had a numeric category code — "the average order is
  2.3 items" made the validator read `2.3` as a claim about category `3`.
- A correct percentage could be flagged when the sentence also contained a
  count ("there are 500 orders, of which 31.4% …").
- Latency histograms in the new metrics endpoint counted each observation once
  per bucket it fell into, inflating every cumulative bucket.
- `/auth/status` required authentication, so a browser could not ask whether
  authentication was required.

### Security

- Generated SQL runs with no filesystem and no network access, with the
  configuration locked so a query cannot re-enable either. Only single
  read-only statements are accepted, results are row-capped and queries are
  deadline-bounded.
- URL sources refuse private, loopback, link-local and cloud-metadata
  addresses. Every address a hostname resolves to must be public, each redirect
  is re-checked, and the connection is pinned to the address that was
  validated. Override with `LANA_ALLOW_PRIVATE_SOURCE_URLS=true` only where
  everyone who can reach LANA is trusted to supply URLs.
- Database credentials are supplied separately from the connection string,
  never echoed back in a response, and redacted from logs and error messages.
- Sessions are denied with a 404 rather than a 403 for a caller that does not
  own them, so the endpoint cannot be used to discover which session ids exist.

## [0.1.0] — 2026-09-17

First tagged version. LANA has been usable for a while; this marks the point
where it is packaged, documented and measured well enough to hand to someone
else.

### Added

- **Docker deployment.** `docker compose up --build` gets a working instance
  with no local Python or Node setup. Connects to Ollama on the host by
  default; an optional `--profile with-ollama` runs that in a container too.
- **Sessions survive a restart.** Off by default, on in the Docker image: set
  `LANA_PERSIST_SESSIONS=true` and uploads are mirrored to disk (SQLite index
  plus Parquet frames) instead of living only in memory.
- **Optional access token.** Set `LANA_AUTH_TOKEN` to require a shared secret
  on every request — for running LANA somewhere more than one person can
  reach. One token for the whole instance, not a login system.
- **Multi-model evaluation.** `python -m eval.run --models a,b,c` runs the
  40-case benchmark against several models in turn and writes a comparison
  table.
- **`SECURITY.md`** stating what LANA protects against and what it knowingly
  does not.

### Changed

- **Exports are safe to open in a spreadsheet.** Cell values that a
  spreadsheet would execute as a formula are escaped on export. Negative
  numbers and ordinary text are untouched.
- **Every upload format is costed before parsing**, not just CSV. An `.xlsx`
  is measured by the uncompressed size it declares, so a file that would
  exhaust memory is refused up front rather than discovered mid-parse.
- **Answers are checked more strictly.** A number is no longer accepted as
  verified just because it resembles some unrelated figure in the context, and
  an average that falls outside its own column's range is refused outright.
  Identifier columns no longer carry an average at all — there is no
  meaningful average of an ID.
- **The benchmark grades against an independent answer key** (numpy/scipy
  computed separately) rather than against LANA's own statistics, and reports
  any disagreement between the two.

### Removed

- **Legacy `.xls` upload.** It was listed as supported and never worked — the
  format needs a dependency this project does not carry, so those uploads
  failed at the parser. They are now refused immediately with an instruction
  to re-save as `.xlsx`.

### Fixed

- Exports no longer fail when an access token is configured.
- A reasoning model's `<think>` scratchpad no longer leaks into answers.
- Long-running work (cleaning, charts, correlation scans, regression) is no
  longer cut off by a deadline meant for quick metadata reads.

[Unreleased]: https://github.com/PreethamNoelP/LANA-load_analyze_advance/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/PreethamNoelP/LANA-load_analyze_advance/releases/tag/v0.1.0
