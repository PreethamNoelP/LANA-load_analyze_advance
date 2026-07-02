export default function DataPreview({ preview, columns }) {
  if (!preview?.length) return null

  return (
    <div style={{
      background: 'var(--surface)',
      border: '1px solid var(--border)',
      borderRadius: 12,
      overflow: 'hidden',
      marginBottom: 20,
    }}>
      {/* Header */}
      <div style={{
        padding: '10px 16px',
        borderBottom: '1px solid var(--border)',
        display: 'flex', alignItems: 'center', gap: 10,
        background: 'rgba(255,255,255,0.02)',
      }}>
        <svg width="13" height="13" fill="none" viewBox="0 0 24 24" stroke="var(--muted)" strokeWidth="2">
          <rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 21V9"/>
        </svg>
        <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--muted)', letterSpacing: '0.04em' }}>
          DATA PREVIEW
        </span>
        <span style={{
          marginLeft: 'auto', fontSize: 11, fontFamily: 'var(--ff-mono)',
          color: 'var(--muted)', background: 'rgba(255,255,255,0.05)',
          border: '1px solid var(--border)', borderRadius: 4,
          padding: '2px 8px',
        }}>
          {columns.length} cols · {preview.length} rows shown
        </span>
      </div>

      {/* Table */}
      <div style={{ overflowX: 'auto' }}>
        <table style={{
          width: '100%', borderCollapse: 'collapse',
          fontSize: 12, fontFamily: 'var(--ff-mono)',
        }}>
          <thead>
            <tr style={{ background: 'rgba(255,255,255,0.03)' }}>
              <th style={thStyle}>#</th>
              {columns.map(c => (
                <th key={c} style={thStyle}>{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {preview.map((row, i) => (
              <tr
                key={i}
                style={{ borderTop: '1px solid var(--border)' }}
                onMouseEnter={e => e.currentTarget.style.background = 'rgba(91,108,255,0.04)'}
                onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
              >
                <td style={tdMuted}>{i + 1}</td>
                {columns.map(c => {
                  const val = row[c]
                  const isNull = val === null || val === undefined
                  return (
                    <td key={c} style={tdStyle}>
                      {isNull
                        ? <span style={{ color: 'var(--muted)', fontStyle: 'italic', opacity: 0.5 }}>null</span>
                        : <span title={String(val)}>{String(val)}</span>
                      }
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Footer */}
      <div style={{
        padding: '7px 16px',
        fontSize: 11, color: 'var(--muted)',
        borderTop: '1px solid var(--border)',
        display: 'flex', alignItems: 'center', gap: 6,
        background: 'rgba(255,255,255,0.01)',
      }}>
        <span style={{ width: 5, height: 5, borderRadius: '50%', background: 'var(--green)', display: 'inline-block', opacity: 0.7 }} />
        Showing first {preview.length} rows · scroll right for all columns
      </div>
    </div>
  )
}

const thStyle = {
  padding: '9px 14px',
  textAlign: 'left',
  fontWeight: 600,
  color: '#6b7190',
  whiteSpace: 'nowrap',
  letterSpacing: '0.03em',
  fontSize: 11,
  textTransform: 'uppercase',
}

const tdStyle = {
  padding: '8px 14px',
  color: 'var(--text)',
  whiteSpace: 'nowrap',
  maxWidth: 200,
  overflow: 'hidden',
  textOverflow: 'ellipsis',
}

const tdMuted = { ...tdStyle, color: '#4a4f66', fontWeight: 600 }