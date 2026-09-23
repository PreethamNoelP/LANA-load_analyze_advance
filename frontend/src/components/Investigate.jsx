import { useState } from 'react'
import { runInvestigation } from '../api.js'

function Verdict({ h }) {
  if (!h.testable) {
    return <span style={badgeStyle('var(--muted)')}>Not testable</span>
  }
  if (h.significant) {
    return <span style={badgeStyle('var(--green)')}>Supported</span>
  }
  return <span style={badgeStyle('var(--amber)')}>Not supported</span>
}

function GroupBars({ groups }) {
  if (!groups || !groups.length) return null
  const max = Math.max(...groups.map(g => Math.abs(g.mean)))
  return (
    <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 5 }}>
      {groups.slice(0, 6).map(g => (
        <div key={g.category} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
          <div style={{ width: 110, color: 'var(--muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {g.category}
          </div>
          <div style={{ flex: 1, background: 'rgba(255,255,255,0.05)', borderRadius: 4, height: 8, overflow: 'hidden' }}>
            <div style={{
              width: `${max > 0 ? (Math.abs(g.mean) / max) * 100 : 0}%`,
              background: 'var(--accent2)', height: '100%', borderRadius: 4,
            }} />
          </div>
          <div style={{ width: 70, fontFamily: 'var(--ff-mono)', color: 'var(--text)', textAlign: 'right' }}>
            {g.mean.toLocaleString(undefined, { maximumFractionDigits: 2 })}
          </div>
          <div style={{ width: 40, fontFamily: 'var(--ff-mono)', color: 'var(--muted)', textAlign: 'right' }}>
            n={g.n}
          </div>
        </div>
      ))}
    </div>
  )
}

function HypothesisCard({ h }) {
  return (
    <div style={{
      background: 'var(--surface)',
      border: '1px solid var(--border)',
      borderRadius: 10,
      padding: '16px 18px',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
        <div style={{ fontWeight: 600, fontSize: 14 }}>{h.driver_column}</div>
        <Verdict h={h} />
      </div>
      {h.rationale && (
        <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 4, fontStyle: 'italic' }}>
          {h.rationale}
        </div>
      )}
      {!h.testable ? (
        <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 8 }}>{h.reason}</div>
      ) : (
        <>
          <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 8, fontFamily: 'var(--ff-mono)' }}>
            {h.test === 'kruskal' ? 'Kruskal-Wallis' : 'Pearson correlation'}
            {' · effect: '}<span style={{ color: 'var(--text)' }}>{h.effect_label}</span>
            {' (r²≈'}{h.effect_size?.toFixed(3)}{') · p='}
            {h.p_value < 0.0001 ? '<0.0001' : h.p_value?.toFixed(4)}
            {' · q='}{h.q_value < 0.0001 ? '<0.0001' : h.q_value?.toFixed(4)}
            {' · n='}{h.n?.toLocaleString()}
          </div>
          {h.direction && (
            <div style={{ fontSize: 13, color: 'var(--text)', marginTop: 6 }}>{h.direction}</div>
          )}
          {h.groups_truncated && (
            <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 4 }}>
              Tested the largest groups only — this column has more than fit in one test.
            </div>
          )}
          <GroupBars groups={h.group_summary} />
        </>
      )}
    </div>
  )
}

export default function Investigate({ session }) {
  const numCols = session.numeric_columns || []
  const [target, setTarget] = useState(numCols[0] || '')
  const [report, setReport] = useState(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState(null)

  async function run() {
    setErr(null)
    setLoading(true)
    setReport(null)
    try {
      const data = await runInvestigation(session.session_id, target)
      setReport(data)
    } catch (e) {
      setErr(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div>
        <div style={sectionHeader}>Investigate</div>
        <div style={{ fontSize: 13, color: 'var(--muted)', marginBottom: 16, lineHeight: 1.6 }}>
          The local model proposes candidate drivers of a metric; a real statistical
          test (Kruskal-Wallis or Pearson correlation) checks each one against your
          actual rows, corrected for testing several hypotheses at once. Only what
          survives correction is reported as supported.
        </div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <div style={fieldWrap}>
            <label style={labelStyle}>Metric to explain</label>
            <select value={target} onChange={e => setTarget(e.target.value)} style={selectStyle}>
              {numCols.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
          <button onClick={run} disabled={loading || !target} style={btnStyle(loading)}>
            {loading ? 'Investigating…' : `Investigate what drives ${target || '…'}`}
          </button>
        </div>
      </div>

      {err && <ErrorBox msg={err} />}

      {report && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div style={{
            background: 'rgba(88,166,255,0.08)',
            border: '1px solid var(--border)',
            borderRadius: 10,
            padding: '14px 18px',
            fontSize: 14,
            lineHeight: 1.6,
          }}>
            {report.narrative}
          </div>
          {(report.proposal_note || report.candidate_note) && (
            <div style={{ fontSize: 11, color: 'var(--muted)' }}>
              {report.proposal_note} {report.candidate_note}
            </div>
          )}
          <div style={{ fontSize: 11, color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.06em', fontWeight: 600 }}>
            {report.significant_count} of {report.tests_run} hypotheses survive false-discovery correction (α={report.fdr_alpha})
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {report.hypotheses.map(h => <HypothesisCard key={h.driver_column} h={h} />)}
          </div>
        </div>
      )}
    </div>
  )
}

function ErrorBox({ msg }) {
  return (
    <div style={{
      padding: '10px 16px',
      background: 'rgba(224,82,82,0.1)',
      border: '1px solid rgba(224,82,82,0.3)',
      borderRadius: 8,
      color: 'var(--red)',
      fontSize: 13,
    }}>{msg}</div>
  )
}

function badgeStyle(color) {
  return {
    fontSize: 11,
    fontWeight: 700,
    letterSpacing: '0.04em',
    textTransform: 'uppercase',
    color,
    border: `1px solid ${color}`,
    borderRadius: 6,
    padding: '2px 8px',
    whiteSpace: 'nowrap',
  }
}

const sectionHeader = {
  fontSize: 15,
  fontWeight: 600,
  marginBottom: 10,
  paddingBottom: 10,
  borderBottom: '1px solid var(--border)',
}
const fieldWrap = { display: 'flex', flexDirection: 'column', gap: 6 }
const labelStyle = { fontSize: 11, fontWeight: 600, color: 'var(--muted)', letterSpacing: '0.06em', textTransform: 'uppercase' }
const selectStyle = { fontSize: 13 }
const btnStyle = (loading) => ({
  padding: '9px 20px',
  background: loading ? 'var(--border)' : 'var(--accent)',
  color: '#fff',
  border: 'none',
  borderRadius: 8,
  fontWeight: 600,
  fontSize: 13,
  cursor: loading ? 'not-allowed' : 'pointer',
})
