// Small reusable overlay confirmation, used wherever an action is hard to
// undo (losing an in-progress session, deleting rows during cleaning). A
// native window.confirm() would work but clashes with the rest of the app's
// custom dark theme, so this matches the same design tokens instead.
export default function ConfirmDialog({
  open, title, message, confirmLabel = 'Confirm', cancelLabel = 'Cancel',
  danger = false, onConfirm, onCancel,
}) {
  if (!open) return null
  return (
    <div style={s.overlay} onClick={onCancel}>
      <div style={s.card} onClick={e => e.stopPropagation()}>
        <div style={s.title}>{title}</div>
        <div style={s.message}>{message}</div>
        <div style={s.actions}>
          <button style={s.cancelBtn} onClick={onCancel}>{cancelLabel}</button>
          <button
            style={{ ...s.confirmBtn, background: danger ? 'var(--red)' : 'var(--accent)' }}
            onClick={onConfirm}
          >{confirmLabel}</button>
        </div>
      </div>
    </div>
  )
}

const s = {
  overlay: {
    position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    zIndex: 1000,
  },
  card: {
    width: 380, maxWidth: 'calc(100vw - 48px)',
    background: 'var(--surface)', border: '1px solid var(--border)',
    borderRadius: 12, padding: '22px 24px',
    boxShadow: '0 20px 60px rgba(0,0,0,0.4)',
  },
  title: { fontSize: 15, fontWeight: 700, color: 'var(--text)', marginBottom: 10 },
  message: { fontSize: 13, color: 'var(--muted)', lineHeight: 1.6, marginBottom: 20 },
  actions: { display: 'flex', justifyContent: 'flex-end', gap: 10 },
  cancelBtn: {
    fontSize: 13, fontWeight: 600, color: 'var(--muted)',
    padding: '8px 18px', borderRadius: 8,
    border: '1px solid var(--border)', background: 'transparent', cursor: 'pointer',
  },
  confirmBtn: {
    fontSize: 13, fontWeight: 600, color: '#fff',
    padding: '8px 18px', borderRadius: 8, border: 'none', cursor: 'pointer',
  },
}
