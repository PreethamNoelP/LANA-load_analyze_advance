import { s } from './styles.js'
import { SectionCard } from './primitives.jsx'
import { friendlyDtype } from './dtypes.js'

export default function SchemaSection({ columnTypes, ops, onOpsChange }) {
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
