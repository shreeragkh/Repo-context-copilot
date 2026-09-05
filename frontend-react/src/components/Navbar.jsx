import { useState } from 'react'
import { Zap, Shield, LogOut, LayoutDashboard, MessageSquare, ChevronDown } from 'lucide-react'
import './Navbar.css'

const BACKEND = import.meta.env.VITE_BACKEND_URL || 'http://localhost:8000'

export default function Navbar({
  sessionId, adminEmail, view, setView,
  repos, selectedRepo, setSelectedRepo, onLogout
}) {
  const [repoOpen, setRepoOpen] = useState(false)

  function openLogin() {
    const w = 500, h = 600
    const left = Math.round((screen.width - w) / 2)
    const top  = Math.round((screen.height - h) / 2)
    window.open(
      `${BACKEND}/auth/login`,
      'google_signin',
      `width=${w},height=${h},left=${left},top=${top},scrollbars=no,resizable=no`
    )
  }

  const currentRepo = repos.find(r => r.repo_name === selectedRepo)

  return (
    <header className="navbar">
      {/* Brand */}
      <div className="navbar-brand">
        <Zap size={22} className="brand-icon" />
        <span className="brand-name text-gradient">Repo Context Copilot</span>
        <span className="brand-tag">Hybrid RAG</span>
      </div>

      {/* Repo selector */}
      {repos.length > 0 && (
        <div className="repo-selector" onClick={() => setRepoOpen(o => !o)}>
          <div className="repo-selector-label">
            <span className="repo-dot" />
            <span>{selectedRepo || 'Select repo'}</span>
          </div>
          <ChevronDown size={14} className={`repo-chevron ${repoOpen ? 'open' : ''}`} />
          {repoOpen && (
            <div className="repo-dropdown">
              {repos.map(r => (
                <div
                  key={r.repo_name}
                  className={`repo-option ${r.repo_name === selectedRepo ? 'active' : ''}`}
                  onClick={() => { setSelectedRepo(r.repo_name); setRepoOpen(false) }}
                >
                  <span className="repo-option-name">{r.repo_name}</span>
                  <span className="repo-option-meta">{r.chunk_count} chunks</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Actions */}
      <div className="navbar-actions">
        {sessionId ? (
          <>
            <button
              className={`nav-btn ${view === 'chat' ? 'active' : ''}`}
              onClick={() => setView('chat')}
            >
              <MessageSquare size={15} /> Chat
            </button>
            <button
              className={`nav-btn ${view === 'admin' ? 'active' : ''}`}
              onClick={() => setView('admin')}
            >
              <LayoutDashboard size={15} /> Admin
            </button>
            <div className="admin-info">
              <Shield size={13} className="shield-icon" />
              <span>{adminEmail}</span>
            </div>
            <button className="nav-btn logout" onClick={onLogout}>
              <LogOut size={14} /> Logout
            </button>
          </>
        ) : (
          <button className="signin-btn" onClick={openLogin}>
            <GoogleIcon />
            Sign in with Google
          </button>
        )}
      </div>
    </header>
  )
}

function GoogleIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
      <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
      <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
      <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/>
      <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
    </svg>
  )
}
