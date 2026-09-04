import { useState } from 'react'
import ConfirmDialog from './ConfirmDialog.jsx'

function StatusPill({ llmStatus }) {
  // llmStatus is null while the first /health check is still in flight —
  // shown as a neutral "checking" state rather than defaulting to either
  // color, since claiming "connected" before actually asking was exactly
  // the bug this replaces.
  const checking = llmStatus == null
  const up = llmStatus?.available === true
  const color = checking ? 'var(--muted)' : up ? 'var(--green)' : 'var(--red)'
  const bg = checking ? 'rgba(122,127,153,0.08)' : up ? 'rgba(78,199,127,0.08)' : 'rgba(224,82,82,0.08)'
  const border = checking ? 'rgba(122,127,153,0.2)' : up ? 'rgba(78,199,127,0.2)' : 'rgba(224,82,82,0.2)'
  const label = checking
    ? 'Checking LLM…'
    : up
      ? (llmStatus.name || 'LLM connected')
      : 'LLM unreachable'

  return (
    <div
      title={up ? undefined : 'Check that Ollama is running and the configured model has been pulled.'}
      style={{
        padding: '10px 12px', background: bg, border: `1px solid ${border}`,
        borderRadius: 8, fontSize: 12, color, display: 'flex', alignItems: 'center', gap: 8,
      }}
    >
      <span style={{
        width: 6, height: 6, borderRadius: '50%', flexShrink: 0,
        background: color, boxShadow: checking ? 'none' : `0 0 6px ${color}`,
      }} />
      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{label}</span>
    </div>
  )
}

export default function Sidebar({ session, llmStatus, onUploadNew }) {
  const [confirmOpen, setConfirmOpen] = useState(false)

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

      {/* LLM status */}
      <div style={{ padding: '16px 12px' }}>
        <StatusPill llmStatus={llmStatus} />
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
          onClick={() => session ? setConfirmOpen(true) : onUploadNew()}
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

      <ConfirmDialog
        open={confirmOpen}
        title="Start a new dataset?"
        message="This clears the current dataset, chat history, and any cleaning you've done. It isn't recoverable from here — you'd need to re-upload the file."
        confirmLabel="Start new"
        danger
        onCancel={() => setConfirmOpen(false)}
        onConfirm={() => { setConfirmOpen(false); onUploadNew() }}
      />
    </aside>
  )
}
