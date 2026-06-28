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
    wordmark: {
      fontWeight: 700,
      fontSize: 17,
      letterSpacing: '-0.02em',
      color: 'var(--text)',
    },
    wordmarkAccent: { color: 'var(--accent2)' },
    topbarSep: { flex: 1 },
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
      width: 30,
      height: 30,
      borderRadius: '50%',
      background: 'var(--accent)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      fontSize: 13,
      fontWeight: 600,
      color: '#fff',
    },
    body: {
      flex: 1,
      overflowY: 'auto',
      padding: session ? '28px 32px' : 0,
      display: 'flex',
      flexDirection: 'column',
    },
    dataHeader: {
      display: 'flex',
      alignItems: 'center',
      gap: 12,
      marginBottom: 20,
    },
    dataFilename: {
      fontWeight: 600,
      fontSize: 16,
    },
    dataMeta: {
      fontSize: 12,
      color: 'var(--muted)',
      fontFamily: 'var(--ff-mono)',
    },
    previewToggle: {
      fontSize: 12,
      color: 'var(--accent2)',
      marginLeft: 'auto',
      padding: '4px 10px',
      border: '1px solid var(--border)',
      borderRadius: 6,
      cursor: 'pointer',
    },
    tabBar: {
      display: 'flex',
      gap: 2,
      borderBottom: '1px solid var(--border)',
      marginBottom: 24,
      marginTop: 8,
    },
    tabBtn: (active) => ({
      padding: '8px 16px',
      fontSize: 13,
      fontWeight: 500,
      color: active ? 'var(--text)' : 'var(--muted)',
      borderBottom: active ? '2px solid var(--accent)' : '2px solid transparent',
      cursor: 'pointer',
      transition: 'color 0.15s',
    }),
  }

  return (
    <div style={s.layout}>
      <Sidebar session={session} onUploadNew={() => setSession(null)} />

      <div style={s.main}>
        {/* Topbar */}
        <div style={s.topbar}>
          <span style={s.wordmark}>L<span style={s.wordmarkAccent}>A</span>NA</span>
          <div style={s.topbarSep} />
          {models.length > 0 && (
            <span style={s.modelPill}>{models[0]}</span>
          )}
          <div style={s.avatar}>P</div>
        </div>

        <div style={s.body}>
          {!session ? (
            <Upload onUpload={setSession} />
          ) : (
            <>
              {/* Dataset header */}
              <div style={s.dataHeader}>
                <svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="var(--accent2)" strokeWidth="2">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                  <polyline points="14 2 14 8 20 8"/>
                </svg>
                <span style={s.dataFilename}>{session.filename}</span>
                <span style={s.dataMeta}>{session.rows.toLocaleString()} rows · {session.columns.length} cols</span>
                <button style={s.previewToggle} onClick={() => setPreviewOpen(o => !o)}>
                  {previewOpen ? 'Hide preview' : 'Show preview'}
                </button>
              </div>

              {previewOpen && <DataPreview preview={session.preview} columns={session.columns} />}

              <KpiTiles session={session} />

              {/* Tabs */}
              <div style={s.tabBar}>
                {TABS.map(t => (
                  <button key={t.id} style={s.tabBtn(tab === t.id)} onClick={() => setTab(t.id)}>
                    {t.label}
                  </button>
                ))}
              </div>

              {tab === 'askai' && <AskAI session={session} />}
              {tab === 'visualize' && <Visualize session={session} />}
              {tab === 'analyze' && <Analyze session={session} />}
              {tab === 'export' && <Export session={session} />}
            </>
          )}
        </div>
      </div>
    </div>
  )
}