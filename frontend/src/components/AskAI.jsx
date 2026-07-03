function Message({ msg }) {
  return (
    <div style={{ marginBottom: 28 }}>
      {/* Question */}
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, marginBottom: 12 }}>
        <div style={{
          width: 24, height: 24, borderRadius: 6,
          background: 'var(--accent)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 11, fontWeight: 700, color: '#fff',
          flexShrink: 0, marginTop: 2,
        }}>Q</div>
        <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text)', lineHeight: 1.5, paddingTop: 3 }}>
          {msg.q}
        </div>
      </div>

      {/* Answer */}
      {msg.loading ? (
        <div style={{
          marginLeft: 34, borderLeft: '2px solid var(--border)',
          paddingLeft: 16, color: 'var(--muted)', fontSize: 13,
          display: 'flex', alignItems: 'center', gap: 8,
        }}>
          <span style={{ animation: 'lana-pulse 1.2s ease-in-out infinite' }}>●</span>
          Thinking…
        </div>
      ) : msg.error ? (
        <div style={{
          marginLeft: 34, borderLeft: '2px solid var(--red)',
          paddingLeft: 16, color: 'var(--red)', fontSize: 13,
        }}>{msg.error}</div>
      ) : (
        <div style={{
          marginLeft: 34,
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          borderLeft: '3px solid var(--accent)',
          borderRadius: '0 10px 10px 0',
          padding: '14px 18px',
          fontSize: 14, lineHeight: 1.8,
          color: 'var(--text)', whiteSpace: 'pre-wrap',
        }}>
          {msg.a}
          <div style={{
            marginTop: 10, fontSize: 11,
            color: 'var(--muted)', fontFamily: 'var(--ff-mono)',
            display: 'flex', alignItems: 'center', gap: 6,
          }}>
            <span style={{ width: 5, height: 5, borderRadius: '50%', background: 'var(--green)', display: 'inline-block' }} />
            LANA AI
          </div>
        </div>
      )}
    </div>
  )
}

export default function AskAI({ messages }) {
  if (!messages.length) {
    return (
      <div style={{ padding: '40px 0 16px', textAlign: 'center', color: 'var(--muted)', fontSize: 14 }}>
        <div style={{ fontSize: 32, marginBottom: 12, opacity: 0.25 }}>◎</div>
        Ask anything about your dataset below
      </div>
    )
  }

  return (
    <div>
      {messages.map(m => <Message key={m.id} msg={m} />)}
      <style>{`@keyframes lana-pulse { 0%,100%{opacity:0.3} 50%{opacity:1} }`}</style>
    </div>
  )
}