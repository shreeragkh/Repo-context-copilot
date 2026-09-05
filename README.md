# ⚡ Repo Context Copilot

**Ask natural-language questions about any public GitHub repository and get precise, context-grounded answers — powered by Hybrid Search RAG.**

Repo Context Copilot ingests a GitHub repository on-demand, indexes its code and documentation with both semantic vector search and BM25 keyword search, fuses the results, re-ranks them with a cross-encoder, and generates concise answers using a cost-optimised LLM router (free Groq models first, paid OpenAI fallback).

---

## ✨ Features

| Feature | Details |
|---|---|
| **Hybrid Retrieval** | BM25 (keyword) + AstraDB vector search fused via Reciprocal Rank Fusion (RRF) |
| **Adaptive Context** | Query complexity classifier (LOW / MEDIUM / HIGH) dynamically sizes the retrieval budget |
| **Cross-encoder Reranking** | `ms-marco-MiniLM-L-6-v2` re-orders candidates for maximum precision |
| **Smart LLM Router** | Free Groq models with automatic cooldown + OpenAI paid fallback |
| **Redis Caching** | 24-hour answer cache keyed by `repo + commit SHA + normalised query` |
| **Multi-repo Support** | Multiple repositories ingested and queried simultaneously; each gets its own AstraDB collection |
| **TTL Cleanup** | Ingested repos auto-expire and are fully purged (vector DB, BM25 index, cloned files) |
| **Firebase Auth** | Google Sign-In via Firebase; admin-gated endpoints for logs, health, cache management |
| **Comparison Mode** | Side-by-side adaptive vs. baseline (fixed top-k) answer logging for quality analysis |
| **Streamlit UI** | Dark-themed, single-page interface with ingestion status, query, and admin panels |

---

## 🏗️ System Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           USER BROWSER                                  │
│                    Streamlit App  (port 8501)                           │
│        ┌────────────────────────────────────────────────────┐           │
│        │  Ingest Panel  │  Query Panel  │  Admin Dashboard  │           │
│        └────────────┬───────────┬───────────────┬───────────┘           │
│                     │           │               │                        │
│          POST /api/ingest  POST /api/query  GET /api/health etc.        │
└─────────────────────┼───────────┼───────────────┼───────────────────────┘
                      │           │               │
                      ▼           ▼               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    FastAPI Backend  (port 8000)                          │
│                                                                         │
│  ┌─────────────┐   ┌──────────────────┐   ┌───────────────────────┐    │
│  │  Auth Layer │   │  Ingestion       │   │  Query Pipeline       │    │
│  │  (Firebase) │   │  (Background)    │   │                       │    │
│  │             │   │                  │   │  1. Classify complexity│    │
│  │  Google     │   │  1. git clone    │   │     (heuristic / LLM) │    │
│  │  Sign-In    │   │  2. Chunk code   │   │  2. Hybrid retrieval  │    │
│  │  + Session  │   │     & docs       │   │  3. RRF fusion        │    │
│  │  store      │   │  3. Embed chunks │   │  4. Cross-enc rerank  │    │
│  │             │   │  4. AstraDB push │   │  5. Adaptive cutoff   │    │
│  │  Admin-only │   │  5. BM25 build   │   │  6. Token budget trim │    │
│  │  endpoints  │   │  6. Register in  │   │  7. LLM generation    │    │
│  └─────────────┘   │     state        │   │  8. Redis cache write │    │
│                    └──────────────────┘   └───────────────────────┘    │
│                                                                         │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │                    Shared In-Memory State                         │  │
│  │   { repo_name -> IngestedRepo(hybrid_search, bm25, vector, ...) }│  │
│  └───────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
         │                    │                        │
         ▼                    ▼                        ▼
┌─────────────┐     ┌──────────────────┐    ┌──────────────────────┐
│  AstraDB    │     │  Local BM25      │    │  Redis               │
│  (Vector DB)│     │  Index (bm25s)   │    │  (Answer Cache)      │
│             │     │                  │    │                      │
│  Per-repo   │     │  Persisted to    │    │  TTL: 24 h           │
│  collection │     │  ./bm25_index/   │    │  Key: sha256(repo +  │
│  dim=1024   │     │  <repo_name>/    │    │  commit + query)     │
│  COSINE     │     │                  │    │                      │
└─────────────┘     └──────────────────┘    └──────────────────────┘
         │
         │  Embedding model
         ▼
