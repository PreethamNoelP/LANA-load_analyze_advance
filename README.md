<div align="center">

# LANA — Load · Analyze · Advance

### Ask your data anything. Fully local. Zero cloud dependency.

LANA is a **local-first AI data analysis platform** — upload any dataset, ask questions in plain English, and get AI-powered insights, interactive visualizations, and polished reports. Everything runs on your machine.

<br/>

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111+-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-18-61DAFB?style=flat-square&logo=react&logoColor=black)](https://react.dev)
[![Vite](https://img.shields.io/badge/Vite-5-646CFF?style=flat-square&logo=vite&logoColor=white)](https://vitejs.dev)
[![Ollama](https://img.shields.io/badge/Ollama-Local%20LLM-black?style=flat-square)](https://ollama.com)
[![scikit-learn](https://img.shields.io/badge/scikit--learn-1.4+-F7931E?style=flat-square&logo=scikit-learn&logoColor=white)](https://scikit-learn.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)

[**Quick Start**](#️-installation--setup) · [**Architecture**](#️-architecture--system-design) · [**Report a Bug**](https://github.com/PreethamNoelP/LANA-load_analyze_advance/issues)

</div>

---

## 📷 Demo

> **Upload → Ask → Visualize → Export** — all in under 60 seconds.

<div align="center">

| Landing Page | Ask AI | Analyze |
|:---:|:---:|:---:|
| *screenshot* | *screenshot* | *screenshot* |

</div>

> Clone the repo and run it yourself in under 2 minutes — see [Quick Start](#️-installation--setup).

---

## 🧠 Problem Statement

Data analysis today forces a painful trade-off:

- **Cloud tools** (Tableau, Power BI, DataRobot) cost hundreds per month and send your data to third-party servers.
- **Code-based analysis** (pandas, matplotlib) requires Python expertise most teams don't have.
- **AI-powered tools** (ChatGPT plugins, Claude) require an internet connection and surrender data privacy.

For individuals, researchers, and organizations handling sensitive data — **none of these work**.

---

## 💡 Solution Overview

LANA takes a different approach: **bring the AI to the data, not the data to the AI.**

- A **FastAPI backend** wraps production-grade Python analysis modules (pandas, scikit-learn, seaborn) into a clean REST API.
- A **React frontend** delivers a ChatGPT-style interface — upload, ask, visualize, export — with zero configuration.
- An **Ollama integration** runs open-source LLMs (Llama 3, Phi-3, Mistral, Gemma) fully locally — **zero data leaves your machine**.

The result: enterprise-quality data analysis with the simplicity of a chat interface, running entirely offline.

---

## ✨ Key Features

| Feature | Description |
|---|---|
| 🗂️ **Multi-format Upload** | Drag-and-drop CSV, Excel (`.xlsx`/`.xls`), and JSON. Instant schema detection. |
| 🤖 **Natural Language Queries** | Ask plain-English questions. LANA builds structured context from your dataset and queries a local LLM. |
| 📊 **9 Chart Types** | Histogram, Line, Bar, Scatter, Box, Heatmap, Violin, Pie, Area — rendered server-side as crisp PNGs. |
| 📐 **Descriptive Statistics** | Count, mean, median, std, variance, IQR, skewness, kurtosis, and more — per column. |
| 📉 **Linear Regression** | OLS regression with R² score, coefficient, RMSE, and a plain-English model interpretation. |
| 📄 **One-click Export** | Download as CSV, or generate a full PDF or DOCX report with dataset context included. |
| 🔒 **100% Local & Private** | No cloud API. No telemetry. No data leaves your machine. |
| 🎨 **Production UI** | Dark-theme React SPA with a ChatGPT-style chat interface and sticky navigation. |
| 🔌 **Pluggable LLM Backend** | Swap between Ollama and any OpenAI-compatible endpoint (Groq, LM Studio, Together.ai) via `.env`. |

---

## 🏗️ Architecture / System Design

```
┌─────────────────────────────────────────────────────────────────┐
│                     Browser  (React 18 + Vite)                   │
│                                                                  │
│  Landing Page → Upload → [Ask AI | Visualize | Analyze | Export] │
│                              ↕  fetch /api/*                     │
└──────────────────────────────┬──────────────────────────────────┘
                               │  Vite dev proxy  :5173 → :8000
                               ↓
┌─────────────────────────────────────────────────────────────────┐
│                    FastAPI  (Uvicorn ASGI)                        │
│                                                                  │
│  POST /upload     →  pandas.read_csv/excel/json                  │
│                   →  _sessions[uuid] = DataFrame                 │
│                                                                  │
│  POST /query      →  generate_context(df)                        │
│                   →  LLMProvider.answer_question()               │
│                                                                  │
│  POST /chart      →  create_chart(df, type, col)  → PNG bytes   │
│  GET  /stats      →  compute_statistics(series)   → JSON        │
│  POST /regression →  perform_linear_regression()  → JSON        │
│  GET  /export/*   →  generate_pdf / generate_word / df.to_csv() │
└──────────┬──────────────────────────┬────────────────────────────┘
           │                          │
           ↓                          ↓
┌─────────────────┐        ┌─────────────────────────┐
│   app/llm/      │        │  app/analysis/           │
│                 │        │  app/visualization/      │
│  OllamaProvider │        │  app/export/             │
│  OpenAICompat   │        │                          │
│  Provider       │        │  pandas · scikit-learn   │
│  (ABC pattern)  │        │  seaborn · fpdf · docx   │
└──────┬──────────┘        └─────────────────────────┘
       │
       ↓
┌──────────────────┐
│   Ollama :11434  │
│                  │
│   phi3:mini      │
│   llama3.1:8b    │
│   mistral:7b     │
│   (any model)    │
└──────────────────┘
```

**Key design decisions:**

- **In-memory session store** (`dict[uuid, DataFrame]`) — zero-latency reads for local use. Clean upgrade path to Redis for multi-user deployment.
- **Server-side chart rendering** — matplotlib/seaborn runs on the backend; the frontend receives PNG bytes. No JavaScript charting library, consistent quality.
- **LLMProvider ABC** — a pluggable interface makes swapping local ↔ cloud LLMs a single `.env` change.
- **Structured LLM context** — `generate_context()` builds a rich text summary (column types, null rates, sample rows, stats ranges) before every query, dramatically improving answer grounding.
- **Vite proxy** — `/api/*` is proxied at the dev-server level, keeping the same configuration valid behind nginx in production.

---

## ⚙️ Tech Stack

### Frontend
| | Technology | Role |
|---|---|---|
| ⚛️ | React 18 | Component-based SPA |
| ⚡ | Vite 5 | Dev server, HMR, build tool |
| 🎨 | CSS Custom Properties | Dark-theme design token system |

### Backend
| | Technology | Role |
|---|---|---|
| 🚀 | FastAPI | Async REST API framework |
| 🦄 | Uvicorn | ASGI production server |
| 🐼 | pandas | Data loading and manipulation |
| 🔢 | NumPy | Numeric computation |
| 🤖 | scikit-learn | Linear regression (OLS) |
| 📊 | matplotlib + seaborn | Server-side chart rendering |
| 📄 | fpdf2 + python-docx | PDF and Word report generation |
| 🧠 | Ollama SDK | Local LLM integration |
| 🔌 | OpenAI SDK | OpenAI-compatible endpoint support |
| ⚙️ | python-dotenv | Environment configuration |

### Infrastructure
| | Technology | Role |
|---|---|---|
| 🦙 | Ollama | Local LLM runtime |
| 🐍 | Python 3.11+ | Backend runtime |
| 📦 | Node.js 18+ | Frontend toolchain |

---

## 📊 How It Works

**Step 1 — Upload**
```
User drops CSV/Excel/JSON
→ FastAPI parses with pandas
→ DataFrame stored in-memory under a UUID session key
→ Frontend receives: rows, columns, numeric_columns, 8-row preview
```

**Step 2 — Context Generation**
```
Every AI query triggers generate_context(df):
→ Column names + inferred dtypes
→ Null counts, unique value counts per column
→ Statistical summary (mean, std, min, max for numeric columns)
→ First 3 sample rows as text
This context is prepended to the user's question before the LLM call.
```

**Step 3 — LLM Query**
```
User question + structured context → Ollama (local inference)
→ Model reads the dataset summary, understands shape and content
→ Returns a grounded, dataset-specific answer
→ No hallucination about columns that don't exist
```

**Step 4 — Visualization**
```
User picks chart type + column
→ POST /chart → FastAPI runs matplotlib/seaborn
→ Returns raw PNG bytes
→ Frontend creates a blob URL and renders the image inline
```

**Step 5 — Statistical Analysis**
```
Statistics:  pandas Series → 15-metric profile (count, mean, median,
             std, variance, IQR, skewness, kurtosis, percentiles…)

Regression:  scikit-learn OLS → R² score, coefficient, intercept, RMSE
             + auto-generated plain-English model interpretation
```

**Step 6 — Export**
```
CSV   → df.to_csv() streamed as a file download
PDF   → fpdf2 builds a formatted report with dataset summary
DOCX  → python-docx builds an editable Word document
```

---

## 🛠️ Installation & Setup

### Prerequisites

| Requirement | Version |
|---|---|
| Python | 3.11+ |
| Node.js | 18+ |
| Ollama | Latest ([install](https://ollama.com)) |

### 1 — Clone

```bash
git clone https://github.com/PreethamNoelP/LANA-load_analyze_advance.git
cd LANA-load_analyze_advance
```

### 2 — Backend

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### 3 — Environment

```bash
cp .env.example .env
```

```env
# .env
LLM_PROVIDER=ollama
LLM_MODEL=phi3:mini          # or llama3.1:8b, mistral:7b, gemma2:2b
OLLAMA_HOST=http://localhost:11434
LLM_TEMPERATURE=0.3
LLM_MAX_TOKENS=2048
```

### 4 — Frontend

```bash
cd frontend
npm install
```

### 5 — Pull a model

```bash
ollama pull phi3:mini         # ~2 GB — fast and capable
# or
ollama pull llama3.1:8b       # ~5 GB — higher quality
```

---

## ▶️ Usage

Open three terminals:

```bash
# Terminal 1 — Ollama (skip if already running as a service)
ollama serve

# Terminal 2 — Backend API
.venv\Scripts\activate   # or source .venv/bin/activate
uvicorn backend.main:app --reload

# Terminal 3 — Frontend
cd frontend && npm run dev
```

Open **[http://localhost:5173](http://localhost:5173)** in your browser.

### User flow

```
1. Drop a CSV, Excel, or JSON file on the upload screen
   → KPI tiles: row count, column count, numeric/text split
   → Toggle "Show preview" to inspect raw data with internal scroll

2. Ask AI — ChatGPT-style interface
   → "What are the outliers in CPU Change?"
   → "Which feature correlates most with execution time?"
   → Click suggestion chips to start instantly
   → Messages survive tab switches — context is never lost

3. Visualize
   → Select chart type and column → Generate chart
   → Scatter Plot: pick X and Y columns independently

4. Analyze
   → Statistics: pick any numeric column → 15-metric profile
   → Regression: pick X and Y columns → R², coefficient, RMSE + interpretation

5. Export
   → CSV: raw data download
   → PDF: formatted analysis report
   → DOCX: editable Word document
```

### Using an OpenAI-compatible endpoint

```env
LLM_PROVIDER=openai_compat
OPENAI_COMPAT_BASE_URL=https://api.groq.com/openai/v1
OPENAI_COMPAT_API_KEY=gsk_your_key_here
LLM_MODEL=llama-3.1-8b-instant
```

Restart the backend — no other changes required.

---

## 📈 Results / Impact

| Metric | Value |
|---|---|
| Time to first AI insight | < 30s from upload |
| Chart generation latency | ~1–2s (server-side render) |
| LLM response (phi3:mini, CPU) | ~3–8s |
| Supported input formats | CSV, Excel `.xlsx`/`.xls`, JSON |
| Export formats | CSV, PDF, DOCX |
| Chart types | 9 |
| Statistical metrics per column | 15 |
| Data privacy | 100% — zero external network calls |

---

## 🔍 Challenges & Learnings

**Ollama SDK breaking change (v0.3.0+)**
Response objects changed from dicts to typed objects. Fix: wrote a thin provider adapter with a clear `generate()` interface contract. Lesson: isolate third-party SDK calls behind an ABC.

**Server-side vs. client-side charting**
Started with D3.js. Switched to backend matplotlib/seaborn — eliminated a large JS dependency, ensured consistent render quality, and kept all chart logic co-located with the data layer.

**Flex layout + scroll in nested SPAs**
A ChatGPT-style fixed-input / scrollable-thread layout inside nested flex containers silently breaks scroll without `minHeight: 0` on intermediate containers — a non-obvious CSS rule that's easy to miss.

**LLM context quality**
Feeding raw DataFrame strings to the LLM produced unreliable answers. Structured context generation (`generate_context()`) — column types, null rates, sample rows, stat ranges — dramatically improved grounding and reduced hallucination.

**Session management without a database**
In-memory `dict[uuid, DataFrame]` is zero-setup and sufficient for local single-user use. Designed with a clean upgrade path: swap the dict for a Redis-backed store to support multi-user, persistent sessions.

---

## 🚀 Future Improvements

- [ ] **Streaming LLM responses** — token-by-token output via SSE for faster perceived latency
- [ ] **Multi-dataset joins** — upload two files, ask questions across both with pandas merge
- [ ] **Persistent sessions** — save and reload analysis sessions via SQLite
- [ ] **SQL query generation** — LLM writes and executes pandas queries directly on the DataFrame
- [ ] **Auto-correlation insights** — surface the strongest correlations with plain-English summaries
- [ ] **Auth + multi-user** — JWT auth layer + Redis session store for team deployment
- [ ] **Docker Compose** — single-command startup for the full stack
- [ ] **Model benchmarking panel** — side-by-side comparison of answers from multiple local models

---

## 🤝 Contributing

Contributions are welcome.

```bash
# Fork, then clone your fork
git checkout -b feature/your-feature-name

# Make changes, then open a Pull Request
```

**High-impact areas:**
- New chart types → `app/visualization/charts.py`
- New export formats → `app/export/exporters.py`
- New LLM providers → `app/llm/`
- Frontend UX improvements → `frontend/src/components/`

Please open an issue before large changes.

---

## 📄 License

MIT — see [LICENSE](LICENSE) for details.

---

## 👤 Author

<div align="center">

**Preetham Noel P**

*Building tools that make data accessible to everyone.*

[![GitHub](https://img.shields.io/badge/GitHub-PreethamNoelP-181717?style=flat-square&logo=github)](https://github.com/PreethamNoelP)
[![LinkedIn](https://img.shields.io/badge/LinkedIn-Connect-0A66C2?style=flat-square&logo=linkedin)](https://www.linkedin.com/in/preethamnoelp/)

</div>

---

<div align="center">

**If LANA saved you time, a ⭐ helps others find it.**

*Built with Python, React, and a conviction that data tools should be private, fast, and free.*

</div>
