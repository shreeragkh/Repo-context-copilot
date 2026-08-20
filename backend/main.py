import logging
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

import auth_pkg as auth
from cache import retrieval_cache
from cleanup import start_cleanup_scheduler, stop_cleanup_scheduler
from config import settings
from db import Reranker, VectorStore
from ingestion import ingest_repo, repo_name_from_url
from logging_utils import (
    clear_logs, clear_query_logs, get_query_logs, get_recent_logs,
    record_query_log, setup_logging,
)
from pipeline import count_tokens, rag_pipeline
from state import state

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class QueryRequest(BaseModel):
    query: str
    repo_name: str = Field(..., description="Which ingested repo to query (see GET /api/repos).")
    top_k: Optional[int] = Field(default=None, ge=1, le=50)
    top_n: Optional[int] = Field(default=None, ge=1, le=20)
    min_score: float = Field(default=0.2, ge=0.0, le=1.0)


class TokenVerifyRequest(BaseModel):
    id_token: str


class IngestRequest(BaseModel):
    repo_url: str


# ---------------------------------------------------------------------------
# Lifespan - heavy init happens once at startup
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    logger.info("=== RAG API Server starting ===", extra={"component": "startup"})

    state.vector_store = VectorStore()
    state.reranker = Reranker()
    state.cache = retrieval_cache

    start_cleanup_scheduler(interval_minutes=2)

    logger.info("Startup complete.", extra={"component": "startup"})
    yield

    stop_cleanup_scheduler()
    logger.info("=== RAG API Server shutting down ===", extra={"component": "startup"})


app = FastAPI(title="Hybrid Search RAG API", version="1.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Auth dependencies
# ---------------------------------------------------------------------------

def get_optional_session(x_session_id: Optional[str] = Header(default=None)) -> Optional[dict]:
    if not x_session_id:
        return None
    return auth.get_session(x_session_id)


def require_admin(x_session_id: str = Header(...)) -> dict:
    session = auth.get_session(x_session_id)
    if session is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return session


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.get("/auth/login", response_class=HTMLResponse)
def login_page():
    """Serves a minimal Firebase Google Sign-In popup page. Designed to be
    opened as a popup window from the Streamlit 'Admin Login' button; on
    success it posts the session back to the opener via window.postMessage
    and closes itself."""
    return f"""<!DOCTYPE html>
<html><head><title>Admin Sign In</title></head>
<body style="font-family: sans-serif; display:flex; align-items:center; justify-content:center; height:100vh; margin:0;">
  <div style="text-align:center;">
    <h3>Admin Sign In</h3>
    <button id="signin" style="padding:10px 20px; font-size:16px; cursor:pointer;">Sign in with Google</button>
    <p id="status"></p>
  </div>

  <script type="module">
    import {{ initializeApp }} from "https://www.gstatic.com/firebasejs/10.12.2/firebase-app.js";
    import {{ getAuth, GoogleAuthProvider, signInWithPopup }} from "https://www.gstatic.com/firebasejs/10.12.2/firebase-auth.js";

    const firebaseConfig = {{
      apiKey: "{settings.FIREBASE_API_KEY}",
      authDomain: "{settings.FIREBASE_AUTH_DOMAIN}",
      projectId: "{settings.FIREBASE_PROJECT_ID}",
    }};
    const app = initializeApp(firebaseConfig);
    const authClient = getAuth(app);
    const provider = new GoogleAuthProvider();

    document.getElementById("signin").onclick = async () => {{
      document.getElementById("status").innerText = "Signing in...";
      try {{
        const result = await signInWithPopup(authClient, provider);
        const idToken = await result.user.getIdToken();

        const resp = await fetch("/auth/verify", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{ id_token: idToken }}),
        }});
        const data = await resp.json();

        if (data.error) {{
          document.getElementById("status").innerText = data.error;
          return;
        }}

        if (window.opener) {{
          window.opener.postMessage({{ type: "admin_login", ...data }}, "*");
          window.close();
        }} else {{
          document.getElementById("status").innerText = "Signed in as " + data.email;
        }}
      }} catch (e) {{
        document.getElementById("status").innerText = "Sign-in failed: " + e.message;
      }}
    }};
  </script>
</body></html>"""


@app.post("/auth/verify")
def verify_token(req: TokenVerifyRequest):
    try:
        claims = auth.verify_firebase_token(req.id_token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))

    email = claims.get("email", "")
    uid = claims.get("sub", "")
    name = claims.get("name", "")
    picture = claims.get("picture", "")

    if not auth.is_admin(email):
        return {"error": "Access denied. Only the admin can log in.", "session_id": None}

    session_id = auth.create_session(email, uid, name, picture)
    return {
        "session_id": session_id, "email": email, "display_name": name,
        "photo_url": picture, "is_admin": True,
    }


@app.get("/auth/me")
def get_me(x_session_id: str = Header(...)):
    session = auth.get_session(x_session_id)
    if session is None:
        raise HTTPException(status_code=401, detail="Session expired or invalid")
    return session


@app.post("/auth/logout")
def logout(x_session_id: str = Header(...)):
    return {"logged_out": auth.delete_session(x_session_id)}


# ---------------------------------------------------------------------------
# Repo listing (used by the frontend to populate a "which repo" picker)
# ---------------------------------------------------------------------------

@app.get("/api/repos")
def list_repos():
    return {
        "repos": [
            {
                "repo_name": r.repo_name, "commit_sha": r.commit_sha,
                "chunk_count": r.chunk_count, "ingested_at": r.ingested_at.isoformat(),
                "expires_at": r.expires_at.isoformat(),
            }
            for r in state.repos.values()
        ]
    }


# ---------------------------------------------------------------------------
# Query endpoint (PUBLIC - trimmed response for non-admin)
# ---------------------------------------------------------------------------