┌─────────────────────────────┐
│  BAAI/bge-large-en-v1.5    │
│  (SentenceTransformer)      │
│  dim = 1024, local CPU/GPU  │
└─────────────────────────────┘
```

### Query Flow (step-by-step)

```
User Query
    │
    ▼
1. Complexity Classification
   ├── Heuristic (fast, keyword-based)
   └── LLM fallback → LOW / MEDIUM / HIGH
    │
    ▼
2. Parallel Retrieval (ThreadPoolExecutor, 15 s timeout)
   ├── BM25Retriever.query()   → keyword matches
   └── VectorRetriever.query() → AstraDB cosine search
    │
    ▼
3. Reciprocal Rank Fusion (RRF)
   weights: BM25 = 0.4, Vector = 0.6, k = 60
    │
    ▼
4. Score Filter  (min_score = 0.2 of normalised RRF)
    │
    ▼
5. Cross-Encoder Reranker (ms-marco-MiniLM-L-6-v2)
    │
    ▼
6. Adaptive Cutoff
   score drop-off threshold per complexity:
   LOW = 8%,  MEDIUM = 10%,  HIGH = 12%
    │
    ▼
7. Token Budget Trim  (context window = 12 000 tokens)
    │
    ▼
8. LLM Generation
   ├── Groq free models (compound-mini → Qwen3.6-27b → ...)
   │   └── auto-cooldown on 429; retries next model
   └── OpenAI paid fallback (gpt-5-mini)
    │
    ▼
9. Redis Cache Write  (on success, TTL 24 h)
    │
    ▼
Answer + Sources + Confidence → User
```

### Ingestion Flow

```
GitHub URL
    │
    ▼
git clone --depth 1  (shallow clone)
    │
    ▼
File Discovery
  ├── Code: .py .js .ts .go .rs .java .rb .c .cpp .cs .php
  └── Docs: .md .rst .txt  (README prioritised)
    │
    ▼
Language-aware Chunking
  ├── Python  → AST-based (functions, classes, methods)
  ├── JS/TS/Go/Rust/... → regex symbol boundaries
  ├── Markdown → header-section splits
  └── Fallback → sliding window (60 lines, 10-line overlap)
    │
    ▼
Chunk splitting  (max 3 000 chars per chunk)
    │
    ├──────────────────────────────────┐
    ▼                                  ▼
Embed chunks                      BM25 Index
(BAAI/bge-large-en-v1.5)         (bm25s, persisted to disk)
batch_size=32                     
    │
    ▼
AstraDB insert_many  (batch_size=50)
Per-repo collection, dim=1024, COSINE metric
    │
    ▼
Register IngestedRepo in server state
(TTL timer starts → APScheduler purges every 2 min)
```

---

## 🗂️ Project Structure

```
repo-context-copilot/
├── backend/
│   ├── main.py           # FastAPI app — all HTTP endpoints
│   ├── config.py         # Centralised settings (loads .env)
│   ├── ingestion.py      # Clone → chunk → embed → index pipeline
│   ├── pipeline.py       # RAG pipeline: classify → retrieve → rerank → generate
│   ├── llm_router.py     # ModelRouter: Groq free → OpenAI paid fallback
│   ├── cache.py          # Redis-backed answer cache
│   ├── state.py          # In-memory server state (repos, vector store, reranker)
│   ├── cleanup.py        # APScheduler: TTL-based repo purge every 2 min
│   ├── logging_utils.py  # Structured logging + per-query log ring buffer
│   ├── auth_pkg/         # Firebase token verification + in-memory session store
│   └── db/
│       ├── chunking.py       # File discovery + language-aware chunking
│       ├── vector_store.py   # AstraDB wrapper (VectorStore, VectorRetriever)
│       ├── bm25_retriever.py # bm25s-based keyword retriever
│       ├── hybrid_search.py  # RRF fusion + parallel retrieval
│       └── reranker.py       # cross-encoder/ms-marco-MiniLM-L-6-v2
├── frontend/
│   └── app.py            # Streamlit UI (ingest, query, admin panels)
├── bm25_index/           # Persisted BM25 indices (one sub-dir per repo)
├── temp/                 # Shallow-cloned repos (auto-cleaned on purge)
├── notebook/             # Jupyter experiments
├── .env                  # Secrets (see Configuration section)
├── pyproject.toml        # uv/pip project metadata
├── requirements.txt      # Flat dependency list
└── run.sh                # One-command launcher (Redis + FastAPI + Streamlit)
```

---

## 🚀 Quick Start

### Prerequisites

- Python 3.13+
- Redis (local instance)
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- An [AstraDB](https://astra.datastax.com) database (free tier works)
- A [Firebase](https://console.firebase.google.com) project with Google Sign-In enabled
- Groq API key (free tier) and/or OpenAI API key

### 1. Clone & Install

```bash
git clone https://github.com/shreeragkh/Repo-context-copilot.git
cd Repo-context-copilot

