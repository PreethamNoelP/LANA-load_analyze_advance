"""LANA — Load Analytics Advanced  |  Streamlit entry point.

Run with:
    streamlit run app/main.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on sys.path regardless of how streamlit invokes this file
sys.path.insert(0, str(Path(__file__).parent.parent))

import json
import pickle
from io import StringIO
from pathlib import Path

import pandas as pd
import streamlit as st

from app.config import config
from app.analysis.statistics import compute_statistics, generate_context, generate_recommendations
from app.analysis.regression import perform_linear_regression
from app.visualization.charts import create_chart, create_regression_chart, CHART_TYPES
from app.export.exporters import generate_pdf_report, generate_word_report
from app.data.loader import detect_and_load, load_from_text, list_sqlite_tables

# ── Optional deps ─────────────────────────────────────────────────────────────
try:
    from streamlit_lottie import st_lottie
    _LOTTIE = True
except ImportError:
    _LOTTIE = False

# ── Auth ───────────────────────────────────────────────────────────────────────
from app.auth.firebase_auth import initialize_firebase, login_user, create_account

st.set_page_config(
    page_title="LANA: Load Analytics Advanced",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Initialise Firebase once per process ──────────────────────────────────────
@st.cache_resource
def _init_firebase():
    return initialize_firebase()

_db = _init_firebase()
_AUTH_ON = config.auth.enabled and _db is not None

# ── LLM provider (cached so Ollama client is reused across reruns) ─────────────
@st.cache_resource
def _get_llm():
    from app.llm import get_provider
    return get_provider()


# =============================================================================
# Shared state helpers
# =============================================================================

def _session(key, default=None):
    if key not in st.session_state:
        st.session_state[key] = default
    return st.session_state[key]


def _ensure_chats_dir() -> Path:
    d = Path("chats")
    d.mkdir(exist_ok=True)
    return d


# =============================================================================
# Pages
# =============================================================================

# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_lottie(filepath: str):
    try:
        with open(filepath) as f:
            return json.load(f)
    except Exception:
        return None


def _lottie_or_placeholder(data, height: int, key: str, placeholder: str = ""):
    if _LOTTIE and data:
        st_lottie(data, height=height, key=key)
    elif placeholder:
        st.markdown(
            f"<div style='height:{height}px;display:flex;align-items:center;"
            f"justify-content:center;font-size:3rem;'>{placeholder}</div>",
            unsafe_allow_html=True,
        )


# ── Home ──────────────────────────────────────────────────────────────────────

def page_home():
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Montserrat:wght@400;600&display=swap');
        .lana-title{text-align:center;font-size:3.2em;letter-spacing:.08em;
                    font-family:'Montserrat',sans-serif;margin-bottom:.2em;}
        .feature-card{background:#1e1e2e;border-radius:12px;padding:1.2rem;margin:.5rem 0;}
        .feature-card h3{margin:0 0 .4rem 0;color:#f0a500;}
        .feature-card p{margin:0;font-size:.95em;color:#ccc;}
        </style>
        """,
        unsafe_allow_html=True,
    )

    lottie_bars  = _load_lottie("assets/Bars.json")
    lottie_chat  = _load_lottie("assets/ChatBot.json")
    lottie_recomm = _load_lottie("assets/Reccomendation.json")

    st.markdown('<div class="lana-title">📊 LANA: Load Analytics Advanced</div>',
                unsafe_allow_html=True)
    st.markdown(
        "<p style='text-align:center;color:#aaa;font-size:1.1em;'>"
        "Open-source · Model-agnostic · Data-driven analytics platform</p>",
        unsafe_allow_html=True,
    )
    st.divider()

    # Feature rows
    c1, c2 = st.columns(2)
    with c1:
        _lottie_or_placeholder(lottie_bars, 260, "bars", "📊")
    with c2:
        st.markdown(
            """
            <div class="feature-card">
                <h3>Advanced Data Insights</h3>
                <p>Upload CSV, Excel, JSON, SQLite, or paste raw data.
                   Get instant statistics, trends, and AI-powered explanations
                   — all without leaving your browser.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    c3, c4 = st.columns(2)
    with c3:
        st.markdown(
            """
            <div class="feature-card">
                <h3>Local LLM Question Answering</h3>
                <p>Ask plain-English questions about your data. Powered by
                   Ollama (Llama 3, Mistral, Phi-3, Gemma 2 …) — no API key,
                   no cloud, fully private.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with c4:
        _lottie_or_placeholder(lottie_chat, 260, "chat", "🤖")

    c5, c6 = st.columns(2)
    with c5:
        _lottie_or_placeholder(lottie_recomm, 260, "recomm", "💡")
    with c6:
        st.markdown(
            """
            <div class="feature-card">
                <h3>Smart Recommendations</h3>
                <p>LANA inspects your column types and automatically suggests
                   the most meaningful charts and analyses to run.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.divider()
    _, mid, _ = st.columns([2, 1, 2])
    with mid:
        if st.button("🚀  Try LANA", use_container_width=True):
            st.session_state.page = "app"
            st.rerun()


# ── Login ─────────────────────────────────────────────────────────────────────

def page_login():
    st.title("Login")
    email    = st.text_input("Email")
    password = st.text_input("Password", type="password")

    if st.button("Login"):
        if not _AUTH_ON:
            st.warning("Authentication is disabled (Firebase not configured). "
                       "Continuing as guest.")
            st.session_state.update({"login_successful": True, "page": "app"})
            st.rerun()
        elif login_user(_db, email, password):
            st.session_state.update({"login_successful": True,
                                     "user_email": email, "page": "app"})
            st.success("Logged in!")
            st.rerun()
        else:
            st.error("Invalid email or password.")

    if st.button("Don't have an account? Sign Up"):
        st.session_state.page = "signup"
        st.rerun()


# ── Sign up ───────────────────────────────────────────────────────────────────

def page_signup():
    st.title("Create Account")
    email    = st.text_input("Email")
    username = st.text_input("Username")
    password = st.text_input("Password", type="password")
    confirm  = st.text_input("Confirm Password", type="password")

    if st.button("Create Account"):
        if password != confirm:
            st.error("Passwords do not match.")
        elif not _AUTH_ON:
            st.warning("Firebase not configured — account was not persisted. "
                       "Continuing as guest.")
            st.session_state.update({"login_successful": True, "page": "app"})
            st.rerun()
        else:
            ok, msg = create_account(_db, email, password, username)
            if ok:
                st.success(msg)
                st.balloons()
                st.session_state.page = "login"
                st.rerun()
            else:
                st.error(msg)

    if st.button("Already have an account? Login"):
        st.session_state.page = "login"
        st.rerun()


# ── Main app ──────────────────────────────────────────────────────────────────

def page_app():
    chats_dir = _ensure_chats_dir()

    # ── Sidebar ───────────────────────────────────────────────────────────────
    with st.sidebar:
        st.image("https://img.shields.io/badge/LANA-Open--Source-orange", width=180)

        # Auth controls
        if st.session_state.get("login_successful"):
            st.caption(f"Signed in as {st.session_state.get('user_email', 'Guest')}")
            if st.button("Logout"):
                st.session_state.update({"login_successful": False, "page": "login"})
                st.rerun()
        else:
            if st.button("Login"):
                st.session_state.page = "login"
                st.rerun()
            if st.button("Sign Up"):
                st.session_state.page = "signup"
                st.rerun()

        st.divider()

        # ── Model configuration ───────────────────────────────────────────────
        st.subheader("🤖 LLM Model")
        provider_choice = st.selectbox(
            "Provider",
            ["ollama", "openai_compat"],
            index=["ollama", "openai_compat"].index(config.llm.provider)
            if config.llm.provider in ["ollama", "openai_compat"]
            else 0,
            key="provider_select",
        )

        llm = None
        if provider_choice == "ollama":
            from app.llm.ollama_provider import OllamaProvider
            host = st.text_input("Ollama Host", value=config.llm.ollama_host)
            tmp_provider = OllamaProvider(model="", host=host)
            local_models = tmp_provider.list_local_models()

            if local_models:
                model_name = st.selectbox("Model", local_models,
                                          index=0 if config.llm.model not in local_models
                                          else local_models.index(config.llm.model))
            else:
                model_name = st.text_input("Model name", value=config.llm.model,
                                           help="Run `ollama pull llama3.1:8b` first.")
                if not local_models:
                    st.warning("Ollama not reachable or no models pulled yet.")

            llm = OllamaProvider(model=model_name, host=host)

        else:  # openai_compat
            from app.llm.openai_compat import OpenAICompatProvider
            base_url  = st.text_input("Base URL", value=config.llm.openai_compat_base_url,
                                      placeholder="https://api.groq.com/openai/v1")
            api_key   = st.text_input("API Key", type="password",
                                      value=config.llm.openai_compat_api_key)
            model_name = st.text_input("Model", value=config.llm.model,
                                       placeholder="llama-3.1-8b-instant")
            llm = OpenAICompatProvider(model=model_name, base_url=base_url, api_key=api_key)

        # Status badge
        if llm and llm.is_available():
            st.success(f"✓ {llm.name}")
        else:
            st.error("✗ Model not reachable")

        st.divider()

        # ── Session management ────────────────────────────────────────────────
        st.subheader("💾 Sessions")
        session_name = st.text_input("Session name", key="session_name_input")

        chat_files = [f.stem for f in chats_dir.glob("*.pkl")]
        selected   = st.selectbox("Load previous session", ["— new —"] + chat_files)

        if st.button("Load") and selected != "— new —":
            with open(chats_dir / f"{selected}.pkl", "rb") as fh:
                st.session_state["chat_history"] = pickle.load(fh)
            st.success(f"Loaded '{selected}'")

        if st.button("Save") and session_name:
            with open(chats_dir / f"{session_name}.pkl", "wb") as fh:
                pickle.dump(st.session_state.get("chat_history", []), fh)
            st.success(f"Saved '{session_name}'")

        if st.button("🔄 New Chat"):
            st.session_state.pop("chat_history", None)
            st.session_state.pop("df", None)
            st.session_state.pop("pdf_text", None)
            st.rerun()

        st.divider()

        # ── Export ────────────────────────────────────────────────────────────
        st.subheader("📤 Export")
        export_format = st.selectbox("Format", ["PDF", "Word"])
        if st.button("Download Report"):
            kwargs = dict(
                report_name=session_name or "LANA Report",
                statistics=st.session_state.get("last_stats"),
                regression=st.session_state.get("last_regression"),
                regression_interp=st.session_state.get("last_regression_interp"),
                regression_plot=st.session_state.get("last_reg_plot"),
                visualization_plot=st.session_state.get("last_vis_plot"),
                chat_history=st.session_state.get("chat_history"),
            )
            if export_format == "PDF":
                data = generate_pdf_report(**kwargs)
                st.download_button("⬇ PDF", data,
                                   file_name=f"{session_name or 'report'}.pdf",
                                   mime="application/pdf")
            else:
                word_kwargs = {k: v for k, v in kwargs.items()
                               if k not in ("regression_plot", "visualization_plot")}
                data = generate_word_report(**word_kwargs)
                st.download_button("⬇ Word", data,
                                   file_name=f"{session_name or 'report'}.docx",
                                   mime="application/vnd.openxmlformats-officedocument"
                                        ".wordprocessingml.document")

    # =========================================================================
    # Main content
    # =========================================================================
    st.title("LANA — Interactive Data Analytics")

    # ── 1. Data Ingestion ─────────────────────────────────────────────────────
    st.header("1. Load Data")

    tab_upload, tab_text, tab_sqlite, tab_sql = st.tabs(
        ["📂 Upload File", "✏️ Paste CSV", "🗄️ SQLite", "🔗 SQL Database"]
    )

    df: pd.DataFrame | None = st.session_state.get("df")
    pdf_text: str | None    = st.session_state.get("pdf_text")

    with tab_upload:
        uploaded = st.file_uploader(
            "CSV, Excel (.xlsx), JSON, or PDF",
            type=["csv", "xlsx", "xls", "json", "pdf"],
        )
        if uploaded:
            with st.spinner("Loading…"):
                try:
                    result_df, ftype, raw_text = detect_and_load(uploaded, uploaded.name)
                    if ftype == "pdf":
                        st.session_state["pdf_text"] = raw_text
                        pdf_text = raw_text
                        st.info(f"PDF loaded — {len(raw_text):,} characters extracted. "
                                "Use the Q&A section to ask questions about it.")
                    else:
                        st.session_state["df"] = result_df
                        df = result_df
                        st.success(f"Loaded {ftype.upper()} — "
                                   f"{len(df):,} rows × {len(df.columns)} columns")
                except Exception as e:
                    st.error(f"Error loading file: {e}")

    with tab_text:
        raw = st.text_area("Paste CSV data here:", height=200,
                           placeholder="col1,col2\n1,a\n2,b")
        if st.button("Parse CSV", key="parse_csv"):
            try:
                st.session_state["df"] = load_from_text(raw)
                df = st.session_state["df"]
                st.success(f"Parsed — {len(df):,} rows × {len(df.columns)} columns")
            except Exception as e:
                st.error(f"Parse error: {e}")

    with tab_sqlite:
        db_path = st.text_input("SQLite file path", placeholder="/path/to/data.db")
        if db_path:
            from app.data.loader import list_sqlite_tables
            try:
                tables = list_sqlite_tables(db_path)
                if tables:
                    tbl = st.selectbox("Table", tables)
                    custom_q = st.text_input("Custom SQL (optional)",
                                             placeholder="SELECT * FROM mytable WHERE ...")
                    if st.button("Load Table"):
                        from app.data.loader import load_sqlite
                        st.session_state["df"] = load_sqlite(
                            db_path, query=custom_q or None, table=tbl
                        )
                        df = st.session_state["df"]
                        st.success(f"Loaded — {len(df):,} rows")
            except Exception as e:
                st.error(f"SQLite error: {e}")

    with tab_sql:
        conn_str = st.text_input("SQLAlchemy connection string",
                                 placeholder="postgresql://user:pass@host/db")
        query    = st.text_area("SQL Query", placeholder="SELECT * FROM table LIMIT 1000",
                                height=80)
        if st.button("Run Query"):
            from app.data.loader import load_sql
            try:
                st.session_state["df"] = load_sql(conn_str, query)
                df = st.session_state["df"]
                st.success(f"Fetched — {len(df):,} rows")
            except Exception as e:
                st.error(f"Query error: {e}")

    # Show loaded data preview
    if df is not None:
        with st.expander("🔍 Data Preview", expanded=True):
            st.dataframe(df.head(100), use_container_width=True)
            st.caption(f"{len(df):,} total rows · {len(df.columns)} columns")

    # ── 2. Q&A ───────────────────────────────────────────────────────────────
    st.header("2. Ask a Question")

    if df is None and pdf_text is None:
        st.info("Load a dataset above to enable Q&A.")
    else:
        if "chat_history" not in st.session_state:
            st.session_state["chat_history"] = []

        # Render previous messages
        for msg in st.session_state["chat_history"]:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

        question = st.chat_input("Ask anything about your data…")
        if question:
            st.session_state["chat_history"].append(
                {"role": "user", "content": question}
            )
            with st.chat_message("user"):
                st.markdown(question)

            with st.chat_message("assistant"):
                with st.spinner("Thinking…"):
                    try:
                        context = (
                            generate_context(df) if df is not None
                            else f"Document text (first 2000 chars):\n{pdf_text[:2000]}"
                        )
                        answer = llm.answer_question(question, context)
                    except Exception as e:
                        answer = f"Error: {e}"
                st.markdown(answer)
            st.session_state["chat_history"].append(
                {"role": "assistant", "content": answer}
            )

    # ── 3. Recommendations ───────────────────────────────────────────────────
    if df is not None:
        st.header("3. Smart Recommendations")
        recs = generate_recommendations(df)
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("Suggested Visualisations")
            for r in recs["visualization"]:
                st.markdown(f"- {r}")
        with c2:
            st.subheader("Suggested Analyses")
            for r in recs["analysis"]:
                st.markdown(f"- {r}")

    # ── 4. Visualisation ─────────────────────────────────────────────────────
    if df is not None:
        st.header("4. Visualise Data")
        col_vis, col_type = st.columns(2)
        with col_vis:
            vis_col = st.selectbox("Column", df.columns, key="vis_col")
        with col_type:
            chart_type = st.selectbox("Chart type", CHART_TYPES, key="chart_type")

        sec_col = None
        if chart_type == "Scatter Plot":
            numeric_cols = df.select_dtypes(include="number").columns.tolist()
            sec_col = st.selectbox("X-axis column", numeric_cols, key="sec_col")

        if st.button("Generate Chart"):
            with st.spinner("Rendering…"):
                try:
                    img = create_chart(df, chart_type, vis_col, secondary_column=sec_col)
                    st.image(img, use_column_width=True)
                    st.session_state["last_vis_plot"] = img
                except Exception as e:
                    st.error(f"Chart error: {e}")

    # ── 5. Linear Regression ─────────────────────────────────────────────────
    if df is not None:
        st.header("5. Linear Regression")
        numeric_cols = df.select_dtypes(include="number").columns.tolist()

        if len(numeric_cols) < 2:
            st.info("Need at least 2 numeric columns for regression.")
        else:
            rc1, rc2 = st.columns(2)
            with rc1:
                x_col = st.selectbox("X column (predictor)", numeric_cols, key="reg_x")
            with rc2:
                y_col = st.selectbox("Y column (target)", numeric_cols,
                                     index=1, key="reg_y")

            if st.button("Run Regression"):
                try:
                    result = perform_linear_regression(df, x_col, y_col)
                    reg_dict = result.as_dict()

                    st.subheader("Results")
                    res_c1, res_c2, res_c3, res_c4 = st.columns(4)
                    res_c1.metric("R²", f"{result.r2:.4f}")
                    res_c2.metric("Coefficient", f"{result.coefficient:.4f}")
                    res_c3.metric("Intercept", f"{result.intercept:.4f}")
                    res_c4.metric("RMSE", f"{result.rmse:.4f}")

                    st.info(result.interpretation())

                    img = create_regression_chart(
                        result.x_values, result.y_values,
                        result.predictions, x_col, y_col
                    )
                    st.image(img, use_column_width=True)

                    st.session_state["last_regression"]       = reg_dict
                    st.session_state["last_regression_interp"] = result.interpretation()
                    st.session_state["last_reg_plot"]          = img
                except Exception as e:
                    st.error(f"Regression error: {e}")

    # ── 6. Statistical Analysis ───────────────────────────────────────────────
    if df is not None:
        st.header("6. Statistical Analysis")
        stat_col = st.selectbox("Column", df.columns, key="stat_col")

        if st.button("Compute Statistics"):
            stats = compute_statistics(df[stat_col])
            st.session_state["last_stats"] = stats

            # Display in a nice 3-column grid
            items = list(stats.items())
            cols  = st.columns(3)
            for i, (k, v) in enumerate(items):
                with cols[i % 3]:
                    val = f"{v:.4f}" if isinstance(v, float) else str(v)
                    st.metric(k, val)


# =============================================================================
# Router
# =============================================================================

def main():
    if "page" not in st.session_state:
        st.session_state.page = "home"

    page = st.session_state.page
    if page == "home":
        page_home()
    elif page == "app":
        page_app()
    elif page == "login":
        page_login()
    elif page == "signup":
        page_signup()
    else:
        page_home()


if __name__ == "__main__":
    main()