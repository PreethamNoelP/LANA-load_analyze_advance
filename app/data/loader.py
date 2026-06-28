"""Multi-format data ingestion layer.

Supported sources
-----------------
  Structured  : CSV, Excel (.xlsx/.xls), JSON, SQLite, any SQLAlchemy-compatible DB
  Unstructured: PDF text → returned as raw string (not DataFrame)
  Manual      : raw CSV text pasted by the user
"""

from __future__ import annotations

import sqlite3
from io import StringIO
from pathlib import Path
from typing import Tuple

import pandas as pd


# ---------------------------------------------------------------------------
# Core loaders
# ---------------------------------------------------------------------------

def load_csv(file) -> pd.DataFrame:
    return pd.read_csv(file)


def load_excel(file) -> pd.DataFrame:
    return pd.read_excel(file)


def load_json(file) -> pd.DataFrame:
    df = pd.read_json(file)
    if isinstance(df, pd.Series):
        df = df.to_frame()
    return df


def load_from_text(text: str) -> pd.DataFrame:
    """Parse raw CSV text (manually pasted by user)."""
    return pd.read_csv(StringIO(text))


def load_sqlite(path: str, query: str | None = None, table: str | None = None) -> pd.DataFrame:
    """Load from a local SQLite file.

    If neither *query* nor *table* is provided the first table is used.
    """
    conn = sqlite3.connect(path)
    try:
        if query:
            df = pd.read_sql(query, conn)
        elif table:
            df = pd.read_sql(f"SELECT * FROM [{table}]", conn)
        else:
            tables = pd.read_sql(
                "SELECT name FROM sqlite_master WHERE type='table'", conn
            )
            if tables.empty:
                raise ValueError("SQLite file contains no tables.")
            first_table = tables.iloc[0]["name"]
            df = pd.read_sql(f"SELECT * FROM [{first_table}]", conn)
    finally:
        conn.close()
    return df


def load_sql(connection_string: str, query: str) -> pd.DataFrame:
    """Load from any SQLAlchemy-compatible database (PostgreSQL, MySQL, etc.)."""
    try:
        from sqlalchemy import create_engine, text
    except ImportError:
        raise ImportError("SQLAlchemy is required: pip install sqlalchemy")
    engine = create_engine(connection_string)
    with engine.connect() as conn:
        return pd.read_sql(text(query), conn)


def load_pdf_as_text(file) -> str:
    """Extract raw text from a PDF file (not converted to DataFrame)."""
    try:
        import pdfplumber
    except ImportError:
        raise ImportError("pdfplumber is required for PDF loading: pip install pdfplumber")
    with pdfplumber.open(file) as pdf:
        pages = [page.extract_text() or "" for page in pdf.pages]
    return "\n".join(pages)


# ---------------------------------------------------------------------------
# Auto-detect loader
# ---------------------------------------------------------------------------

def detect_and_load(file, filename: str = "") -> Tuple[pd.DataFrame | None, str, str | None]:
    """Detect file type from extension and load accordingly.

    Returns
    -------
    (DataFrame | None, file_type, raw_text | None)
      - DataFrame is None for PDF inputs; raw_text holds the extracted text.
      - raw_text is None for all tabular formats.
    """
    name = filename or (getattr(file, "name", "") or "")
    ext = Path(name).suffix.lower()

    if ext in (".xlsx", ".xls"):
        return load_excel(file), "excel", None
    elif ext == ".json":
        return load_json(file), "json", None
    elif ext in (".db", ".sqlite", ".sqlite3"):
        path = file.name if hasattr(file, "name") else str(file)
        return load_sqlite(path), "sqlite", None
    elif ext == ".pdf":
        text = load_pdf_as_text(file)
        return None, "pdf", text
    else:
        return load_csv(file), "csv", None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def list_sqlite_tables(path: str) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        tables = pd.read_sql(
            "SELECT name FROM sqlite_master WHERE type='table'", conn
        )
        return tables["name"].tolist()
    finally:
        conn.close()


def dataframe_preview(df: pd.DataFrame, max_rows: int = 5) -> str:
    """Return a compact text preview of a DataFrame for debugging."""
    return df.head(max_rows).to_string(index=False)