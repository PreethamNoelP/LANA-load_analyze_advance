<!--
Keep this short. The point is that a reviewer can tell what changed and what
you checked, without reading the whole diff first.
-->

## What this changes

<!-- The effect, not the file list. -->

## Why

<!-- The problem, the constraint, or the bug. If you tried something else
     first and it did not work, that is worth a line — it saves the next
     person from trying it again. -->

## How it was checked

<!-- Which tests you added or ran, and anything you verified by hand. -->

- [ ] `pytest tests/ -q` passes
- [ ] `ruff check .` passes
- [ ] Frontend touched? `npm run lint`, `npm test` and `npm run build` pass
- [ ] Validator touched? Added both a case it should catch and a nearby one it
      must leave alone (see CONTRIBUTING.md)
- [ ] Capability claims changed? `docs/provenance.md` updated to match

## What this does not do

<!-- Known gaps, deliberate omissions, follow-ups. An honest "not done" list
     is expected here, not a sign of an unfinished PR. -->
