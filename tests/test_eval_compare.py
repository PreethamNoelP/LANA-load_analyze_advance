"""The model-comparison table.

Rendering is a pure function over saved run summaries, so it is testable
without Ollama, a GPU, or the twenty minutes a real multi-model run costs.
That is the point of keeping it out of the runner: the part that needs a
model produces JSON, and everything downstream of that JSON is ordinary code
under ordinary tests.
"""

import json

from eval.compare import load_runs, render_markdown


def _run(model: str, *, lana_correct=0.8, base_correct=0.5, caught=0.4,
         false_flags=0.03, disagreements=()) -> dict:
    return {
        "model": model,
        "n_cases": 40,
        "summary": {
            "conditions": {
                "lana": {"correct_rate": lana_correct, "hallucinated_rate": 0.05},
                "baseline": {"correct_rate": base_correct, "hallucinated_rate": 0.15},
            },
            "lana_validator_on_real_answers": {
                "wrong_answers_caught": caught,
                "correct_answers_wrongly_flagged": false_flags,
            },
            "ground_truth_disagreements": list(disagreements),
        },
    }


def test_one_row_per_model_sorted_by_name():
    table = render_markdown([_run("mistral:7b"), _run("gemma2:2b"), _run("llama3.1:8b")])
    body = [ln for ln in table.splitlines() if ln.startswith("| `")]

    assert len(body) == 3
    assert body[0].startswith("| `gemma2:2b`")
    assert body[2].startswith("| `mistral:7b`")


def test_both_conditions_are_reported_not_just_the_delta():
    # A model whose baseline is already strong and one the grounding rescues
    # are different results; a single "improvement" number hides which.
    table = render_markdown([_run("phi3:mini", lana_correct=0.825, base_correct=0.525)])
    assert "82.5%" in table
    assert "52.5%" in table


def test_missing_metrics_render_as_a_dash_rather_than_crashing():
    sparse = {"model": "half-finished", "n_cases": 3, "summary": {}}
    table = render_markdown([sparse])
    assert "half-finished" in table
    assert "—" in table


def test_no_runs_says_so():
    assert "No eval runs" in render_markdown([])


def test_a_ground_truth_disagreement_is_surfaced_under_the_table():
    # A disagreement between the independent answer key and LANA's own
    # statistics changes how every number above it should be read, so it
    # cannot be left in a JSON file nobody opens.
    table = render_markdown([
        _run("phi3:mini", disagreements=["mean('revenue'): independent=1.0 production=2.0 (DISAGREE)"]),
    ])
    assert "disagreed" in table
    assert "mean('revenue')" in table


def test_clean_runs_carry_no_disagreement_section():
    assert "disagreed" not in render_markdown([_run("phi3:mini")])


def test_runs_load_from_disk(tmp_path):
    path = tmp_path / "run_1.json"
    path.write_text(json.dumps(_run("phi3:mini")), encoding="utf-8")
    assert load_runs([str(path)])[0]["model"] == "phi3:mini"
