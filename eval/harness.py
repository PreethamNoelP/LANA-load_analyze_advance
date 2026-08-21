"""Runs one case under one condition, or the whole adversarial suite.

Two conditions, same real model, same question:

* ``baseline`` — the context LANA itself used to build before grounding
  existed (``app.analysis.statistics.generate_context``, imported as
  ``naive_context`` below — this is the project's own prior, documented
  approach, not a strawman invented for this benchmark), a generic system
  prompt with no refusal licensing, and no post-hoc check.
* ``lana`` — the real, current pipeline: ``app.llm.context.build_context``
  + ``ANSWER_SYSTEM_PROMPT`` + ``app.llm.validation.validate_answer``.

Every LLM call goes through the real, configured ``OllamaProvider`` — see
``eval/run.py`` for how it is constructed and its explicit availability check.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import pandas as pd

from app.analysis.statistics import generate_context as naive_context
from app.llm.context import build_context
from app.llm.ollama_provider import OllamaProvider
from app.llm.validation import validate_answer

from . import grading
from .adversarial import AdversarialCase
from .ground_truth import GroundTruth, resolve as resolve_ground_truth

NAIVE_SYSTEM_PROMPT = (
    "You are a helpful data analyst assistant. Answer the user's question "
    "using the dataset information provided below."
)


@dataclass
class CaseResult:
    case_id: str
    dataset: str
    category: str
    condition: str          # "baseline" | "lana"
    question: str
    raw_answer: str
    verdict: str             # "correct" | "incorrect" | "partial" | "hedged" |
                              # "hallucinated" | "ambiguous" | "error"
    ground_truth_note: str
    latency_s: float
    validator_flagged: bool | None = None    # only meaningful for condition == "lana"
    validator_warnings: list[str] = field(default_factory=list)


def _naive_prompt(question: str, context_text: str) -> str:
    return f"{context_text}\n\nQuestion: {question}"


def grade(gt: GroundTruth, answer: str) -> str:
    if gt.kind == "unanswerable":
        return "correct" if grading.looks_like_refusal(answer) else "hallucinated"

    if gt.kind == "categorical":
        return grading.grade_categorical(gt.value, gt.candidates, answer)

    numbers = grading.extract_numbers(answer)

    if gt.kind == "multi":
        hits = sum(
            1 for part in gt.value
            if grading.numeric_matches(part.value, numbers, part.tolerance, part.tolerance_kind)
        )
        if hits == len(gt.value):
            return "correct"
        return "partial" if hits > 0 else "incorrect"

    # kind == "numeric"
    if not numbers:
        return "incorrect"
    matched = grading.numeric_matches(gt.value, numbers, gt.tolerance, gt.tolerance_kind)
    return "correct" if matched else "incorrect"


def run_case(case: dict, df: pd.DataFrame, provider: OllamaProvider, condition: str) -> CaseResult:
    gt = resolve_ground_truth(df, case["gt"])
    question = case["question"]
    start = time.perf_counter()

    if condition == "lana":
        context = build_context(df)
        try:
            answer = provider.answer_question(question, context.text)
        except Exception as exc:  # noqa: BLE001 — a failed call is itself a result, not a crash
            return CaseResult(case["id"], case["dataset"], case["category"], condition,
                               question, f"[error: {exc}]", "error", gt.note,
                               time.perf_counter() - start)
        validation = validate_answer(answer, context)
        verdict = grade(gt, answer)
        return CaseResult(
            case["id"], case["dataset"], case["category"], condition, question, answer,
            verdict, gt.note, time.perf_counter() - start,
            validator_flagged=not validation.trustworthy,
            validator_warnings=list(validation.warnings),
        )

    # condition == "baseline"
    ctx_text = naive_context(df)
    try:
        answer = provider.generate(_naive_prompt(question, ctx_text), system_prompt=NAIVE_SYSTEM_PROMPT)
    except Exception as exc:  # noqa: BLE001
        return CaseResult(case["id"], case["dataset"], case["category"], condition,
                           question, f"[error: {exc}]", "error", gt.note,
                           time.perf_counter() - start)
    verdict = grade(gt, answer)
    return CaseResult(case["id"], case["dataset"], case["category"], condition, question,
                       answer, verdict, gt.note, time.perf_counter() - start)


def run_adversarial(cases: list[AdversarialCase], dfs: dict[str, pd.DataFrame]) -> list[dict]:
    """No model calls — checks scripted answers straight against the real validator."""
    results = []
    for case in cases:
        context = build_context(dfs[case.dataset])
        validation = validate_answer(case.text, context)
        results.append({
            "id": case.id,
            "dataset": case.dataset,
            "capability": case.capability,
            "should_flag": case.should_flag,
            "flagged": not validation.trustworthy,
            "text": case.text,
            "note": case.note,
            "warnings": list(validation.warnings),
        })
    return results
