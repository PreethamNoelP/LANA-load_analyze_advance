import { useState } from 'react'
import { s } from './styles.js'
import { SectionCard } from './primitives.jsx'

export default function LineagePanel({ lineage }) {
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
              {step.destructive ? 'destructive' : 'non-destructive'}
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
