# Hybrid Search RAG - Backend

Production-oriented FastAPI backend, ported from `experiment.ipynb`.

## What changed vs. the notebook

- **No judge/eval LLM in the app.** `judge_llm` in the notebook only scored answers
  in the offline `run_eval()` harness - it never generated a user-facing answer, so
  it's not part of this backend at all.
- **Model routing (`llm_router.py`).** Every classification/generation call first
  tries your configured `GROQ_FREE_MODELS` (in order). If Groq returns a rate-limit
  / quota error, that model is put in cooldown and the router tries the next free
  model, then falls back to the paid OpenAI model (`gpt-5-nano` for classification,
  `gpt-5-mini` for generation - same models you had). Every answer records which
  model/provider answered and whether it was paid.
- **Multiple repos, fully isolated (`state.py`, `db/vector_store.py`).** Each
  ingested repo gets its **own AstraDB collection**, named after the repo
  (`sanitize_collection_name`, e.g. `Hybrid-Search-RAG` -> `hybrid_search_rag`),
  and its own BM25 index directory. Several repos can be live at once; queries
  pick one via `repo_name`. Re-ingesting the same repo name replaces that one
  repo only - it never touches the others.
- **Repo TTL cleanup (`cleanup.py` + `ingestion.py`).** Each ingested repo gets its
  own `expires_at` (default 60 min from ingestion, `REPO_TTL_MINUTES`). A background
  job checks every 2 minutes and, per repo that's expired, deletes: the cloned repo
  files, the BM25 index directory, that repo's entire AstraDB collection, and its
  Redis cache entries. Other still-live repos are untouched.
- **Admin dashboard data.** `/api/health` (system health), `/api/logs` (general app
  logs), `/api/query-logs` (per-query: adaptive answer, baseline answer, token
  reduction %, chunks used, model name, free/paid).
- **`LOG_COMPARISON_MODE`** (default `true`): when on, every *new* (non-cached)
  query also runs a fixed-budget baseline pass purely so the admin log can show the
  adaptive-vs-baseline diff, like `run_eval()` did. This is a 2x-LLM-call cost per
  query - turn it off in `.env` once you're happy with the pipeline, since it works
  against your free-tier-budget goal.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in Firebase, AstraDB, Redis, Groq, OpenAI keys
uvicorn main:app --reload --port 8000
```

Redis must be reachable (local `redis-server`, or a hosted instance) for caching
and for repo-scoped cache purging to work; if it's down, the app degrades
gracefully (logs a warning, skips the cache) rather than failing requests.

## Key endpoints

| Method | Endpoint | Access | Notes |
|---|---|---|---|
| `GET`  | `/api/repos` | Public | List currently-ingested repos (name, chunk count, TTL) - use this to populate a "which repo" picker in the chat UI. |
| `POST` | `/api/query` | Public | `{"query": "...", "repo_name": "..."}`. Trimmed response for non-admin. 404s if `repo_name` isn't currently ingested. |
| `POST` | `/api/ingest` | Admin | `{"repo_url": "..."}` - clones, chunks, embeds into its own collection, indexes. Runs alongside any other already-ingested repo. Re-ingesting the same repo replaces just that one. |
| `GET`  | `/api/ingest/status` | Admin | In-progress ingestions, last errors per repo, all active repos + TTLs. |
| `DELETE` | `/api/ingest/{repo_name}` | Admin | Manually purge one repo before its TTL. |
| `GET`  | `/auth/login` | Public | Firebase Google Sign-In popup page (open with `window.open`, listen for `postMessage`). |
| `POST` | `/auth/verify` | Public | Verifies ID token, creates a session **only if** the email matches `ADMIN_EMAIL`. |
| `GET`  | `/auth/me` / `POST /auth/logout` | Session | |
| `GET`  | `/api/health` | Admin | Redis health, per-repo vector/BM25 doc counts + TTL. |
| `GET`  | `/api/logs` | Admin | General structured app logs. |
| `GET`  | `/api/query-logs` | Admin | Per-query adaptive/baseline/model comparison log. |
| `POST` | `/api/cache/clear`, `/api/logs/clear`, `/api/query-logs/clear` | Admin | |

## Admin login flow for the Streamlit frontend

`/auth/login` is meant to be opened via `window.open(...)` from a "Login" button.
On success it calls `window.opener.postMessage({type: "admin_login", session_id, ...})`
and closes itself; the Streamlit host page (a small HTML/JS component) listens for
that message and stores `session_id`, then sends it back to Streamlit via a query
param or a custom component callback, same pattern the original `streamlit_app.py`
used with `st.query_params`.

## Not included here (backend-only deliverable)

You asked specifically for the backend. The Streamlit frontend (public chat UI +
admin dashboard with health/logs/comparison tables + the login popup wiring) is a
separate follow-up - happy to build that next against these exact endpoints.
