import { useState, useRef, useEffect } from 'react'
import { queryAI } from '../api.js'

const SUGGESTIONS = [
  'What are the key trends in this dataset?',
  'Which columns have the most missing values?',
  'Summarize the distribution of numeric columns.',
  'What insights can you draw from the top rows?',
]

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
          flexShrink: 0, marginTop: 1,
        }}>Q</div>
        <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text)', lineHeight: 1.5, paddingTop: 3 }}>
          {msg.q}
        </div>
      </div>

      {/* Answer */}
      {msg.loading ? (
        <div style={{
          borderLeft: '2px solid var(--border)', paddingLeft: 16, marginLeft: 34,
          color: 'var(--muted)', fontSize: 13,
          display: 'flex', alignItems: 'center', gap: 8,
        }}>
          <span style={{ animation: 'pulse 1.2s ease-in-out infinite' }}>●</span>
          Thinking…
        </div>
      ) : msg.error ? (
        <div style={{
          borderLeft: '2px solid var(--red)', paddingLeft: 16, marginLeft: 34,
          color: 'var(--red)', fontSize: 13,
        }}>{msg.error}</div>
      ) : (
        <div style={{
          marginLeft: 34,
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          borderLeft: '3px solid var(--accent)',
          borderRadius: '0 10px 10px 0',
          padding: '14px 18px',
          fontSize: 14, lineHeight: 1.75,
          color: 'var(--text)', whiteSpace: 'pre-wrap',
        }}>
          {msg.a}
          <div style={{
            marginTop: 10,
            fontSize: 11, color: 'var(--muted)',
            fontFamily: 'var(--ff-mono)',
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

export default function AskAI({ session }) {
  const [input, setInput] = useState('')
  const [messages, setMessages] = useState([])
  const bottomRef = useRef()

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  async function send(q) {
    const question = (q || input).trim()
    if (!question) return
    setInput('')

    const id = Date.now()
    setMessages(prev => [...prev, { id, q: question, loading: true }])

    try {
      const { answer } = await queryAI(session.session_id, question)
      setMessages(prev => prev.map(m => m.id === id ? { ...m, loading: false, a: answer } : m))
    } catch (e) {
      setMessages(prev => prev.map(m => m.id === id ? { ...m, loading: false, error: e.message } : m))
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>

      {/* ── Thread ──────────────────────────────────────────────────────── */}
      <div style={{ paddingBottom: 8 }}>
        {messages.length === 0 ? (
          <div style={{
            padding: '48px 0 32px',
            textAlign: 'center',
            color: 'var(--muted)',
            fontSize: 14,
          }}>
            <div style={{ fontSize: 28, marginBottom: 12, opacity: 0.4 }}>◎</div>
            Ask anything about your dataset
          </div>
        ) : (
          messages.map(m => <Message key={m.id} msg={m} />)
        )}
        <div ref={bottomRef} />
      </div>

      {/* ── Input bar — sticky at the bottom of the scroll area ─────────── */}
      <div style={{
        position: 'sticky', bottom: 24,
        background: 'var(--bg)',
        paddingTop: 12,
        borderTop: messages.length > 0 ? '1px solid var(--border)' : 'none',
      }}>
        <div style={{
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          borderRadius: 14,
          padding: '14px 16px 10px',
          boxShadow: '0 4px 24px rgba(0,0,0,0.35)',
        }}>
          {/* Text input row */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
            <input
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && !e.shiftKey && send()}
              placeholder="Ask anything about your data…"
              style={{
                flex: 1, background: 'transparent',
                border: 'none', fontSize: 14,
                color: 'var(--text)',
              }}
            />
            <button
              onClick={() => send()}
              disabled={!input.trim()}
              style={{
                width: 34, height: 34, borderRadius: 9,
                background: input.trim() ? 'var(--accent)' : 'var(--border)',
                color: '#fff', fontSize: 16, flexShrink: 0,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                border: 'none', cursor: input.trim() ? 'pointer' : 'default',
                transition: 'background 0.15s',
              }}
            >→</button>
          </div>

          {/* Suggestion chips */}
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
            <span style={{ fontSize: 11, color: 'var(--muted)', marginRight: 2 }}>Try</span>
            {SUGGESTIONS.map(s => (
              <button
                key={s}
                onClick={() => send(s)}
                style={{
                  fontSize: 11, color: '#c0c4dc',
                  background: 'rgba(91,108,255,0.07)',
                  border: '1px solid rgba(91,108,255,0.2)',
                  borderRadius: 20, padding: '3px 11px',
                  cursor: 'pointer',
                  transition: 'color 0.15s, border-color 0.15s, background 0.15s',
                  fontFamily: 'var(--ff-ui)',
                }}
                onMouseEnter={e => {
                  e.currentTarget.style.color = '#fff'
                  e.currentTarget.style.borderColor = 'var(--accent2)'
                  e.currentTarget.style.background = 'rgba(91,108,255,0.15)'
                }}
                onMouseLeave={e => {
                  e.currentTarget.style.color = '#c0c4dc'
                  e.currentTarget.style.borderColor = 'rgba(91,108,255,0.2)'
                  e.currentTarget.style.background = 'rgba(91,108,255,0.07)'
                }}
              >{s}</button>
            ))}
          </div>
        </div>
        <div style={{ textAlign: 'right', fontSize: 11, color: '#4a4f66', marginTop: 5, paddingRight: 4 }}>
          ↵ Enter to send
        </div>
      </div>

      <style>{`@keyframes pulse { 0%,100%{opacity:0.3} 50%{opacity:1} }`}</style>
    </div>
  )
}