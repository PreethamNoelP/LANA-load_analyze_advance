import { s } from './styles.js'
import { GRADE_COLORS } from '../../constants.js'

export default function QualityBanner({ quality }) {
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
