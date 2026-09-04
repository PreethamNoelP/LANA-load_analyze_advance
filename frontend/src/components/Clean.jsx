import { useState, useEffect } from 'react'
import { getCleanPreview, applyClean, switchVersion, getCleanStatus } from '../api.js'
import ConfirmDialog from './ConfirmDialog.jsx'

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

function Reasoning({ text, caveats }) {
  if (!text && !(caveats?.length > 0)) return null
  return (
    <div style={s.reasonBox}>
      {text && <div>{text}</div>}
      {caveats?.map((c, i) => (
        <div key={i} style={{ display: 'flex', gap: 7, marginTop: 6, color: 'var(--amber)' }}>
          <span style={{ flexShrink: 0 }}>⚠</span><span>{c}</span>
        </div>
      ))}
    </div>
  )
}

function NullsSection({ info, ops, onOpsChange }) {
  const total = Object.values(info).reduce((sum, col) => sum + col.count, 0)

  return (
    <SectionCard icon="◻" title="Missing Values" badge={`${total} nulls across ${Object.keys(info).length} columns`}>
      <p style={s.hint}>
        Every fill is a guess. LANA recommends the option its profile supports and explains the
        cost — imputed columns also gain a <code style={s.codeChip}>__was_missing</code> flag so
        the guess stays visible downstream.
      </p>
      <div style={s.colList}>
        {Object.entries(info).map(([col, meta]) => {
          const numeric = meta.mean != null
          return (
            <div key={col} style={s.colBlock}>
              <div style={s.colRow}>
                <div style={s.colName}>
                  <span style={s.colLabel}>{col}</span>
                  <span style={s.colSub}>{meta.count} nulls · {meta.pct}% · {meta.dtype}</span>
                </div>
                <div style={s.methodGroup}>
                  <Pill
                    label={meta.suggested === 'flag' ? 'Flag only ✓' : 'Flag only'}
                    active={ops[col] === 'flag'}
                    onClick={() => onOpsChange(col, 'flag')}
                  />
                  {numeric ? (
                    <>
                      <Pill label={`Median (${meta.median})${meta.suggested === 'median' ? ' ✓' : ''}`} active={ops[col] === 'median'} onClick={() => onOpsChange(col, 'median')} />
                      <Pill label={`Mean (${meta.mean})`} active={ops[col] === 'mean'} onClick={() => onOpsChange(col, 'mean')} />
                      <Pill label="Zero" active={ops[col] === 'zero'} onClick={() => onOpsChange(col, 'zero')} />
                    </>
                  ) : (
                    <Pill label={`Mode${meta.suggested === 'mode' ? ' ✓' : ''}`} active={ops[col] === 'mode'} onClick={() => onOpsChange(col, 'mode')} />
                  )}
                  <Pill label="Drop rows" active={ops[col] === 'drop'} onClick={() => onOpsChange(col, 'drop')} />
                  <Pill label="Skip" active={!ops[col] || ops[col] === 'skip'} onClick={() => onOpsChange(col, 'skip')} />
                </div>
              </div>
              <Reasoning text={meta.rationale} />
            </div>
          )
        })}
      </div>
    </SectionCard>
  )
}

/* ── Section: Outliers ───────────────────────────────────────────────────── */

