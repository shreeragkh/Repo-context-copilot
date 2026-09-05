const BASE = import.meta.env.VITE_BACKEND_URL || 'http://localhost:8000'

async function request(method, path, { body, headers = {}, signal } = {}) {
  const opts = {
    method,
    headers: { 'Content-Type': 'application/json', ...headers },
    signal,
  }
  if (body !== undefined) opts.body = JSON.stringify(body)
  const res = await fetch(`${BASE}${path}`, opts)
  const data = await res.json().catch(() => ({}))
  return { data, status: res.status, ok: res.ok }
}

export const api = {
  get:    (path, headers, signal) => request('GET',    path, { headers, signal }),
  post:   (path, body, headers)   => request('POST',   path, { body, headers }),
  delete: (path, headers)         => request('DELETE',  path, { headers }),
}

export function authHeaders(sessionId) {
  return sessionId ? { 'X-Session-ID': sessionId } : {}
}
