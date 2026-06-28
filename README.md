# LANA — Load, Analyze, Advance

**LANA** is an open-source, model-agnostic data analytics platform.  
Load any dataset, ask questions in plain English, and get instant charts, statistics, and AI-powered insights — **no API key, no cloud dependency, fully private**.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        Interface Layer                          │
│              Streamlit web app  (app/main.py)                   │
└───────────────┬─────────────────────────┬───────────────────────┘
                │                         │
    ┌───────────▼──────────┐   ┌──────────▼──────────────┐
    │     LLM Layer        │   │      Data Layer          │
    │  app/llm/            │   │  app/data/               │
    │  ┌────────────────┐  │   │  CSV · Excel · JSON      │
    │  │ OllamaProvider │  │   │  SQLite · PostgreSQL     │
    │  │ OpenAICompat   │  │   │  MySQL · PDF             │
    │  └────────────────┘  │   └──────────────────────────┘
    │  Pluggable via .env  │
    └──────────────────────┘
                │
    ┌───────────▼──────────────────────────────────────────┐
    │                   Processing Layer                    │
    │  app/analysis/statistics.py  — descriptive stats      │
    │  app/analysis/regression.py  — linear regression      │
    │  app/visualization/charts.py — 9 chart types          │
    │  app/export/exporters.py     — PDF & Word reports     │
    └───────────────────────────────────────────────────────┘
                │
    ┌───────────▼──────────┐
    │    Auth Layer        │
    │  app/auth/           │
    │  Firebase Firestore  │
    │  (optional)          │
    └──────────────────────┘
```

### Component breakdown

| Module | Purpose |
|---|---|
| `app/config.py` | Reads all settings from `.env`; single source of truth |
| `app/llm/base.py` | `LLMProvider` ABC — swap backends without touching the UI |
| `app/llm/ollama_provider.py` | Local LLM via [Ollama](https://ollama.ai) |
| `app/llm/openai_compat.py` | Any OpenAI-compatible API (Groq, LM Studio, Together.ai…) |
| `app/data/loader.py` | Auto-detect & load CSV / Excel / JSON / SQLite / PDF |
| `app/analysis/statistics.py` | Descriptive stats + rich LLM context generation |
| `app/analysis/regression.py` | OLS linear regression with interpretation |
| `app/visualization/charts.py` | 9 chart types, returned as PNG bytes |
| `app/export/exporters.py` | PDF and Word report generation |
| `app/auth/firebase_auth.py` | Optional Firebase Firestore user auth |

---

## Features

- **Multi-format data ingestion** — CSV, Excel, JSON, SQLite, SQL databases, PDF text, manual paste
- **Local LLM Q&A** — ask questions about your data; powered by Ollama (Llama 3, Mistral, Phi-3, Gemma 2, Qwen2.5 …)
- **OpenAI-compatible APIs** — drop-in support for Groq, LM Studio, Together.ai, Fireworks, Anyscale
- **9 chart types** — line, bar, scatter, histogram, box, heatmap, violin, pie, area
- **Linear regression** — R², coefficients, RMSE, plain-English interpretation
- **Rich statistical analysis** — mean, median, mode, std, variance, IQR, skewness, kurtosis
- **Smart recommendations** — LANA suggests the most relevant charts and analyses for your data
- **PDF & Word export** — reports include stats, regression results, and charts
- **Session persistence** — save and reload Q&A sessions
- **Firebase auth** — optional login/signup; runs in guest mode without credentials
- **Zero API keys required** — fully local with Ollama

---

## Quick Start

### 1. Clone & install

```bash
git clone https://github.com/PreethamNoelP/LANA-load_analyze_advance.git
cd LANA-load_analyze_advance
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env — at minimum set LLM_PROVIDER and LLM_MODEL
```

### 3. Start Ollama and pull a model

Install Ollama from **https://ollama.ai**, then:

```bash
ollama serve               # start the Ollama daemon
ollama pull llama3.1:8b    # ~4.7 GB — good all-rounder
# or smaller options:
ollama pull phi3:mini      # ~2.2 GB — fast on CPU
ollama pull gemma2:2b      # ~1.6 GB — very fast
```

### 4. Copy Lottie animation assets (optional)

The home page animations are large JSON files not included in this repo.  
Copy them from your original project:

```
assets/Bars.json
assets/ChatBot.json
assets/Reccomendation.json
```

The app works without them — it shows emoji placeholders instead.

### 5. Run

```bash
streamlit run app/main.py
```

Open **http://localhost:8501** in your browser.

---

## How to Switch Models

All model settings live in `.env`. No code changes needed.

**Switch to a different Ollama model:**
```ini
LLM_MODEL=mistral:7b
```

**Switch to Groq (free API, open-source models):**
```ini
LLM_PROVIDER=openai_compat
OPENAI_COMPAT_BASE_URL=https://api.groq.com/openai/v1
OPENAI_COMPAT_API_KEY=gsk_your_key_here
LLM_MODEL=llama-3.1-8b-instant
```

**Switch to LM Studio (local, no Ollama needed):**
```ini
LLM_PROVIDER=openai_compat
OPENAI_COMPAT_BASE_URL=http://localhost:1234/v1
OPENAI_COMPAT_API_KEY=lm-studio
LLM_MODEL=your-model-name
```

---

## How to Connect New Datasets

| Source | How |
|---|---|
| CSV file | Upload via UI or paste raw CSV text |
| Excel (.xlsx) | Upload via UI |
| JSON | Upload via UI |
| SQLite | Enter file path in the SQLite tab |
| PostgreSQL | Enter `postgresql://user:pass@host/db` + SQL query in SQL tab |
| MySQL | Enter `mysql+pymysql://user:pass@host/db` + SQL query |
| PDF | Upload via UI (text is extracted; use Q&A to query it) |

