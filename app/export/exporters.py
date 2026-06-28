"""PDF and Word document export."""

from __future__ import annotations

import io
import os
import tempfile
from typing import Any, Dict, List, Optional

from fpdf import FPDF
from docx import Document


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def _embed_png_in_pdf(pdf: FPDF, image_bytes: bytes, caption: str = "") -> None:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(image_bytes)
        tmp_path = tmp.name
    try:
        if caption:
            pdf.set_font("Arial", "B", 10)
            pdf.cell(0, 7, caption, ln=True)
        available_h = pdf.h - pdf.get_y() - pdf.b_margin
        img_h = min(90, available_h - 5)
        if img_h < 20:
            pdf.add_page()
            img_h = 90
        pdf.image(tmp_path, x=10, w=180, h=img_h)
        pdf.ln(4)
    finally:
        os.unlink(tmp_path)


def generate_pdf_report(
    report_name: str,
    statistics: Optional[Dict[str, Any]] = None,
    regression: Optional[Dict[str, Any]] = None,
    regression_interp: Optional[str] = None,
    regression_plot: Optional[bytes] = None,
    visualization_plot: Optional[bytes] = None,
    chat_history: Optional[List[Dict]] = None,
) -> bytes:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # Title
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 12, report_name or "LANA Report", ln=True, align="C")
    pdf.ln(2)

    # Statistical analysis
    if statistics:
        pdf.set_font("Arial", "B", 12)
        pdf.cell(0, 9, "Statistical Analysis", ln=True)
        pdf.set_font("Arial", "", 10)
        for k, v in statistics.items():
            pdf.cell(0, 7, f"  {k}: {v}", ln=True)
        pdf.ln(3)

    # Regression results
    if regression:
        pdf.set_font("Arial", "B", 12)
        pdf.cell(0, 9, "Linear Regression", ln=True)
        pdf.set_font("Arial", "", 10)
        for k, v in regression.items():
            pdf.cell(0, 7, f"  {k}: {v}", ln=True)
        if regression_interp:
            pdf.set_font("Arial", "I", 9)
            pdf.multi_cell(0, 6, f"  {regression_interp}")
        pdf.ln(3)

    if regression_plot:
        _embed_png_in_pdf(pdf, regression_plot, "Regression Plot")

    if visualization_plot:
        _embed_png_in_pdf(pdf, visualization_plot, "Visualization")

    # Chat history
    if chat_history:
        pdf.set_font("Arial", "B", 12)
        pdf.cell(0, 9, "Q&A History", ln=True)
        pdf.set_font("Arial", "", 9)
        for entry in chat_history:
            role = entry.get("role", "").upper()
            content = str(entry.get("content", ""))
            pdf.set_font("Arial", "B", 9)
            pdf.cell(0, 6, f"[{role}]", ln=True)
            pdf.set_font("Arial", "", 9)
            pdf.multi_cell(0, 5, content)
            pdf.ln(1)

    return pdf.output(dest="S").encode("latin-1")


# ---------------------------------------------------------------------------
# Word
# ---------------------------------------------------------------------------

def generate_word_report(
    report_name: str,
    statistics: Optional[Dict[str, Any]] = None,
    regression: Optional[Dict[str, Any]] = None,
    regression_interp: Optional[str] = None,
    chat_history: Optional[List[Dict]] = None,
) -> bytes:
    doc = Document()
    doc.add_heading(report_name or "LANA Report", level=0)

    if statistics:
        doc.add_heading("Statistical Analysis", level=1)
        for k, v in statistics.items():
            doc.add_paragraph(f"{k}: {v}", style="List Bullet")

    if regression:
        doc.add_heading("Linear Regression", level=1)
        for k, v in regression.items():
            doc.add_paragraph(f"{k}: {v}", style="List Bullet")
        if regression_interp:
            p = doc.add_paragraph()
            p.add_run(regression_interp).italic = True

    if chat_history:
        doc.add_heading("Q&A History", level=1)
        for entry in chat_history:
            role = entry.get("role", "").upper()
            content = str(entry.get("content", ""))
            p = doc.add_paragraph()
            p.add_run(f"[{role}] ").bold = True
            p.add_run(content)

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.read()