import { useEffect, useState } from 'react'
import { getValidatorCapabilities } from '../api.js'

// A local model plans a query, runs it, and writes the answer before a single
// byte reaches the browser (see backend/main.py's _query_stream_gen) — there
// is no intermediate event to reflect, so a per-step status label would be
// fiction. What the wait genuinely lacks is any sign of life: on a cold
// Ollama process this measured 60-90s, during which a static "Thinking…" is
// indistinguishable from a hang. A ticking elapsed time is small, honest, and
// costs nothing to build wrong the other way.
function ThinkingIndicator({ since }) {
  const [elapsedMs, setElapsedMs] = useState(() => Date.now() - since)
  useEffect(() => {
    const id = setInterval(() => setElapsedMs(Date.now() - since), 1000)
    return () => clearInterval(id)
  }, [since])
  const seconds = Math.floor(elapsedMs / 1000)
  return (
    <div style={{
      marginLeft: 34, borderLeft: '2px solid var(--border)',
      paddingLeft: 16, color: 'var(--muted)', fontSize: 13,
      display: 'flex', alignItems: 'center', gap: 8,
    }}>
      <span style={{ animation: 'lana-pulse 1.2s ease-in-out infinite' }}>●</span>
      Thinking… {seconds}s
      {seconds >= 20 && (
        <span style={{ opacity: 0.7 }}>
          — a local model can take a minute on its first question after starting
        </span>
      )}
    </div>
  )
}

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

// The provenance of an answer's figures, shown rather than asserted.
//
// LANA answers a question two ways. The strong path plans a SQL query, runs it
// against the session's actual rows in a sandbox, and answers from the result
// table — so every figure is a computation, not a recollection. The fallback
// path answers from a precomputed fact ledger. Which one ran is the single
// most useful thing a user can know about how much to trust a number, and
// until now the backend streamed it and the interface discarded it.
//
// Collapsed by default: the badge is the everyday signal, the statement and
// its result are for the moment someone wants to check the work — or re-run it
// themselves, which is the strongest form of "you do not have to take our
// word for it".
// A result cell as a person reads it, not as DuckDB serialises it.
//
// An average comes back as 267.5210191082802. Printing that verbatim makes
// the evidence table harder to read than the sentence it is supposed to
// justify, and it will not match the answer's "267.52" by eye — which defeats
// the point, since checking the two against each other is the only reason
// this table is on screen.
//
// Two decimals and thousands separators for anything fractional; integers
// stay integers, because a count of 157 orders is not 157.00.
function cell(value) {
  if (value === null || value === undefined) return '—'
  if (typeof value !== 'number' || !Number.isFinite(value)) return String(value)
  if (Number.isInteger(value)) return value.toLocaleString()
  // Very small magnitudes (a correlation of 0.0032, a p-value) would round to
  // 0.00 and lose the only information they carry.
  const decimals = Math.abs(value) < 0.01 ? 6 : 2
  return value.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: decimals,
  })
}


