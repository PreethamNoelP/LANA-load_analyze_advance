import io

import pandas as pd
from fpdf import FPDF
from docx import Document


def generate_pdf_report(df: pd.DataFrame, context: str) -> bytes:
    """Generate a PDF summary report for the uploaded dataset."""
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("Arial", "B", 18)
    pdf.cell(0, 12, "LANA Analysis Report", ln=True, align="C")
    pdf.ln(4)

    pdf.set_font("Arial", "B", 13)
    pdf.cell(0, 9, "Dataset Overview", ln=True)
    pdf.set_font("Arial", "", 10)
    pdf.cell(0, 7, f"  Rows: {len(df):,}", ln=True)
    pdf.cell(0, 7, f"  Columns: {len(df.columns)}", ln=True)
    pdf.cell(0, 7, f"  Column names: {', '.join(df.columns.tolist())}", ln=True)
    pdf.ln(3)

    pdf.set_font("Arial", "B", 13)
    pdf.cell(0, 9, "Data Profile", ln=True)
    pdf.set_font("Arial", "", 9)
    for line in context.splitlines():
        pdf.multi_cell(0, 5, f"  {line}")
    pdf.ln(3)

    numeric_cols = df.select_dtypes("number").columns.tolist()
    if numeric_cols:
        pdf.set_font("Arial", "B", 13)
        pdf.cell(0, 9, "Numeric Column Summary", ln=True)
        pdf.set_font("Arial", "", 9)
        for col in numeric_cols:
            s = df[col].dropna()
            pdf.multi_cell(
                0, 5,
                f"  {col}: "
                f"mean={s.mean():.4g}, std={s.std():.4g}, "
                f"min={s.min():.4g}, max={s.max():.4g}, "
                f"nulls={df[col].isnull().sum()}"
            )
        pdf.ln(3)

    return pdf.output(dest="S").encode("latin-1")


def generate_word_report(df: pd.DataFrame, context: str) -> bytes:
    """Generate a Word document summary report for the uploaded dataset."""
    doc = Document()
    doc.add_heading("LANA Analysis Report", level=0)

    doc.add_heading("Dataset Overview", level=1)
    doc.add_paragraph(f"Rows: {len(df):,}", style="List Bullet")
    doc.add_paragraph(f"Columns: {len(df.columns)}", style="List Bullet")
    doc.add_paragraph(f"Column names: {', '.join(df.columns.tolist())}", style="List Bullet")

    doc.add_heading("Data Profile", level=1)
    for line in context.splitlines():
        if line.strip():
            doc.add_paragraph(line, style="List Bullet")

    numeric_cols = df.select_dtypes("number").columns.tolist()
    if numeric_cols:
        doc.add_heading("Numeric Column Summary", level=1)
        for col in numeric_cols:
            s = df[col].dropna()
            doc.add_paragraph(
                f"{col}: mean={s.mean():.4g}, std={s.std():.4g}, "
                f"min={s.min():.4g}, max={s.max():.4g}, "
                f"nulls={df[col].isnull().sum()}",
                style="List Bullet",
            )

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.read()