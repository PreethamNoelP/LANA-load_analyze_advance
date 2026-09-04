import { Component } from 'react'

// Any uncaught render exception below this point (a malformed API response,
// a bad prop) used to white-screen the entire app with no recovery UI. This
// is the one place that can't itself be a function component - React error
// boundaries require the class lifecycle methods below.
export default class ErrorBoundary extends Component {
  state = { error: null }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    console.error('LANA crashed:', error, info.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div style={s.wrap}>
        <div style={s.card}>
          <div style={s.icon}>⚠</div>
          <h1 style={s.title}>Something went wrong</h1>
          <p style={s.body}>
            LANA hit an unexpected error and couldn't continue rendering this screen.
            Your dataset is still on the server — reloading will not lose it.
          </p>
          {this.state.error?.message && (
            <div style={s.detail}>{this.state.error.message}</div>
          )}
          <button style={s.btn} onClick={() => window.location.reload()}>
            Reload LANA
          </button>
        </div>
      </div>
    )
  }
}

const s = {
  wrap: {
    height: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
    background: 'var(--bg)', padding: 24,
  },
  card: {
    maxWidth: 440, textAlign: 'center', padding: '36px 32px',
    background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12,
  },
  icon: { fontSize: 28, color: 'var(--red)', marginBottom: 12 },
  title: { fontSize: 18, fontWeight: 700, color: 'var(--text)', marginBottom: 10 },
  body: { fontSize: 13, color: 'var(--muted)', lineHeight: 1.6, marginBottom: 16 },
  detail: {
    fontSize: 11, fontFamily: 'var(--ff-mono)', color: 'var(--red)',
    background: 'rgba(224,82,82,0.08)', border: '1px solid rgba(224,82,82,0.2)',
    borderRadius: 8, padding: '10px 12px', marginBottom: 20, textAlign: 'left',
    wordBreak: 'break-word',
  },
  btn: {
    padding: '10px 24px', fontSize: 13, fontWeight: 600, color: '#fff',
    background: 'var(--accent)', border: 'none', borderRadius: 8, cursor: 'pointer',
  },
}
