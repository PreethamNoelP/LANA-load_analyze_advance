/* The Clean tab: fetch issues, collect the user's choices, apply them.
 *
 * The nine presentational pieces this used to contain now live in
 * ./clean/. Splitting them was not cosmetic: at 1,023 lines this file held
 * the data-fetching state machine, nine independent section renderers and a
 * 230-line style object, so any change to one section meant scrolling past
 * the other eight. What is left here is the part that actually coordinates.
 */

import { useState, useEffect } from 'react'
import { getCleanPreview, applyClean, switchVersion, getCleanStatus } from '../api.js'
import ConfirmDialog from './ConfirmDialog.jsx'
import { s } from './clean/styles.js'
import DuplicatesSection from './clean/DuplicatesSection.jsx'
import NullsSection from './clean/NullsSection.jsx'
import OutliersSection from './clean/OutliersSection.jsx'
import TextSection from './clean/TextSection.jsx'
import SchemaSection from './clean/SchemaSection.jsx'
import QualityBanner from './clean/QualityBanner.jsx'
import LineagePanel from './clean/LineagePanel.jsx'
import ResultBanner from './clean/ResultBanner.jsx'

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
    const sessionId = session?.session_id
    if (!sessionId || !hasCleanedData) return
    let cancelled = false
    getCleanStatus(sessionId)
      .then(data => { if (!cancelled) setRestoredLineage(data.lineage || null) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [session?.session_id, hasCleanedData])

  // Operation selections
  const [removeDupes,  setRemoveDupes]  = useState(false)
  const [nullOps,      setNullOps]      = useState({})
  const [outlierOps,   setOutlierOps]   = useState({})
  const [textOps,      setTextOps]      = useState({})
  const [schemaOps,    setSchemaOps]    = useState({})

  // Mounted with key={session.session_id} by the caller, so a dataset switch
  // remounts this component with empty state instead of needing an effect to
  // clear a dozen selection fields by hand. This effect only has to kick off
  // the initial scan.
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

  useEffect(() => {
    if (!session?.session_id) return
    fetchIssues()
    // fetchIssues is redefined each render; depending on it would re-scan in
    // a loop. The session id is the only input that should retrigger a scan.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.session_id])

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