function Provenance({ sql, grounding }) {
  const [open, setOpen] = useState(false)
  if (!grounding && !sql) return null

  const executed = Boolean(sql)
  const rows = sql?.rows || []
  const columns = sql?.columns || []

  return (
    <div style={{ marginTop: 12 }}>
      <div style={ps.row}>
        <span style={{ ...ps.badge, ...(executed ? ps.badgeExecuted : ps.badgeLedger) }}>
          {executed ? '⚡ Computed by query' : '◇ From computed summary'}
        </span>
        {executed && (
          <button style={ps.toggle} onClick={() => setOpen(o => !o)}>
            {open ? 'Hide the query' : 'Show the query and its result'}
          </button>
        )}
      </div>

      {executed && open && (
        <div style={ps.body}>
          <div style={ps.caption}>
            Run against your data
            {sql.elapsed_ms != null && ` · ${Math.round(sql.elapsed_ms)} ms`}
            {sql.attempts > 1 && ` · ${sql.attempts} attempts`}
          </div>
          <pre style={ps.sql}>{sql.sql}</pre>

          {columns.length > 0 && (
            <>
              <div style={ps.caption}>
                Result — every figure in the answer above comes from this table
              </div>
              <div style={ps.tableWrap}>
                <table style={ps.table}>
                  <thead>
                    <tr>{columns.map(c => <th key={c} style={ps.th}>{c}</th>)}</tr>
                  </thead>
                  <tbody>
                    {rows.slice(0, 25).map((r, i) => (
                      <tr key={i}>
                        {r.map((v, j) => (
                          <td key={j} style={ps.td}>{cell(v)}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {(rows.length > 25 || sql.truncated) && (
                <div style={ps.caption}>
                  Showing the first 25 rows
                  {sql.truncated && ' — the result was capped before this point'}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}

const ps = {
  row: { display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' },
  badge: {
    fontSize: 11, fontWeight: 600, padding: '3px 9px', borderRadius: 20,
    fontFamily: 'var(--ff-mono)', whiteSpace: 'nowrap',
  },
  badgeExecuted: {
    color: 'var(--green)', background: 'rgba(78,199,127,0.12)',
    border: '1px solid rgba(78,199,127,0.3)',
  },
  badgeLedger: {
    color: 'var(--muted)', background: 'rgba(255,255,255,0.05)',
    border: '1px solid var(--border)',
  },
  toggle: {
    background: 'transparent', border: 'none', padding: 0,
    color: 'var(--muted)', fontSize: 11.5, cursor: 'pointer',
    fontFamily: 'var(--ff-ui)', textDecoration: 'underline',
  },
  body: {
    marginTop: 10, padding: '12px 14px', background: 'var(--bg)',
    border: '1px solid var(--border)', borderRadius: 8,
  },
  caption: {
    fontSize: 11, color: 'var(--muted)', fontFamily: 'var(--ff-mono)',
    marginBottom: 6,
  },
  sql: {
    margin: '0 0 12px', padding: '10px 12px', background: 'var(--surface)',
    border: '1px solid var(--border)', borderRadius: 6,
    fontSize: 12, lineHeight: 1.6, fontFamily: 'var(--ff-mono)',
    color: 'var(--text)', overflowX: 'auto', whiteSpace: 'pre-wrap',
  },
  // Bounded height: a 200-row result must not push the next message off screen.
  tableWrap: { overflow: 'auto', maxHeight: 260, border: '1px solid var(--border)', borderRadius: 6 },
  table: { borderCollapse: 'collapse', width: '100%', fontSize: 12 },
  th: {
    textAlign: 'left', padding: '7px 10px', background: 'var(--surface)',
    color: 'var(--muted)', fontWeight: 600, fontFamily: 'var(--ff-mono)',
    borderBottom: '1px solid var(--border)', position: 'sticky', top: 0,
    whiteSpace: 'nowrap',
  },
  td: {
    padding: '6px 10px', borderBottom: '1px solid var(--border)',
    fontFamily: 'var(--ff-mono)', color: 'var(--text)', whiteSpace: 'nowrap',
  },
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
        <ThinkingIndicator since={msg.id} />
      ) : msg.error && !msg.a ? (
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

          {/* The stream failed partway. The text above is real and was already
              read, so it stays — but it is not a complete answer and was never
              validated, and saying so is the whole point. */}
          {msg.truncated && (
            <div style={{
              marginTop: 14, padding: '11px 14px',
              background: 'rgba(224,82,82,0.08)',
              border: '1px solid rgba(224,82,82,0.28)',
              borderRadius: 8, fontSize: 12.5, lineHeight: 1.6,
              color: 'var(--red)', whiteSpace: 'normal',
            }}>
              <div style={{ fontWeight: 700, marginBottom: 5 }}>⚠ Response cut off</div>
              <div>{msg.error}</div>
              <div style={{ marginTop: 5 }}>
                The text above is only part of an answer and was not checked
                against your data. Ask again for a complete, verified response.
              </div>
            </div>
          )}

          {/* Trust verdict — the answer's figures were checked against the
              statistics LANA actually computed. Only failures are shown.
              Misattribution is called out separately from fabrication: a
              number that exists but is labelled wrong is a different problem
              from one that exists nowhere in the data. */}
          {msg.validation?.warnings?.length > 0 && (() => {
            const misattributed = msg.validation.misattributed || 0
            const unsupported = msg.validation.unsupported || 0
            const heading = misattributed && !unsupported
              ? '⚠ Figure may be labelled wrong'
              : misattributed
                ? '⚠ Unverified and mislabelled figures'
                : '⚠ Unverified figures'
            return (
              <div style={{
                marginTop: 14, padding: '11px 14px',
                background: 'rgba(240,180,60,0.08)',
                border: '1px solid rgba(240,180,60,0.28)',
                borderRadius: 8, fontSize: 12.5, lineHeight: 1.6,
                color: 'var(--amber)', whiteSpace: 'normal',
              }}>
                <div style={{ fontWeight: 700, marginBottom: 5 }}>{heading}</div>
                {msg.validation.warnings.map((w, i) => (
                  <div key={i} style={{ marginTop: i > 0 ? 5 : 0 }}>{w}</div>
                ))}
                <div style={{ marginTop: 7, fontSize: 11, opacity: 0.75, fontFamily: 'var(--ff-mono)' }}>
                  {msg.validation.verified} of {msg.validation.numbers_checked} numbers matched a computed statistic
                  {misattributed > 0 && ` · ${misattributed} matched a different label than the text claims`}
                </div>
              </div>
            )
          })()}

          {/* Where the figures came from. Placed after the warnings so a
              problem is read first, and before the footer so the statement
              sits with the answer it produced. */}
          <Provenance sql={msg.sql} grounding={msg.grounding} />

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