function OutliersSection({ info, ops, onOpsChange }) {
  const total = Object.values(info).reduce((sum, col) => sum + col.count, 0)

  return (
    <SectionCard icon="⬥" title="Unusual Values" badge={`${total} across ${Object.keys(info).length} columns`}>
      <p style={s.hint}>
        These are candidates for review, not errors. Two independent rules are run — Tukey IQR
        fences and the robust MAD rule — and where they disagree, that disagreement is shown.
        Flagging keeps every row; removal is available but discards the whole record.
      </p>
      <div style={s.colList}>
        {Object.entries(info).map(([col, meta]) => {
          const mode = ops[col] || 'skip'
          const iqr = meta.methods?.iqr
          const mad = meta.methods?.modified_zscore
          return (
            <div key={col} style={s.colBlock}>
              <div style={s.colRow}>
                <div style={s.colName}>
                  <span style={s.colLabel}>{col}</span>
                  <span style={s.colSub}>
                    IQR flags {iqr?.applicable ? iqr.count : '—'} · MAD flags {mad?.applicable ? mad.count : '—'}
                    {meta.agreement ? ` · both agree on ${meta.agreement.both}` : ''}
                  </span>
                </div>
                <div style={s.methodGroup}>
                  <Pill label="Flag ✓" active={mode === 'flag'} onClick={() => onOpsChange(col, 'flag')} />
                  <Pill label="Cap to fence" active={mode === 'winsorize'} onClick={() => onOpsChange(col, 'winsorize')} />
                  <Pill label="Delete rows" active={mode === 'remove'} onClick={() => onOpsChange(col, 'remove')} />
                  <Pill label="Skip" active={mode === 'skip'} onClick={() => onOpsChange(col, 'skip')} />
                </div>
              </div>
              {meta.sample_values?.length > 0 && (
                <div style={s.sampleRow}>
                  <span style={{ color: 'var(--muted)' }}>most extreme:</span>
                  {meta.sample_values.map((v, i) => (
                    <code key={i} style={s.variantChip}>{v}</code>
                  ))}
                </div>
              )}
              <Reasoning text={meta.interpretation} caveats={meta.caveats} />
              {mode === 'remove' && (
                <div style={s.dangerBox}>
                  This deletes {iqr?.count ?? meta.count} complete row(s) and everything else they
                  contain. Prefer Flag or Cap unless you know these values are invalid.
                </div>
              )}
            </div>
          )
        })}
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

/* ── Section: Column Types (schema override) ────────────────────────────── */

function friendlyDtype(dtype) {
  if (dtype.startsWith('int') || dtype.startsWith('float')) return 'Number'
  if (dtype === 'bool') return 'Boolean'
  if (dtype === 'category') return 'Category'
  if (dtype.startsWith('datetime')) return 'Date/Time'
  if (dtype === 'object' || dtype === 'str' || dtype === 'string') return 'Text'
  return dtype
}

function SchemaSection({ columnTypes, ops, onOpsChange }) {
  const entries = Object.entries(columnTypes)
  return (
    <SectionCard icon="⌗" title="Column Types" badge={`${entries.length} columns`}>
      <p style={s.hint}>
        Override a column's type before analysis — useful when a date or category column was read in as plain text.
      </p>
      <div style={s.colList}>
        {entries.map(([col, dtype]) => (
          <div key={col} style={s.colRow}>
            <div style={s.colName}>
              <span style={s.colLabel}>{col}</span>
              <span style={s.colSub}>currently {friendlyDtype(dtype)}</span>
            </div>
            <select
              value={ops[col] || ''}
              onChange={e => onOpsChange(col, e.target.value)}
              style={s.select}
            >
              <option value="">Keep as is</option>
              <option value="text">Text</option>
              <option value="numeric">Number</option>
              <option value="category">Category</option>
              <option value="datetime">Date/Time</option>
            </select>
          </div>
        ))}
      </div>
    </SectionCard>
  )
}

/* ── Quality Score ───────────────────────────────────────────────────────── */

export const GRADE_COLORS = {
  excellent: 'var(--green)', good: 'var(--green)',
  fair: 'var(--amber)', poor: 'var(--red)',
}

function QualityBanner({ quality }) {
  const color = GRADE_COLORS[quality.grade] || 'var(--muted)'
  return (
    <div style={s.qualityCard}>
      <div style={{ ...s.qualityScore, color, borderColor: color }}>
        {Math.round(quality.score)}
      </div>
      <div style={{ flex: 1, minWidth: 200 }}>
        <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text)' }}>
          Data quality: <span style={{ color }}>{quality.grade}</span>
        </div>
        <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 3, fontFamily: 'var(--ff-mono)' }}>
          {quality.total_rows?.toLocaleString()} rows × {quality.total_columns} columns ·{' '}
          {quality.missing_pct}% of cells empty
        </div>
        {quality.issues?.length > 0 && (
          <ul style={s.qualityList}>
            {quality.issues.map((issue, i) => (
              <li key={i}>{issue.detail} <span style={{ opacity: 0.55 }}>(−{issue.penalty})</span></li>
            ))}
          </ul>
        )}
        {quality.skewed_columns?.length > 0 && (
          <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 8 }}>
            Highly skewed (not a defect, but read the median rather than the mean):{' '}
            <strong style={{ color: 'var(--accent2)' }}>{quality.skewed_columns.join(', ')}</strong>
          </div>
        )}
      </div>
    </div>
  )
}

