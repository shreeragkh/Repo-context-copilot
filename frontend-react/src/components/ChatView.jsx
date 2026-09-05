import { useState, useRef, useEffect, useCallback } from 'react'
import { Send, GitBranch, Loader2, ChevronDown, ChevronUp, FileCode2, Clock, Hash } from 'lucide-react'
import ReactMarkdown from 'react-markdown'

import { api, authHeaders } from '../api'
import IngestPanel from './IngestPanel'
import './ChatView.css'

export default function ChatView({ sessionId, repos, selectedRepo, setSelectedRepo, refreshRepos }) {
  const [messages, setMessages]   = useState([])
  const [input, setInput]         = useState('')
  const [loading, setLoading]     = useState(false)
  const bottomRef                 = useRef(null)

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages])

  const currentRepo = repos.find(r => r.repo_name === selectedRepo)

  async function sendMessage(e) {
    e.preventDefault()
    if (!input.trim() || !selectedRepo || loading) return

    const query = input.trim()
    setInput('')
    setMessages(m => [...m, { role: 'user', content: query }])
    setLoading(true)

    const { data, status } = await api.post(
      '/api/query',
      { query, repo_name: selectedRepo },
      authHeaders(sessionId)
    )

    setLoading(false)
    if (status === 200) {
      setMessages(m => [...m, {
        role: 'assistant',
        content: data.answer || 'No answer generated.',
        sources: data.sources || [],
        meta: sessionId ? {
          model:    data.model || {},
          complexity: data.complexity,
          chunks:   data.retrieval_chunks,
          cached:   data.cached,
          confidence: data.confidence,
        } : null,
      }])
    } else {
      setMessages(m => [...m, {
        role: 'assistant',
        content: `⚠️ Error: ${data.detail || 'Something went wrong.'}`,
        error: true,
      }])
    }
  }

  return (
    <div className="chat-root">
      {/* Ingest panel */}
      <IngestPanel repos={repos} onIngestDone={refreshRepos} />

      {/* Repo info bar */}
      {currentRepo && (
        <div className="repo-info-bar">
          <span className="repo-info-name"><FileCode2 size={13} />{currentRepo.repo_name}</span>
          <span className="repo-info-sep">·</span>
          <span><Hash size={12} />{currentRepo.commit_sha}</span>
          <span className="repo-info-sep">·</span>
          <span>{currentRepo.chunk_count} chunks</span>
          <span className="repo-info-sep">·</span>
          <span className="repo-info-exp"><Clock size={12} />Expires {new Date(currentRepo.expires_at).toLocaleTimeString()}</span>
        </div>
      )}

      {/* Messages */}
      <div className="chat-messages">
        {messages.length === 0 && !loading && (
          <div className="chat-welcome fade-up">
            <div className="welcome-icon">⚡</div>
            <h2 className="text-gradient">Ask anything about your codebase</h2>
            <p>
              {selectedRepo
                ? `"${selectedRepo}" is ready. Ask about architecture, functions, auth flows, data models, or any implementation detail.`
                : 'Ingest a GitHub repository above to get started.'}
            </p>
            {!selectedRepo && (
              <div className="example-queries">
                {['How does authentication work?', 'Trace a query end-to-end', 'What is the project architecture?'].map(q => (
                  <button key={q} className="example-chip" onClick={() => setInput(q)}>{q}</button>
                ))}
              </div>
            )}
          </div>
        )}

        {messages.map((msg, i) => (
          <MessageBubble key={i} msg={msg} />
        ))}

        {loading && (
          <div className="chat-bubble assistant fade-up">
            <div className="bubble-avatar assistant-avatar">⚡</div>
            <div className="bubble-body loading-body">
              <div className="loading-dots"><span/><span/><span/></div>
              <span className="loading-text">Searching codebase…</span>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <form className="chat-input-form" onSubmit={sendMessage}>
        <div className="chat-input-wrapper">
          <input
            className="chat-input"
            value={input}
            onChange={e => setInput(e.target.value)}
            placeholder={selectedRepo ? `Ask about ${selectedRepo}…` : 'Ingest a repo first…'}
            disabled={!selectedRepo || loading}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(e) } }}
          />
          <button className="send-btn" type="submit" disabled={!input.trim() || !selectedRepo || loading}>
            {loading ? <Loader2 size={16} className="spin" /> : <Send size={16} />}
          </button>
        </div>
      </form>
    </div>
  )
}

function MessageBubble({ msg }) {
  const [sourcesOpen, setSourcesOpen] = useState(false)
  const isUser = msg.role === 'user'

  const complexityClass = msg.meta?.complexity
    ? `badge-${msg.meta.complexity.toLowerCase()}`
    : 'badge-medium'

  return (
    <div className={`chat-bubble ${isUser ? 'user' : 'assistant'} fade-up`}>
      <div className={`bubble-avatar ${isUser ? 'user-avatar' : 'assistant-avatar'}`}>
        {isUser ? '👤' : '⚡'}
      </div>
      <div className="bubble-body">
        <div className={`bubble-content ${msg.error ? 'bubble-error' : ''}`}>
          <ReactMarkdown>{msg.content}</ReactMarkdown>
        </div>

        {msg.meta && (
          <div className="bubble-meta">
            <span className={`badge ${complexityClass}`}>{msg.meta.complexity}</span>
            <span className={`badge ${msg.meta.model?.paid ? 'badge-paid' : 'badge-free'}`}>
              {msg.meta.model?.paid ? 'PAID' : 'FREE'}
            </span>
            <span className="meta-item">{msg.meta.model?.model_name}</span>
            <span className="meta-sep">·</span>
            <span className="meta-item">{msg.meta.chunks} chunks</span>
            {msg.meta.cached && <span className="badge badge-free">CACHED</span>}
          </div>
        )}

        {msg.sources?.length > 0 && (
          <div className="sources-section">
            <button className="sources-toggle" onClick={() => setSourcesOpen(o => !o)}>
              <FileCode2 size={13} />
              {sourcesOpen ? 'Hide' : 'Show'} {msg.sources.length} cited source{msg.sources.length !== 1 ? 's' : ''}
              {sourcesOpen ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
            </button>
            {sourcesOpen && (
              <div className="sources-list">
                {msg.sources.map((src, i) => (
                  <div key={i} className="source-item">
                    <div className="source-header">
                      <code className="source-path">{src.source}</code>
                      <span className="source-score">score {(src.score * 100).toFixed(0)}%</span>
                    </div>
                    <pre className="source-preview">{src.preview}</pre>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
