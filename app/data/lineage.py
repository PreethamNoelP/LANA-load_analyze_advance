"""Data lineage — an auditable record of every transformation applied.

The rule this module enforces: nothing changes the data without leaving a
record of *what* changed, *how much* changed, *why it was defensible*, and
*whether it discarded anything irreplaceable*. A cleaned dataset with no
ledger is an unattributed claim; with one, every number in the final report
can be traced back to the raw upload.

Records are immutable and ordered. The ledger is append-only.

``reversible``/``inverse`` on a record are audit metadata, not a working
feature: they state whether an operation's effect *could* be reconstructed
and, if so, what parameters that would take — but nothing in LANA actually
reads ``inverse`` to reconstruct a frame. The real, working "undo" is
switching the whole session back to the original version (`/clean/version`);
there is no per-step undo endpoint. Do not present `reversible: true` to a
user as "click to undo" — it is not that.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class TransformRecord:
    """One applied operation, described completely enough to audit — and, in
    principle, to manually reconstruct the prior state from ``inverse`` if the
    caller writes that logic. LANA itself does not apply ``inverse`` anywhere."""

    step: int
    operation: str
    column: str | None
    params: dict[str, Any]

    rows_before: int
    rows_after: int
    columns_before: int
    columns_after: int

    cells_changed: int              # values whose content actually differed
    columns_added: list[str] = field(default_factory=list)

    rationale: str = ""             # why this is statistically defensible
    caveats: list[str] = field(default_factory=list)

    reversible: bool = True
    inverse: dict[str, Any] | None = None   # parameters needed to undo it

    @property
    def rows_removed(self) -> int:
        return self.rows_before - self.rows_after

    @property
    def is_destructive(self) -> bool:
        """True when the operation discarded rows or overwrote originals."""
        return self.rows_removed > 0 or not self.reversible

    def to_dict(self) -> dict[str, Any]:
        out = {
            "step": self.step,
            "operation": self.operation,
            "column": self.column,
            "params": self.params,
            "rows_before": self.rows_before,
            "rows_after": self.rows_after,
            "rows_removed": self.rows_removed,
            "columns_before": self.columns_before,
            "columns_after": self.columns_after,
            "cells_changed": self.cells_changed,
            "columns_added": list(self.columns_added),
            "rationale": self.rationale,
            "caveats": list(self.caveats),
            "reversible": self.reversible,
            "destructive": self.is_destructive,
        }
        if self.inverse is not None:
            out["inverse"] = self.inverse
        return out

    def describe(self) -> str:
        """One-line human summary, used in reports and LLM context."""
        target = f" on '{self.column}'" if self.column else ""
        parts = [f"Step {self.step}: {self.operation}{target}"]
        if self.rows_removed:
            pct = self.rows_removed / self.rows_before * 100 if self.rows_before else 0
            parts.append(f"removed {self.rows_removed:,} rows ({pct:.1f}%)")
        if self.cells_changed:
            parts.append(f"changed {self.cells_changed:,} values")
        if self.columns_added:
            parts.append(f"added {', '.join(self.columns_added)}")
        return " — ".join(parts) + "."


class CleaningLedger:
    """Append-only log of transformations applied to one dataset."""

    def __init__(self) -> None:
        self._records: list[TransformRecord] = []
        self._skipped: list[dict[str, Any]] = []

    # ── Recording ────────────────────────────────────────────────────────────

    def record(
        self,
        operation: str,
        before: pd.DataFrame,
        after: pd.DataFrame,
        *,
        column: str | None = None,
        params: dict[str, Any] | None = None,
        rationale: str = "",
        caveats: list[str] | None = None,
        reversible: bool = True,
        inverse: dict[str, Any] | None = None,
        cells_changed: int | None = None,
    ) -> TransformRecord:
        """Append a record, measuring the change between two frames.

        ``cells_changed`` is measured automatically when the operation
        preserved the row count; pass it explicitly for operations where the
        automatic comparison is not meaningful.
        """
        added = [c for c in after.columns if c not in before.columns]
        if cells_changed is None:
            cells_changed = _count_changed_cells(before, after, column)

        rec = TransformRecord(
            step=len(self._records) + 1,
            operation=operation,
            column=column,
            params=dict(params or {}),
            rows_before=len(before),
            rows_after=len(after),
            columns_before=len(before.columns),
            columns_after=len(after.columns),
            cells_changed=cells_changed,
            columns_added=added,
            rationale=rationale,
            caveats=list(caveats or []),
            reversible=reversible,
            inverse=inverse,
        )
        self._records.append(rec)
        return rec

    def skip(self, operation: str, column: str | None, reason: str) -> None:
        """Record an operation that was requested but not applied, and why.

        Silent no-ops are how a user ends up believing a column was cleaned
        when it was not — every skip is surfaced.
        """
        self._skipped.append({"operation": operation, "column": column, "reason": reason})

    # ── Reading ──────────────────────────────────────────────────────────────

    @property
    def records(self) -> list[TransformRecord]:
        return list(self._records)

    @property
    def skipped(self) -> list[dict[str, Any]]:
        return list(self._skipped)

    @property
    def warnings(self) -> list[str]:
        """Flat list of every caveat and skip reason, for simple UI display."""
        out = [f"{s['operation']} skipped: {s['reason']}" for s in self._skipped]
        for rec in self._records:
            out.extend(rec.caveats)
        return out

    def summary(self, rows_original: int) -> dict[str, Any]:
        """Aggregate impact of the whole pipeline, with data loss called out."""
        rows_final = self._records[-1].rows_after if self._records else rows_original
        rows_lost = rows_original - rows_final
        destructive = [r for r in self._records if r.is_destructive]

        return {
            "steps": len(self._records),
            "rows_original": rows_original,
            "rows_final": rows_final,
            "rows_removed": rows_lost,
            "rows_removed_pct": round(rows_lost / rows_original * 100, 2) if rows_original else 0.0,
            "cells_changed": sum(r.cells_changed for r in self._records),
            "columns_added": [c for r in self._records for c in r.columns_added],
            "destructive_steps": len(destructive),
            "fully_reversible": all(r.reversible for r in self._records) and rows_lost == 0,
            "skipped": self.skipped,
        }

    def to_dict(self, rows_original: int) -> dict[str, Any]:
        return {
            "summary": self.summary(rows_original),
            "steps": [r.to_dict() for r in self._records],
            "narrative": self.narrative(rows_original),
        }

    def narrative(self, rows_original: int) -> str:
        """Plain-English account of the pipeline, for reports and LLM context."""
        if not self._records:
            return "No transformations were applied — this is the raw uploaded data."

        summary = self.summary(rows_original)
        # ASCII arrow deliberately: this narrative is embedded in PDF reports,
        # whose core fonts are latin-1 only and would render it as '?'.
        lines = [
            f"{summary['steps']} transformation(s) applied to the raw upload "
            f"({rows_original:,} rows -> {summary['rows_final']:,} rows)."
        ]
        lines.extend(f"  {rec.describe()}" for rec in self._records)

        if summary["rows_removed"]:
            lines.append(
                f"  Data loss: {summary['rows_removed']:,} rows "
                f"({summary['rows_removed_pct']}%) are present in the raw data "
                f"but absent from this version."
            )
        else:
            lines.append("  No rows were discarded — every uploaded record is still present.")

        if self._skipped:
            lines.append("  Requested but not applied:")
            lines.extend(
                f"    {s['operation']}"
                + (f" on '{s['column']}'" if s["column"] else "")
                + f" — {s['reason']}"
                for s in self._skipped
            )
        return "\n".join(lines)


def _count_changed_cells(
    before: pd.DataFrame,
    after: pd.DataFrame,
    column: str | None,
) -> int:
    """Count values that actually differ between two frames.

    Only meaningful when the row index is preserved; row-dropping operations
    report 0 here because their impact is already captured by ``rows_removed``.
    """
    if len(before) != len(after):
        return 0

    columns = [column] if column else [c for c in before.columns if c in after.columns]
    changed = 0
    for col in columns:
        if col not in before.columns or col not in after.columns:
            continue
        old, new = before[col], after[col]
        try:
            # Compare positionally: an operation may have reset the index.
            old_vals = old.to_numpy()
            new_vals = new.to_numpy()
            if len(old_vals) != len(new_vals):
                continue
            old_na = pd.isna(old_vals)
            new_na = pd.isna(new_vals)
            # A cell changed if its null-ness flipped, or both are non-null
            # and unequal. Comparing NaN to NaN would otherwise read as a change.
            differs = (old_na != new_na) | (~old_na & ~new_na & (old_vals != new_vals))
            changed += int(differs.sum())
        except (TypeError, ValueError):
            # Mixed-type columns can defeat vectorised comparison; fall back to
            # reporting no measurable change rather than crashing the pipeline.
            continue
    return changed
