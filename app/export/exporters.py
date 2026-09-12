"""Report generation — PDF and Word summaries of a session.

The exported report is what a user forwards to someone who never touched
LANA, so it is the one artifact that has to stand on its own. That means it
carries the same caveats the UI shows: how the data was transformed, what
was imputed or discarded, which columns are skewed, and the uncertainty
around every headline number. A report quoting a mean without its confidence
interval, or a cleaned row count without saying what was removed, is the
failure mode this module exists to avoid.
"""

import io
from typing import Any

import pandas as pd
from docx import Document
from fpdf import FPDF
from fpdf.enums import XPos, YPos

from ..analysis.statistics import compute_statistics
from ..data.profile import ColumnProfile, profile_dataframe

# A report is a summary, not a data dump. Past this many columns the per
# column detail stops being readable and the file stops being shareable.
MAX_DETAIL_COLUMNS = 40


def _pdf_text(text: str) -> str:
    """Make text safe for fpdf2's built-in core fonts (latin-1 only).

    Characters outside latin-1 are replaced with '?' instead of crashing
    the export. Swap the core font for a Unicode TTF to lift this limit.
    """
    return str(text).encode("latin-1", "replace").decode("latin-1")


def _numeric_summary_lines(
    df: pd.DataFrame,
    profiles: dict[str, ColumnProfile] | None,
) -> tuple[list[tuple[str, list[str]]], int]:
    """Per-column headline figures, each with its uncertainty and caveats.

    Returns ``(summaries, omitted_count)``. The count is reported rather than
    silently dropped: a reader who cannot tell that columns were left out will
    read the ones shown as the whole picture.
    """
    out: list[tuple[str, list[str]]] = []
    if profiles is None:
        # Every other module that takes an optional ``profiles`` derives it
        # when absent (statistics._correlate, cleaner.detect_issues). Doing the
        # same here means the identifier/annotation guard below cannot be
        # bypassed by a caller that simply didn't have profiles to hand.
        profiles = profile_dataframe(df)

    all_numeric = df.select_dtypes("number").columns.tolist()
    numeric_cols = all_numeric[:MAX_DETAIL_COLUMNS]
    omitted = len(all_numeric) - len(numeric_cols)

    for col in numeric_cols:
        profile = profiles.get(col)
        if profile is not None and not profile.is_numeric_measure:
            # Identifiers and LANA's own annotation columns are numeric by
            # dtype but averaging them is meaningless.
            continue

        stats = compute_statistics(df[col])
        centre = stats.get("robust_center", "mean")
        headline = (
            f"{col}: {centre} = "
            f"{stats.get(centre if centre in stats else 'mean')}"
            f", range {stats.get('min')} to {stats.get('max')}"
            f", n = {stats.get('count'):,}"
            f", {stats.get('null_count'):,} missing"
        )
        detail: list[str] = []
        if stats.get("ci95_low") is not None:
            detail.append(
                f"95% CI for the mean: {stats['ci95_low']} to {stats['ci95_high']}"
            )
        if stats.get("shape") and stats["shape"] != "unknown":
            detail.append(f"Distribution is {stats['shape']}; trust the {centre}.")
        detail.extend(stats.get("caveats", [])[:2])
        out.append((headline, detail))

    return out, omitted


# ── PDF ──────────────────────────────────────────────────────────────────────

