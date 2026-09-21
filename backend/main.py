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
from eval_harness import score_query_async
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
    allow_origins=["*"],
    allow_credentials=False,
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


class DirectAdminLoginRequest(BaseModel):
    email: str


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.get("/auth/button", response_class=HTMLResponse)
def auth_button_component():
    """Streamlit custom component (declare_component URL).
    Served at localhost:8000 — SAME origin as /auth/login popup.
    Popup sends postMessage to this iframe (no cross-origin issues!).
    This iframe then sends streamlit:setComponentValue → Python reads session_id."""
    return """<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body {
    display:flex; justify-content:flex-end; align-items:center;
    min-height:52px; background:transparent;
    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
    padding:4px 0;
  }
  #btn {
    display:inline-flex; align-items:center; gap:9px;
    background:linear-gradient(135deg,#4f46e5 0%,#3b82f6 100%);
    color:white; border:none; padding:9px 20px; font-size:13.5px;
    font-weight:700; border-radius:22px; cursor:pointer;
    box-shadow:0 4px 14px rgba(59,130,246,0.5); white-space:nowrap;
    transition:transform 0.15s ease,box-shadow 0.15s ease;
  }
  #btn:hover { transform:translateY(-2px); box-shadow:0 7px 20px rgba(59,130,246,0.65); }
  #btn:active { transform:translateY(0); opacity:0.88; }
  #btn:disabled { opacity:0.55; cursor:not-allowed; transform:none; }
  #err { margin-top:4px; font-size:11px; color:#f87171; display:none; text-align:right; }
</style>
</head>
<body>
<div>
  <button id="btn" onclick="openLogin()">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
      <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
      <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
      <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/>
      <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
    </svg>
    Sign in with Google
  </button>
  <div id="err"></div>
</div>

<script>
  var popup = null;

  // ── Streamlit component protocol ──────────────────────────────────────────
  // Tell Streamlit this component is ready
  window.parent.postMessage({isStreamlitMessage:true, type:"streamlit:componentReady", apiVersion:1}, "*");
  // Set iframe height so Streamlit renders it correctly
  window.parent.postMessage({isStreamlitMessage:true, type:"streamlit:setFrameHeight", args:{height:52}}, "*");

  function sendToStreamlit(value) {
    window.parent.postMessage({
      isStreamlitMessage: true,
      type: "streamlit:setComponentValue",
      args: {value: value}
    }, "*");
  }

  // ── Listen for auth result from the popup ────────────────────────────────
  // Popup is at localhost:8000/auth/login — SAME ORIGIN as this page.
  // So postMessage from popup to window.opener (this page) has NO cross-origin restriction.
  window.addEventListener("message", function(event) {
    // Only handle messages from our own popup or same origin
    if (event.data && event.data.type === "auth_success" && event.data.session_id) {
      var btn = document.getElementById("btn");
      btn.disabled = true;
      btn.lastChild.data = " Authenticated!";
      // Send session_id to Streamlit Python side via component value protocol
      sendToStreamlit({session_id: event.data.session_id});
    }
    if (event.data && event.data.type === "auth_error") {
      showErr(event.data.message);
    }
  });

  function showErr(msg) {
    var e = document.getElementById("err");
    e.textContent = "\\u26d4 " + msg;
    e.style.display = "block";
    var btn = document.getElementById("btn");
    btn.disabled = false;
    btn.lastChild.data = " Sign in with Google";
  }

  function openLogin() {
    var btn = document.getElementById("btn");
    btn.disabled = true;
    btn.lastChild.data = " Opening sign-in\\u2026";
    document.getElementById("err").style.display = "none";

    var w = 500, h = 600;
    var left = Math.round((screen.width  - w) / 2);
    var top  = Math.round((screen.height - h) / 2);
    popup = window.open(
      "/auth/login",          // ← same origin (localhost:8000) — no cross-origin issues!
      "google_signin",
      "width=" + w + ",height=" + h + ",left=" + left + ",top=" + top +
      ",scrollbars=no,resizable=no,toolbar=no,menubar=no,location=no,status=no"
    );
    if (!popup || popup.closed) {
      showErr("Popup blocked — allow popups for this site and retry.");
      return;
    }
    // Re-enable if user closes popup without signing in
    var poll = setInterval(function() {
      if (!popup || popup.closed) {
        clearInterval(poll);
        if (btn.disabled && btn.lastChild.data.includes("Opening")) {
          btn.disabled = false;
          btn.lastChild.data = " Sign in with Google";
        }
      }
    }, 600);
  }
</script>
</body>
</html>"""


