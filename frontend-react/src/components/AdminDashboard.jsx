import { useState, useEffect, useCallback } from 'react'
import { RefreshCw, Trash2, Activity, Package, ClipboardList, Server, Database, Zap } from 'lucide-react'
import { api, authHeaders } from '../api'
import QueryLogCard from './QueryLogCard'
import './AdminDashboard.css'

export default function AdminDashboard({ sessionId, repos, refreshRepos }) {
  const [tab, setTab] = useState('health')
  const hdrs = authHeaders(sessionId)

  const tabs = [
    { id: 'health',    icon: <Activity size={15} />,      label: 'System Health' },
    { id: 'ingest',    icon: <Package size={15} />,       label: 'Ingestion Manager' },
    { id: 'querylogs', icon: <ClipboardList size={15} />, label: 'Query Logs' },
    { id: 'syslogs',   icon: <Server size={15} />,        label: 'System Logs' },
  ]

  return (
    <div className="admin-root">
      <div className="admin-header">
        <h1 className="admin-title"><Zap size={20} className="admin-title-icon" /> Admin Dashboard</h1>
        <div className="admin-tabs">
          {tabs.map(t => (
            <button
              key={t.id}
              className={`admin-tab ${tab === t.id ? 'active' : ''}`}
              onClick={() => setTab(t.id)}
            >
              {t.icon}{t.label}
            </button>
          ))}
        </div>
      </div>

      <div className="admin-content">
        {tab === 'health'    && <HealthTab hdrs={hdrs} />}
        {tab === 'ingest'    && <IngestTab hdrs={hdrs} repos={repos} refreshRepos={refreshRepos} />}
        {tab === 'querylogs' && <QueryLogsTab hdrs={hdrs} />}
        {tab === 'syslogs'   && <SysLogsTab hdrs={hdrs} />}
      </div>
    </div>
  )
}