/* ── Lineage ─────────────────────────────────────────────────────────────── */

function LineagePanel({ lineage }) {
  const [open, setOpen] = useState(false)
  if (!lineage?.steps?.length) return null
  const { summary, steps } = lineage

  return (
    <SectionCard icon="⌥" title="Transformation Log" badge={`${summary.steps} step${summary.steps !== 1 ? 's' : ''}`}>
      <div style={s.lineageSummary}>
        <span><strong style={{ color: 'var(--text)' }}>{summary.rows_original.toLocaleString()}</strong> raw rows</span>
        <span style={{ opacity: 0.5 }}>→</span>
        <span><strong style={{ color: 'var(--text)' }}>{summary.rows_final.toLocaleString()}</strong> after cleaning</span>
        <span style={{
          marginLeft: 'auto',
          color: summary.rows_removed > 0 ? 'var(--amber)' : 'var(--green)',
        }}>
          {summary.rows_removed > 0
            ? `${summary.rows_removed.toLocaleString()} rows lost (${summary.rows_removed_pct}%)`
            : 'no rows lost'}
        </span>
      </div>

      <button style={s.disclosure} onClick={() => setOpen(o => !o)}>
        {open ? '▾' : '▸'} {open ? 'Hide' : 'Show'} step-by-step detail
      </button>

      {open && steps.map(step => (
        <div key={step.step} style={s.stepRow}>
          <div style={s.stepHead}>
            <span style={s.stepIndex}>{step.step}</span>
            <code style={{ fontSize: 12, color: 'var(--accent2)' }}>{step.operation}</code>
            {step.column && <code style={{ fontSize: 12, color: 'var(--muted)' }}>{step.column}</code>}
            <span style={{
              marginLeft: 'auto', fontSize: 10.5, padding: '2px 8px', borderRadius: 20,
              background: step.destructive ? 'rgba(224,82,82,0.14)' : 'rgba(78,199,127,0.14)',
              color: step.destructive ? 'var(--red)' : 'var(--green)',
            }}>
              {step.destructive ? 'destructive' : 'reversible'}
            </span>
          </div>
          <div style={{ fontSize: 12.5, color: 'var(--muted)', lineHeight: 1.55 }}>{step.rationale}</div>
          <div style={s.stepMeta}>
            {step.rows_removed > 0 && <span>−{step.rows_removed.toLocaleString()} rows</span>}
            {step.cells_changed > 0 && <span>{step.cells_changed.toLocaleString()} values changed</span>}
            {step.columns_added?.length > 0 && <span>+{step.columns_added.join(', ')}</span>}
          </div>
          {step.caveats?.map((c, i) => (
            <div key={i} style={{ display: 'flex', gap: 7, marginTop: 6, fontSize: 12, color: 'var(--amber)' }}>
              <span style={{ flexShrink: 0 }}>⚠</span><span>{c}</span>
            </div>
          ))}
        </div>
      ))}
    </SectionCard>
  )
}

/* ── Result Banner ───────────────────────────────────────────────────────── */

