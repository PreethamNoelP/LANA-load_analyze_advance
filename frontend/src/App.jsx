import { useState, useEffect } from 'react'
import Sidebar from './components/Sidebar.jsx'
import Upload from './components/Upload.jsx'
import DataPreview from './components/DataPreview.jsx'
import KpiTiles from './components/KpiTiles.jsx'
import AskAI from './components/AskAI.jsx'
import Visualize from './components/Visualize.jsx'
import Analyze from './components/Analyze.jsx'
import Export from './components/Export.jsx'
import { getModels } from './api.js'

const TABS = [
  { id: 'askai', label: 'Ask AI' },
  { id: 'visualize', label: 'Visualize' },
  { id: 'analyze', label: 'Analyze' },
  { id: 'export', label: 'Export' },
]

export default function App() {
  const [session, setSession] = useState(null)
  const [tab, setTab] = useState('askai')
  const [models, setModels] = useState([])
  const [previewOpen, setPreviewOpen] = useState(false)

  useEffect(() => {
    getModels().then(d => setModels(d.models || [])).catch(() => {})
  }, [])

  return (
    <div style={s.layout}>
      <Sidebar session={session} onUploadNew={() => { setSession(null); setPreviewOpen(false) }} />

      <div style={s.main}>
        {/* ── Global topbar ─────────────────────────────────────── */}
        <div style={s.topbar}>
          <span style={s.wordmark}>L<span style={s.wordmarkAccent}>A</span>NA</span>
          <div style={{ flex: 1 }} />
          {models.length > 0 && <span style={s.modelPill}>{models[0]}</span>}
          <div style={s.avatar}>P</div>
        </div>

        {!session ? (
          /* ── Upload screen ───────────────────────────────────── */
          <div style={s.uploadWrap}>
            <Upload onUpload={sess => { setSession(sess); setTab('askai') }} />
          </div>
        ) : (
          /* ── Session screen ──────────────────────────────────── */
          <div style={s.sessionShell}>

            {/* Sticky sub-header: file info + tab navigation */}
            <div style={s.subHeader}>
              <div style={s.fileRow}>
                <svg width="15" height="15" fill="none" viewBox="0 0 24 24"
                  stroke="var(--accent2)" strokeWidth="2" style={{ flexShrink: 0 }}>
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                  <polyline points="14 2 14 8 20 8"/>
                </svg>
                <span style={s.filename}>{session.filename}</span>
                <span style={s.fileMeta}>
                  {session.rows.toLocaleString()} rows · {session.columns.length} cols
                </span>
                <button style={s.previewBtn} onClick={() => setPreviewOpen(o => !o)}>
                  {previewOpen ? 'Hide preview' : 'Show preview'}
                </button>
              </div>

              {/* Tab bar — always visible */}
              <div style={s.tabBar}>
                {TABS.map(t => (
                  <button
                    key={t.id}
                    style={s.tabBtn(tab === t.id)}
                    onClick={() => setTab(t.id)}
                  >
                    {t.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Scrollable content area */}
            <div style={s.scrollArea}>
              {previewOpen && (
                <div style={s.previewSection}>
                  <DataPreview preview={session.preview} columns={session.columns} />
                </div>
              )}

              <KpiTiles session={session} />

              <div style={s.tabPanel}>
                {tab === 'askai'    && <AskAI     session={session} />}
                {tab === 'visualize' && <Visualize session={session} />}
                {tab === 'analyze'  && <Analyze   session={session} />}
                {tab === 'export'   && <Export    session={session} />}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

/* ── Styles ────────────────────────────────────────────────────────────────── */
const s = {
  layout: {
    display: 'flex',
    height: '100vh',
    overflow: 'hidden',
  },
  main: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
    marginLeft: 'var(--sidebar)',
  },

  /* topbar */
  topbar: {
    height: 'var(--topbar)',
    borderBottom: '1px solid var(--border)',
    display: 'flex',
    alignItems: 'center',
    padding: '0 24px',
    gap: 12,
    flexShrink: 0,
    background: 'var(--bg)',
  },
  wordmark: { fontWeight: 700, fontSize: 17, letterSpacing: '-0.02em' },
  wordmarkAccent: { color: 'var(--accent2)' },
  modelPill: {
    fontSize: 12,
    fontFamily: 'var(--ff-mono)',
    color: 'var(--muted)',
    background: 'var(--surface)',
    border: '1px solid var(--border)',
    borderRadius: 20,
    padding: '3px 10px',
  },
  avatar: {
    width: 30, height: 30, borderRadius: '50%',
    background: 'var(--accent)',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    fontSize: 13, fontWeight: 600, color: '#fff',
  },

  /* upload screen */
  uploadWrap: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
  },

  /* session shell — fills remaining height, no overflow */
  sessionShell: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
  },

  /* sticky sub-header */
  subHeader: {
    flexShrink: 0,
    background: 'var(--bg)',
    borderBottom: '1px solid var(--border)',
    padding: '14px 32px 0',
  },
  fileRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    marginBottom: 12,
  },
  filename: { fontWeight: 600, fontSize: 15 },
  fileMeta: { fontSize: 12, color: 'var(--muted)', fontFamily: 'var(--ff-mono)' },
  previewBtn: {
    marginLeft: 'auto',
    fontSize: 12,
    color: 'var(--accent2)',
    padding: '4px 10px',
    border: '1px solid var(--border)',
    borderRadius: 6,
    cursor: 'pointer',
    background: 'transparent',
  },

  /* tab bar sits at bottom of sub-header */
  tabBar: {
    display: 'flex',
    gap: 2,
  },
  tabBtn: (active) => ({
    padding: '8px 18px',
    fontSize: 13,
    fontWeight: 500,
    color: active ? 'var(--text)' : 'var(--muted)',
    border: 'none',
    borderBottom: active ? '2px solid var(--accent)' : '2px solid transparent',
    cursor: 'pointer',
    transition: 'color 0.15s',
    background: 'none',
  }),

  /* scrollable area below the sticky header */
  scrollArea: {
    flex: 1,
    overflowY: 'auto',
    padding: '24px 32px',
    display: 'flex',
    flexDirection: 'column',
    gap: 24,
  },
  previewSection: {
    // preview table lives here — scrolls with the content
  },
  tabPanel: {
    flex: 1,
  },
}