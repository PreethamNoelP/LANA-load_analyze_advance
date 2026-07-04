import { useState, useEffect } from 'react'
import { getCleanPreview, applyClean, switchVersion } from '../api.js'

/* ── Small shared primitives ─────────────────────────────────────────────── */

function SectionCard({ icon, title, badge, children }) {
  return (
    <div style={s.card}>
      <div style={s.cardHeader}>
        <span style={s.cardIcon}>{icon}</span>
        <span style={s.cardTitle}>{title}</span>
        {badge != null && <span style={s.badge}>{badge}</span>}
      </div>
      <div style={s.cardBody}>{children}</div>
    </div>
  )
}

function Pill({ label, active, onClick }) {
  return (
    <button
      onClick={onClick}
      style={{
        ...s.pill,
        background: active ? 'var(--accent)' : 'var(--surface)',
        color: active ? '#fff' : 'var(--muted)',
        border: `1px solid ${active ? 'var(--accent)' : 'var(--border)'}`,
      }}
    >{label}</button>
  )
}

/* ── Section: Duplicates ─────────────────────────────────────────────────── */

function DuplicatesSection({ info, enabled, onToggle }) {
  return (
    <SectionCard icon="⟳" title="Duplicate Rows" badge={`${info.count} found`}>
      <div style={s.row}>
        <label style={s.checkRow}>
          <input
            type="checkbox"
            checked={enabled}
            onChange={e => onToggle(e.target.checked)}
            style={{ accentColor: 'var(--accent)', width: 15, height: 15, cursor: 'pointer' }}
          />
          <span style={{ fontSize: 13 }}>
            Remove all <strong style={{ color: 'var(--text)' }}>{info.count}</strong> duplicate rows
          </span>
        </label>
      </div>
      {info.sample_rows?.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 6, letterSpacing: '0.04em', textTransform: 'uppercase' }}>Sample duplicates</div>
          <div style={{ overflowX: 'auto' }}>
            <table style={s.miniTable}>
              <thead>
                <tr>
                  {Object.keys(info.sample_rows[0]).map(k => (
                    <th key={k} style={s.th}>{k}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {info.sample_rows.slice(0, 4).map((row, i) => (
                  <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                    {Object.values(row).map((v, j) => (
                      <td key={j} style={s.td}>{v === null ? <em style={{ opacity: 0.4 }}>null</em> : String(v)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </SectionCard>
  )
}

/* ── Section: Missing Values ─────────────────────────────────────────────── */

function NullsSection({ info, ops, onOpsChange }) {
  const total = Object.values(info).reduce((sum, col) => sum + col.count, 0)

  return (
    <SectionCard icon="◻" title="Missing Values" badge={`${total} nulls across ${Object.keys(info).length} columns`}>
      <div style={s.colList}>
        {Object.entries(info).map(([col, meta]) => (
          <div key={col} style={s.colRow}>
            <div style={s.colName}>
              <span style={s.colLabel}>{col}</span>
              <span style={s.colSub}>{meta.count} nulls · {meta.pct}% · {meta.dtype}</span>
            </div>
            <div style={s.methodGroup}>
              {meta.dtype === 'object' || !meta.mean ? (
                <>
                  <Pill label="Mode" active={ops[col] === 'mode'} onClick={() => onOpsChange(col, 'mode')} />
                  <Pill label="Drop rows" active={ops[col] === 'drop'} onClick={() => onOpsChange(col, 'drop')} />
                  <Pill label="Skip" active={!ops[col] || ops[col] === 'skip'} onClick={() => onOpsChange(col, 'skip')} />
                </>
              ) : (
                <>
                  <Pill label={`Mean (${meta.mean})`} active={ops[col] === 'mean'} onClick={() => onOpsChange(col, 'mean')} />
                  <Pill label={`Median (${meta.median})`} active={ops[col] === 'median'} onClick={() => onOpsChange(col, 'median')} />
                  <Pill label="Zero" active={ops[col] === 'zero'} onClick={() => onOpsChange(col, 'zero')} />
                  <Pill label="Drop rows" active={ops[col] === 'drop'} onClick={() => onOpsChange(col, 'drop')} />
                  <Pill label="Skip" active={!ops[col] || ops[col] === 'skip'} onClick={() => onOpsChange(col, 'skip')} />
                </>
              )}
            </div>
          </div>
        ))}
      </div>
    </SectionCard>
  )
}

/* ── Section: Outliers ───────────────────────────────────────────────────── */

function OutliersSection({ info, ops, onOpsChange }) {
  const total = Object.values(info).reduce((sum, col) => sum + col.count, 0)

  return (
    <SectionCard icon="⬥" title="Outliers" badge={`${total} across ${Object.keys(info).length} columns`}>
      <p style={s.hint}>Detected using the IQR method (Q1 − 1.5×IQR, Q3 + 1.5×IQR).</p>
      <div style={s.colList}>
        {Object.entries(info).map(([col, meta]) => (
          <div key={col} style={s.colRow}>
            <div style={s.colName}>
              <span style={s.colLabel}>{col}</span>
              <span style={s.colSub}>
                {meta.count} outliers · valid range [{meta.lower_bound}, {meta.upper_bound}]
              </span>
            </div>
            <label style={s.checkRow}>
              <input
                type="checkbox"
                checked={!!ops[col]}
                onChange={e => onOpsChange(col, e.target.checked)}
                style={{ accentColor: 'var(--accent)', width: 15, height: 15, cursor: 'pointer' }}
              />
              <span style={{ fontSize: 13 }}>Remove {meta.count} outlier{meta.count !== 1 ? 's' : ''}</span>
            </label>
          </div>
        ))}
      </div>
    </SectionCard>
  )
}

/* ── Section: Text Inconsistencies ──────────────────────────────────────── */

function TextSection({ info, ops, onOpsChange }) {
  const totalAffected = Object.values(info).reduce((sum, col) => sum + col.total_affected, 0)

  return (
    <SectionCard icon="Aa" title="Text Inconsistencies" badge={`${totalAffected} values affected`}>
      <p style={s.hint}>
        Same value appears in multiple forms (case / whitespace). Pick the canonical form for each group.
      </p>
      {Object.entries(info).map(([col, meta]) => (
        <div key={col} style={{ marginBottom: 18 }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--accent2)', marginBottom: 8 }}>
            Column: {col}
            <span style={s.colSub2}>{meta.total_affected} values affected</span>
          </div>
          <div style={s.groupList}>
            {Object.entries(meta.groups).map(([norm, variants]) => (
              <div key={norm} style={s.groupRow}>
                <div style={s.variantList}>
                  {variants.map(v => (
                    <code key={v} style={{
                      ...s.variantChip,
                      background: ops[col]?.[norm] === v ? 'rgba(91,108,255,0.2)' : 'rgba(255,255,255,0.04)',
                      border: `1px solid ${ops[col]?.[norm] === v ? 'var(--accent)' : 'var(--border)'}`,
                      color: ops[col]?.[norm] === v ? 'var(--accent2)' : 'var(--muted)',
                    }}>{v}</code>
                  ))}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
                  <span style={{ fontSize: 11, color: 'var(--muted)' }}>keep as</span>
                  <select
                    value={ops[col]?.[norm] ?? norm}
                    onChange={e => onOpsChange(col, norm, e.target.value)}
                    style={s.select}
                  >
                    <option value={norm}>{norm} (normalized)</option>
                    {variants.filter(v => v !== norm).map(v => (
                      <option key={v} value={v}>{v}</option>
                    ))}
                  </select>
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </SectionCard>
  )
}

/* ── Result Banner ───────────────────────────────────────────────────────── */

function ResultBanner({ result, version, onVersionSwitch }) {
  return (
    <div style={s.resultBanner}>
      <div style={s.resultLeft}>
        <span style={s.resultCheck}>✓</span>
        <div>
          <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text)' }}>
            Cleaning applied
          </div>
          <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 3, fontFamily: 'var(--ff-mono)' }}>
            {result.rows_removed > 0
              ? `${result.rows_before.toLocaleString()} → ${result.rows_after.toLocaleString()} rows  ·  ${result.rows_removed} removed`
              : `${result.rows_after.toLocaleString()} rows  ·  no rows removed`}
          </div>
        </div>
      </div>
      <div style={s.versionToggle}>
        <span style={{ fontSize: 11, color: 'var(--muted)' }}>View:</span>
        <button
          style={{ ...s.vBtn, background: version === 'original' ? 'var(--surface)' : 'transparent', color: version === 'original' ? 'var(--text)' : 'var(--muted)', border: `1px solid ${version === 'original' ? 'var(--border)' : 'transparent'}` }}
          onClick={() => onVersionSwitch('original')}
        >Original</button>
        <button
          style={{ ...s.vBtn, background: version === 'cleaned' ? 'var(--accent)' : 'transparent', color: version === 'cleaned' ? '#fff' : 'var(--muted)', border: `1px solid ${version === 'cleaned' ? 'var(--accent)' : 'transparent'}` }}
          onClick={() => onVersionSwitch('cleaned')}
        >Cleaned</button>
      </div>
    </div>
  )
}

/* ── Main component ──────────────────────────────────────────────────────── */

export default function Clean({ session, cleanVersion, hasCleanedData, onCleanApplied, onVersionChange }) {
  const [issues,   setIssues]   = useState(null)
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState(null)
  const [applying, setApplying] = useState(false)
  const [result,   setResult]   = useState(null)

  // Operation selections
  const [removeDupes,  setRemoveDupes]  = useState(false)
  const [nullOps,      setNullOps]      = useState({})
  const [outlierOps,   setOutlierOps]   = useState({})
  const [textOps,      setTextOps]      = useState({})

  useEffect(() => {
    if (!session) return
    setResult(null)
    fetchIssues()
  }, [session?.session_id])

  async function fetchIssues() {
    setLoading(true)
    setError(null)
    setRemoveDupes(false)
    setNullOps({})
    setOutlierOps({})
    setTextOps({})
    try {
      const data = await getCleanPreview(session.session_id)
      setIssues(data)

      // Pre-populate sensible defaults
      if (data.nulls) {
        const defaults = {}
        Object.entries(data.nulls).forEach(([col, info]) => {
          defaults[col] = info.suggested  // 'mean' or 'mode'
        })
        setNullOps(defaults)
      }
      if (data.outliers) {
        const defaults = {}
        Object.keys(data.outliers).forEach(col => { defaults[col] = false })
        setOutlierOps(defaults)
      }
      if (data.text_inconsistencies) {
        const defaults = {}
        Object.entries(data.text_inconsistencies).forEach(([col, meta]) => {
          const colMap = {}
          Object.keys(meta.groups).forEach(norm => { colMap[norm] = norm })
          defaults[col] = colMap
        })
        setTextOps(defaults)
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  function buildOps() {
    const ops = []

    if (removeDupes && issues?.duplicates) {
      ops.push({ type: 'remove_duplicates' })
    }

    if (issues?.nulls) {
      Object.entries(nullOps).forEach(([col, method]) => {
        if (method && method !== 'skip') {
          ops.push({ type: 'fill_nulls', column: col, method })
        }
      })
    }

    if (issues?.outliers) {
      Object.entries(outlierOps).forEach(([col, enabled]) => {
        if (enabled) ops.push({ type: 'remove_outliers', column: col })
      })
    }

    if (issues?.text_inconsistencies) {
      Object.entries(textOps).forEach(([col, groupMap]) => {
        const mapping = {}
        const colMeta = issues.text_inconsistencies[col]
        Object.entries(groupMap).forEach(([norm, canonical]) => {
          const variants = colMeta?.groups[norm] || []
          variants.forEach(v => { if (v !== canonical) mapping[v] = canonical })
        })
        if (Object.keys(mapping).length > 0) {
          ops.push({ type: 'fix_text', column: col, mapping })
        }
      })
    }

    return ops
  }

  async function handleApply() {
    const ops = buildOps()
    if (ops.length === 0) return
    setApplying(true)
    setError(null)
    try {
      const data = await applyClean(session.session_id, ops)
      setResult(data)
      onCleanApplied()
    } catch (e) {
      setError(e.message)
    } finally {
      setApplying(false)
    }
  }

  async function handleVersionSwitch(v) {
    try {
      await switchVersion(session.session_id, v)
      onVersionChange(v)
    } catch (e) {
      setError(e.message)
    }
  }

  const activeOps = buildOps()
  const hasOps = activeOps.length > 0

  const issueCount = [
    issues?.duplicates ? 1 : 0,
    Object.keys(issues?.nulls || {}).length > 0 ? 1 : 0,
    Object.keys(issues?.outliers || {}).length > 0 ? 1 : 0,
    Object.keys(issues?.text_inconsistencies || {}).length > 0 ? 1 : 0,
  ].reduce((a, b) => a + b, 0)

  return (
    <div style={s.root}>
      {/* Page header */}
      <div style={s.pageHeader}>
        <div>
          <h2 style={s.h2}>Data Cleaning</h2>
          <p style={s.subline}>Detect and fix data quality issues before analysis.</p>
        </div>
        <button style={s.rescanBtn} onClick={fetchIssues} disabled={loading}>
          {loading ? 'Scanning…' : '↻ Re-scan'}
        </button>
      </div>

      {/* Result banner + version toggle */}
      {(result || hasCleanedData) && (
        <ResultBanner
          result={result || { rows_before: 0, rows_after: 0, rows_removed: 0 }}
          version={cleanVersion}
          onVersionSwitch={handleVersionSwitch}
        />
      )}

      {/* Error */}
      {error && (
        <div style={s.errorBox}>
          <span style={{ color: 'var(--red)', fontWeight: 600 }}>Error: </span>{error}
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div style={s.loadingBox}>
          <span style={{ animation: 'lana-pulse 1.2s ease-in-out infinite', marginRight: 8 }}>●</span>
          Scanning dataset for issues…
          <style>{`@keyframes lana-pulse{0%,100%{opacity:0.3}50%{opacity:1}}`}</style>
        </div>
      )}

      {/* No issues */}
      {!loading && !error && issues && issueCount === 0 && (
        <div style={s.cleanCard}>
          <div style={{ fontSize: 28, marginBottom: 10 }}>✓</div>
          <div style={{ fontWeight: 600, fontSize: 15, color: 'var(--text)' }}>Your data looks clean</div>
          <div style={{ fontSize: 13, color: 'var(--muted)', marginTop: 6 }}>
            No duplicates, missing values, outliers, or text inconsistencies detected.
          </div>
        </div>
      )}

      {/* Issue sections */}
      {!loading && issues && issueCount > 0 && (
        <>
          {issues.duplicates && (
            <DuplicatesSection
              info={issues.duplicates}
              enabled={removeDupes}
              onToggle={setRemoveDupes}
            />
          )}

          {issues.nulls && (
            <NullsSection
              info={issues.nulls}
              ops={nullOps}
              onOpsChange={(col, method) => setNullOps(prev => ({ ...prev, [col]: method }))}
            />
          )}

          {issues.outliers && (
            <OutliersSection
              info={issues.outliers}
              ops={outlierOps}
              onOpsChange={(col, enabled) => setOutlierOps(prev => ({ ...prev, [col]: enabled }))}
            />
          )}

          {issues.text_inconsistencies && (
            <TextSection
              info={issues.text_inconsistencies}
              ops={textOps}
              onOpsChange={(col, norm, canonical) =>
                setTextOps(prev => ({
                  ...prev,
                  [col]: { ...(prev[col] || {}), [norm]: canonical },
                }))
              }
            />
          )}

          {/* Apply bar */}
          <div style={s.applyBar}>
            <span style={{ fontSize: 13, color: 'var(--muted)' }}>
              {hasOps
                ? `${activeOps.length} operation${activeOps.length !== 1 ? 's' : ''} selected`
                : 'Select at least one operation above'}
            </span>
            <button
              onClick={handleApply}
              disabled={!hasOps || applying}
              style={{
                ...s.applyBtn,
                background: hasOps && !applying ? 'var(--accent)' : 'var(--border)',
                cursor: hasOps && !applying ? 'pointer' : 'default',
              }}
            >
              {applying ? 'Applying…' : result ? 'Re-apply' : 'Apply Cleaning'}
            </button>
          </div>
        </>
      )}
    </div>
  )
}

/* ── Styles ─────────────────────────────────────────────────────────────── */
const s = {
  root: { maxWidth: 820 },

  pageHeader: {
    display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between',
    marginBottom: 20,
  },
  h2: { fontSize: 18, fontWeight: 700, color: 'var(--text)', margin: 0 },
  subline: { fontSize: 13, color: 'var(--muted)', margin: '4px 0 0' },
  rescanBtn: {
    fontSize: 12, color: 'var(--accent2)',
    background: 'transparent', border: '1px solid var(--border)',
    borderRadius: 6, padding: '6px 14px', cursor: 'pointer',
    fontFamily: 'var(--ff-ui)', flexShrink: 0, marginTop: 2,
  },

  card: {
    background: 'var(--surface)',
    border: '1px solid var(--border)',
    borderRadius: 12,
    marginBottom: 16,
    overflow: 'hidden',
  },
  cardHeader: {
    display: 'flex', alignItems: 'center', gap: 10,
    padding: '12px 18px',
    borderBottom: '1px solid var(--border)',
    background: 'rgba(255,255,255,0.02)',
  },
  cardIcon: {
    fontSize: 15, width: 24, textAlign: 'center', color: 'var(--accent2)',
  },
  cardTitle: { fontSize: 13, fontWeight: 700, color: 'var(--text)' },
  badge: {
    marginLeft: 'auto', fontSize: 11, fontFamily: 'var(--ff-mono)',
    background: 'rgba(91,108,255,0.12)', color: 'var(--accent2)',
    border: '1px solid rgba(91,108,255,0.25)',
    borderRadius: 20, padding: '2px 10px',
  },
  cardBody: { padding: '16px 18px' },

  row: { display: 'flex', alignItems: 'center', gap: 10 },
  checkRow: { display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', color: 'var(--muted)', fontSize: 13 },

  hint: { fontSize: 12, color: 'var(--muted)', margin: '0 0 14px', lineHeight: 1.5 },

  colList: { display: 'flex', flexDirection: 'column', gap: 12 },
  colRow: {
    display: 'flex', alignItems: 'center', gap: 16,
    padding: '10px 14px',
    background: 'rgba(255,255,255,0.02)',
    border: '1px solid var(--border)',
    borderRadius: 8,
    flexWrap: 'wrap',
  },
  colName: { flex: 1, minWidth: 140 },
  colLabel: { display: 'block', fontSize: 13, fontWeight: 600, color: 'var(--text)', fontFamily: 'var(--ff-mono)' },
  colSub: { display: 'block', fontSize: 11, color: 'var(--muted)', marginTop: 2 },
  colSub2: { fontSize: 11, color: 'var(--muted)', marginLeft: 8 },

  methodGroup: { display: 'flex', gap: 6, flexWrap: 'wrap' },
  pill: {
    fontSize: 11, padding: '4px 11px', borderRadius: 20,
    cursor: 'pointer', fontFamily: 'var(--ff-ui)',
    transition: 'background 0.15s, color 0.15s, border-color 0.15s',
  },

  groupList: { display: 'flex', flexDirection: 'column', gap: 8 },
  groupRow: {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12,
    padding: '8px 12px',
    background: 'rgba(255,255,255,0.02)',
    border: '1px solid var(--border)',
    borderRadius: 8,
    flexWrap: 'wrap',
  },
  variantList: { display: 'flex', gap: 6, flexWrap: 'wrap', flex: 1 },
  variantChip: {
    fontSize: 12, padding: '3px 10px', borderRadius: 6,
    fontFamily: 'var(--ff-mono)', transition: 'background 0.15s, border-color 0.15s',
  },
  select: {
    fontSize: 12, background: 'var(--surface)',
    border: '1px solid var(--border)', borderRadius: 6,
    color: 'var(--text)', padding: '4px 8px',
    fontFamily: 'var(--ff-ui)', cursor: 'pointer',
  },

  miniTable: { width: '100%', borderCollapse: 'collapse', fontSize: 11, fontFamily: 'var(--ff-mono)' },
  th: { padding: '6px 12px', textAlign: 'left', fontWeight: 600, color: '#6b7190', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.04em', whiteSpace: 'nowrap' },
  td: { padding: '6px 12px', color: 'var(--text)', whiteSpace: 'nowrap', maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis' },

  applyBar: {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    padding: '16px 20px',
    background: 'var(--surface)',
    border: '1px solid var(--border)',
    borderRadius: 10,
    marginTop: 4,
  },
  applyBtn: {
    fontSize: 13, fontWeight: 600, color: '#fff',
    padding: '9px 22px', borderRadius: 8, border: 'none',
    fontFamily: 'var(--ff-ui)', transition: 'background 0.15s',
  },

  resultBanner: {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    padding: '14px 20px',
    background: 'rgba(78,199,127,0.08)',
    border: '1px solid rgba(78,199,127,0.25)',
    borderRadius: 10,
    marginBottom: 20,
    flexWrap: 'wrap',
    gap: 12,
  },
  resultLeft: { display: 'flex', alignItems: 'center', gap: 14 },
  resultCheck: {
    width: 32, height: 32, borderRadius: 8,
    background: 'rgba(78,199,127,0.2)', color: 'var(--green)',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    fontSize: 16, fontWeight: 700, flexShrink: 0,
  },
  versionToggle: { display: 'flex', alignItems: 'center', gap: 6 },
  vBtn: {
    fontSize: 12, fontWeight: 600,
    padding: '5px 14px', borderRadius: 6,
    cursor: 'pointer', fontFamily: 'var(--ff-ui)',
    transition: 'background 0.15s, color 0.15s',
  },

  errorBox: {
    background: 'rgba(224,82,82,0.08)', border: '1px solid rgba(224,82,82,0.25)',
    borderRadius: 8, padding: '12px 16px', fontSize: 13,
    color: 'var(--muted)', marginBottom: 16,
  },
  loadingBox: {
    background: 'var(--surface)', border: '1px solid var(--border)',
    borderRadius: 8, padding: '16px 20px', fontSize: 13,
    color: 'var(--muted)', display: 'flex', alignItems: 'center',
    marginBottom: 16,
  },
  cleanCard: {
    background: 'rgba(78,199,127,0.06)', border: '1px solid rgba(78,199,127,0.2)',
    borderRadius: 12, padding: '32px 24px', textAlign: 'center',
    color: 'var(--green)',
  },
}