@app.get("/auth/login", response_class=HTMLResponse)
def login_page():
    """Popup served at localhost:8000. Auto-triggers Google sign-in on load (no 2nd click).
    Uses postMessage (not window.opener.location.href) to avoid cross-origin 'property access denied'."""
    return f"""<!DOCTYPE html>
<html>
<head>
<title>Signing in…</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{
    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
    display:flex; align-items:center; justify-content:center;
    height:100vh; background:#0f172a; color:#f8fafc;
  }}
  .card {{
    text-align:center; background:#1e293b; padding:2.5rem 2rem;
    border-radius:16px; border:1px solid #334155; max-width:380px; width:90%;
    box-shadow:0 20px 40px rgba(0,0,0,0.4);
  }}
  .logo {{ font-size:2rem; margin-bottom:0.75rem; }}
  h3 {{ color:#f8fafc; font-size:1.1rem; margin-bottom:0.5rem; }}
  #status {{ color:#94a3b8; font-size:0.9rem; margin-top:1rem; min-height:24px; }}
  #retry-btn {{
    margin-top:1.25rem; display:none;
    background:linear-gradient(135deg,#4f46e5,#3b82f6);
    color:white; border:none; padding:11px 28px; font-size:14px;
    font-weight:700; border-radius:10px; cursor:pointer; width:100%;
  }}
  #retry-btn:hover {{ opacity:0.9; }}
  .spinner {{
    display:inline-block; width:28px; height:28px; margin-top:1rem;
    border:3px solid #334155; border-top-color:#3b82f6;
    border-radius:50%; animation:spin 0.8s linear infinite;
  }}
  @keyframes spin {{ to {{ transform:rotate(360deg); }} }}
</style>
</head>
<body>
<div class="card">
  <div class="logo">⚡</div>
  <h3>Repo Context Copilot</h3>
  <div class="spinner" id="spinner"></div>
  <div id="status">Opening Google sign-in…</div>
  <button id="retry-btn">Try again</button>
</div>

<script type="module">
  import {{ initializeApp }} from "https://www.gstatic.com/firebasejs/10.12.2/firebase-app.js";
  import {{ getAuth, GoogleAuthProvider, signInWithPopup }}
    from "https://www.gstatic.com/firebasejs/10.12.2/firebase-auth.js";

  const firebaseConfig = {{
    apiKey:            "{settings.FIREBASE_API_KEY}",
    authDomain:        "{settings.FIREBASE_AUTH_DOMAIN}",
    projectId:         "{settings.FIREBASE_PROJECT_ID}",
    storageBucket:     "{settings.FIREBASE_STORAGE_BUCKET}",
    messagingSenderId: "{settings.FIREBASE_MESSAGING_SENDER_ID}",
    appId:             "{settings.FIREBASE_APP_ID}",
  }};

  const fbApp    = initializeApp(firebaseConfig);
  const auth     = getAuth(fbApp);
  const provider = new GoogleAuthProvider();
  const status   = document.getElementById("status");
  const spinner  = document.getElementById("spinner");
  const retryBtn = document.getElementById("retry-btn");

  async function doSignIn() {{
    spinner.style.display = "inline-block";
    retryBtn.style.display = "none";
    status.textContent = "Opening Google sign-in\u2026";
    try {{
      // signInWithPopup auto-called — Google picker opens immediately, no 2nd click needed
      const result  = await signInWithPopup(auth, provider);
      const idToken = await result.user.getIdToken();

      status.textContent = "Verifying credentials\u2026";
      const resp = await fetch("/auth/verify", {{
        method:  "POST",
        headers: {{ "Content-Type": "application/json" }},
        body:    JSON.stringify({{ id_token: idToken }}),
      }});
      const data = await resp.json();

      if (data.error) {{
        spinner.style.display = "none";
        status.innerHTML = "<span style='color:#f87171;'>\u26d4 " + data.error + "</span>";
        retryBtn.style.display = "block";
        return;
      }}

      spinner.style.display = "none";
      status.innerHTML = "<span style='color:#34d399;'>\u2705 Authenticated! Closing\u2026</span>";

      const targetUrl = "{settings.FRONTEND_URL}/?session_id=" + encodeURIComponent(data.session_id);


      if (window.opener && !window.opener.closed) {{
        try {{ window.opener.postMessage({{ type: "auth_success", session_id: data.session_id }}, "*"); }} catch(e) {{}}
        try {{ window.opener.top.location.href = targetUrl; }} catch(e) {{}}
      }}
      window.location.href = targetUrl;

    }} catch (e) {{
      spinner.style.display = "none";
      const msgs = {{
        "auth/popup-closed-by-user":    "Sign-in cancelled.",
        "auth/cancelled-popup-request": "Sign-in cancelled.",
        "auth/popup-blocked":           "Popup blocked. Please try again.",
      }};
      status.innerHTML = "<span style='color:#f87171;'>\u26d4 " +
                         (msgs[e.code] || e.message) + "</span>";
      retryBtn.style.display = "block";
    }}
  }}

  retryBtn.onclick = doSignIn;

  // Auto-trigger immediately — no second click needed
  doSignIn();
</script>
</body>
</html>"""


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
        return {"error": f"Access denied. '{email}' is not the authorized admin.", "session_id": None}

    session_id = auth.create_session(email, uid, name, picture)
    return {
        "session_id": session_id, "email": email, "display_name": name,
        "photo_url": picture, "is_admin": True,
    }