def generate_pdf_report(
    df: pd.DataFrame,
    context: str,
    *,
    quality: dict[str, Any] | None = None,
    lineage: str | None = None,
    profiles: dict[str, ColumnProfile] | None = None,
) -> bytes:
    """Generate a PDF summary report for the dataset.

    ``quality``, ``lineage`` and ``profiles`` are optional so existing callers
    keep working; supplying them is what turns the file from a statistics dump
    into a defensible report.
    """
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    def heading(text: str, size: int = 13) -> None:
        pdf.set_font("helvetica", "B", size)
        pdf.cell(0, 9, _pdf_text(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def body(text: str, size: int = 9, indent: str = "  ") -> None:
        pdf.set_font("helvetica", "", size)
        pdf.multi_cell(0, 5, _pdf_text(f"{indent}{text}"),
                       new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_font("helvetica", "B", 18)
    pdf.cell(0, 12, "LANA Analysis Report", align="C",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(4)

    # ── Overview ─────────────────────────────────────────────────────────────
    heading("Dataset Overview")
    body(f"Rows: {len(df):,}")
    body(f"Columns: {len(df.columns)}")
    body(f"Column names: {', '.join(str(c) for c in df.columns)}")
    pdf.ln(3)

    # ── Data quality ─────────────────────────────────────────────────────────
    if quality:
        heading("Data Quality")
        body(f"Score: {quality.get('score')} / 100 ({quality.get('grade')})")
        body(f"Missing cells: {quality.get('missing_cells', 0):,} "
             f"({quality.get('missing_pct', 0)}%)")
        for issue in quality.get("issues", []):
            body(f"- {issue['detail']}")
        if quality.get("skewed_columns"):
            body("- Highly skewed (read the median, not the mean): "
                 f"{', '.join(quality['skewed_columns'])}")
        pdf.ln(3)

    # ── Provenance ───────────────────────────────────────────────────────────
    # Placed before the numbers deliberately: a reader must know what was done
    # to the data before they read statistics computed from it.
    if lineage:
        heading("How This Data Was Produced")
        for line in lineage.splitlines():
            if line.strip():
                body(line.strip(), indent="  ")
        pdf.ln(3)

    # ── Per-column figures with uncertainty ──────────────────────────────────
    summaries, omitted = _numeric_summary_lines(df, profiles)
    if summaries:
        heading("Numeric Column Summary")
        for headline, detail in summaries:
            body(headline)
            for line in detail:
                body(line, size=8, indent="      ")
        if omitted:
            body(f"(+{omitted} further numeric column(s) not detailed here.)", size=8)
        pdf.ln(3)

    # ── Raw profile block ────────────────────────────────────────────────────
    heading("Data Profile")
    for line in context.splitlines():
        body(line, indent="  ")

    return bytes(pdf.output())


# ── Word ─────────────────────────────────────────────────────────────────────

def generate_word_report(
    df: pd.DataFrame,
    context: str,
    *,
    quality: dict[str, Any] | None = None,
    lineage: str | None = None,
    profiles: dict[str, ColumnProfile] | None = None,
) -> bytes:
    """Generate a Word summary report for the dataset."""
    doc = Document()
    doc.add_heading("LANA Analysis Report", level=0)

    doc.add_heading("Dataset Overview", level=1)
    doc.add_paragraph(f"Rows: {len(df):,}", style="List Bullet")
    doc.add_paragraph(f"Columns: {len(df.columns)}", style="List Bullet")
    doc.add_paragraph(
        f"Column names: {', '.join(str(c) for c in df.columns)}", style="List Bullet"
    )

    if quality:
        doc.add_heading("Data Quality", level=1)
        doc.add_paragraph(
            f"Score: {quality.get('score')} / 100 ({quality.get('grade')})",
            style="List Bullet",
        )
        doc.add_paragraph(
            f"Missing cells: {quality.get('missing_cells', 0):,} "
            f"({quality.get('missing_pct', 0)}%)",
            style="List Bullet",
        )
        for issue in quality.get("issues", []):
            doc.add_paragraph(issue["detail"], style="List Bullet")
        if quality.get("skewed_columns"):
            doc.add_paragraph(
                "Highly skewed (read the median, not the mean): "
                f"{', '.join(quality['skewed_columns'])}",
                style="List Bullet",
            )

    if lineage:
        doc.add_heading("How This Data Was Produced", level=1)
        for line in lineage.splitlines():
            if line.strip():
                doc.add_paragraph(line.strip(), style="List Bullet")

    summaries, omitted = _numeric_summary_lines(df, profiles)
    if summaries:
        doc.add_heading("Numeric Column Summary", level=1)
        for headline, detail in summaries:
            doc.add_paragraph(headline, style="List Bullet")
            for line in detail:
                doc.add_paragraph(line, style="List Bullet 2")
        if omitted:
            doc.add_paragraph(
                f"(+{omitted} further numeric column(s) not detailed here.)",
                style="List Bullet",
            )

    doc.add_heading("Data Profile", level=1)
    for line in context.splitlines():
        if line.strip():
            doc.add_paragraph(line, style="List Bullet")

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.read()
