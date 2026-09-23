"""Tests for the LLM-facing half of Investigate: propose, correct, narrate.

A ``FakeProvider`` scripts the model's replies — the same approach
tests/test_sql_answer.py uses for the SQL-planning path — so the interesting
behaviours (a fenced JSON reply, an invented column name, a model that
ignores the instruction entirely, a narrative call that fails) are all
reproducible without a real model.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.data.profile import profile_dataframe
from app.llm.investigate import (
    InvestigationFailed,
    _extract_json_array,
    _fallback_narrative,
    investigate,
    propose_hypotheses,
)


def rng(seed: int = 909) -> np.random.Generator:
    return np.random.default_rng(seed)


class FakeProvider:
    """Returns queued replies in order; raises if it runs out and asked to."""

    def __init__(self, *replies: str):
        self.replies = list(replies)
        self.prompts: list[tuple[str, str | None]] = []

    def generate(self, prompt: str, system_prompt: str | None = None) -> str:
        self.prompts.append((prompt, system_prompt))
        if not self.replies:
            raise RuntimeError("FakeProvider ran out of scripted replies")
        return self.replies.pop(0)


@pytest.fixture()
def driven_df():
    """'region' genuinely explains 'revenue'; the rest is noise."""
    r, n = rng(), 300
    region = r.choice(["north", "south"], size=n)
    base = np.where(region == "north", 300.0, 100.0)
    return pd.DataFrame({
        "revenue": base + r.normal(0, 20.0, size=n),
        "region": region,
        "channel": r.choice(["web", "store", "phone"], size=n),
        "notes": [f"free text {i}" for i in range(n)],
    })


# ── JSON extraction ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected_len", [
    ('[{"column": "a", "rationale": "x"}]', 1),
    ('```json\n[{"column": "a", "rationale": "x"}]\n```', 1),
    ('Sure, here you go:\n[{"column": "a", "rationale": "x"}]\nHope that helps.', 1),
    ("not json at all", 0),
    ("", 0),
])
def test_extract_json_array_handles_the_usual_model_wrappers(raw, expected_len):
    assert len(_extract_json_array(raw)) == expected_len


# ── Proposal parsing and validation ──────────────────────────────────────────

def test_propose_hypotheses_accepts_a_clean_reply(driven_df):
    profiles = profile_dataframe(driven_df)
    candidates = [profiles["region"], profiles["channel"]]
    provider = FakeProvider(
        '[{"column": "region", "rationale": "geography often drives spend"},'
        ' {"column": "channel", "rationale": "channels have different price points"}]'
    )
    items, note = propose_hypotheses(provider, "revenue", candidates)
    assert note is None
    assert {i["column"] for i in items} == {"region", "channel"}


def test_propose_hypotheses_drops_an_invented_column(driven_df):
    profiles = profile_dataframe(driven_df)
    candidates = [profiles["region"]]
    provider = FakeProvider(
        '[{"column": "region", "rationale": "ok"},'
        ' {"column": "not_a_real_column", "rationale": "made up"}]'
    )
    items, note = propose_hypotheses(provider, "revenue", candidates)
    assert [i["column"] for i in items] == ["region"]


def test_propose_hypotheses_falls_back_when_the_reply_is_unusable(driven_df):
    profiles = profile_dataframe(driven_df)
    candidates = [profiles["region"], profiles["channel"]]
    provider = FakeProvider("I refuse to output JSON, sorry.")
    items, note = propose_hypotheses(provider, "revenue", candidates)
    assert note is not None
    assert len(items) == 2
    assert {i["column"] for i in items} == {"region", "channel"}


# ── Narrative fallback ────────────────────────────────────────────────────────

def test_fallback_narrative_reports_a_supported_driver(driven_df):
    from app.analysis.hypothesis import test_driver

    profiles = profile_dataframe(driven_df)
    result = test_driver(driven_df, "revenue", "region", profiles)
    result.q_value = 0.001
    result.significant = True
    text = _fallback_narrative("revenue", [result])
    assert "region" in text
    assert "0.001" in text or "0.001" in text.replace(" ", "")


def test_fallback_narrative_is_honest_about_a_null_result(driven_df):
    from app.analysis.hypothesis import test_driver

    profiles = profile_dataframe(driven_df)
    result = test_driver(driven_df, "revenue", "channel", profiles)
    result.q_value = 0.9
    result.significant = False
    text = _fallback_narrative("revenue", [result])
    assert "No candidate driver" in text


# ── End-to-end orchestration ──────────────────────────────────────────────────

def test_investigate_ranks_the_real_driver_first(driven_df):
    profiles = profile_dataframe(driven_df)
    provider = FakeProvider(
        '[{"column": "region", "rationale": "geography"},'
        ' {"column": "channel", "rationale": "sales channel"}]',
        "Revenue differs by region and survives correction.",
    )
    report = investigate(provider, driven_df, "revenue", profiles)

    assert report.target_column == "revenue"
    assert report.tests_run == 2
    assert report.hypotheses[0].driver_column == "region"
    assert report.hypotheses[0].significant is True
    assert report.narrative  # the scripted second reply, non-empty


def test_investigate_refuses_a_non_numeric_target(driven_df):
    profiles = profile_dataframe(driven_df)
    provider = FakeProvider()
    with pytest.raises(InvestigationFailed):
        investigate(provider, driven_df, "region", profiles)


def test_investigate_refuses_an_unknown_target(driven_df):
    profiles = profile_dataframe(driven_df)
    provider = FakeProvider()
    with pytest.raises(InvestigationFailed):
        investigate(provider, driven_df, "does_not_exist", profiles)


def test_investigate_uses_the_fallback_narrative_when_the_model_call_fails(driven_df):
    profiles = profile_dataframe(driven_df)
    # One reply for the proposal step; the narrative step then finds the
    # queue empty and FakeProvider raises, which investigate() must absorb.
    provider = FakeProvider(
        '[{"column": "region", "rationale": "geography"}]',
    )
    report = investigate(provider, driven_df, "revenue", profiles)
    assert report.narrative  # fallback narrative, not an exception