# Using uv (recommended)
uv sync

# Or using pip
pip install -r requirements.txt
```

### 2. Configure Environment

Create a `.env` file at the project root:

```ini
# AstraDB
API_ENDPOINT="https://<db-id>-<region>.apps.astra.datastax.com"
API_TOKEN="AstraCS:..."

# LLM APIs
GROQ_API_KEY="gsk_..."
OPENAI_API_KEY="sk-proj-..."

# Firebase (from Firebase Console > Project Settings > Your apps)
VITE_FIREBASE_API_KEY="AIza..."
VITE_FIREBASE_AUTH_DOMAIN="your-project.firebaseapp.com"
VITE_FIREBASE_PROJECT_ID="your-project"
VITE_FIREBASE_STORAGE_BUCKET="your-project.firebasestorage.app"
VITE_FIREBASE_MESSAGING_SENDER_ID="999..."
VITE_FIREBASE_APP_ID="1:999...:web:..."

# Admin access
ADMIN_EMAIL="your@email.com"

# Redis (defaults work for local Redis)
REDIS_HOST=localhost
REDIS_PORT=6379

# Optional tuning
GROQ_FREE_MODELS=groq/compound-mini,qwen/qwen3.6-27b
CLASSIFIER_PAID_MODEL=gpt-5-nano
GENERATION_PAID_MODEL=gpt-5-mini
REPO_TTL_MINUTES=60
LOG_COMPARISON_MODE=true
```

### 3. Run

```bash
./run.sh
```

This script:
1. Starts Redis as a background daemon
2. Starts the FastAPI backend on `http://localhost:8000`
3. Waits for the backend health check to pass
4. Starts the Streamlit frontend on `http://localhost:8501`

Open **http://localhost:8501** in your browser.

---

## 🔌 API Reference

All endpoints are served by the FastAPI backend on port 8000.

### Public Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/ingest` | Start async repo ingestion. Body: `{"repo_url": "https://github.com/..."}` |
| `GET` | `/api/ingest/status` | Get ingestion progress and active repos |
| `GET` | `/api/repos` | List all currently ingested repos |
| `POST` | `/api/query` | Query an ingested repo |
| `GET` | `/auth/login` | Google Sign-In popup (served as HTML) |
| `GET` | `/auth/button` | Streamlit custom component for auth button |
| `POST` | `/auth/verify` | Exchange Firebase ID token for session |
| `GET` | `/auth/me` | Get current session info |
| `POST` | `/auth/logout` | Invalidate session |

#### Query Request Body

```json
{
  "query": "How does authentication work?",
  "repo_name": "Hybrid-Search-RAG",
  "top_k": null,
  "top_n": null,
  "min_score": 0.2
}
```

**Public response** (unauthenticated): `{"answer": "..."}`

**Admin response** (with `X-Session-Id` header): includes `sources`, `confidence`, `complexity`, `cached`, `model`, `retrieval_chunks`.

### Admin-only Endpoints

