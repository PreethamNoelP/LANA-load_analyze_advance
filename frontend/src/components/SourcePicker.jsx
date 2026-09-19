/* Connect to a data source that is not a file upload.
 *
 * Rendered entirely from GET /sources. The backend describes each connector's
 * capabilities — whether it lists tables, whether it needs one, whether it
 * takes a query, what to call its connection field — and this component lays
 * out a form from that description. Registering a new connector on the
 * backend therefore makes it appear here with no change to this file, which
 * is the property the DataSource abstraction exists to provide.
 *
 * The flow is deliberately three steps rather than one button:
 *
 *   Test      → prove the credentials work and find out what is there
 *   Preview   → see the actual columns and rows before committing
 *   Load      → pull it into a session
 *
 * Loading a wrong table from a production database is slow, memory-hungry and
 * annoying to undo. Seeing eight rows first costs a second and prevents it.
 */

import { useEffect, useState } from 'react'
import { listSources, testSource, previewSource, loadSource } from '../api.js'

const KIND_ICONS = {
  file: '🗂️',
  sql: '🛢️',
  mongodb: '🍃',
  url: '🌐',
}

export default function SourcePicker({ onLoaded, onCancel }) {
  const [sources, setSources]   = useState([])
  const [kind, setKind]         = useState(null)
  const [target, setTarget]     = useState('')
  const [entity, setEntity]     = useState('')
  const [secret, setSecret]     = useState('')
  const [query, setQuery]       = useState('')

  const [testing, setTesting]   = useState(false)
  const [test, setTest]         = useState(null)
  const [preview, setPreview]   = useState(null)
  const [loading, setLoading]   = useState(false)
  const [error, setError]       = useState(null)

  useEffect(() => {
    listSources()
      .then(d => setSources(d.sources || []))
      .catch(e => setError(e.message))
  }, [])

  const selected = sources.find(s => s.kind === kind)
  const caps = selected?.capabilities

  function spec() {
    const out = { kind, target, options: {} }
    if (entity) out.entity = entity
    // Only sent when the user actually typed one. An empty string would be
    // stored as a credential and shown as "has_secret: true".
    if (secret) out.secret = secret
    if (query) out.options.query = query
    return out
  }

  function reset() {
    setTest(null)
    setPreview(null)
    setError(null)
  }

  async function run(fn, setBusy, onDone) {
    setBusy(true)
    setError(null)
    try {
      onDone(await fn())
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const onTest = () => run(() => testSource(spec()), setTesting, r => {
    setTest(r)
    setPreview(null)
    if (!r.ok) setError(r.detail)
  })

  const onPreview = () => run(() => previewSource(spec()), setTesting, setPreview)

  const onLoad = () => run(() => loadSource(spec()), setLoading, session => {
    onLoaded(session)
  })

  /* ── Source chooser ───────────────────────────────────────────────────── */

  if (!kind) {
    return (
      <div style={s.wrap}>
        <div style={s.header}>
          <h2 style={s.title}>Connect a data source</h2>
          <p style={s.sub}>
            Everything LANA does — profiling, cleaning, questions, charts,
            statistics, export — works the same whichever of these you pick.
          </p>
        </div>

        {error && <div style={s.error}>{error}</div>}

        <div style={s.grid}>
          {sources.map(source => (
            <button
              key={source.kind}
              style={{ ...s.card, ...(source.available ? {} : s.cardDisabled) }}
              disabled={!source.available}
              onClick={() => { reset(); setKind(source.kind) }}
              title={source.available ? '' : source.unavailable_reason}
            >
              <div style={s.cardIcon}>{KIND_ICONS[source.kind] || '🔌'}</div>
              <div style={s.cardName}>{source.kind}</div>
              <div style={s.cardDesc}>{source.description}</div>
              {!source.available && (
                <div style={s.cardUnavailable}>{source.unavailable_reason}</div>
              )}
            </button>
          ))}
        </div>

        {onCancel && (
          <button style={s.linkBtn} onClick={onCancel}>← Back to file upload</button>
        )}
      </div>
    )
  }

  /* ── Connection form ──────────────────────────────────────────────────── */

  return (
    <div style={s.wrap}>
      <div style={s.header}>
        <h2 style={s.title}>
          {KIND_ICONS[kind]} Connect to {kind}
        </h2>
        <p style={s.sub}>{selected?.description}</p>
      </div>

      <label style={s.label}>
        {caps?.target_label || 'Connection'}
        <input
          style={s.input}
          value={target}
          placeholder={caps?.target_placeholder || ''}
          onChange={e => { setTarget(e.target.value); reset() }}
          autoFocus
        />
      </label>

      {caps?.uses_secret && (
        <label style={s.label}>
          Password or API token <span style={s.optional}>(optional)</span>
          <input
            style={s.input}
            type="password"
            value={secret}
            placeholder="Kept out of the URL, never sent back to the browser"
            onChange={e => { setSecret(e.target.value); reset() }}
          />
          <span style={s.hint}>
            Supplied separately from the connection string so it never appears
            in a form field you can read back, a log line, or an error message.
          </span>
        </label>
      )}

      {caps?.needs_entity && (
        <label style={s.label}>
          {caps.entity_label || 'Table'}
          {test?.entities?.length ? (
            <select
              style={s.input}
              value={entity}
              onChange={e => { setEntity(e.target.value); setPreview(null) }}
            >
              <option value="">Choose one…</option>
              {test.entities.map(name => (
                <option key={name} value={name}>{name}</option>
              ))}
            </select>
          ) : (
            <input
              style={s.input}
              value={entity}
              placeholder="Test the connection to list what is available"
              onChange={e => { setEntity(e.target.value); setPreview(null) }}
            />
          )}
        </label>
      )}

      {caps?.accepts_query && (
        <label style={s.label}>
          Query <span style={s.optional}>(optional — overrides the {caps.entity_label?.toLowerCase() || 'table'})</span>
          <textarea
            style={{ ...s.input, ...s.textarea }}
            value={query}
            rows={3}
            placeholder={
              kind === 'mongodb'
                ? '{"status": "active"}'
                : 'SELECT region, SUM(revenue) AS total FROM orders GROUP BY region'
            }
            onChange={e => { setQuery(e.target.value); setPreview(null) }}
          />
        </label>
      )}

      {error && <div style={s.error}>{error}</div>}

      {test?.ok && (
        <div style={s.success}>
          ✓ {test.detail}
          {test.truncated_entities && (
            <span style={s.hint}> (showing the first {test.entities.length})</span>
          )}
        </div>
      )}

      {preview && (
        <div style={s.preview}>
          <div style={s.previewHead}>
            <strong>{preview.label}</strong>
            <span style={s.hint}>
              {preview.rows} sample row{preview.rows === 1 ? '' : 's'} ·{' '}
              {preview.columns.length} columns
            </span>
          </div>
          {preview.notes?.map((note, i) => (
            <div key={i} style={s.note}>{note}</div>
          ))}
          <div style={s.tableWrap}>
            <table style={s.table}>
              <thead>
                <tr>
                  {preview.columns.map(col => (
                    <th key={col} style={s.th}>
                      {col}
                      <span style={s.thType}>{preview.dtypes[col]}</span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {preview.preview.slice(0, 8).map((row, i) => (
                  <tr key={i}>
                    {preview.columns.map(col => (
                      <td key={col} style={s.td}>
                        {row[col] === null || row[col] === undefined
                          ? <span style={s.null}>null</span>
                          : String(row[col])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div style={s.actions}>
        <button style={s.btnGhost} onClick={() => { setKind(null); reset() }}>
          ← Back
        </button>
        <div style={s.actionsRight}>
          <button style={s.btnGhost} onClick={onTest} disabled={!target || testing}>
            {testing ? 'Testing…' : 'Test connection'}
          </button>
          <button
            style={s.btnGhost}
            onClick={onPreview}
            disabled={!target || testing || (caps?.needs_entity && !entity && !query)}
          >
            Preview data
          </button>
          <button
            style={s.btnPrimary}
            onClick={onLoad}
            disabled={!target || loading || (caps?.needs_entity && !entity && !query)}
          >
            {loading ? 'Loading…' : 'Load into LANA'}
          </button>
        </div>
      </div>
    </div>
  )
}

const s = {
  wrap: { maxWidth: 780, margin: '0 auto', padding: '32px 24px', display: 'flex',
          flexDirection: 'column', gap: 18 },
  header: { textAlign: 'center' },
  title: { margin: 0, fontSize: 24, fontWeight: 600, color: 'var(--text)' },
  sub: { margin: '8px 0 0', fontSize: 14, color: 'var(--text-dim)', lineHeight: 1.5 },

  grid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
          gap: 14 },
  card: { textAlign: 'left', padding: 18, borderRadius: 12, cursor: 'pointer',
          background: 'var(--surface)', border: '1px solid var(--border)',
          color: 'var(--text)', display: 'flex', flexDirection: 'column', gap: 6 },
  cardDisabled: { opacity: 0.5, cursor: 'not-allowed' },
  cardIcon: { fontSize: 24 },
  cardName: { fontSize: 15, fontWeight: 600, textTransform: 'capitalize' },
  cardDesc: { fontSize: 12.5, color: 'var(--text-dim)', lineHeight: 1.5 },
  cardUnavailable: { fontSize: 11.5, color: 'var(--warn, #d08770)', marginTop: 4 },

  label: { display: 'flex', flexDirection: 'column', gap: 6, fontSize: 13,
           fontWeight: 500, color: 'var(--text)' },
  optional: { fontWeight: 400, color: 'var(--text-dim)' },
  input: { padding: '10px 12px', borderRadius: 8, fontSize: 13.5,
           background: 'var(--bg)', color: 'var(--text)',
           border: '1px solid var(--border)', fontFamily: 'inherit' },
  textarea: { fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
              resize: 'vertical' },
  hint: { fontSize: 11.5, fontWeight: 400, color: 'var(--text-dim)', lineHeight: 1.5 },

  error: { padding: '10px 14px', borderRadius: 8, fontSize: 13,
           background: 'rgba(191,97,106,0.12)', border: '1px solid rgba(191,97,106,0.35)',
           color: '#d08770' },
  success: { padding: '10px 14px', borderRadius: 8, fontSize: 13,
             background: 'rgba(163,190,140,0.12)',
             border: '1px solid rgba(163,190,140,0.35)', color: '#a3be8c' },

  preview: { border: '1px solid var(--border)', borderRadius: 10, overflow: 'hidden' },
  previewHead: { padding: '10px 14px', display: 'flex', justifyContent: 'space-between',
                 alignItems: 'center', background: 'var(--surface)', fontSize: 13 },
  note: { padding: '6px 14px', fontSize: 11.5, color: 'var(--text-dim)',
          borderTop: '1px solid var(--border)' },
  tableWrap: { overflowX: 'auto', maxHeight: 280 },
  table: { borderCollapse: 'collapse', width: '100%', fontSize: 12 },
  th: { textAlign: 'left', padding: '8px 12px', whiteSpace: 'nowrap',
        borderTop: '1px solid var(--border)', borderBottom: '1px solid var(--border)',
        background: 'var(--surface)', display: 'table-cell' },
  thType: { display: 'block', fontSize: 10, fontWeight: 400, color: 'var(--text-dim)' },
  td: { padding: '6px 12px', whiteSpace: 'nowrap',
        borderBottom: '1px solid var(--border)', color: 'var(--text-dim)' },
  null: { fontStyle: 'italic', opacity: 0.6 },

  actions: { display: 'flex', justifyContent: 'space-between', alignItems: 'center',
             gap: 10, marginTop: 4 },
  actionsRight: { display: 'flex', gap: 10 },
  btnGhost: { padding: '9px 16px', borderRadius: 8, fontSize: 13, cursor: 'pointer',
              background: 'transparent', color: 'var(--text)',
              border: '1px solid var(--border)' },
  btnPrimary: { padding: '9px 18px', borderRadius: 8, fontSize: 13, fontWeight: 600,
                cursor: 'pointer', background: 'var(--accent)', color: '#0b0d10',
                border: 'none' },
  linkBtn: { alignSelf: 'center', background: 'none', border: 'none', cursor: 'pointer',
             color: 'var(--text-dim)', fontSize: 13, textDecoration: 'underline' },
}
