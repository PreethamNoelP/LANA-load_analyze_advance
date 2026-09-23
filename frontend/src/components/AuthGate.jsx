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
import {
  getAuthStatus, login, openAuthSession, requestPasswordReset, resetPassword,
} from '../api.js'

// The reset link in the email points here with ?token=..., and this is the
// only place in the SPA that needs to know that — there is no router, so a
// query param is the simplest way for a link outside the app to hand it a
// piece of state.
//
// Deliberately pure — no side effect. It used to also strip the param from
// the URL right here, which broke under React 18 StrictMode: a useState
// lazy initializer runs twice in development, and the second call saw the
// URL the first call had already stripped, so the token was silently lost.
// The strip now happens once, safely, in the effect below.
function readResetTokenFromUrl() {
  return new URLSearchParams(window.location.search).get('token')
}

export default function AuthGate({ children }) {
  const [state, setState] = useState('checking')   // checking | needed | open | unreachable
  const [mode, setMode] = useState('open')         // open | accounts | token
  const [view, setView] = useState('signin')       // signin | forgot
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [token, setToken] = useState('')
  const [error, setError] = useState(null)
  const [submitting, setSubmitting] = useState(false)
  const [resetToken, setResetToken] = useState(() => readResetTokenFromUrl())
  const [resetNewPassword, setResetNewPassword] = useState('')
  const [resetDone, setResetDone] = useState(false)
  const [resetError, setResetError] = useState(null)
  const [resetSubmitting, setResetSubmitting] = useState(false)
  const [forgotMessage, setForgotMessage] = useState(null)

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

  useEffect(() => {
    if (!resetToken) return
    // Drop it from the visible URL and history: a password-reset token is a
    // working credential for exactly one action, and leaving it in the
    // address bar (and so in browser history, and in any screen share) has
    // no upside once this component has read it. Safe to run more than once
    // (StrictMode's double-invoke in development) — stripping an
    // already-stripped URL is a no-op.
    const url = new URL(window.location.href)
    url.searchParams.delete('token')
    window.history.replaceState({}, '', url)
  }, [resetToken])

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

  async function submitForgot(event) {
    event.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      const res = await requestPasswordReset(username)
      // The server's own generic message — this component does not know,
      // and must not pretend to know, whether the username existed.
      setForgotMessage(res.detail)
    } catch (e) {
      setError(e.message)
    } finally {
      setSubmitting(false)
    }
  }

  async function submitReset(event) {
    event.preventDefault()
    setResetSubmitting(true)
    setResetError(null)
    try {
      await resetPassword(resetToken, resetNewPassword)
      setResetDone(true)
      setResetNewPassword('')
    } catch (e) {
      setResetError(e.message)
    } finally {
      setResetSubmitting(false)
    }
  }

  if (resetToken) {
    return (
      <div style={s.wrap}>
        <form style={s.card} onSubmit={submitReset}>
          <h1 style={s.title}>Set a new password</h1>
          {resetDone ? (
            <>
              <p style={s.body}>
                Your password has been changed. Any devices that were signed
                in have been signed out.
              </p>
              <button
                style={s.button} type="button"
                onClick={() => setResetToken(null)}
              >
                Continue to sign in
              </button>
            </>
          ) : (
            <>
              <p style={s.body}>This link works once, for the next 30 minutes.</p>
              <input
                style={s.input}
                type="password"
                value={resetNewPassword}
                onChange={e => setResetNewPassword(e.target.value)}
                placeholder="New password"
                aria-label="New password"
                autoFocus
                autoComplete="new-password"
              />
              {resetError && <div style={s.error}>{resetError}</div>}
              <button
                style={s.button} type="submit"
                disabled={!resetNewPassword || resetSubmitting}
              >
                {resetSubmitting ? 'Saving…' : 'Set new password'}
              </button>
            </>
          )}
        </form>
      </div>
    )
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

  if (mode === 'accounts' && view === 'forgot') {
    return (
      <div style={s.wrap}>
        <form style={s.card} onSubmit={submitForgot}>
          <h1 style={s.title}>Reset your password</h1>
          {forgotMessage ? (
            <p style={s.body}>{forgotMessage}</p>
          ) : (
            <p style={s.body}>
              If a password-reset email was set up for this instance, we'll
              send a link to sign back in.
            </p>
          )}
          <input
            style={s.input}
            value={username}
            onChange={e => setUsername(e.target.value)}
            placeholder="Username"
            aria-label="Username"
            autoFocus
            autoComplete="username"
            disabled={!!forgotMessage}
          />
          {error && <div style={s.error}>{error}</div>}
          {!forgotMessage && (
            <button style={s.button} type="submit" disabled={!username || submitting}>
              {submitting ? 'Sending…' : 'Send reset link'}
            </button>
          )}
          <button
            style={s.linkButton} type="button"
            onClick={() => { setView('signin'); setForgotMessage(null); setError(null) }}
          >
            Back to sign in
          </button>
        </form>
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
          <button
            style={s.linkButton} type="button"
            onClick={() => { setView('forgot'); setError(null) }}
          >
            Forgot password?
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
  linkButton: { padding: 0, fontSize: 12.5, textAlign: 'left', cursor: 'pointer',
                background: 'none', border: 'none', color: 'var(--text-dim)',
                textDecoration: 'underline' },
  error: { padding: '9px 12px', borderRadius: 8, fontSize: 13,
           background: 'rgba(191,97,106,0.12)',
           border: '1px solid rgba(191,97,106,0.35)', color: '#d08770' },
  note: { margin: 0, fontSize: 11.5, lineHeight: 1.6, color: 'var(--text-dim)' },
}
