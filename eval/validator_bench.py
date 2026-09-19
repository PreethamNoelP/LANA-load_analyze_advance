"""Measure validator precision and recall, optionally against an older revision.

The audit's sharpest finding was that the validator — the thing LANA claims
as its differentiator — caught 1 of 7 wrong answers in the measured 40-case
run. "We improved it" is not an answer to that; a number that can be
recomputed is. This script produces that number, and can produce the *same*
number for any earlier git revision of ``app/llm/validation.py`` so an
improvement is a measured delta rather than an assertion.

    python -m eval.validator_bench
    python -m eval.validator_bench --baseline HEAD
    python -m eval.validator_bench --baseline v1.0 --json results.json

No model is involved: the adversarial suite feeds scripted right and wrong
answers straight into ``validate_answer``, so this runs in about a second and
is deterministic. That is deliberate — the part of the trust layer that can
be measured hermetically should not be gated on a GPU being free.

Note on what this does and does not measure. These are *scripted* failures
chosen to probe named capabilities, not a random sample of what a model
actually produces. Recall here is recall against the failure modes the suite
knows about, which is a lower bound on the work still to do and an upper
bound on nothing. The end-to-end figure — the validator's catch rate on real
model output — comes from ``eval/run.py`` and needs a live model.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.llm.context import build_context  # noqa: E402
from eval.adversarial import build_cases  # noqa: E402
from eval.datasets import employee_survey, retail_orders  # noqa: E402


@dataclass
class Score:
    """Confusion matrix over the adversarial suite, plus what it got wrong."""

    n: int
    tp: int
    fp: int
    fn: int
    tn: int
    misses: list[str]
    false_alarms: list[str]

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "n": self.n, "tp": self.tp, "fp": self.fp, "fn": self.fn, "tn": self.tn,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "misses": list(self.misses),
            "false_alarms": list(self.false_alarms),
        }

    def render(self, title: str) -> str:
        return (
            f"{title}\n"
            f"  n={self.n}  tp={self.tp} fp={self.fp} fn={self.fn} tn={self.tn}\n"
            f"  precision={self.precision:.3f}  recall={self.recall:.3f}  "
            f"f1={self.f1:.3f}\n"
            f"  missed:       {', '.join(self.misses) or 'none'}\n"
            f"  false alarms: {', '.join(self.false_alarms) or 'none'}"
        )


def score_validator(validate: Callable[..., Any]) -> Score:
    dfs = {"retail": retail_orders(), "survey": employee_survey()}
    contexts = {name: build_context(df) for name, df in dfs.items()}
    cases = build_cases(dfs["retail"], dfs["survey"])

    tp = fp = fn = tn = 0
    misses: list[str] = []
    false_alarms: list[str] = []

    for case in cases:
        result = validate(case.text, contexts[case.dataset])
        flagged = bool(result.warnings)
        if case.should_flag and flagged:
            tp += 1
        elif case.should_flag:
            fn += 1
            misses.append(f"{case.id}({case.capability})")
        elif flagged:
            fp += 1
            false_alarms.append(f"{case.id}({case.capability})")
        else:
            tn += 1

    return Score(len(cases), tp, fp, fn, tn, misses, false_alarms)


def load_validator_from_revision(revision: str) -> Callable[..., Any]:
    """Import ``app/llm/validation.py`` as it existed at a git revision.

    The module is rewritten to import ``Fact``/``GroundedContext`` absolutely
    rather than relatively, so it loads standalone. That is the only edit —
    everything the comparison is about is left exactly as that revision wrote
    it. It also means a baseline older than the ``Fact.statistic`` field still
    loads: extra dataclass fields are additive, and the old code simply never
    reads them.
    """
    source = subprocess.run(
        ["git", "show", f"{revision}:app/llm/validation.py"],
        capture_output=True, text=True, check=True,
        cwd=Path(__file__).resolve().parent.parent,
    ).stdout
    source = source.replace(
        "from .context import", "from app.llm.context import"
    )

    tmp_dir = Path(tempfile.mkdtemp(prefix="lana_validator_bench_"))
    module_path = tmp_dir / "baseline_validation.py"
    module_path.write_text(source, encoding="utf-8")

    spec = importlib.util.spec_from_file_location("baseline_validation", module_path)
    module = importlib.util.module_from_spec(spec)
    # Registered before execution: @dataclass resolves annotations through
    # sys.modules, and fails on a module that is not there yet.
    sys.modules["baseline_validation"] = module
    spec.loader.exec_module(module)
    return module.validate_answer


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline", metavar="GIT_REV",
        help="also score app/llm/validation.py as of this revision, and print the delta",
    )
    parser.add_argument("--json", metavar="PATH", help="write the scores as JSON")
    args = parser.parse_args()

    from app.llm.validation import validate_answer

    current = score_validator(validate_answer)
    print(current.render("CURRENT (working tree)"))

    payload: dict[str, Any] = {"current": current.to_dict()}

    if args.baseline:
        baseline = score_validator(load_validator_from_revision(args.baseline))
        print()
        print(baseline.render(f"BASELINE ({args.baseline})"))
        print()
        print(
            f"DELTA  recall {baseline.recall:.3f} -> {current.recall:.3f}"
            f"  ({current.recall - baseline.recall:+.3f})   "
            f"precision {baseline.precision:.3f} -> {current.precision:.3f}"
            f"  ({current.precision - baseline.precision:+.3f})"
        )
        payload["baseline"] = baseline.to_dict()
        payload["baseline_revision"] = args.baseline

    if args.json:
        Path(args.json).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nWrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
