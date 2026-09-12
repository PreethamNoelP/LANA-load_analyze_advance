import { useState, useEffect } from 'react'
import { getRecommendations } from '../api.js'

// Surfaces GET /recommendations, which was fully built server-side
// (generate_recommendations() picks charts/analyses that actually suit this
// dataset's columns — a scatter plot only for a pair that really correlates,
// a time series only when a real datetime column exists) but had no
// consumer anywhere in the UI.
// Mounted with key={sessionId} by the caller, so switching datasets remounts
// this component and its state starts empty — no reset-on-change effect, and
// no window where the previous dataset's suggestions are shown against the
// new one. `cancelled` still guards the in-flight response, since a fast
// dataset switch can resolve an old request after the new mount.
export default function Recommendations({ sessionId, onGoTo }) {
  const [recs, setRecs] = useState(null)

  useEffect(() => {
    if (!sessionId) return
    let cancelled = false
    getRecommendations(sessionId)
      .then(d => { if (!cancelled) setRecs(d.recommendations) })
      .catch(() => { if (!cancelled) setRecs(null) })
    return () => { cancelled = true }
  }, [sessionId])

  if (!recs || (!recs.viz?.length && !recs.analysis?.length)) return null

  return (
    <div style={s.card}>
      <div style={s.title}>◈ Suggested for this dataset</div>
      <div style={s.groups}>
        {recs.viz?.length > 0 && (
          <div style={s.group}>
            <div style={s.groupLabel}>Visualize</div>
            <ul style={s.list}>
              {recs.viz.map((r, i) => <li key={i}>{r}</li>)}
            </ul>
            <button style={s.goBtn} onClick={() => onGoTo('visualize')}>Go to Visualize →</button>
          </div>
        )}
        {recs.analysis?.length > 0 && (
          <div style={s.group}>
            <div style={s.groupLabel}>Analyze</div>
            <ul style={s.list}>
              {recs.analysis.map((r, i) => <li key={i}>{r}</li>)}
            </ul>
            <button style={s.goBtn} onClick={() => onGoTo('analyze')}>Go to Analyze →</button>
          </div>
        )}
      </div>
    </div>
  )
}

const s = {
  card: {
    background: 'var(--surface)', border: '1px solid var(--border)',
    borderRadius: 12, padding: '16px 18px', marginBottom: 16,
  },
  title: { fontSize: 13, fontWeight: 700, color: 'var(--text)', marginBottom: 12 },
  groups: { display: 'flex', gap: 24, flexWrap: 'wrap' },
  group: { flex: '1 1 220px', minWidth: 200 },
  groupLabel: {
    fontSize: 11, fontWeight: 600, color: 'var(--accent2)',
    textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 6,
  },
  list: {
    margin: '0 0 10px', paddingLeft: 16,
    fontSize: 12.5, color: 'var(--muted)', lineHeight: 1.6,
  },
  goBtn: {
    fontSize: 12, fontWeight: 600, color: 'var(--accent2)',
    background: 'transparent', border: 'none', padding: 0, cursor: 'pointer',
  },
}
