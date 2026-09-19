"""Regenerate the capability block in docs/provenance.md from the code.

``docs/provenance.md`` states that its "Verifies" / "Does not verify" lists
are sourced directly from ``VERIFIED_CLAIM_TYPES`` and ``KNOWN_BLIND_SPOTS``
in ``app/llm/validation.py``. ``tests/test_docs_match_capabilities.py``
enforces that. Until now the syncing itself was manual, which is how the doc
drifted twice before that test existed — the promise was mechanical but the
process was not.

    python -m scripts.regen_provenance          # rewrite the block
    python -m scripts.regen_provenance --check  # exit 1 if it would change

``--check`` is what CI runs: it fails the build on drift without writing to
the working tree.
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.llm.validation import capability_summary  # noqa: E402

DOC = Path(__file__).resolve().parent.parent / "docs" / "provenance.md"

VERIFIES_HEADING = "**Verifies:**"
BLIND_HEADING = "**Does not verify:**"
# The prose that closes the section. Regeneration replaces everything between
# the first heading and this line, so edits to the surrounding narrative are
# preserved while the two lists stay mechanical.
CLOSING_MARKER = "A validation layer that quietly promises more"

# Matches the existing hand-wrapped style so regeneration produces no
# incidental diff noise the first time it runs.
WRAP_WIDTH = 72


def _render(items: tuple[str, ...]) -> str:
    out = []
    for item in items:
        wrapped = textwrap.fill(
            " ".join(item.split()),
            width=WRAP_WIDTH,
            initial_indent="- ",
            subsequent_indent="  ",
        )
        out.append(wrapped)
    return "\n".join(out)


def build_block() -> str:
    summary = capability_summary()
    return (
        f"{VERIFIES_HEADING}\n"
        f"{_render(tuple(summary['verifies']))}\n\n"
        f"{BLIND_HEADING}\n"
        f"{_render(tuple(summary['does_not_verify']))}\n"
    )


def rewrite(text: str) -> str:
    start = text.index(VERIFIES_HEADING)
    end = text.index(CLOSING_MARKER)
    return text[:start] + build_block() + "\n" + text[end:]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true",
        help="exit non-zero if the doc is out of date, without writing it",
    )
    args = parser.parse_args()

    current = DOC.read_text(encoding="utf-8")
    updated = rewrite(current)

    if args.check:
        if current != updated:
            print(
                "docs/provenance.md is out of date with app/llm/validation.py.\n"
                "Run: python -m scripts.regen_provenance",
                file=sys.stderr,
            )
            return 1
        print("docs/provenance.md is in sync.")
        return 0

    if current == updated:
        print("docs/provenance.md already in sync — nothing written.")
        return 0
    DOC.write_text(updated, encoding="utf-8")
    print(f"Rewrote the capability block in {DOC}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
