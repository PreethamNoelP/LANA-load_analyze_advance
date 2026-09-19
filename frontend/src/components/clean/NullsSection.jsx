import { s } from './styles.js'
import { SectionCard, Pill, Reasoning } from './primitives.jsx'

export default function NullsSection({ info, ops, onOpsChange }) {
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
