# Changelog

What changed, for people who use LANA rather than work on it. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[semantic versioning](https://semver.org/spec/v2.0.0.html).

For the engineering account — what was tried, what it cost, what is still
missing — see [`docs/engineering-changelog.md`](docs/engineering-changelog.md).

## [Unreleased]

Nothing yet.

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
