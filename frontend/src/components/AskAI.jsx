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
    <div style={{ marginBottom: 24 }}>
      {/* Question */}
      <div style={{
        display: 'flex',
        alignItems: 'flex-start',
        gap: 10,
        marginBottom: 12,
      }}>
        <div style={{
          width: 22,
          height: 22,
          borderRadius: 4,
          background: 'var(--accent)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: 11,
          fontWeight: 700,
          color: '#fff',
          flexShrink: 0,
          marginTop: 1,
        }}>Q</div>
        <div style={{ fontSize: 14, fontWeight: 500, color: 'var(--text)', lineHeight: 1.5 }}>
          {msg.q}
        </div>
      </div>

      {/* Answer */}
      {msg.loading ? (
        <div style={{
          borderLeft: '2px solid var(--border)',
          paddingLeft: 16,
          marginLeft: 32,
          color: 'var(--muted)',
          fontSize: 13,
          display: 'flex',
          alignItems: 'center',
          gap: 8,
        }}>
          <span style={{ animation: 'pulse 1.2s ease-in-out infinite' }}>●</span>
          Thinking…
        </div>
      ) : msg.error ? (
        <div style={{
          borderLeft: '2px solid var(--red)',
          paddingLeft: 16,
          marginLeft: 32,
          color: 'var(--red)',
          fontSize: 13,
        }}>
          {msg.error}
        </div>
      ) : (
        <div style={{
          borderLeft: '2px solid var(--accent)',
          paddingLeft: 16,
          marginLeft: 32,
          fontSize: 14,
          lineHeight: 1.7,
          color: 'var(--text)',
          whiteSpace: 'pre-wrap',
        }}>
          {msg.a}
          <div style={{
            marginTop: 8,
            fontSize: 11,
            color: 'var(--muted)',
            fontFamily: 'var(--ff-mono)',
          }}>
            — LANA AI
          </div>
        </div>
      )}
    </div>
  )
}

export default function AskAI({ session }) {
  const [input, setInput] = useState('')
  const [messages, setMessages] = useState([])
  const threadRef = useRef()

  useEffect(() => {
    if (threadRef.current) {
      threadRef.current.scrollTop = threadRef.current.scrollHeight
    }
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
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
      {/* Query bar */}
      <div style={{
        background: 'var(--surface)',
        border: '1px solid var(--border)',
        borderRadius: 12,
        padding: '12px 16px',
        marginBottom: 16,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 18, color: 'var(--muted)' }}>⌕</span>
          <input
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && send()}
            placeholder="Ask anything about your data…"
            style={{
              flex: 1,
              background: 'transparent',
              border: 'none',
              fontSize: 14,
              color: 'var(--text)',
            }}
          />
          <span style={{
            fontSize: 11,
            color: 'var(--muted)',
            fontFamily: 'var(--ff-mono)',
          }}>↵ to send</span>
          <button
            onClick={() => send()}
            style={{
              width: 32,
              height: 32,
              borderRadius: 8,
              background: 'var(--accent)',
              color: '#fff',
              fontSize: 15,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              flexShrink: 0,
            }}
          >→</button>
        </div>

        {/* Suggestion chips */}
        <div style={{
          display: 'flex',
          gap: 8,
          marginTop: 10,
          flexWrap: 'wrap',
          alignItems: 'center',
        }}>
          <span style={{ fontSize: 11, color: 'var(--muted)' }}>Try</span>
          {SUGGESTIONS.map(s => (
            <button
              key={s}
              onClick={() => send(s)}
              style={{
                fontSize: 12,
                color: 'var(--muted)',
                background: 'rgba(255,255,255,0.04)',
                border: '1px solid var(--border)',
                borderRadius: 20,
                padding: '3px 10px',
                cursor: 'pointer',
                transition: 'color 0.15s, border-color 0.15s',
              }}
              onMouseEnter={e => {
                e.target.style.color = 'var(--text)'
                e.target.style.borderColor = 'var(--accent2)'
              }}
              onMouseLeave={e => {
                e.target.style.color = 'var(--muted)'
                e.target.style.borderColor = 'var(--border)'
              }}
            >{s}</button>
          ))}
        </div>
      </div>

      {/* Thread */}
      <div
        ref={threadRef}
        style={{
          flex: 1,
          overflowY: 'auto',
          paddingRight: 4,
        }}
      >
        {messages.length === 0 ? (
          <div style={{
            padding: '40px 0',
            textAlign: 'center',
            color: 'var(--muted)',
            fontSize: 14,
          }}>
            Ask a question to get started.
          </div>
        ) : (
          messages.map(m => <Message key={m.id} msg={m} />)
        )}
      </div>

      <style>{`@keyframes pulse { 0%,100%{opacity:0.3} 50%{opacity:1} }`}</style>
    </div>
  )
}