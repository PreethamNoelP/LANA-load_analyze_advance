/* The small pieces every Clean section is built from. */

import { s } from './styles.js'

function SectionCard({ icon, title, badge, children }) {
  return (
    <div style={s.card}>
      <div style={s.cardHeader}>
        <span style={s.cardIcon}>{icon}</span>
        <span style={s.cardTitle}>{title}</span>
        {badge != null && <span style={s.badge}>{badge}</span>}
      </div>
      <div style={s.cardBody}>{children}</div>
    </div>
  )
}

function Pill({ label, active, onClick }) {
  return (
    <button
      onClick={onClick}
      style={{
        ...s.pill,
        background: active ? 'var(--accent)' : 'var(--surface)',
        color: active ? '#fff' : 'var(--muted)',
        border: `1px solid ${active ? 'var(--accent)' : 'var(--border)'}`,
      }}
    >{label}</button>
  )
}

/* ── Section: Duplicates ─────────────────────────────────────────────────── */

function Reasoning({ text, caveats }) {
  if (!text && !(caveats?.length > 0)) return null
  return (
    <div style={s.reasonBox}>
      {text && <div>{text}</div>}
      {caveats?.map((c, i) => (
        <div key={i} style={{ display: 'flex', gap: 7, marginTop: 6, color: 'var(--amber)' }}>
          <span style={{ flexShrink: 0 }}>⚠</span><span>{c}</span>
        </div>
      ))}
    </div>
  )
}

export { SectionCard, Pill, Reasoning }
