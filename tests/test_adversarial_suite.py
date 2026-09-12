"""Run eval/'s adversarial validator suite as a regression gate.

The eval harness has two halves. The model-driven half needs a live Ollama and
40 real completions, and belongs outside CI. This half does not: it feeds
scripted right and wrong answers straight into the real `validate_answer()`
against two seeded dataframes, so it is exactly as hermetic and as fast as
anything else in tests/ — it just happened to live in a directory nobody ran
automatically.

That mattered: the aggregate precision/recall this suite measures is the
evidence the project's own docs cite, and until now nothing checked it still
held after a change to the validator.
"""

import pytest

from eval.adversarial import build_cases
from eval.datasets import employee_survey, retail_orders
from eval.harness import run_adversarial

# Buckets KNOWN_BLIND_SPOTS explicitly says are not checked. They are kept in
# the suite because naming what is *not* caught is the point of it, but they
# must not gate a build.
OUT_OF_SCOPE = {"in_range", "causal"}


@pytest.fixture(scope="module")
def adversarial_results():
    dfs = {"retail": retail_orders(), "survey": employee_survey()}
    return run_adversarial(build_cases(dfs["retail"], dfs["survey"]), dfs)


def test_every_in_scope_adversarial_case_behaves_as_documented(adversarial_results):
    in_scope = [r for r in adversarial_results if r["capability"] not in OUT_OF_SCOPE]
    assert in_scope, "expected in-scope adversarial cases to exist"

    mismatches = [
        f"{r['id']} ({r['capability']}): expected flagged={r['should_flag']}, got {r['flagged']}"
        for r in in_scope
        if r["flagged"] != r["should_flag"]
    ]
    assert not mismatches, "validator behaviour changed:\n  " + "\n  ".join(mismatches)


def test_correct_answers_are_not_flagged(adversarial_results):
    # Precision matters more than recall here: a warning on a correct answer
    # teaches the reader to ignore warnings.
    false_alarms = [
        r["id"] for r in adversarial_results
        if not r["should_flag"] and r["flagged"]
    ]
    assert not false_alarms, f"flagged correct answers: {false_alarms}"


def test_the_documented_blind_spots_are_still_blind(adversarial_results):
    # If one of these starts passing, the capability text in
    # app/llm/validation.py is now overclaiming in reverse — it says LANA does
    # not catch something it now does. That is worth a deliberate update
    # rather than a silent drift, so it fails here.
    out_of_scope = [r for r in adversarial_results if r["capability"] in OUT_OF_SCOPE]
    newly_caught = [r["id"] for r in out_of_scope if r["flagged"]]
    assert not newly_caught, (
        f"{newly_caught} are now caught — update KNOWN_BLIND_SPOTS and this "
        "test's OUT_OF_SCOPE set together."
    )
