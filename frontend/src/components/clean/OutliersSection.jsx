import { s } from './styles.js'
import { SectionCard, Pill, Reasoning } from './primitives.jsx'

export default function OutliersSection({ info, ops, onOpsChange }) {
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
