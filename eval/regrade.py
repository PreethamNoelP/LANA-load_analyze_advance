"""Re-apply grading to an already-collected run's saved transcripts.

Used when a grading bug is fixed (see docs/engineering-changelog.md,
2026-08-19 refusal-detector fix) and the correction should be re-applied to
existing raw model output without spending more real LLM calls to get it.
This is a re-grade, not a re-run: it reads the .jsonl log's raw_answer
field for every case and recomputes verdict/summary from scratch.

    .venv/Scripts/python.exe -m eval.regrade eval/results/run_<id>.jsonl
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.cases import CASES  # noqa: E402
from eval.datasets import employee_survey, retail_orders  # noqa: E402
from eval.ground_truth import resolve as resolve_ground_truth  # noqa: E402
from eval.harness import grade  # noqa: E402
from eval.run import summarize, print_summary  # noqa: E402


def main(jsonl_path: str) -> None:
    dfs = {"retail": retail_orders(), "survey": employee_survey()}
    cases_by_id = {c["id"]: c for c in CASES}

    rows = [json.loads(line) for line in Path(jsonl_path).read_text(encoding="utf-8").splitlines()]

    changed = 0
    for row in rows:
        if row["verdict"] == "error":
            continue
        case = cases_by_id[row["case_id"]]
        gt = resolve_ground_truth(dfs[case["dataset"]], case["gt"])
        new_verdict = grade(gt, row["raw_answer"])
        if new_verdict != row["verdict"]:
            print(f"  regraded {row['case_id']:12s} [{row['condition']:8s}] "
                  f"{row['verdict']:12s} -> {new_verdict}")
            changed += 1
        row["verdict"] = new_verdict

    print(f"\n{changed} of {len(rows)} results changed verdict after the refusal-detector fix.\n")

    # Reconstruct the lightweight namespace summarize()/print_summary() expect.
    class _R:
        def __init__(self, d):
            self.__dict__.update(d)

    main_results = [_R(r) for r in rows]
    summary = summarize(main_results, [])  # adversarial suite is unaffected — no model text there
    # summarize() computes an adversarial section unconditionally; drop it here since
    # we didn't recompute it (nothing about it depends on the refusal-detector fix).
    summary.pop("adversarial_in_scope", None)
    summary.pop("adversarial_out_of_scope_blind_spots", None)

    print("=" * 72)
    print("REGRADED SUMMARY (main comparison only — adversarial suite unaffected)")
    print("=" * 72)
    for cond, stats in summary["conditions"].items():
        print(f"\n[{cond}]  n={stats['n']}")
        print(f"  correct rate:       {stats['correct_rate']}")
        print(f"  hallucinated rate:  {stats['hallucinated_rate']}")
        print(f"  by verdict:         {stats['by_verdict']}")
    lv = summary["lana_validator_on_real_answers"]
    print("\n[lana validator, on real model output]")
    print(f"  wrong answers caught:            {lv['wrong_answers_caught']}  (n={lv['n_wrong']})")
    print(f"  correct answers wrongly flagged: {lv['correct_answers_wrongly_flagged']}  (n={lv['n_correct']})")

    out_path = Path(jsonl_path).with_name(Path(jsonl_path).stem + "_regraded.json")
    out_path.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2, default=str))
    print(f"\nRegraded results written to {out_path}")


if __name__ == "__main__":
    main(sys.argv[1])
