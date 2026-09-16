"""Render a model-comparison table from one or more eval runs.

    python -m eval.compare eval/results/run_*.json

The headline claim this project makes — grounding measurably reduces
hallucination — was until now measured on exactly one model (phi3:mini). One
model is an anecdote: a reader cannot tell whether the effect is a property
of the pipeline or of that model's particular failure modes. Running the same
40 cases across several and publishing the spread is the difference between
"it worked when we tried it" and a claim someone else can check.

Table rendering lives here, separate from the runner, for two reasons: it can
be re-run over result files saved weeks apart without touching a model, and
it is a pure function over dicts, so it is covered by the normal test suite
rather than only by a run that needs a GPU and twenty minutes.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _condition(run: dict[str, Any], condition: str, key: str) -> float | None:
    return run.get("summary", {}).get("conditions", {}).get(condition, {}).get(key)


def render_markdown(runs: list[dict[str, Any]]) -> str:
    """A comparison table over runs, one row per model.

    Deliberately reports both conditions side by side rather than only the
    delta. A model whose baseline is already strong and a model the grounding
    rescues are different results, and a single "improvement" number hides
    which one you are looking at.
    """
    if not runs:
        return "_No eval runs supplied._"

    header = (
        "| Model | Cases | Correct (grounded) | Correct (baseline) | "
        "Hallucinated (grounded) | Hallucinated (baseline) | "
        "Wrong answers caught | Correct answers wrongly flagged |"
    )
    divider = "|" + "---|" * 8
    rows = [header, divider]

    for run in sorted(runs, key=lambda r: str(r.get("model", ""))):
        validator = run.get("summary", {}).get("lana_validator_on_real_answers", {})
        rows.append(
            f"| `{run.get('model', 'unknown')}` "
            f"| {run.get('n_cases', '—')} "
            f"| {_pct(_condition(run, 'lana', 'correct_rate'))} "
            f"| {_pct(_condition(run, 'baseline', 'correct_rate'))} "
            f"| {_pct(_condition(run, 'lana', 'hallucinated_rate'))} "
            f"| {_pct(_condition(run, 'baseline', 'hallucinated_rate'))} "
            f"| {_pct(validator.get('wrong_answers_caught'))} "
            f"| {_pct(validator.get('correct_answers_wrongly_flagged'))} |"
        )

    notes = [
        "",
        "Grounded = LANA's fact ledger + answer validation. Baseline = the "
        "project's own pre-grounding context builder, same model, same "
        "questions.",
    ]

    disagreements = sorted({
        d for run in runs
        for d in run.get("summary", {}).get("ground_truth_disagreements", [])
    })
    if disagreements:
        notes += [
            "",
            "**The independent answer key disagreed with LANA's own statistics "
            "on these cases — read the numbers above with that in mind:**",
            *(f"- {d}" for d in disagreements),
        ]

    return "\n".join(rows + notes)


def load_runs(paths: list[str]) -> list[dict[str, Any]]:
    runs = []
    for path in paths:
        text = Path(path).read_text(encoding="utf-8")
        runs.append(json.loads(text))
    return runs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+", help="Paths to run_<timestamp>.json files.")
    parser.add_argument("--out", default=None, help="Write the table here instead of stdout.")
    args = parser.parse_args(argv)

    table = render_markdown(load_runs(args.runs))
    if args.out:
        Path(args.out).write_text(table + "\n", encoding="utf-8")
        print(f"Wrote {args.out}")
    else:
        print(table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
