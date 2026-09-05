import { useState, useEffect, useCallback } from 'react'
import { api, authHeaders } from './api'
import Navbar from './components/Navbar'
import ChatView from './components/ChatView'
import AdminDashboard from './components/AdminDashboard'
import './App.css'

export default function App() {
  const [sessionId, setSessionId]       = useState(null)
  const [adminEmail, setAdminEmail]     = useState(null)
  const [view, setView]                 = useState('chat')
  const [repos, setRepos]               = useState([])
  const [selectedRepo, setSelectedRepo] = useState(null)

  // Pick up ?session_id= from URL after Firebase popup redirect
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const sid = params.get('session_id')
    if (sid) {
      api.get('/auth/me', { 'X-Session-ID': sid }).then(({ data, ok }) => {
        if (ok && data.is_admin) {
          setSessionId(sid); setAdminEmail(data.email); setView('admin')
        }
      })
      window.history.replaceState({}, '', window.location.pathname)
    }
  }, [])

  // Listen for auth_success postMessage from the popup
  useEffect(() => {
    function onMsg(e) {
      if (e.data?.type === 'auth_success' && e.data.session_id) {
        const sid = e.data.session_id
        api.get('/auth/me', { 'X-Session-ID': sid }).then(({ data, ok }) => {
          if (ok && data.is_admin) {
            setSessionId(sid); setAdminEmail(data.email); setView('admin')
          }
        })
      }
    }
    window.addEventListener('message', onMsg)
    return () => window.removeEventListener('message', onMsg)
  }, [])

  // Load repos on mount and every 10 s
  const fetchRepos = useCallback(async () => {
    const { data, ok } = await api.get('/api/repos')
    if (ok) setRepos(data.repos || [])
  }, [])

  useEffect(() => {
    fetchRepos()
    const id = setInterval(fetchRepos, 10_000)
    return () => clearInterval(id)
  }, [fetchRepos])

  useEffect(() => {
    if (!selectedRepo && repos.length) setSelectedRepo(repos[0].repo_name)
  }, [repos, selectedRepo])

  function logout() {
    if (sessionId) api.post('/auth/logout', {}, authHeaders(sessionId))
    setSessionId(null); setAdminEmail(null); setView('chat')
  }

  return (
    <div className="app-root">
      <Navbar
        sessionId={sessionId} adminEmail={adminEmail}
        view={view} setView={setView}
        repos={repos} selectedRepo={selectedRepo}
        setSelectedRepo={setSelectedRepo} onLogout={logout}
      />
      <main className="app-main">
        {view === 'chat' ? (
          <ChatView
            sessionId={sessionId} repos={repos}
            selectedRepo={selectedRepo} setSelectedRepo={setSelectedRepo}
            refreshRepos={fetchRepos}
          />
        ) : (
          <AdminDashboard sessionId={sessionId} repos={repos} refreshRepos={fetchRepos} />
        )}
      </main>
    </div>
  )
}
