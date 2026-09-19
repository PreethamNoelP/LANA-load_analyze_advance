import { s } from './styles.js'
import { SectionCard } from './primitives.jsx'

export default function DuplicatesSection({ info, enabled, onToggle }) {
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