---

## Research Summary — Why This Stack

| Tool | Choice | Reason |
|---|---|---|
| **LLM runner** | [Ollama](https://ollama.ai) | Easiest local setup on Windows/Mac/Linux; single binary; supports 100+ models; simple Python client |
| **OpenAI-compat fallback** | `openai` package | One interface covers Groq (free), Together.ai, LM Studio, Fireworks — model-agnostic |
| **Best models for tabular Q&A** | Llama 3.1 8B · Phi-3 Mini · Gemma 2 2B | Strong instruction following; fit in 8 GB VRAM or CPU RAM |
| **Orchestration** | Direct Ollama client (no LangChain) | This use case is single-turn Q&A — LangChain overhead is unnecessary; raw client is simpler and faster |
| **Vector DB (optional RAG)** | ChromaDB | Pure Python, zero-config, persistent; best for prototyping; easy to swap for Qdrant in production |
| **Data loading** | pandas + SQLAlchemy + pdfplumber | Battle-tested; covers 95% of real-world data sources |
| **UI** | Streamlit | Maintained; rapid iteration; excellent dataframe + chart widgets |

---

## Fine-Tuning & Adaptability

LANA's LLM layer is **fully pluggable**:

1. **Swap model** — change `LLM_MODEL` in `.env`
2. **Fine-tune with Ollama** — create a `Modelfile`, run `ollama create my-model -f Modelfile`
3. **Use a fine-tuned HuggingFace model** — serve it with [vLLM](https://github.com/vllm-project/vllm) and point `OPENAI_COMPAT_BASE_URL` at it
4. **RAG on custom data** — enable `ENABLE_RAG=true`, install `chromadb` and `sentence-transformers`, and extend `app/data/loader.py` with a chunking + embedding pipeline

---

## Future Improvements

- [ ] **RAG pipeline** — ChromaDB + `nomic-embed-text` embeddings for large document Q&A
- [ ] **Multi-table SQL** — join explorer and schema browser
- [ ] **Streaming responses** — real-time token streaming in the chat UI
- [ ] **Chart → LLM insight** — auto-describe generated charts using vision models
- [ ] **Fine-tune UI** — upload labelled Q&A pairs and trigger Ollama fine-tuning from the browser
- [ ] **Docker deployment** — one-command `docker compose up` with Ollama sidecar
- [ ] **User workspaces** — per-user dataset and session storage
- [ ] **Batch analysis** — process multiple files in one run

---

## Project Structure

```
LANA-load_analyze_advance/
├── app/
│   ├── main.py                  # Streamlit entry point
│   ├── config.py                # All settings (reads .env)
│   ├── llm/
│   │   ├── base.py              # LLMProvider ABC
│   │   ├── ollama_provider.py   # Ollama backend
│   │   └── openai_compat.py     # OpenAI-compatible backend
│   ├── data/
│   │   └── loader.py            # Multi-format data ingestion
│   ├── analysis/
│   │   ├── statistics.py        # Descriptive stats + LLM context
│   │   └── regression.py        # Linear regression
│   ├── visualization/
│   │   └── charts.py            # 9 chart types → PNG bytes
│   ├── export/
│   │   └── exporters.py         # PDF & Word reports
│   └── auth/
│       └── firebase_auth.py     # Optional Firebase auth
├── assets/                      # Lottie JSON animations (copy from original)
├── chats/                       # Session pickle files (auto-created)
├── .env.example                 # Environment variable template
├── requirements.txt
└── README.md
```

---

## Contact

- LinkedIn: https://www.linkedin.com/in/preetham-noel-p-752564268/
- Email: preethamnoel.05@gmail.com