Require `X-Session-Id` header from a verified admin session.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/health` | Redis status, reranker load, per-repo vector counts |
| `GET` | `/api/logs?n=50` | Recent structured server logs |
| `GET` | `/api/query-logs?n=100` | Per-query log: adaptive vs baseline, model used, token counts |
| `POST` | `/api/logs/clear` | Clear server log buffer |
| `POST` | `/api/query-logs/clear` | Clear query log buffer |
| `POST` | `/api/cache/clear` | Flush all Redis cache entries |
| `DELETE` | `/api/ingest/{repo_name}` | Manually purge an ingested repo |

---

## ⚙️ Configuration Reference

All settings live in [`backend/config.py`](backend/config.py) and are sourced from `.env`.

| Variable | Default | Description |
|----------|---------|-------------|
| `API_ENDPOINT` | — | AstraDB REST endpoint URL |
| `API_TOKEN` | — | AstraDB application token (`AstraCS:...`) |
| `GROQ_API_KEY` | — | Groq API key (free tier) |
| `OPENAI_API_KEY` | — | OpenAI API key (paid fallback) |
| `GROQ_FREE_MODELS` | `groq/compound-mini,qwen/qwen3.6-27b` | Comma-separated free model list, tried in order |
| `CLASSIFIER_PAID_MODEL` | `gpt-5-nano` | Paid model for complexity classification |
| `GENERATION_PAID_MODEL` | `gpt-5-mini` | Paid model for answer generation |
| `EMBEDDING_MODEL` | `BAAI/bge-large-en-v1.5` | SentenceTransformer model (dim=1024) |
| `RERANKER_MODEL` | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Cross-encoder for reranking |
| `REDIS_HOST` | `localhost` | Redis hostname |
| `REDIS_PORT` | `6379` | Redis port |
| `REPO_TTL_MINUTES` | `60` | How long an ingested repo stays alive |
| `LOG_COMPARISON_MODE` | `true` | Log adaptive vs. baseline answers for analysis |
| `ADMIN_EMAIL` | — | Only this email can access admin endpoints |
| `CORS_ORIGINS` | `*` | Comma-separated CORS allow-list |

---

## 🧠 Key Design Decisions

### Why Hybrid Search?

Pure vector search can miss exact identifiers (function names, class names, variable names) that BM25 handles well. Pure BM25 misses semantic similarity ("how does auth work?" won't keyword-match `verify_firebase_token`). RRF fusion with 40/60 BM25/vector weighting captures the best of both worlds.

### Why Adaptive Retrieval?

Fetching a fixed top-k for every query wastes context window on simple questions and under-fetches for architectural ones. The complexity classifier (heuristic-first, LLM-fallback) sizes the retrieval budget dynamically: `LOW=10`, `MEDIUM=20`, `HIGH=30` chunks.

### Why the Score Drop-Off Cutoff?

After reranking, results often show a sharp quality cliff. The adaptive cutoff detects when consecutive scores drop by more than 8–12% (relative, depending on complexity) and stops there, preventing low-quality chunks from diluting the prompt.

### Why the LLM Router?

Groq's free tier is fast and sufficient for most queries. By maintaining per-model cooldowns on 429 errors and falling back gracefully to a paid OpenAI model, the system remains available even during free-tier rate limiting — with minimal extra cost.

### Per-repo AstraDB Collections

Each ingested repository gets its own AstraDB collection (named after the sanitised repo name). This provides:
- Complete isolation between repos
- O(1) purge on TTL expiry via `drop_collection`
- No cross-repo result leakage

---

## 🛠️ Development

### Run Backend Only

```bash
cd backend
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### Run Frontend Only

```bash
streamlit run frontend/app.py --server.port 8501
```

### Experiments

See the [`notebook/`](notebook/) directory for Jupyter notebooks used to prototype and validate the retrieval pipeline.

---

## 📦 Core Dependencies

| Package | Purpose |
|---------|---------|
| `fastapi` + `uvicorn` | REST API backend |
| `streamlit` | Web UI |
| `astrapy >= 2.3.1` | AstraDB Data API client |
| `sentence-transformers` | Local embedding model (BAAI/bge-large-en-v1.5) |
| `bm25s` | Fast BM25 index (keyword retrieval) |
| `langchain-groq` | Groq LLM client |
| `langchain-openai` | OpenAI LLM client |
| `redis` | Answer caching |
| `tiktoken` | Token counting for context budget |
| `firebase-admin` | Firebase ID token verification |
| `gitpython` | Shallow-clone GitHub repos |
| `apscheduler` | TTL-based cleanup scheduler |

---

## 📄 License

MIT — see [LICENSE](LICENSE).
