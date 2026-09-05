import os
import time
import requests
import streamlit as st
import streamlit.components.v1 as components
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Load .env so Firebase config is available when running via run.sh
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:
    pass

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

FIREBASE_CONFIG = {
    "apiKey": os.getenv("VITE_FIREBASE_API_KEY", ""),
    "authDomain": os.getenv("VITE_FIREBASE_AUTH_DOMAIN", ""),
    "projectId": os.getenv("VITE_FIREBASE_PROJECT_ID", ""),
    "storageBucket": os.getenv("VITE_FIREBASE_STORAGE_BUCKET", ""),
    "messagingSenderId": os.getenv("VITE_FIREBASE_MESSAGING_SENDER_ID", ""),
    "appId": os.getenv("VITE_FIREBASE_APP_ID", ""),
}

# Proper custom component served from localhost:8000 (same origin as the auth popup).
# Using declare_component avoids Streamlit's sandbox restriction on window.top navigation
# and allows sending session_id back to Python via streamlit:setComponentValue.
_google_auth_component = components.declare_component(
    "google_auth_btn",
    url=f"{BACKEND_URL}/auth/button",
)

st.set_page_config(
    page_title="Repo Context Copilot",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# Global Styles
# ---------------------------------------------------------------------------
st.markdown("""
<style>
    section[data-testid="stSidebar"] { display: none !important; }
    div[data-testid="collapsedControl"] { display: none !important; }
    .stApp { background: linear-gradient(135deg, #0b0f19 0%, #111827 100%); color: #f3f4f6; }
    .main-logo {
        font-family: 'Inter', sans-serif; font-weight: 800; font-size: 1.8rem;
        background: linear-gradient(90deg, #38bdf8 0%, #818cf8 100%);
        -webkit-background-clip: text; -webkit-text-fill-color: transparent; margin: 0;
    }
    .main-subtitle { color: #9ca3af; font-size: 0.85rem; margin-top: 2px; }
    .badge-free {
        background-color: #059669; color: #ecfdf5; padding: 3px 8px;
        border-radius: 12px; font-size: 0.75rem; font-weight: 700; text-transform: uppercase;
    }
    .badge-paid {
        background-color: #d97706; color: #fffbeb; padding: 3px 8px;
        border-radius: 12px; font-size: 0.75rem; font-weight: 700; text-transform: uppercase;
    }
    .metric-card {
        background: rgba(31,41,55,0.7); border: 1px solid rgba(255,255,255,0.08);
        border-radius: 12px; padding: 1.2rem; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);
    }
    .metric-val { font-size: 1.8rem; font-weight: 700; color: #f9fafb; }
    .metric-lbl { font-size: 0.85rem; color: #9ca3af; text-transform: uppercase; letter-spacing: 0.05em; }
    .hero-card {
        background: rgba(17,24,39,0.8); border: 1px solid rgba(56,189,248,0.2);
        border-radius: 16px; padding: 2.5rem; text-align: center;
        margin: 2rem 0; box-shadow: 0 10px 25px -5px rgba(0,0,0,0.3);
    }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Session State Initialization
# ---------------------------------------------------------------------------
if "session_id" not in st.session_state:
    st.session_state.session_id = None
if "admin_email" not in st.session_state:
    st.session_state.admin_email = None
if "active_view" not in st.session_state:
    st.session_state.active_view = "chat"
if "messages" not in st.session_state:
    st.session_state.messages = []
if "selected_repo" not in st.session_state:
    st.session_state.selected_repo = None

# ---------------------------------------------------------------------------
# Auto-activate admin session from ?session_id= query param
# ---------------------------------------------------------------------------
query_params = st.query_params
if "session_id" in query_params:
    token_param = query_params["session_id"]
    if token_param and token_param != st.session_state.session_id:
        try:
            resp = requests.get(
                f"{BACKEND_URL}/auth/me",
                headers={"X-Session-ID": token_param},
                timeout=5,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("is_admin"):
                    st.session_state.session_id = token_param
                    st.session_state.admin_email = data.get("email")
                    st.session_state.active_view = "admin"
        except Exception:
            pass
    st.query_params.clear()

# ---------------------------------------------------------------------------
# API Helpers
# ---------------------------------------------------------------------------
def api_get(endpoint: str, headers: dict = None):
    try:
        resp = requests.get(f"{BACKEND_URL}{endpoint}", headers=headers or {}, timeout=10)
        return resp.json() if resp.status_code == 200 else None
    except Exception:
        return None

def api_post(endpoint: str, json_payload: dict, headers: dict = None):
    try:
        resp = requests.post(f"{BACKEND_URL}{endpoint}", json=json_payload, headers=headers or {}, timeout=90)
        return resp.json(), resp.status_code
    except Exception as e:
        return {"detail": str(e)}, 500

def api_delete(endpoint: str, headers: dict = None):
    try:
        resp = requests.delete(f"{BACKEND_URL}{endpoint}", headers=headers or {}, timeout=10)
        return resp.json(), resp.status_code
    except Exception as e:
        return {"detail": str(e)}, 500

repos_res = api_get("/api/repos")
available_repos = repos_res.get("repos", []) if repos_res else []

# ---------------------------------------------------------------------------
# Top Navigation Header
# ---------------------------------------------------------------------------
col_brand, col_repo_actions, col_top_auth = st.columns([3, 3, 2])

with col_brand:
    st.markdown("<h1 class='main-logo'>⚡ Repo Context Copilot</h1>", unsafe_allow_html=True)
    st.markdown("<div class='main-subtitle'>Production-Grade Adaptive RAG Engine</div>", unsafe_allow_html=True)

with col_repo_actions:
    if available_repos:
        repo_names = [r["repo_name"] for r in available_repos]
        selected_repo_name = st.selectbox(
            "Select Repository",
            options=repo_names,
            index=0,
            label_visibility="collapsed",
            key="header_repo_selector",
        )
        st.session_state.selected_repo = selected_repo_name
    else:
        st.session_state.selected_repo = None

with col_top_auth:
    if st.session_state.session_id:
        st.markdown(
            f"<div style='text-align:right; font-size:0.85rem; color:#10b981; font-weight:bold;'>"
            f"🛡️ Admin: {st.session_state.admin_email}</div>",
            unsafe_allow_html=True,
        )
        btn_c1, btn_c2 = st.columns(2)
        with btn_c1:
            view_label = "💬 Chat View" if st.session_state.active_view == "admin" else "🛡️ Admin View"
            if st.button(view_label, key="toggle_view_btn", use_container_width=True):
                st.session_state.active_view = "chat" if st.session_state.active_view == "admin" else "admin"
                st.rerun()
        with btn_c2:
            if st.button("🚪 Logout", key="logout_btn", use_container_width=True):
                api_post("/auth/logout", {}, headers={"X-Session-ID": st.session_state.session_id})
                st.session_state.session_id = None
                st.session_state.admin_email = None
                st.session_state.active_view = "chat"
                st.rerun()
    else:
        signin_btn_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{
    display:flex; justify-content:flex-end; align-items:center;
    min-height:45px; background:transparent;
    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  }}
  #signin-btn {{
    display:inline-flex; align-items:center; gap:8px;
    background:linear-gradient(135deg,#4f46e5 0%,#3b82f6 100%);
    color:white; border:none; padding:8px 18px; font-size:13px;
    font-weight:700; border-radius:20px; cursor:pointer;
    box-shadow:0 4px 12px rgba(59,130,246,0.4); white-space:nowrap;
    transition:transform 0.15s ease,box-shadow 0.15s ease;
  }}
  #signin-btn:hover {{ transform:translateY(-1px); box-shadow:0 6px 16px rgba(59,130,246,0.6); }}
  #signin-btn:active {{ transform:translateY(0); opacity:0.88; }}
</style>
</head>
<body>
<button id="signin-btn" onclick="openLogin()">
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
    <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
    <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
    <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/>
    <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
  </svg>
  Sign in with Google
</button>

<script>
  window.addEventListener("message", function(event) {{
    if (event.data && event.data.type === "auth_success" && event.data.session_id) {{
      try {{
        window.top.location.href = "{BACKEND_URL}".replace("8000", "8501") + "/?session_id=" + encodeURIComponent(event.data.session_id);
      }} catch(e) {{
        window.location.href = "http://localhost:8501/?session_id=" + encodeURIComponent(event.data.session_id);
      }}
    }}
  }});

  function openLogin() {{
    var w = 500, h = 600;
    var left = Math.round((screen.width - w) / 2);
    var top  = Math.round((screen.height - h) / 2);
    window.open(
      "{BACKEND_URL}/auth/login",
      "google_signin",
      "width=" + w + ",height=" + h + ",left=" + left + ",top=" + top + ",scrollbars=no,resizable=no"
    );
  }}
</script>
</body>
</html>"""
        components.html(signin_btn_html, height=50)

st.divider()

# ===========================================================================
# VIEW 1: Chat Interface
# ===========================================================================
if st.session_state.active_view == "chat":
    with st.expander("📥 Ingest a New GitHub Repository", expanded=not available_repos):
        st.caption("Enter any public GitHub URL to clone, chunk, and index it (1-hour TTL).")
        ing_col1, ing_col2 = st.columns([4, 1])
        with ing_col1:
            repo_url_input = st.text_input(
                "GitHub URL",
                placeholder="https://github.com/owner/repository",
                label_visibility="collapsed",
                key="chat_ingest_input",
            )
        with ing_col2:
            if st.button("🚀 Ingest", use_container_width=True, key="btn_chat_ingest"):
                if repo_url_input:
                    res, status = api_post("/api/ingest", {"repo_url": repo_url_input})
                    if status in (200, 409):
                        r_name = res.get("repo_name") or repo_url_input.rstrip("/").split("/")[-1]
                        with st.spinner(f"⚡ Ingesting '{r_name}'... Cloning codebase, generating embeddings, and building AstraDB vector store..."):
                            completed = False
                            for _ in range(60): # Poll up to 2 minutes
                                time.sleep(2)
                                st_res = api_get("/api/ingest/status")
                                if st_res:
                                    in_prog = st_res.get("in_progress", [])
                                    errors = st_res.get("errors", {})
                                    if r_name in errors:
                                        st.error(f"❌ Ingestion failed for `{r_name}`: {errors[r_name]}")
                                        break
                                    if r_name not in in_prog:
                                        completed = True
                                        break
                            if completed:
                                st.session_state.selected_repo = r_name
                                st.success(f"🎉 Ingestion complete for `{r_name}`!")
                                time.sleep(1)
                                st.rerun()
                    else:
                        st.error(f"Ingestion error: {res.get('detail')}")

    # Check if there are any repos currently ingesting in the background
    ingest_status_data = api_get("/api/ingest/status")
    if ingest_status_data and ingest_status_data.get("in_progress"):
        ing_list = ingest_status_data["in_progress"]
        st.info(f"⏳ Currently ingesting in background: **{', '.join(ing_list)}**. Please wait a moment...")
        time.sleep(3)
        st.rerun()

    if not st.session_state.selected_repo:
        st.markdown("""
        <div class='hero-card'>
            <h2>👋 Welcome to Repo Context Copilot</h2>
            <p style='color:#9ca3af; font-size:1.1rem;'>
                Ingest a GitHub repository above to start asking questions about any codebase.
            </p>
        </div>
        """, unsafe_allow_html=True)
    else:
        current_repo_info = next(
            (r for r in available_repos if r["repo_name"] == st.session_state.selected_repo), None
        )
        if current_repo_info:
            exp_time = datetime.fromisoformat(current_repo_info["expires_at"]).strftime("%H:%M:%S UTC")
            st.caption(
                f"Active: **`{st.session_state.selected_repo}`** | "
                f"SHA: `{current_repo_info['commit_sha']}` | "
                f"Chunks: **{current_repo_info['chunk_count']}** | "
                f"⏳ Expires: **{exp_time}**"
            )

        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
                if msg.get("sources"):
                    with st.expander("📚 Cited Sources"):
                        for src in msg["sources"]:
                            st.markdown(f"**`{src.get('source')}`** *(Score: {src.get('score', 0):.2f})*")
                            st.code(src.get("preview", ""), language="python")
                if msg.get("meta"):
                    meta = msg["meta"]
                    model_info = meta.get("model", {})
                    badge_cls = "badge-paid" if model_info.get("paid") else "badge-free"
                    tier_str = "PAID" if model_info.get("paid") else "FREE"
                    st.caption(
                        f"⚡ **{model_info.get('model_name', 'unknown')}** | "
                        f"Complexity: **{meta.get('complexity', 'N/A')}** | "
                        f"Chunks: **{meta.get('retrieval_chunks', 0)}** | "
                        f"<span class='{badge_cls}'>{tier_str}</span>",
                        unsafe_allow_html=True,
                    )

        if prompt := st.chat_input(f"Ask a question about {st.session_state.selected_repo}…"):
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)

            with st.chat_message("assistant"):
                with st.spinner("Analyzing repository context & generating answer…"):
                    req_headers = {}
                    if st.session_state.session_id:
                        req_headers["X-Session-ID"] = st.session_state.session_id

                    data, status = api_post(
                        "/api/query",
                        {"query": prompt, "repo_name": st.session_state.selected_repo},
                        headers=req_headers,
                    )

                    if status == 200:
                        answer = data.get("answer", "No answer generated.")
                        sources = data.get("sources", [])
                        st.markdown(answer)
                        if sources:
                            with st.expander("📚 Cited Sources"):
                                for src in sources:
                                    st.markdown(f"**`{src.get('source')}`** *(Score: {src.get('score', 0):.2f})*")
                                    st.code(src.get("preview", ""), language="python")

                        meta = None
                        if st.session_state.session_id:
                            model_info = data.get("model", {})
                            badge_cls = "badge-paid" if model_info.get("paid") else "badge-free"
                            tier_str = "PAID" if model_info.get("paid") else "FREE"
                            st.caption(
                                f"⚡ **{model_info.get('model_name', 'unknown')}** | "
                                f"Complexity: **{data.get('complexity')}** | "
                                f"Chunks: **{data.get('retrieval_chunks')}** | "
                                f"<span class='{badge_cls}'>{tier_str}</span>",
                                unsafe_allow_html=True,
                            )
                            meta = {
                                "model": model_info,
                                "complexity": data.get("complexity"),
                                "retrieval_chunks": data.get("retrieval_chunks"),
                            }

                        st.session_state.messages.append({
                            "role": "assistant",
                            "content": answer,
                            "sources": sources,
                            "meta": meta,
                        })
                    else:
                        st.error(f"Error: {data.get('detail', 'Unknown error')}")

# ===========================================================================
# VIEW 2: Admin Dashboard
# ===========================================================================
elif st.session_state.active_view == "admin" and st.session_state.session_id:
    st.subheader("🛡️ Admin Dashboard & System Telemetry")
    auth_headers = {"X-Session-ID": st.session_state.session_id}

    adm_tab1, adm_tab2, adm_tab3 = st.tabs([
        "🩺 System Health", "📦 Ingestion Manager", "📋 Query & System Logs"
    ])

    # Tab 1: System Health
    with adm_tab1:
        st.markdown("#### Real-Time Backend & Cache Status")
        health_data = api_get("/api/health", headers=auth_headers)
        if health_data:
            c1, c2, c3 = st.columns(3)
            with c1:
                redis_ok = health_data.get("redis", False)
                color = "#10b981" if redis_ok else "#ef4444"
                label = "🟢 ONLINE" if redis_ok else "🔴 DOWN"
                st.markdown(
                    f"<div class='metric-card'><div class='metric-lbl'>Redis Caching</div>"
                    f"<div class='metric-val' style='color:{color};'>{label}</div></div>",
                    unsafe_allow_html=True,
                )
            with c2:
                reranker_ok = health_data.get("reranker_loaded", False)
                color = "#10b981" if reranker_ok else "#ef4444"
                label = "🟢 LOADED" if reranker_ok else "🔴 UNLOADED"
                st.markdown(
                    f"<div class='metric-card'><div class='metric-lbl'>Cross-Encoder Reranker</div>"
                    f"<div class='metric-val' style='color:{color};'>{label}</div></div>",
                    unsafe_allow_html=True,
                )
            with c3:
                active_cnt = len(health_data.get("repos", []))
                st.markdown(
                    f"<div class='metric-card'><div class='metric-lbl'>Active Repo Collections</div>"
                    f"<div class='metric-val'>{active_cnt}</div></div>",
                    unsafe_allow_html=True,
                )
            st.divider()
            st.markdown("#### AstraDB Vector Store & BM25 Collection Metrics")
            repo_health = health_data.get("repos", [])
            if repo_health:
                st.dataframe(
                    repo_health,
                    column_config={
                        "repo_name": "Repository",
                        "vector_count": "AstraDB Documents",
                        "bm25_docs": "BM25 Docs",
                        "expires_at": "Expires (UTC)",
                    },
                    use_container_width=True,
                )
            else:
                st.info("No active repository collections in vector store.")

    # Tab 2: Ingestion Manager
    with adm_tab2:
        st.markdown("#### Ingest New GitHub Repository")
        with st.form("admin_ingest_form"):
            repo_url_input = st.text_input(
                "GitHub Repository URL", placeholder="https://github.com/owner/repository"
            )
            if st.form_submit_button("🚀 Start Ingestion") and repo_url_input:
                res, status = api_post("/api/ingest", {"repo_url": repo_url_input}, headers=auth_headers)
                if status == 200:
                    st.success(f"Ingestion started for `{res.get('repo_name')}`!")
                    st.rerun()
                else:
                    st.error(f"Ingestion error: {res.get('detail')}")

        st.divider()
        st.markdown("#### Active Repositories & Auto-Purge Manager")
        ingest_status_data = api_get("/api/ingest/status", headers=auth_headers)
        if ingest_status_data:
            in_prog = ingest_status_data.get("in_progress", [])
            if in_prog:
                st.warning(f"⏳ Ingesting: {', '.join(in_prog)}")
            active_repos = ingest_status_data.get("active_repos", [])
            if active_repos:
                for r in active_repos:
                    col1, col2, col3 = st.columns([3, 2, 1])
                    with col1:
                        st.write(f"**{r['repo_name']}** (`{r['commit_sha']}`)")
                        exp_dt = datetime.fromisoformat(r["expires_at"])
                        st.caption(f"Chunks: {r['chunk_count']} | Expires: {exp_dt.strftime('%H:%M:%S UTC')}")
                    with col2:
                        st.progress(1.0, text="1-Hour TTL Active")
                    with col3:
                        if st.button("🗑️ Purge", key=f"adm_purge_{r['repo_name']}"):
                            api_delete(f"/api/ingest/{r['repo_name']}", headers=auth_headers)
                            st.success(f"Purged {r['repo_name']}")
                            st.rerun()
            else:
                st.info("No active ingested repositories.")

    # Tab 3: Logs
    with adm_tab3:
        st.markdown("#### 📊 Adaptive vs Baseline Query Logs")
        c_act1, c_act2, _ = st.columns([1, 1, 4])
        with c_act1:
            if st.button("🔄 Refresh", key="btn_ref_logs"):
                st.rerun()
        with c_act2:
            if st.button("🧹 Clear Logs", key="btn_clr_logs"):
                api_post("/api/query-logs/clear", {}, headers=auth_headers)
                st.success("Query logs cleared.")
                st.rerun()

        query_logs_data = api_get("/api/query-logs?n=100", headers=auth_headers)
        q_logs = query_logs_data.get("logs", []) if query_logs_data else []

        if q_logs:
            for log in reversed(q_logs):
                tier = log.get("model_tier", "free").upper()
                badge_color = "#059669" if tier == "FREE" else "#d97706"
                red_pct = log.get("token_reduction_pct")
                red_str = f"{red_pct}% token reduction" if red_pct is not None else "N/A"
                with st.expander(
                    f"❓ [{log.get('timestamp', '')[:19]}] {log.get('query')} — "
                    f"({log.get('model_name')} | {tier})"
                ):
                    st.markdown(
                        f"**Model**: `{log.get('model_name')}` ({log.get('model_provider')}) | "
                        f"**Tier**: <span style='background:{badge_color}; color:white; "
                        f"padding:2px 8px; border-radius:10px; font-weight:bold;'>{tier}</span>",
                        unsafe_allow_html=True,
                    )
                    st.markdown(
                        f"**Complexity**: `{log.get('complexity')}` | "
                        f"**Context Reduction**: `{red_str}` | "
                        f"**Latency**: `{log.get('latency_ms')} ms` | "
                        f"**Cache Hit**: `{log.get('cache_hit')}`"
                    )
                    col_a, col_b = st.columns(2)
                    with col_a:
                        st.markdown(
                            f"**⚡ Adaptive Answer** "
                            f"(Chunks: `{log.get('adaptive_chunks_used')}`, Tokens: `{log.get('adaptive_tokens')}`)"
                        )
                        st.info(log.get("adaptive_answer"))
                    with col_b:
                        st.markdown(
                            f"**📏 Baseline Answer** "
                            f"(Chunks: `{log.get('baseline_chunks_used')}`, Tokens: `{log.get('baseline_tokens')}`)"
                        )
                        if log.get("baseline_answer"):
                            st.warning(log.get("baseline_answer"))
                        elif log.get("cache_hit"):
                            st.caption("Baseline comparison skipped (cache hit).")
                        else:
                            st.caption("Baseline comparison disabled.")

        else:
            st.info("No query logs recorded yet.")

        st.divider()
        st.markdown("#### 📜 Application System Logs")
        sys_logs_data = api_get("/api/logs?n=50", headers=auth_headers)
        sys_logs = sys_logs_data.get("logs", []) if sys_logs_data else []
        if sys_logs:
            st.dataframe(sys_logs, use_container_width=True)
        else:
            st.info("No system logs.")
