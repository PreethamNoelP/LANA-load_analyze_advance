/* Sign-in, for the case where LANA is running somewhere shared.
 *
 * Renders nothing at all in the default single-user setup: the backend
 * reports `required: false`, and this component gets out of the way before
 * anything is drawn. It exists only because LANA_AUTH_TOKEN is now exchanged
 * for a cookie at runtime instead of being compiled into the bundle — which
 * is what made it safe, and which also means something has to ask for it once.
 *
 * Deliberately not a login form: there is one shared token and no concept of
 * a user, and calling it "Password" would imply an account system that does
 * not exist. SECURITY.md says the same thing in the same words.
 */

import { useEffect, useState } from 'react'
import { getAuthStatus, openAuthSession } from '../api.js'

export default function AuthGate({ children }) {
  const [state, setState] = useState('checking')   // checking | needed | open | unreachable
  const [token, setToken] = useState('')
  const [error, setError] = useState(null)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    let cancelled = false
    getAuthStatus()
      .then(status => {
        if (cancelled) return
        // `authenticated` is already true when a valid cookie survived a
        // reload, so a returning user is not asked again.
        setState(!status.required || status.authenticated ? 'open' : 'needed')
      })
      .catch(() => { if (!cancelled) setState('unreachable') })
    return () => { cancelled = true }
  }, [])

  async function submit(event) {
    event.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await openAuthSession(token)
      setState('open')
      // Cleared immediately: there is no reason for the secret to stay in
      // component state once the cookie exists.
      setToken('')
    } catch (e) {
      setError(e.message)
    } finally {
      setSubmitting(false)
    }
  }

  if (state === 'checking') return null
  if (state === 'open') return children

  if (state === 'unreachable') {
    return (
      <div style={s.wrap}>
        <div style={s.card}>
          <h1 style={s.title}>Can't reach LANA</h1>
          <p style={s.body}>
            The backend did not respond. Check that it is running
            (<code style={s.code}>uvicorn backend.main:app</code>) and reload.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div style={s.wrap}>
      <form style={s.card} onSubmit={submit}>
        <h1 style={s.title}>This LANA instance needs a token</h1>
        <p style={s.body}>
          Whoever runs this instance set <code style={s.code}>LANA_AUTH_TOKEN</code>.
          Paste it once — the browser stores a cookie it cannot read, so the
          token itself never stays in the page.
        </p>
        <input
          style={s.input}
          type="password"
          value={token}
          onChange={e => setToken(e.target.value)}
          placeholder="Access token"
          autoFocus
          autoComplete="off"
        />
        {error && <div style={s.error}>{error}</div>}
        <button style={s.button} type="submit" disabled={!token || submitting}>
          {submitting ? 'Checking…' : 'Continue'}
        </button>
        <p style={s.note}>
          One token for the whole instance, not a personal account. Everyone
          holding it has the same level of access.
        </p>
      </form>
    </div>
  )
}

const s = {
  wrap: { minHeight: '100vh', display: 'flex', alignItems: 'center',
          justifyContent: 'center', padding: 24, background: 'var(--bg)' },
  card: { width: '100%', maxWidth: 420, display: 'flex', flexDirection: 'column',
          gap: 14, padding: 28, borderRadius: 14, background: 'var(--surface)',
          border: '1px solid var(--border)' },
  title: { margin: 0, fontSize: 19, fontWeight: 600, color: 'var(--text)' },
  body: { margin: 0, fontSize: 13.5, lineHeight: 1.6, color: 'var(--text-dim)' },
  code: { fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
          fontSize: 12.5, padding: '1px 5px', borderRadius: 4,
          background: 'var(--bg)', border: '1px solid var(--border)' },
  input: { padding: '11px 13px', borderRadius: 8, fontSize: 14,
           background: 'var(--bg)', color: 'var(--text)',
           border: '1px solid var(--border)', fontFamily: 'inherit' },
  button: { padding: '11px 18px', borderRadius: 8, fontSize: 14, fontWeight: 600,
            cursor: 'pointer', background: 'var(--accent)', color: '#0b0d10',
            border: 'none' },
  error: { padding: '9px 12px', borderRadius: 8, fontSize: 13,
           background: 'rgba(191,97,106,0.12)',
           border: '1px solid rgba(191,97,106,0.35)', color: '#d08770' },
  note: { margin: 0, fontSize: 11.5, lineHeight: 1.6, color: 'var(--text-dim)' },
}
