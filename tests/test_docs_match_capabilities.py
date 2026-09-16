"""docs/provenance.md claims to mirror the validator's own capability lists.

It says so in its own text: "Sourced directly from VERIFIED_CLAIM_TYPES and
KNOWN_BLIND_SPOTS ... this section is not an independent description that
could quietly drift from what the code does."

Nothing enforced that, and it had drifted twice — once when the attribution
check was added, once when the collision and central-value checks were. A
document that promises to match the code and doesn't is worse than one that
never promised, because a reader has no way to tell which parts still hold.
This test is the enforcement the claim always implied.
"""

import re
from pathlib import Path

import pytest

from app.llm.validation import capability_summary

DOC = Path(__file__).resolve().parents[1] / "docs" / "provenance.md"


def _normalized(text: str) -> str:
    """Collapse whitespace so line wrapping in the doc is not a difference."""
    return re.sub(r"\s+", " ", text).strip()


@pytest.fixture(scope="module")
def doc_text():
    return _normalized(DOC.read_text(encoding="utf-8"))


@pytest.mark.parametrize("claim", capability_summary()["verifies"])
def test_every_verified_claim_type_appears_in_the_doc(claim, doc_text):
    assert _normalized(claim) in doc_text, (
        "docs/provenance.md is missing a VERIFIED_CLAIM_TYPES entry — it "
        "promises to mirror app/llm/validation.py, so update it:\n\n"
        f"{claim}"
    )


@pytest.mark.parametrize("blind_spot", capability_summary()["does_not_verify"])
def test_every_blind_spot_appears_in_the_doc(blind_spot, doc_text):
    assert _normalized(blind_spot) in doc_text, (
        "docs/provenance.md is missing a KNOWN_BLIND_SPOTS entry. Overclaiming "
        "by omission is the failure this doc exists to prevent:\n\n"
        f"{blind_spot}"
    )
