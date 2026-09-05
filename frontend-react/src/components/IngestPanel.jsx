import { useState } from 'react'
import { GitBranch, Rocket, ChevronDown, ChevronUp, Loader2, CheckCircle, XCircle } from 'lucide-react'
import { api } from '../api'
import './IngestPanel.css'


export default function IngestPanel({ repos, onIngestDone }) {
  const [open, setOpen]       = useState(repos.length === 0)
  const [url, setUrl]         = useState('')
  const [status, setStatus]   = useState(null) // null | 'ingesting' | 'done' | 'error'
  const [message, setMessage] = useState('')

  async function handleIngest(e) {
    e.preventDefault()
    if (!url.trim()) return
    setStatus('ingesting')
    setMessage('')

    const { data, status: s } = await api.post('/api/ingest', { repo_url: url.trim() })
    if (s !== 200 && s !== 409) {
      setStatus('error')
      setMessage(data.detail || 'Ingestion failed.')
      return
    }

    const repoName = data.repo_name || url.trim().split('/').at(-1)
    setMessage(`Indexing "${repoName}"…`)

    // Poll until done (up to 5 minutes)
    for (let i = 0; i < 150; i++) {
      await sleep(2000)
      const { data: st } = await api.get('/api/ingest/status')
      if (!st) continue
      if (st.errors?.[repoName]) {
        setStatus('error')
        setMessage(`Ingestion failed: ${st.errors[repoName]}`)
        return
      }
      if (!st.in_progress?.includes(repoName)) {
        setStatus('done')
        setMessage(`"${repoName}" ingested successfully!`)
        setUrl('')
        onIngestDone()
        setTimeout(() => { setStatus(null); setMessage('') }, 3000)
        return
      }
    }

    // Final check before throwing timeout error
    const { data: activeRepos } = await api.get('/api/repos')
    if (Array.isArray(activeRepos) && activeRepos.some(r => r.repo_name === repoName)) {
      setStatus('done')
      setMessage(`"${repoName}" ingested successfully!`)
      setUrl('')
      onIngestDone()
      setTimeout(() => { setStatus(null); setMessage('') }, 3000)
      return
    }

    setStatus('error')
    setMessage('Timed out waiting for ingestion. Check server logs.')

  }

  const sleep = ms => new Promise(r => setTimeout(r, ms))

  return (
    <div className="ingest-panel">
      <button className="ingest-toggle" onClick={() => setOpen(o => !o)}>
        <GitBranch size={15} />

        <span>Ingest GitHub Repository</span>
        {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
      </button>

      {open && (
        <div className="ingest-body">
          <form className="ingest-form" onSubmit={handleIngest}>
            <div className="ingest-input-row">
              <input
                className="ingest-input"
                value={url}
                onChange={e => setUrl(e.target.value)}
                placeholder="https://github.com/owner/repository"
                disabled={status === 'ingesting'}
              />
              <button
                className="ingest-btn"
                type="submit"
                disabled={!url.trim() || status === 'ingesting'}
              >
                {status === 'ingesting'
                  ? <><Loader2 size={14} className="spin" /> Ingesting…</>
                  : <><Rocket size={14} /> Ingest</>
                }
              </button>
            </div>
          </form>

          {message && (
            <div className={`ingest-status ${status}`}>
              {status === 'ingesting' && <Loader2 size={13} className="spin" />}
              {status === 'done'      && <CheckCircle size={13} />}
              {status === 'error'     && <XCircle size={13} />}
              {message}
            </div>
          )}

          {repos.length > 0 && (
            <div className="ingest-note">
              {repos.length} repo{repos.length !== 1 ? 's' : ''} currently active (1-hour TTL)
            </div>
          )}
        </div>
      )}
    </div>
  )
}