/* ── Health Tab ─────────────────────────────────────────────── */
function HealthTab({ hdrs }) {
  const [health, setHealth] = useState(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    const { data, ok } = await api.get('/api/health', hdrs)
    if (ok) setHealth(data)
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

  if (loading) return <div className="admin-spinner"><div className="skeleton" style={{height:200,borderRadius:16}} /></div>

  const metrics = [
    { label: 'Redis Cache',       value: health?.redis       ? '🟢 ONLINE'  : '🔴 DOWN',    color: health?.redis      ? '#34d399' : '#f87171' },
    { label: 'Cross-Encoder',     value: health?.reranker_loaded ? '🟢 LOADED' : '🔴 UNLOADED', color: health?.reranker_loaded ? '#34d399' : '#f87171' },
    { label: 'Active Collections', value: health?.repos?.length ?? 0, color: '#38bdf8' },
  ]

  return (
    <div className="health-tab fade-up">
      <div className="metric-grid">
        {metrics.map(m => (
          <div key={m.label} className="metric-card">
            <div className="metric-label">{m.label}</div>
            <div className="metric-value" style={{ color: m.color }}>{m.value}</div>
          </div>
        ))}
      </div>

      {health?.repos?.length > 0 && (
        <>
          <h3 className="section-title"><Database size={15} /> Vector Store Collections</h3>
          <div className="health-table-wrap">
            <table className="data-table">
              <thead>
                <tr><th>Repository</th><th>AstraDB Docs</th><th>BM25 Docs</th><th>Expires (UTC)</th></tr>
              </thead>
              <tbody>
                {health.repos.map(r => (
                  <tr key={r.repo_name}>
                    <td><code>{r.repo_name}</code></td>
                    <td>{r.vector_count}</td>
                    <td>{r.bm25_docs}</td>
                    <td>{new Date(r.expires_at).toLocaleTimeString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <button className="refresh-btn" onClick={load}><RefreshCw size={14} /> Refresh</button>
    </div>
  )
}

/* ── Ingest Tab ─────────────────────────────────────────────── */
function IngestTab({ hdrs, repos, refreshRepos }) {
  const [url, setUrl]       = useState('')
  const [loading, setLoading] = useState(false)
  const [msg, setMsg]       = useState(null)
  const [ingestStatus, setIngestStatus] = useState(null)

  useEffect(() => {
    const id = setInterval(async () => {
      const { data, ok } = await api.get('/api/ingest/status', hdrs)
      if (ok) setIngestStatus(data)
    }, 3000)
    api.get('/api/ingest/status', hdrs).then(({ data, ok }) => ok && setIngestStatus(data))
    return () => clearInterval(id)
  }, [])

  async function doIngest(e) {
    e.preventDefault()
    if (!url.trim()) return
    setLoading(true); setMsg(null)
    const { data, status } = await api.post('/api/ingest', { repo_url: url.trim() }, hdrs)
    setLoading(false)
    if (status === 200) {
      setMsg({ type: 'ok', text: `Ingestion started for "${data.repo_name}"` })
      setUrl(''); refreshRepos()
    } else {
      setMsg({ type: 'err', text: data.detail || 'Failed' })
    }
  }

  async function doPurge(repoName) {
    await api.delete(`/api/ingest/${repoName}`, hdrs)
    refreshRepos()
  }

  return (
    <div className="ingest-tab fade-up">
      <h3 className="section-title"><Package size={15} /> Ingest New Repository</h3>
      <form className="admin-ingest-form" onSubmit={doIngest}>
        <input
          className="admin-input"
          value={url} onChange={e => setUrl(e.target.value)}
          placeholder="https://github.com/owner/repository"
          disabled={loading}
        />
        <button className="admin-btn primary" type="submit" disabled={!url.trim() || loading}>
          {loading ? '⏳ Starting…' : '🚀 Ingest'}
        </button>
      </form>
      {msg && <div className={`admin-msg ${msg.type}`}>{msg.text}</div>}

      {ingestStatus?.in_progress?.length > 0 && (
        <div className="in-progress-banner">
          ⏳ Currently ingesting: <strong>{ingestStatus.in_progress.join(', ')}</strong>
        </div>
      )}

      <h3 className="section-title" style={{marginTop:28}}><Database size={15} /> Active Repositories</h3>
      {repos.length === 0 ? (
        <p className="empty-state">No active repositories.</p>
      ) : (
        <div className="repo-cards">
          {repos.map(r => (
            <div key={r.repo_name} className="repo-card">
              <div className="repo-card-info">
                <div className="repo-card-name">{r.repo_name}</div>
                <div className="repo-card-meta">
                  <span>SHA: <code>{r.commit_sha}</code></span>
                  <span>{r.chunk_count} chunks</span>
                  <span>Expires: {new Date(r.expires_at).toLocaleTimeString()}</span>
                </div>
              </div>
              <button className="admin-btn danger" onClick={() => doPurge(r.repo_name)}>
                <Trash2 size={13} /> Purge
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/* ── Query Logs Tab ─────────────────────────────────────────── */
function QueryLogsTab({ hdrs }) {
  const [logs, setLogs]     = useState([])
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    const { data, ok } = await api.get('/api/query-logs?n=100', hdrs)
    if (ok) setLogs((data.logs || []).slice().reverse())
    setLoading(false)
  }, [])

  async function clearLogs() {
    await api.post('/api/query-logs/clear', {}, hdrs)
    setLogs([])
  }

  useEffect(() => { load() }, [load])

  return (
    <div className="logs-tab fade-up">
      <div className="logs-toolbar">
        <h3 className="section-title"><ClipboardList size={15} /> Adaptive vs Baseline Query Logs</h3>
        <div className="logs-actions">
          <button className="admin-btn secondary" onClick={load}><RefreshCw size={13} /> Refresh</button>
          <button className="admin-btn danger"    onClick={clearLogs}><Trash2 size={13} /> Clear</button>
        </div>
      </div>

      {loading ? (
        <div style={{display:'flex',flexDirection:'column',gap:12}}>
          {[1,2,3].map(i=><div key={i} className="skeleton" style={{height:80,borderRadius:12}} />)}
        </div>
      ) : logs.length === 0 ? (
        <p className="empty-state">No query logs yet. Ask some questions!</p>
      ) : (
        <div className="query-log-list">
          {logs.map((log, i) => <QueryLogCard key={i} log={log} />)}
        </div>
      )}
    </div>
  )
}

/* ── System Logs Tab ────────────────────────────────────────── */
function SysLogsTab({ hdrs }) {
  const [logs, setLogs] = useState([])
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    const { data, ok } = await api.get('/api/logs?n=50', hdrs)
    if (ok) setLogs((data.logs || []).slice().reverse())
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

  const levelColor = { INFO: '#38bdf8', WARNING: '#fbbf24', ERROR: '#f87171', DEBUG: '#94a3b8' }

  return (
    <div className="logs-tab fade-up">
      <div className="logs-toolbar">
        <h3 className="section-title"><Server size={15} /> Application System Logs</h3>
        <button className="admin-btn secondary" onClick={load}><RefreshCw size={13} /> Refresh</button>
      </div>
      {loading ? (
        <div className="skeleton" style={{height:300,borderRadius:12}} />
      ) : logs.length === 0 ? (
        <p className="empty-state">No system logs.</p>
      ) : (
        <div className="sys-log-list">
          {logs.map((log, i) => (
            <div key={i} className="sys-log-row">
              <span className="sys-log-time">{log.timestamp?.slice(11,19)}</span>
              <span className="sys-log-level" style={{color: levelColor[log.level] || '#94a3b8'}}>{log.level}</span>
              <span className="sys-log-logger">[{log.logger?.split('.').at(-1)}]</span>
              <span className="sys-log-msg">{log.message}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
