/* Sign-in, for the case where LANA is running somewhere shared.
 *
 * Renders nothing at all in the default single-user setup: the backend
 * reports `mode: "open"`, and this component gets out of the way before
 * anything is drawn.
 *
 * There are two other modes, and which form to show is read from the server
 * rather than guessed — a client that assumed would show the wrong one:
 *
 *   accounts — a real username and password, one identity per person. This is
 *              what makes session ownership mean something between two
 *              colleagues rather than between two holders of the same secret.
 *   token    — the older single shared secret. Kept working because instances
 *              are running on it. Deliberately not labelled "password": there
 *              is no account behind it and saying so would imply one.
 */

import { useEffect, useState } from 'react'
import { getAuthStatus, login, openAuthSession } from '../api.js'

export default function AuthGate({ children }) {
  const [state, setState] = useState('checking')   // checking | needed | open | unreachable
  const [mode, setMode] = useState('open')         // open | accounts | token
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [token, setToken] = useState('')
  const [error, setError] = useState(null)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    let cancelled = false
    getAuthStatus()
      .then(status => {
        if (cancelled) return
        setMode(status.mode || (status.required ? 'token' : 'open'))
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
      if (mode === 'accounts') {
        await login(username, password)
      } else {
        await openAuthSession(token)
      }
      setState('open')
      // Cleared immediately: there is no reason for a credential to stay in
      // component state once the cookie exists.
      setPassword('')
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

  if (mode === 'accounts') {
    return (
      <div style={s.wrap}>
        <form style={s.card} onSubmit={submit}>
          <h1 style={s.title}>Sign in to LANA</h1>
          <p style={s.body}>
            Your datasets are yours: another account on this instance cannot
            open them.
          </p>
          <input
            style={s.input}
            value={username}
            onChange={e => setUsername(e.target.value)}
            placeholder="Username"
            aria-label="Username"
            autoFocus
            autoComplete="username"
          />
          <input
            style={s.input}
            type="password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            placeholder="Password"
            aria-label="Password"
            autoComplete="current-password"
          />
          {error && <div style={s.error}>{error}</div>}
          <button
            style={s.button}
            type="submit"
            disabled={!username || !password || submitting}
          >
            {submitting ? 'Signing in…' : 'Sign in'}
          </button>
          <p style={s.note}>
            Accounts are created by whoever runs this instance, with{' '}
            <code style={s.code}>python -m scripts.manage_users add</code>.
            There is no sign-up.
          </p>
        </form>
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
          aria-label="Access token"
          autoFocus
          autoComplete="off"
        />
        {error && <div style={s.error}>{error}</div>}
        <button style={s.button} type="submit" disabled={!token || submitting}>
          {submitting ? 'Checking…' : 'Continue'}
        </button>
        <p style={s.note}>
          One token for the whole instance, not a personal account. Everyone
          holding it has the same level of access — set up accounts
          (<code style={s.code}>LANA_ACCOUNTS=true</code>) if people here
          should not see each other's data.
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