@app.post("/auth/admin-login")
def direct_admin_login(req: DirectAdminLoginRequest):
    if not auth.is_admin(req.email):
        raise HTTPException(
            status_code=403,
            detail=f"Access denied. '{req.email}' is not authorized as admin ({settings.ADMIN_EMAIL}).",
        )
    session_id = auth.create_session(req.email, uid="admin-direct", display_name="Admin")
    return {
        "session_id": session_id, "email": req.email, "display_name": "Admin",
        "is_admin": True,
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
            top_k=req.top_k or 20, top_n=req.top_n or 10, min_score=req.min_score, return_context=True,
        )
        if state.cache and result.get("answer") and "Generation failed" not in result["answer"]:
            state.cache.set(repo.repo_name, repo.commit_sha, req.query, result)

    latency_ms = (time.monotonic() - t0) * 1000
    adaptive_tokens = count_tokens(result.get("context", ""))

    baseline_answer = baseline_tokens = baseline_chunks = None
    baseline_ctx = None
    if settings.LOG_COMPARISON_MODE and not cache_hit:
        baseline = rag_pipeline(
            req.query, hybrid_search=repo.hybrid_search, reranker=state.reranker,
            top_k=20, top_n=10, use_adaptive=False, return_context=True,
        )
        baseline_answer = baseline["answer"]
        baseline_ctx = baseline.get("context", "") or ""
        baseline_tokens = count_tokens(baseline_ctx)
        baseline_chunks = baseline["final_chunk_count"]

    model_info = result.get("model_info", {})
    log_entry = record_query_log(
        query=req.query, is_admin=is_admin_user, complexity=result.get("complexity", "UNKNOWN"),
        adaptive_answer=result["answer"], adaptive_chunks=result["final_chunk_count"],
        adaptive_tokens=adaptive_tokens, model_name=model_info.get("model_name", "cache"),
        model_provider=model_info.get("provider", "cache"), model_paid=model_info.get("paid", False),
        baseline_answer=baseline_answer, baseline_chunks=baseline_chunks, baseline_tokens=baseline_tokens,
        cache_hit=cache_hit, latency_ms=latency_ms, eval_status="pending",
    )

    score_query_async(log_entry, adaptive_context=result.get("context", ""), baseline_context=baseline_ctx, llm=getattr(state, "llm", None))

    if is_admin_user:
        return {
            "answer": result["answer"], "sources": result.get("sources", []),
            "confidence": result.get("confidence", 0.0), "complexity": result.get("complexity"),
            "cached": cache_hit, "model": model_info, "retrieval_chunks": result.get("final_chunk_count"),
        }
    return {"answer": result["answer"]}



# ---------------------------------------------------------------------------
# Ingestion (Public & Admin) - multiple repos can be in flight / live at once
# ---------------------------------------------------------------------------

@app.post("/api/ingest")
def start_ingest(req: IngestRequest, background_tasks: BackgroundTasks):
    repo_name = repo_name_from_url(req.repo_url)
    if repo_name in state.ingest_in_progress:
        raise HTTPException(status_code=409, detail=f"Ingestion for '{repo_name}' is already running.")
    background_tasks.add_task(ingest_repo, req.repo_url)
    return {"status": "started", "repo_name": repo_name, "repo_url": req.repo_url}


@app.get("/api/ingest/status")
def ingest_status():
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
