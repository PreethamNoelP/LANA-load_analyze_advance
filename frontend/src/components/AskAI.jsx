import { useEffect, useState } from 'react'
import { getValidatorCapabilities } from '../api.js'

// A one-time, collapsed-by-default disclosure rather than a per-message
// badge — the trust signal that matters on every answer is the warning
// itself; this is for the person who wants to know exactly what "verified"
// does and doesn't mean, once, not a permanent fixture competing for
// attention with the actual answers.
function CapabilityNote() {
  const [open, setOpen] = useState(false)
  const [caps, setCaps] = useState(null)
  const [error, setError] = useState(false)

  useEffect(() => {
    if (!open || caps || error) return
    getValidatorCapabilities().then(setCaps).catch(() => setError(true))
  }, [open, caps, error])

  return (
    <div style={cs.note}>
      <button style={cs.toggle} onClick={() => setOpen(o => !o)}>
        {open ? '▾' : '▸'} What LANA checks in every answer
      </button>
      {open && caps && (
        <div style={cs.body}>
          <div style={cs.group}>
            <div style={{ ...cs.label, color: 'var(--green)' }}>Verified against your data</div>
            <ul style={cs.list}>{caps.verifies.map((v, i) => <li key={i}>{v}</li>)}</ul>
          </div>
          <div style={cs.group}>
            <div style={{ ...cs.label, color: 'var(--amber)' }}>Not checked — read these yourself</div>
            <ul style={cs.list}>{caps.does_not_verify.map((v, i) => <li key={i}>{v}</li>)}</ul>
          </div>
        </div>
      )}
      {open && error && (
        <div style={cs.body}>Could not load — the backend may be unreachable.</div>
      )}
    </div>
  )
}

const cs = {
  note: { marginBottom: 16 },
  toggle: {
    background: 'transparent', border: 'none', padding: 0,
    color: 'var(--muted)', fontSize: 12, cursor: 'pointer', fontFamily: 'var(--ff-ui)',
  },
  body: {
    marginTop: 10, padding: '12px 16px',
    background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8,
    fontSize: 12.5, lineHeight: 1.6, color: 'var(--muted)',
  },
  group: { marginBottom: 10 },
  label: { fontWeight: 700, fontSize: 11.5, marginBottom: 4, textTransform: 'uppercase', letterSpacing: '0.04em' },
  list: { margin: '0 0 0 18px', padding: 0 },
}

function Message({ msg }) {
  return (
    <div style={{ marginBottom: 28 }}>
      {/* Question */}
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, marginBottom: 12 }}>
        <div style={{
          width: 24, height: 24, borderRadius: 6,
          background: 'var(--accent)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 11, fontWeight: 700, color: '#fff',
          flexShrink: 0, marginTop: 2,
        }}>Q</div>
        <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text)', lineHeight: 1.5, paddingTop: 3 }}>
          {msg.q}
        </div>
      </div>

      {/* Answer */}
      {msg.loading ? (
        <div style={{
          marginLeft: 34, borderLeft: '2px solid var(--border)',
          paddingLeft: 16, color: 'var(--muted)', fontSize: 13,
          display: 'flex', alignItems: 'center', gap: 8,
        }}>
          <span style={{ animation: 'lana-pulse 1.2s ease-in-out infinite' }}>●</span>
          Thinking…
        </div>
      ) : msg.error ? (
        <div style={{
          marginLeft: 34, borderLeft: '2px solid var(--red)',
          paddingLeft: 16, color: 'var(--red)', fontSize: 13,
        }}>{msg.error}</div>
      ) : (
        <div style={{
          marginLeft: 34,
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          borderLeft: '3px solid var(--accent)',
          borderRadius: '0 10px 10px 0',
          padding: '14px 18px',
          fontSize: 14, lineHeight: 1.8,
          color: 'var(--text)', whiteSpace: 'pre-wrap',
        }}>
          {msg.a}

          {/* Trust verdict — the answer's figures were checked against the
              statistics LANA actually computed. Only failures are shown. */}
          {msg.validation?.warnings?.length > 0 && (
            <div style={{
              marginTop: 14, padding: '11px 14px',
              background: 'rgba(240,180,60,0.08)',
              border: '1px solid rgba(240,180,60,0.28)',
              borderRadius: 8, fontSize: 12.5, lineHeight: 1.6,
              color: 'var(--amber)', whiteSpace: 'normal',
            }}>
              <div style={{ fontWeight: 700, marginBottom: 5 }}>⚠ Unverified figures</div>
              {msg.validation.warnings.map((w, i) => (
                <div key={i} style={{ marginTop: i > 0 ? 5 : 0 }}>{w}</div>
              ))}
              <div style={{ marginTop: 7, fontSize: 11, opacity: 0.75, fontFamily: 'var(--ff-mono)' }}>
                {msg.validation.verified} of {msg.validation.numbers_checked} numbers matched a computed statistic
              </div>
            </div>
          )}

          <div style={{
            marginTop: 10, fontSize: 11,
            color: 'var(--muted)', fontFamily: 'var(--ff-mono)',
            display: 'flex', alignItems: 'center', gap: 6,
          }}>
            <span style={{
              width: 5, height: 5, borderRadius: '50%', display: 'inline-block',
              background: msg.validation?.warnings?.length ? 'var(--amber)' : 'var(--green)',
            }} />
            LANA AI
            {msg.validation && !msg.validation.warnings.length && msg.validation.verified > 0 && (
              <span style={{ opacity: 0.7 }}>· {msg.validation.verified} figure(s) verified against the data</span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

export default function AskAI({ messages }) {
  if (!messages.length) {
    return (
      <div>
        <CapabilityNote />
        <div style={{ padding: '40px 0 16px', textAlign: 'center', color: 'var(--muted)', fontSize: 14 }}>
          <div style={{ fontSize: 32, marginBottom: 12, opacity: 0.25 }}>◎</div>
          Ask anything about your dataset below
        </div>
      </div>
    )
  }

  return (
    <div>
      <CapabilityNote />
      {messages.map(m => <Message key={m.id} msg={m} />)}
      <style>{`@keyframes lana-pulse { 0%,100%{opacity:0.3} 50%{opacity:1} }`}</style>
    </div>
  )
}