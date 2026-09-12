import { useEffect, useId, useRef } from 'react'

// Small reusable overlay confirmation, used wherever an action is hard to
// undo (losing an in-progress session, deleting rows during cleaning). A
// native window.confirm() would work but clashes with the rest of the app's
// custom dark theme, so this matches the same design tokens instead — which
// means it also has to re-implement the behaviour a native dialog gives for
// free: announcing itself, taking focus, keeping focus, and closing on
// Escape. This is the control standing between a user and data loss, so it
// has to be operable without a mouse.
export default function ConfirmDialog({
  open, title, message, confirmLabel = 'Confirm', cancelLabel = 'Cancel',
  danger = false, onConfirm, onCancel,
}) {
  const cardRef = useRef(null)
  const cancelRef = useRef(null)
  const titleId = useId()
  const messageId = useId()

  // Focus starts on Cancel, not Confirm: if a keyboard user hits Enter out of
  // reflex on a dialog they didn't expect, the safe choice is the one that
  // fires.
  useEffect(() => {
    if (open) cancelRef.current?.focus()
  }, [open])

  useEffect(() => {
    if (!open) return
    function onKeyDown(e) {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onCancel?.()
        return
      }
      if (e.key !== 'Tab') return
      // Keep Tab inside the dialog. Without this, focus walks out to the
      // controls underneath the overlay, which are visually obscured but
      // still reachable — so a keyboard user can operate a page they cannot
      // see while a modal is nominally blocking it.
      const focusable = cardRef.current?.querySelectorAll(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      )
      if (!focusable?.length) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKeyDown, true)
    return () => document.removeEventListener('keydown', onKeyDown, true)
  }, [open, onCancel])

  if (!open) return null
  return (
    <div style={s.overlay} onClick={onCancel}>
      <div
        ref={cardRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={messageId}
        style={s.card}
        onClick={e => e.stopPropagation()}
      >
        <div id={titleId} style={s.title}>{title}</div>
        <div id={messageId} style={s.message}>{message}</div>
        <div style={s.actions}>
          <button ref={cancelRef} style={s.cancelBtn} onClick={onCancel}>{cancelLabel}</button>
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
