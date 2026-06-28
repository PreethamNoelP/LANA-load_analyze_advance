export default function KpiTiles({ session }) {
  if (!session) return null

  const numCols = session.numeric_columns || []
  const totalCols = session.columns?.length || 0

  const tiles = [
    { label: 'Total Rows', value: session.rows?.toLocaleString(), color: 'var(--accent)' },
    { label: 'Columns', value: totalCols, color: 'var(--green)' },
    { label: 'Numeric Cols', value: numCols.length, color: 'var(--amber)' },
    { label: 'Text Cols', value: totalCols - numCols.length, color: 'var(--muted)' },
  ]

  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: 'repeat(4, 1fr)',
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
            fontSize: 28,
            fontWeight: 700,
            fontFamily: 'var(--ff-mono)',
            color: t.color,
            letterSpacing: '-0.02em',
          }}>
            {t.value}
          </div>
        </div>
      ))}
    </div>
  )
}