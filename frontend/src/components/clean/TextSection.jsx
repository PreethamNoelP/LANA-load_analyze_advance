import { s } from './styles.js'
import { SectionCard } from './primitives.jsx'

export default function TextSection({ info, ops, onOpsChange }) {
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
