export default function Sidebar({ session, onUploadNew }) {
  return (
    <aside style={{
      position: 'fixed',
      top: 0, left: 0,
      width: 'var(--sidebar)',
      height: '100vh',
      background: 'var(--surface)',
      borderRight: '1px solid var(--border)',
      display: 'flex',
      flexDirection: 'column',
      padding: '20px 0',
      zIndex: 100,
    }}>
      {/* Logo */}
      <div style={{
        padding: '0 20px 20px',
        borderBottom: '1px solid var(--border)',
        display: 'flex',
        alignItems: 'center',
        gap: 8,
      }}>
        <div style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--accent)' }} />
        <span style={{ fontWeight: 700, fontSize: 20, letterSpacing: '-0.03em' }}>LANA</span>
      </div>

      {/* Ollama status */}
      <div style={{ padding: '16px 12px' }}>
        <div style={{
          padding: '10px 12px',
          background: 'rgba(78,199,127,0.08)',
          border: '1px solid rgba(78,199,127,0.2)',
          borderRadius: 8,
          fontSize: 12,
          color: 'var(--green)',
          display: 'flex',
          alignItems: 'center',
          gap: 8,
        }}>
          <span style={{
            width: 6, height: 6, borderRadius: '50%',
            background: 'var(--green)',
            boxShadow: '0 0 6px var(--green)',
            flexShrink: 0,
          }} />
          Ollama connected
        </div>
      </div>

      {/* Footer */}
      <div style={{
        marginTop: 'auto',
        padding: '16px 20px',
        borderTop: '1px solid var(--border)',
      }}>
        {session && (
          <div style={{
            fontSize: 11,
            color: 'var(--muted)',
            fontFamily: 'var(--ff-mono)',
            marginBottom: 10,
            lineHeight: 1.6,
          }}>
            <div style={{
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}>{session.filename}</div>
            <div>{session.rows?.toLocaleString()} rows · {session.columns?.length} cols</div>
          </div>
        )}
        <button
          onClick={onUploadNew}
          style={{
            display: 'block',
            width: '100%',
            padding: '8px 0',
            fontSize: 13,
            fontWeight: 500,
            color: 'var(--accent2)',
            background: 'transparent',
            border: '1px solid var(--border)',
            borderRadius: 6,
            textAlign: 'center',
            cursor: 'pointer',
          }}
        >
          + New dataset
        </button>
      </div>
    </aside>
  )
}