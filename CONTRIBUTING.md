# Contributing to LANA

Thanks for looking. This is a small project with one maintainer, so the most
useful thing you can do is make your change easy to review: small, tested, and
clear about what it does and does not do.

## Getting set up

```bash
git clone https://github.com/PreethamNoelP/LANA-load_analyze_advance.git
cd LANA-load_analyze_advance

python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS / Linux
pip install -r requirements.txt -r requirements-dev.txt

cd frontend && npm install && cd ..
cp .env.example .env
```

Run it with two terminals — `uvicorn backend.main:app --reload` and
`npm run dev --prefix frontend` — or with `docker compose up --build`, which
needs no local Python or Node at all. Ollama only matters for the Ask AI tab;
profiling, cleaning, charts, statistics and export all work without it.

## Before you open a pull request

```bash
pytest tests/ -q                 # 240 tests, no model required
ruff check .
npm run lint --prefix frontend
npm test --prefix frontend
npm run build --prefix frontend
```

CI runs exactly this, on Python 3.11 and 3.13, plus a Docker build of both
images. Nothing needs a GPU, a model, or network access.

## What this codebase cares about

These are not style preferences — they are the things a review will actually
push back on.

**Say why, not what.** Comments explain the constraint, the measurement, or
the bug that made the code look like this. A comment restating the line above
it will be removed; one recording "we tried the obvious thing and here is what
it cost" is the most valuable thing in the file.

**Numbers in comments should be measured.** There are constants here derived
from real measurements — upload peak multipliers, chunk sizes, the frame-size
ratios for Excel and JSON. If you add one, say what you measured and roughly
how. A plausible-looking magic number is worse than an explicit guess labelled
as a guess.

**Tests assert behaviour, not status codes.** `assert r.status_code == 200` on
its own is not a test. The suite checks real values: statistics pinned against
scipy, quality scores in range, exports round-tripped. Follow that.

**A change to the validator needs a precision control.** `app/llm/validation.py`
decides whether to tell a user their answer may be wrong. Making it catch more
is easy; making it catch more *without* flagging correct answers is the actual
work. If you add a check, add both an adversarial case it should catch
(`eval/adversarial.py`) and one nearby case it must leave alone. A false alarm
on a correct answer costs more trust than a missed flag.

**Do not let the docs drift.** `docs/provenance.md` mirrors the capability
constants in `app/llm/validation.py`, and `tests/test_docs_match_capabilities.py`
fails if it stops doing so. If you change what the validator claims, change
both.

**Say what you did not do.** `docs/engineering-changelog.md` records each round
of work as problem / what was tried / what actually worked / what is still
missing, including the dead ends. If your change is substantive, add an entry
in that shape. The "Not done here" sections are deliberate, not an oversight.

## Commits and pull requests

Conventional-commit style, describing the effect rather than the file:

```
fix(llm): stop the validator vouching for answers it cannot support
feat(eval): run the suite across several models and compare them
docs: state the threat model in SECURITY.md
```

Keep a pull request to one concern. Two small ones are easier to review, and
much easier to revert, than one that does two things.

## Good first issues

Issues labelled [`good first issue`](https://github.com/PreethamNoelP/LANA-load_analyze_advance/labels/good%20first%20issue)
are self-contained and low-risk — mostly test coverage and splitting oversized
files. They touch one area, have a clear finish line, and will not put you in
the middle of the grounding pipeline on day one.

## Reporting a security problem

Do not open a public issue. [`SECURITY.md`](SECURITY.md) explains the scope
LANA is built for and how to report privately.

## Questions

Open an issue. A question that turns out to be a documentation gap is a useful
bug report.
