import { GRADE_COLORS } from '../constants.js'

export default function KpiTiles({ session }) {
  if (!session) return null

  const numCols = session.numeric_columns || []
  const totalCols = session.columns?.length || 0
  const quality = session.quality

  const tiles = [
    { label: 'Total Rows', value: session.rows?.toLocaleString(), color: 'var(--accent)' },
    { label: 'Columns', value: totalCols, color: 'var(--green)' },
    { label: 'Numeric Cols', value: numCols.length, color: 'var(--amber)' },
    { label: 'Text Cols', value: totalCols - numCols.length, color: 'var(--muted)' },
  ]
  // The quality score was already computed at upload — surfacing it here
  // means a user sees it before ever opening the Clean tab, rather than it
  // only existing inside a screen they might not visit.
  if (quality) {
    tiles.push({
      label: 'Data Quality',
      value: `${Math.round(quality.score)} · ${quality.grade}`,
      color: GRADE_COLORS[quality.grade] || 'var(--muted)',
    })
  }

  return (
    <div style={{
      display: 'grid',
      // auto-fit rather than a fixed repeat(N,1fr): on a narrow viewport the
      // tiles wrap onto more rows at a readable width instead of all N
      // columns compressing down to fit regardless of how narrow that gets.
      gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))',
      gap: 12,
      marginBottom: 24,
    }}>
      {tiles.map(t => (
        <div key={t.label} style={{
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          borderRadius: 10,
          padding: '16px 18px',
          position: 'relative',
          overflow: 'hidden',
        }}>
          <div style={{
            position: 'absolute',
            top: 0, left: 0, right: 0,
            height: 2,
            background: t.color,
            opacity: 0.7,
          }} />
          <div style={{
            fontSize: 11,
            fontWeight: 600,
            letterSpacing: '0.06em',
            textTransform: 'uppercase',
            color: 'var(--muted)',
            marginBottom: 8,
          }}>
            {t.label}
          </div>
          <div style={{
            fontSize: t.label === 'Data Quality' ? 22 : 28,
            fontWeight: 700,
            fontFamily: 'var(--ff-mono)',
            color: t.color,
            letterSpacing: '-0.02em',
            textTransform: t.label === 'Data Quality' ? 'capitalize' : 'none',
          }}>
            {t.value}
          </div>
        </div>
      ))}
    </div>
  )
}
