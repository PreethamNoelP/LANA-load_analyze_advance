"""CLI entrypoint for LANA's evaluation harness.

    .venv/Scripts/python.exe -m eval.run                 # full run
    .venv/Scripts/python.exe -m eval.run --limit 3        # smoke test, 3 cases

Runs, in order:

  1. The baseline-vs-LANA comparison: every case in eval/cases.py, through
     the actually-running local model, under both conditions.
  2. The adversarial validator suite: scripted answers, no model calls,
     checked against the real app.llm.validation.validate_answer().

Nothing here is mocked. The model, its host, and its parameters are pinned
below rather than read from the developer's .env — an eval harness should
name its own test subject explicitly, not inherit whatever a local config
file happens to say. If Ollama is not reachable, the run fails immediately
with an explicit error instead of substituting a canned result.

Every case's result is appended to a .jsonl log as it completes (so a run
that's interrupted partway still leaves usable data), and a consolidated
summary + full result set is written to eval/results/run_<timestamp>.json
at the end.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.llm.ollama_provider import OllamaProvider  # noqa: E402
from eval.adversarial import build_cases as build_adversarial_cases  # noqa: E402
from eval.cases import CASES  # noqa: E402
from eval.datasets import employee_survey, retail_orders  # noqa: E402
from eval.harness import run_adversarial, run_case  # noqa: E402

RESULTS_DIR = Path(__file__).resolve().parent / "results"

# Pinned independently of the developer's .env — see module docstring.
MODEL = "phi3:mini"
HOST = "http://localhost:11434"
TEMPERATURE = 0.2
MAX_TOKENS = 1024
TIMEOUT = 90.0
NUM_CTX = 8192


def main(limit: int | None, model: str) -> None:
    provider = OllamaProvider(model=model, host=HOST, temperature=TEMPERATURE,
                               max_tokens=MAX_TOKENS, timeout=TIMEOUT, num_ctx=NUM_CTX)
    if not provider.is_available():
        raise SystemExit(
            f"Ollama is not reachable at {HOST}. Start it with `ollama serve` "
            "before running the evaluation."
        )
    print(f"Model under test: {provider.name}")
    print(f"Temperature={TEMPERATURE}  max_tokens={MAX_TOKENS}  num_ctx={NUM_CTX}\n")

    dfs = {"retail": retail_orders(), "survey": employee_survey()}
    cases = CASES[:limit] if limit else CASES

    RESULTS_DIR.mkdir(exist_ok=True)
    run_id = int(time.time())
    log_path = RESULTS_DIR / f"run_{run_id}.jsonl"

    main_results = []
    total = len(cases) * 2
    done = 0
    with log_path.open("w", encoding="utf-8") as log_file:
        for case in cases:
            df = dfs[case["dataset"]]
            for condition in ("baseline", "lana"):
                result = run_case(case, df, provider, condition)
                main_results.append(result)
                done += 1
                print(f"[{done}/{total}] {condition:8s} {case['id']:12s} -> {result.verdict:12s}"
                      f" ({result.latency_s:5.1f}s)")
                log_file.write(json.dumps(asdict(result)) + "\n")
                log_file.flush()

    print("\nRunning adversarial validator suite (no model calls)...")
    adv_cases = build_adversarial_cases(dfs["retail"], dfs["survey"])
    adv_results = run_adversarial(adv_cases, dfs)
    for r in adv_results:
        status = "FLAGGED" if r["flagged"] else "not flagged"
        print(f"  {r['id']:8s} [{r['capability']:20s}] should_flag={r['should_flag']!s:5s} -> {status}")

    summary = summarize(main_results, adv_results)
    print_summary(summary)

    out_path = RESULTS_DIR / f"run_{run_id}.json"
    out_path.write_text(json.dumps({
        "run_id": run_id,
        "model": provider.name,
        "n_cases": len(cases),
        "temperature": TEMPERATURE,
        "main_results": [asdict(r) for r in main_results],
        "adversarial_results": adv_results,
        "summary": summary,
    }, indent=2, default=str))
    print(f"\nPer-case log:      {log_path}")
    print(f"Full results+summary: {out_path}")


def summarize(main_results, adv_results) -> dict:
    by_condition = defaultdict(list)
    for r in main_results:
        by_condition[r.condition].append(r)

    def rate(results, verdicts):
        return round(sum(1 for r in results if r.verdict in verdicts) / len(results), 3) if results else None

    summary: dict = {"conditions": {}}
    for cond, results in by_condition.items():
        summary["conditions"][cond] = {
            "n": len(results),
            "correct_rate": rate(results, {"correct"}),
            "hallucinated_rate": rate(results, {"hallucinated"}),
            "error_rate": rate(results, {"error"}),
            "by_verdict": _counts(r.verdict for r in results),
        }

    lana_results = by_condition.get("lana", [])
    wrong_lana = [r for r in lana_results if r.verdict in ("incorrect", "hallucinated", "partial", "hedged")]
    right_lana = [r for r in lana_results if r.verdict == "correct"]
    summary["lana_validator_on_real_answers"] = {
        "wrong_answers_caught": _safe_rate(sum(1 for r in wrong_lana if r.validator_flagged), len(wrong_lana)),
        "n_wrong": len(wrong_lana),
        "correct_answers_wrongly_flagged": _safe_rate(sum(1 for r in right_lana if r.validator_flagged), len(right_lana)),
        "n_correct": len(right_lana),
    }

    in_scope = [a for a in adv_results if a["capability"] == "numeric_fabrication"]
    tp = sum(1 for a in in_scope if a["should_flag"] and a["flagged"])
    fn = sum(1 for a in in_scope if a["should_flag"] and not a["flagged"])
    fp = sum(1 for a in in_scope if not a["should_flag"] and a["flagged"])
    tn = sum(1 for a in in_scope if not a["should_flag"] and not a["flagged"])
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    f1 = (2 * precision * recall / (precision + recall)) if precision and recall and (precision + recall) else None
    summary["adversarial_in_scope"] = {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": round(precision, 3) if precision is not None else None,
        "recall": round(recall, 3) if recall is not None else None,
        "f1": round(f1, 3) if f1 is not None else None,
    }

    out_of_scope = [a for a in adv_results if a["capability"] != "numeric_fabrication"]
    by_cap = defaultdict(lambda: {"n": 0, "caught": 0})
    for a in out_of_scope:
        by_cap[a["capability"]]["n"] += 1
        by_cap[a["capability"]]["caught"] += int(a["flagged"])
    summary["adversarial_out_of_scope_blind_spots"] = {
        cap: {"n": v["n"], "caught": v["caught"], "caught_rate": _safe_rate(v["caught"], v["n"])}
        for cap, v in by_cap.items()
    }
    return summary


def _counts(items) -> dict:
    out: dict[str, int] = {}
    for item in items:
        out[item] = out.get(item, 0) + 1
    return out


def _safe_rate(num, den):
    return round(num / den, 3) if den else None


def print_summary(summary: dict) -> None:
    print("\n" + "=" * 72)
    print("SUMMARY")
    print("=" * 72)
    for cond, stats in summary["conditions"].items():
        print(f"\n[{cond}]  n={stats['n']}")
        print(f"  correct rate:       {stats['correct_rate']}")
        print(f"  hallucinated rate:  {stats['hallucinated_rate']}")
        print(f"  by verdict:         {stats['by_verdict']}")

    lv = summary["lana_validator_on_real_answers"]
    print("\n[lana validator, checked against the real model's own output]")
    print(f"  wrong answers caught:            {lv['wrong_answers_caught']}  (n={lv['n_wrong']})")
    print(f"  correct answers wrongly flagged: {lv['correct_answers_wrongly_flagged']}  (n={lv['n_correct']})")

    ais = summary["adversarial_in_scope"]
    print("\n[adversarial suite — in-scope: numeric fabrication]")
    print(f"  precision={ais['precision']}  recall={ais['recall']}  f1={ais['f1']}"
          f"  (tp={ais['tp']} fp={ais['fp']} fn={ais['fn']} tn={ais['tn']})")

    print("\n[adversarial suite — documented blind spots, not expected to be caught]")
    for cap, stats in summary["adversarial_out_of_scope_blind_spots"].items():
        print(f"  {cap:12s}  caught {stats['caught']}/{stats['n']}  (rate={stats['caught_rate']})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="Only run the first N cases (smoke test).")
    parser.add_argument("--model", default=MODEL, help=f"Ollama model tag (default: {MODEL}).")
    args = parser.parse_args()
    main(limit=args.limit, model=args.model)
