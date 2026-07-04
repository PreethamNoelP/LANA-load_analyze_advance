import { useState, useEffect, useRef } from 'react'
import Landing from './components/Landing.jsx'
import Sidebar from './components/Sidebar.jsx'
import Upload from './components/Upload.jsx'
import DataPreview from './components/DataPreview.jsx'
import KpiTiles from './components/KpiTiles.jsx'
import AskAI from './components/AskAI.jsx'
import Visualize from './components/Visualize.jsx'
import Analyze from './components/Analyze.jsx'
import Export from './components/Export.jsx'
import { getModels, queryAI } from './api.js'

const TABS = [
  { id: 'askai',     label: 'Ask AI' },
  { id: 'visualize', label: 'Visualize' },
  { id: 'analyze',   label: 'Analyze' },
  { id: 'export',    label: 'Export' },
]

const SUGGESTIONS = [
  'What are the key trends in this dataset?',
  'Which columns have the most missing values?',
  'Summarize the distribution of numeric columns.',
  'What insights can you draw from the top rows?',
]

export default function App() {
  const [view, setView]               = useState('landing')
  const [session, setSession]         = useState(null)
  const [tab, setTab]                 = useState('askai')
  const [models, setModels]           = useState([])
  const [previewOpen, setPreviewOpen] = useState(false)

  // AskAI state — lifted here so messages survive tab switches
  // and so we can control the fixed-input / scrollable-messages split
  const [aiMessages, setAiMessages]   = useState([])
  const [aiInput, setAiInput]         = useState('')
  const [aiLoading, setAiLoading]     = useState(false)
  const aiScrollRef                   = useRef(null)

  useEffect(() => {
    getModels().then(d => setModels(d.models || [])).catch(() => {})
  }, [])

  // Reset conversation when a new dataset is loaded
  useEffect(() => {
    setAiMessages([])
    setAiInput('')
  }, [session?.session_id])

  // Auto-scroll the AI thread to the latest message
  useEffect(() => {
    if (aiScrollRef.current) {
      aiScrollRef.current.scrollTop = aiScrollRef.current.scrollHeight
    }
  }, [aiMessages])

  async function sendAI(q) {
    const question = (q ?? aiInput).trim()
    if (!question || !session || aiLoading) return
    setAiInput('')
    setAiLoading(true)
    const id = Date.now()
    setAiMessages(prev => [...prev, { id, q: question, loading: true }])
    try {
      const { answer } = await queryAI(session.session_id, question)
      setAiMessages(prev => prev.map(m => m.id === id ? { ...m, loading: false, a: answer } : m))
    } catch (e) {
      setAiMessages(prev => prev.map(m => m.id === id ? { ...m, loading: false, error: e.message } : m))
    } finally {
      setAiLoading(false)
    }
  }

  if (view === 'landing') return <Landing onTry={() => setView('app')} />

  return (
    <div style={s.layout}>
      <Sidebar
        session={session}
        onUploadNew={() => { setSession(null); setPreviewOpen(false); setAiMessages([]) }}
        onGoHome={() => setView('landing')}
      />

      <div style={s.main}>
        {/* ── Global topbar ──────────────────────────────────────────────── */}
        <div style={s.topbar}>
          <button style={s.wordmarkBtn} onClick={() => setView('landing')}>
            L<span style={s.wordmarkAccent}>A</span>NA
          </button>
          <div style={{ flex: 1 }} />
          {models.length > 0 && <span style={s.modelPill}>{models[0]}</span>}
          <div style={s.avatar}>
            <svg width="15" height="15" fill="none" viewBox="0 0 24 24" stroke="#fff" strokeWidth="2">
              <circle cx="12" cy="8" r="4"/>
              <path d="M4 20c0-4 3.6-7 8-7s8 3 8 7"/>
            </svg>
          </div>
        </div>

        {!session ? (
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
            <Upload onUpload={sess => { setSession(sess); setTab('askai') }} />
          </div>
        ) : (
          <div style={s.sessionShell}>

            {/* ── Sticky sub-header: file info + tab nav ─────────────────── */}
            <div style={s.subHeader}>
              <div style={s.fileRow}>
                <svg width="14" height="14" fill="none" viewBox="0 0 24 24"
                  stroke="var(--accent2)" strokeWidth="2" style={{ flexShrink: 0 }}>
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                  <polyline points="14 2 14 8 20 8"/>
                </svg>
                <span style={s.filename}>{session.filename}</span>
                <span style={s.fileMeta}>
                  {session.rows.toLocaleString()} rows · {session.columns.length} cols
                </span>
                <button style={s.previewBtn} onClick={() => setPreviewOpen(o => !o)}>
                  {previewOpen ? '↑ Hide preview' : '↓ Show preview'}
                </button>
              </div>
              <div style={s.tabBar}>
                {TABS.map(t => (
                  <button key={t.id} style={s.tabBtn(tab === t.id)} onClick={() => setTab(t.id)}>
                    {t.label}
                  </button>
                ))}
              </div>
            </div>

            {/* ── Ask AI layout — ChatGPT style ───────────────────────────── */}
            {/* Always in DOM (display:none when inactive) so messages are never lost */}
            <div style={{ display: tab === 'askai' ? 'flex' : 'none', flex: 1, flexDirection: 'column', overflow: 'hidden' }}>

              {/* Scrollable area: KPI tiles → data preview → message thread */}
              <div ref={aiScrollRef} style={s.aiScroll}>
                <KpiTiles session={session} />
                {previewOpen && <div style={{ marginTop: 16 }}><DataPreview preview={session.preview} columns={session.columns} /></div>}
                <div style={{ marginTop: 20 }}>
                  <AskAI messages={aiMessages} />
                </div>
              </div>

              {/* Fixed input bar — never scrolls */}
              <div style={s.inputDock}>
                <div style={s.inputBox}>
                  <input
                    value={aiInput}
                    onChange={e => setAiInput(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && !e.shiftKey && sendAI()}
                    placeholder="Ask anything about your data…"
                    style={s.inputField}
                    disabled={aiLoading}
                  />
                  <button
                    onClick={() => sendAI()}
                    disabled={!aiInput.trim() || aiLoading}
                    style={{
                      ...s.sendBtn,
                      background: aiInput.trim() && !aiLoading ? 'var(--accent)' : 'var(--border)',
                      cursor: aiInput.trim() && !aiLoading ? 'pointer' : 'default',
                    }}
                  >→</button>
                </div>

                {/* Suggestion chips */}
                <div style={s.chips}>
                  <span style={{ fontSize: 11, color: 'var(--muted)', flexShrink: 0 }}>Try</span>
                  {SUGGESTIONS.map(sg => (
                    <button
                      key={sg}
                      onClick={() => sendAI(sg)}
                      disabled={aiLoading}
                      style={s.chip}
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
                    >{sg}</button>
                  ))}
                </div>

                <div style={{ textAlign: 'right', fontSize: 11, color: '#4a4f66', paddingRight: 2, marginTop: 4 }}>
                  ↵ Enter to send
                </div>
              </div>
            </div>

            {/* ── Other tabs — scrollable, all mounted via display:none ───── */}
            <div style={{ display: tab !== 'askai' ? 'flex' : 'none', flex: 1, minHeight: 0, overflowY: 'auto', flexDirection: 'column', padding: '24px 32px' }}>
              <KpiTiles session={session} />
              {previewOpen && <div style={{ marginTop: 16 }}><DataPreview preview={session.preview} columns={session.columns} /></div>}
              <div style={{ marginTop: 24, display: tab === 'visualize' ? 'block' : 'none' }}>
                <Visualize session={session} />
              </div>
              <div style={{ marginTop: 24, display: tab === 'analyze' ? 'block' : 'none' }}>
                <Analyze session={session} />
              </div>
              <div style={{ marginTop: 24, display: tab === 'export' ? 'block' : 'none' }}>
                <Export session={session} />
              </div>
            </div>

          </div>
        )}
      </div>
    </div>
  )
}

/* ── Styles ─────────────────────────────────────────────────────────────────── */
const s = {
  layout:  { display: 'flex', height: '100vh', overflow: 'hidden' },
  main:    { flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', marginLeft: 'var(--sidebar)' },

  topbar: {
    height: 'var(--topbar)', borderBottom: '1px solid var(--border)',
    display: 'flex', alignItems: 'center', padding: '0 24px', gap: 12,
    flexShrink: 0, background: 'var(--bg)',
  },
  wordmarkBtn: { fontWeight: 800, fontSize: 17, letterSpacing: '-0.03em', background: 'none', border: 'none', color: 'var(--text)', cursor: 'pointer', padding: 0 },
  wordmarkAccent: { color: 'var(--accent2)' },
  modelPill: { fontSize: 12, fontFamily: 'var(--ff-mono)', color: 'var(--muted)', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 20, padding: '3px 10px' },
  avatar:   { width: 30, height: 30, borderRadius: '50%', background: 'var(--accent)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 13, fontWeight: 700, color: '#fff' },

  sessionShell: { flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' },

  subHeader: { flexShrink: 0, background: 'var(--bg)', borderBottom: '1px solid var(--border)', padding: '14px 32px 0' },
  fileRow:   { display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 },
  filename:  { fontWeight: 600, fontSize: 15 },
  fileMeta:  { fontSize: 12, color: 'var(--muted)', fontFamily: 'var(--ff-mono)' },
  previewBtn: {
    marginLeft: 'auto', fontSize: 12, color: 'var(--accent2)',
    padding: '4px 12px', border: '1px solid var(--border)',
    borderRadius: 6, cursor: 'pointer', background: 'transparent', fontFamily: 'var(--ff-ui)',
  },
  tabBar:  { display: 'flex', gap: 2 },
  tabBtn: (active) => ({
    padding: '9px 18px', fontSize: 13, fontWeight: 600,
    color: active ? 'var(--text)' : '#6b7190',
    border: 'none', borderBottom: active ? '2px solid var(--accent)' : '2px solid transparent',
    cursor: 'pointer', transition: 'color 0.15s', background: 'none', fontFamily: 'var(--ff-ui)',
  }),

  /* AskAI-specific layout */
  aiScroll: {
    flex: 1, overflowY: 'auto',
    padding: '24px 32px 16px',
    display: 'flex', flexDirection: 'column',
  },

  /* Fixed input dock at bottom */
  inputDock: {
    flexShrink: 0,
    padding: '12px 32px 20px',
    background: 'var(--bg)',
    borderTop: '1px solid var(--border)',
  },
  inputBox: {
    display: 'flex', alignItems: 'center', gap: 10,
    background: 'var(--surface)',
    border: '1px solid var(--border)',
    borderRadius: 12,
    padding: '12px 14px',
    marginBottom: 10,
  },
  inputField: {
    flex: 1, background: 'transparent', border: 'none',
    fontSize: 14, color: 'var(--text)', fontFamily: 'var(--ff-ui)',
  },
  sendBtn: {
    width: 34, height: 34, borderRadius: 9,
    color: '#fff', fontSize: 16, flexShrink: 0,
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    border: 'none', transition: 'background 0.15s',
    fontFamily: 'var(--ff-ui)',
  },
  chips: {
    display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center',
  },
  chip: {
    fontSize: 11, color: '#c0c4dc',
    background: 'rgba(91,108,255,0.07)',
    border: '1px solid rgba(91,108,255,0.2)',
    borderRadius: 20, padding: '3px 11px',
    cursor: 'pointer', transition: 'color 0.15s, border-color 0.15s, background 0.15s',
    fontFamily: 'var(--ff-ui)',
  },
}