function ResultBanner({ result, version, onVersionSwitch }) {
  return (
    <div>
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
      {/* Requested but not applied — a silent no-op is how a user ends up
          believing a column was cleaned when it was not. Per-step caveats
          live in the transformation log rather than being repeated here. */}
      {result.lineage?.summary?.skipped?.length > 0 && (
        <div style={s.warningsBox}>
          <div style={{ fontWeight: 600, marginBottom: 6 }}>Not applied:</div>
          {result.lineage.summary.skipped.map((skip, i) => (
            <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'flex-start', marginTop: i > 0 ? 6 : 0 }}>
              <span style={{ color: 'var(--amber)', flexShrink: 0 }}>⚠</span>
              <span>
                <code>{skip.operation}</code>
                {skip.column ? <> on <code>{skip.column}</code></> : null} — {skip.reason}
              </span>
            </div>
          ))}
        </div>
      )}
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
  const [confirmOpen, setConfirmOpen] = useState(false)
  // Populated from /clean/status on load when a cleaned version already
  // existed before this component mounted (e.g. a session restored after a
  // page refresh) — `result` only ever comes from a fresh Apply in this tab.
  const [restoredLineage, setRestoredLineage] = useState(null)

  useEffect(() => {
    if (!session || !hasCleanedData) { setRestoredLineage(null); return }
    getCleanStatus(session.session_id)
      .then(data => setRestoredLineage(data.lineage || null))
      .catch(() => {})
  }, [session?.session_id, hasCleanedData])

  // Operation selections
  const [removeDupes,  setRemoveDupes]  = useState(false)
  const [nullOps,      setNullOps]      = useState({})
  const [outlierOps,   setOutlierOps]   = useState({})
  const [textOps,      setTextOps]      = useState({})
  const [schemaOps,    setSchemaOps]    = useState({})

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
    setSchemaOps({})
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
        // Nothing is pre-selected — the user opts in. 'flag' is marked as
        // recommended in the UI, but LANA never transforms data unasked.
        const defaults = {}
        Object.keys(data.outliers).forEach(col => { defaults[col] = 'skip' })
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

    // Type casts run first, so a later fill_nulls/remove_outliers in the same
    // Apply already sees the corrected dtype (e.g. cast to Number, then mean-fill).
    if (issues?.column_types) {
      Object.entries(schemaOps).forEach(([col, dtype]) => {
        if (dtype) ops.push({ type: 'cast_type', column: col, dtype })
      })
    }

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
      Object.entries(outlierOps).forEach(([col, mode]) => {
        if (mode === 'flag')           ops.push({ type: 'flag_outliers', column: col })
        else if (mode === 'winsorize') ops.push({ type: 'winsorize', column: col })
        else if (mode === 'remove')    ops.push({ type: 'remove_outliers', column: col })
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

  // Row-deleting operations lose data outright, unlike a fill or a flag,
  // which is why Apply otherwise runs with no gate at all — cleaning is
  // non-destructive by default (outliers default to 'skip', not 'remove'),
  // so a confirmation only earns its place when something irreversible is
  // actually about to happen.
  function destructiveSummary() {
    const parts = []
    if (removeDupes) parts.push(`remove ${issues.duplicates.count} duplicate row(s)`)
    const droppedCols = Object.entries(nullOps).filter(([, m]) => m === 'drop').map(([c]) => c)
    if (droppedCols.length) parts.push(`drop rows missing ${droppedCols.join(', ')}`)
    const removedCols = Object.entries(outlierOps).filter(([, m]) => m === 'remove').map(([c]) => c)
    if (removedCols.length) parts.push(`delete outlier rows in ${removedCols.join(', ')}`)
    return parts
  }

  function handleApplyClick() {
    if (destructiveSummary().length > 0) {
      setConfirmOpen(true)
    } else {
      handleApply()
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

      {/* Result banner + version toggle. When restored (no fresh `result` in
          this tab yet), the counts come from the same lineage summary the
          Transformation Log below reads — otherwise the two would disagree
          about whether anything was removed. */}
      {(result || hasCleanedData) && (
        <ResultBanner
          result={result || (restoredLineage ? {
            rows_before: restoredLineage.summary.rows_original,
            rows_after: restoredLineage.summary.rows_final,
            rows_removed: restoredLineage.summary.rows_removed,
          } : { rows_before: 0, rows_after: 0, rows_removed: 0 })}
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

      {/* Transformation log — the audit trail from raw upload to this version */}
      {(result?.lineage || restoredLineage) && (
        <LineagePanel lineage={result?.lineage || restoredLineage} />
      )}

      {/* Quality assessment — what the data looks like before any action */}
      {!loading && issues?.quality && <QualityBanner quality={issues.quality} />}

      {/* Column types — independent of issue detection, always available */}
      {!loading && issues?.column_types && (
        <SchemaSection
          columnTypes={issues.column_types}
          ops={schemaOps}
          onOpsChange={(col, dtype) => setSchemaOps(prev => ({ ...prev, [col]: dtype }))}
        />
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
        </>
      )}

      {/* Apply bar — available whenever a schema override or a detected issue can be acted on */}
      {!loading && issues && (
        <div style={s.applyBar}>
          <span style={{ fontSize: 13, color: 'var(--muted)' }}>
            {hasOps
              ? `${activeOps.length} operation${activeOps.length !== 1 ? 's' : ''} selected`
              : 'Select at least one operation above'}
          </span>
          <button
            onClick={handleApplyClick}
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
      )}

      <ConfirmDialog
        open={confirmOpen}
        title="This will permanently remove data"
        message={
          <>
            This cleaning step will {destructiveSummary().join('; ')}. Removed
            rows are gone from the cleaned version — the original stays
            switchable, but this specific data won't be in either view again
            unless you re-run cleaning without it.
          </>
        }
        confirmLabel="Apply anyway"
        danger
        onCancel={() => setConfirmOpen(false)}
        onConfirm={() => { setConfirmOpen(false); handleApply() }}
      />
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

  colBlock: {
    padding: '12px 14px',
    background: 'rgba(255,255,255,0.02)',
    border: '1px solid var(--border)',
    borderRadius: 8,
  },

  methodGroup: { display: 'flex', gap: 6, flexWrap: 'wrap' },

  reasonBox: {
    marginTop: 10, paddingTop: 10,
    borderTop: '1px solid var(--border)',
    fontSize: 12, color: 'var(--muted)', lineHeight: 1.55,
  },
  dangerBox: {
    marginTop: 10, padding: '9px 12px',
    background: 'rgba(224,82,82,0.08)',
    border: '1px solid rgba(224,82,82,0.25)',
    borderRadius: 7, fontSize: 12, color: 'var(--red)', lineHeight: 1.5,
  },
  sampleRow: {
    display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center',
    marginTop: 10, fontSize: 11,
  },
  codeChip: {
    fontFamily: 'var(--ff-mono)', fontSize: 11,
    background: 'rgba(255,255,255,0.05)', padding: '1px 5px', borderRadius: 4,
  },

  qualityCard: {
    display: 'flex', alignItems: 'flex-start', gap: 18,
    padding: '16px 20px',
    background: 'var(--surface)',
    border: '1px solid var(--border)',
    borderRadius: 12,
    marginBottom: 16, flexWrap: 'wrap',
  },
  qualityScore: {
    width: 58, height: 58, borderRadius: 12,
    border: '2px solid', display: 'flex', alignItems: 'center',
    justifyContent: 'center', fontSize: 20, fontWeight: 700,
    fontFamily: 'var(--ff-mono)', flexShrink: 0,
  },
  qualityList: {
    margin: '8px 0 0', paddingLeft: 16,
    fontSize: 12, color: 'var(--muted)', lineHeight: 1.6,
  },

  lineageSummary: {
    display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
    fontSize: 12.5, color: 'var(--muted)', fontFamily: 'var(--ff-mono)',
    paddingBottom: 12, borderBottom: '1px solid var(--border)',
  },
  disclosure: {
    background: 'transparent', border: 'none', padding: '10px 0 4px',
    color: 'var(--accent2)', fontSize: 12, cursor: 'pointer',
    fontFamily: 'var(--ff-ui)',
  },
  stepRow: {
    padding: '12px 14px', marginTop: 8,
    background: 'rgba(255,255,255,0.02)',
    border: '1px solid var(--border)', borderRadius: 8,
  },
  stepHead: {
    display: 'flex', alignItems: 'center', gap: 9,
    marginBottom: 7, flexWrap: 'wrap',
  },
  stepIndex: {
    width: 19, height: 19, borderRadius: 5, flexShrink: 0,
    background: 'rgba(91,108,255,0.15)', color: 'var(--accent2)',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    fontSize: 10.5, fontWeight: 700,
  },
  stepMeta: {
    display: 'flex', gap: 14, flexWrap: 'wrap', marginTop: 7,
    fontSize: 11, color: 'var(--muted)', fontFamily: 'var(--ff-mono)',
  },
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
  warningsBox: {
    padding: '12px 16px',
    background: 'rgba(240,180,60,0.08)',
    border: '1px solid rgba(240,180,60,0.25)',
    borderRadius: 10,
    marginTop: -8, marginBottom: 20,
    fontSize: 12.5, color: 'var(--amber)',
    lineHeight: 1.5,
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