import sys
import io
import uuid
import math
from pathlib import Path
import pandas as pd
from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.llm import get_provider
from app.llm.ollama_provider import OllamaProvider
from app.analysis.statistics import compute_statistics, generate_context, generate_recommendations
from app.analysis.regression import perform_linear_regression
from app.visualization.charts import create_chart

try:
    from app.export.exporters import generate_pdf_report, generate_word_report
    EXPORT_OK = True
except ImportError:
    EXPORT_OK = False

app = FastAPI(title="LANA API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_sessions: dict[str, pd.DataFrame] = {}


def _session(sid: str) -> pd.DataFrame:
    df = _sessions.get(sid)
    if df is None:
        raise HTTPException(404, "Session not found — upload a dataset first.")
    return df


def _clean(val):
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return None
    return val


def _clean_record(rec: dict) -> dict:
    return {k: _clean(v) for k, v in rec.items()}


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"ok": True}


@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    content = await file.read()
    ext = Path(file.filename).suffix.lower()
    buf = io.BytesIO(content)

    try:
        if ext == ".csv":
            df = pd.read_csv(buf)
        elif ext in (".xlsx", ".xls"):
            df = pd.read_excel(buf)
        elif ext == ".json":
            df = pd.read_json(buf)
        else:
            raise HTTPException(400, f"Unsupported type '{ext}'. Use CSV, Excel, or JSON.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Could not parse file: {e}")

    sid = str(uuid.uuid4())
    _sessions[sid] = df

    numeric_cols = df.select_dtypes("number").columns.tolist()
    preview = [_clean_record(r) for r in df.head(8).to_dict(orient="records")]

    return {
        "session_id": sid,
        "filename": file.filename,
        "rows": len(df),
        "columns": df.columns.tolist(),
        "numeric_columns": numeric_cols,
        "preview": preview,
    }


class QueryReq(BaseModel):
    session_id: str
    question: str


@app.post("/query")
def query(req: QueryReq):
    df = _session(req.session_id)
    context = generate_context(df)
    try:
        provider = get_provider()
        answer = provider.answer_question(req.question, context)
    except Exception as e:
        raise HTTPException(500, f"LLM error: {e}")
    return {"answer": answer}


@app.get("/stats/{session_id}")
def stats(session_id: str, column: str = Query(...)):
    df = _session(session_id)
    if column not in df.columns:
        raise HTTPException(400, f"Column '{column}' not found.")
    s = df[column]
    if not pd.api.types.is_numeric_dtype(s):
        raise HTTPException(400, f"Column '{column}' is not numeric.")
    return {k: _clean(v) for k, v in compute_statistics(s).items()}


class RegressionReq(BaseModel):
    session_id: str
    x_col: str
    y_col: str


@app.post("/regression")
def regression(req: RegressionReq):
    df = _session(req.session_id)
    try:
        result = perform_linear_regression(df, req.x_col, req.y_col)
        return {k: _clean(v) for k, v in result.as_dict().items()}
    except Exception as e:
        raise HTTPException(400, str(e))


class ChartReq(BaseModel):
    session_id: str
    column: str
    chart_type: str
    x_col: str | None = None


@app.post("/chart")
def chart(req: ChartReq):
    df = _session(req.session_id)
    try:
        png = create_chart(df, req.chart_type, req.column, secondary_column=req.x_col)
    except Exception as e:
        raise HTTPException(400, str(e))
    return Response(content=png, media_type="image/png")


@app.get("/recommendations/{session_id}")
def recommendations(session_id: str):
    df = _session(session_id)
    return {"recommendations": generate_recommendations(df)}


@app.get("/models")
def models():
    try:
        return {"models": OllamaProvider().list_local_models()}
    except Exception:
        return {"models": []}


@app.get("/export/csv/{session_id}")
def export_csv(session_id: str):
    df = _session(session_id)
    return Response(
        content=df.to_csv(index=False).encode(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="lana_data.csv"'},
    )


@app.get("/export/pdf/{session_id}")
def export_pdf(session_id: str):
    if not EXPORT_OK:
        raise HTTPException(501, "Export dependencies not installed.")
    df = _session(session_id)
    pdf = generate_pdf_report(df, generate_context(df))
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="lana_report.pdf"'})


@app.get("/export/docx/{session_id}")
def export_docx(session_id: str):
    if not EXPORT_OK:
        raise HTTPException(501, "Export dependencies not installed.")
    df = _session(session_id)
    docx = generate_word_report(df, generate_context(df))
    return Response(
        content=docx,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": 'attachment; filename="lana_report.docx"'},
    )