@app.post("/api/query")
def query_rag(req: QueryRequest, session: Optional[dict] = Depends(get_optional_session)):
    repo = state.repos.get(req.repo_name)
    if repo is None:
        raise HTTPException(
            status_code=404,
            detail=f"Repo '{req.repo_name}' is not currently ingested. See GET /api/repos.",
        )

    is_admin_user = session is not None and session.get("is_admin", False)
    t0 = time.monotonic()

    cache_hit = False
    cached = state.cache.get(repo.repo_name, repo.commit_sha, req.query) if state.cache else None
    if cached is not None:
        result = cached
        cache_hit = True
    else:
        result = rag_pipeline(
            req.query, hybrid_search=repo.hybrid_search, reranker=state.reranker,
            top_k=req.top_k, top_n=req.top_n, min_score=req.min_score, return_context=True,
        )
        if state.cache and result.get("answer") and "Generation failed" not in result["answer"]:
            state.cache.set(repo.repo_name, repo.commit_sha, req.query, result)

    latency_ms = (time.monotonic() - t0) * 1000
    adaptive_tokens = count_tokens(result.get("context", ""))

    baseline_answer = baseline_tokens = baseline_chunks = None
    if settings.LOG_COMPARISON_MODE and not cache_hit:
        baseline = rag_pipeline(
            req.query, hybrid_search=repo.hybrid_search, reranker=state.reranker,
            top_k=20, top_n=10, use_adaptive=False, return_context=True,
        )
        baseline_answer = baseline["answer"]
        baseline_tokens = count_tokens(baseline.get("context", ""))
        baseline_chunks = baseline["final_chunk_count"]

    model_info = result.get("model_info", {})
    record_query_log(
        query=req.query, is_admin=is_admin_user, complexity=result.get("complexity", "UNKNOWN"),
        adaptive_answer=result["answer"], adaptive_chunks=result["final_chunk_count"],
        adaptive_tokens=adaptive_tokens, model_name=model_info.get("model_name", "cache"),
        model_provider=model_info.get("provider", "cache"), model_paid=model_info.get("paid", False),
        baseline_answer=baseline_answer, baseline_chunks=baseline_chunks, baseline_tokens=baseline_tokens,
        cache_hit=cache_hit, latency_ms=latency_ms,
    )

    if is_admin_user:
        return {
            "answer": result["answer"], "sources": result.get("sources", []),
            "confidence": result.get("confidence", 0.0), "complexity": result.get("complexity"),
            "cached": cache_hit, "model": model_info, "retrieval_chunks": result.get("final_chunk_count"),
        }
    return {"answer": result["answer"]}


# ---------------------------------------------------------------------------
# Ingestion (admin only) - multiple repos can be in flight / live at once
# ---------------------------------------------------------------------------

@app.post("/api/ingest")
def start_ingest(req: IngestRequest, background_tasks: BackgroundTasks, _admin: dict = Depends(require_admin)):
    repo_name = repo_name_from_url(req.repo_url)
    if repo_name in state.ingest_in_progress:
        raise HTTPException(status_code=409, detail=f"Ingestion for '{repo_name}' is already running.")
    background_tasks.add_task(ingest_repo, req.repo_url)
    return {"status": "started", "repo_name": repo_name, "repo_url": req.repo_url}


@app.get("/api/ingest/status")
def ingest_status(_admin: dict = Depends(require_admin)):
    return {
        "in_progress": sorted(state.ingest_in_progress),
        "errors": state.ingest_errors,
        "active_repos": [
            {
                "repo_name": r.repo_name, "commit_sha": r.commit_sha,
                "chunk_count": r.chunk_count, "ingested_at": r.ingested_at.isoformat(),
                "expires_at": r.expires_at.isoformat(),
            }
            for r in state.repos.values()
        ],
    }


@app.delete("/api/ingest/{repo_name}")
def delete_repo(repo_name: str, _admin: dict = Depends(require_admin)):
    from ingestion import _purge_repo
    if repo_name not in state.repos:
        raise HTTPException(status_code=404, detail=f"Repo '{repo_name}' is not ingested.")
    _purge_repo(repo_name)
    return {"purged": repo_name}


# ---------------------------------------------------------------------------
# Admin-only endpoints: system health, logs, cache
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health(_admin: dict = Depends(require_admin)):
    return {
        "redis": state.cache.health() if state.cache else False,
        "reranker_loaded": state.reranker is not None,
        "ingest_in_progress": sorted(state.ingest_in_progress),
        "repos": [
            {
                "repo_name": r.repo_name,
                "vector_count": state.vector_store.count(r.collection_name) if state.vector_store else -1,
                "bm25_docs": len(r.bm25_retriever) if r.bm25_retriever else 0,
                "expires_at": r.expires_at.isoformat(),
            }
            for r in state.repos.values()
        ],
    }


@app.get("/api/logs")
def logs(n: int = 100, _admin: dict = Depends(require_admin)):
    return {"logs": get_recent_logs(n)}


@app.post("/api/logs/clear")
def logs_clear(_admin: dict = Depends(require_admin)):
    return {"cleared": clear_logs()}


@app.get("/api/query-logs")
def query_logs(n: int = 100, _admin: dict = Depends(require_admin)):
    """Per-query log: adaptive answer, baseline answer, token reduction %,
    chunks used, and which model (free/paid) answered."""
    return {"logs": get_query_logs(n)}


@app.post("/api/query-logs/clear")
def query_logs_clear(_admin: dict = Depends(require_admin)):
    return {"cleared": clear_query_logs()}


@app.post("/api/cache/clear")
def cache_clear(_admin: dict = Depends(require_admin)):
    state.cache.clear_all()
    return {"cleared": True}
