export default function DataPreview({ preview, columns }) {
  if (!preview?.length) return null

  return (
    <div style={{
      marginBottom: 20,
      background: 'var(--surface)',
      border: '1px solid var(--border)',
      borderRadius: 10,
      overflow: 'hidden',
    }}>
      <div style={{ overflowX: 'auto' }}>
        <table style={{
          width: '100%',
          borderCollapse: 'collapse',
          fontSize: 12,
          fontFamily: 'var(--ff-mono)',
        }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border)' }}>
              <th style={thStyle}>#</th>
              {columns.map(c => (
                <th key={c} style={thStyle}>{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {preview.map((row, i) => (
              <tr key={i} style={{
                borderBottom: '1px solid var(--border)',
                background: i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.015)',
              }}>
                <td style={tdMuted}>{i + 1}</td>
                {columns.map(c => (
                  <td key={c} style={tdStyle}>
                    {row[c] === null || row[c] === undefined
                      ? <span style={{ color: 'var(--muted)', fontStyle: 'italic' }}>null</span>
                      : String(row[c])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div style={{
        padding: '8px 16px',
        fontSize: 11,
        color: 'var(--muted)',
        borderTop: '1px solid var(--border)',
      }}>
        Showing first {preview.length} rows
      </div>
    </div>
  )
}

const thStyle = {
  padding: '10px 14px',
  textAlign: 'left',
  fontWeight: 600,
  color: 'var(--muted)',
  whiteSpace: 'nowrap',
  letterSpacing: '0.02em',
}

const tdStyle = {
  padding: '8px 14px',
  color: 'var(--text)',
  whiteSpace: 'nowrap',
  maxWidth: 200,
  overflow: 'hidden',
  textOverflow: 'ellipsis',
}

const tdMuted = { ...tdStyle, color: 'var(--muted)' }