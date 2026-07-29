import { useState } from 'react'
import { getStats, runRegression, getCorrelations } from '../api.js'

function StatTable({ stats }) {
  if (!stats) return null
  const rows = Object.entries(stats).filter(([, v]) => v !== null)
  return (
    <div style={{
      background: 'var(--surface)',
      border: '1px solid var(--border)',
      borderRadius: 10,
      overflow: 'hidden',
    }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <tbody>
          {rows.map(([k, v]) => (
            <tr key={k} style={{ borderBottom: '1px solid var(--border)' }}>
              <td style={{
                padding: '9px 16px',
                color: 'var(--muted)',
                fontWeight: 500,
                width: '50%',
                textTransform: 'capitalize',
              }}>{k.replace(/_/g, ' ')}</td>
              <td style={{
                padding: '9px 16px',
                fontFamily: 'var(--ff-mono)',
                color: 'var(--text)',
                fontWeight: 500,
              }}>
                {typeof v === 'number' ? v.toLocaleString(undefined, { maximumFractionDigits: 4 }) : String(v)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function RegressionResult({ result }) {
  if (!result) return null
  const r2Color = result.r2_score > 0.7 ? 'var(--green)' : result.r2_score > 0.4 ? 'var(--amber)' : 'var(--red)'
  return (
    <div style={{
      background: 'var(--surface)',
      border: '1px solid var(--border)',
      borderRadius: 10,
      padding: '20px',
    }}>
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 16 }}>
        {[
          { label: 'R² Score', value: result.r2_score?.toFixed(4), color: r2Color },
          { label: 'Coefficient', value: result.coefficient?.toFixed(4), color: 'var(--accent2)' },
          { label: 'Intercept', value: result.intercept?.toFixed(4), color: 'var(--muted)' },
          { label: 'RMSE', value: result.rmse?.toFixed(4), color: 'var(--amber)' },
        ].map(m => (
          <div key={m.label} style={{
            flex: 1,
            minWidth: 120,
            background: 'rgba(255,255,255,0.03)',
            border: '1px solid var(--border)',
            borderRadius: 8,
            padding: '12px 14px',
          }}>
            <div style={{ fontSize: 10, color: 'var(--muted)', fontWeight: 600, letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 4 }}>
              {m.label}
            </div>
            <div style={{ fontFamily: 'var(--ff-mono)', fontSize: 18, fontWeight: 600, color: m.color }}>
              {m.value}
            </div>
          </div>
        ))}
      </div>
      {result.interpretation && (
        <div style={{
          fontSize: 13,
          color: 'var(--muted)',
          lineHeight: 1.6,
          borderTop: '1px solid var(--border)',
          paddingTop: 12,
        }}>
          {result.interpretation}
        </div>
      )}
    </div>
  )
}

function CorrelationTable({ pairs }) {
  if (!pairs) return null
  if (!pairs.length) {
    return (
      <div style={{ color: 'var(--muted)', fontSize: 13, padding: '4px 0' }}>
        Not enough numeric columns (or overlapping data) to compute correlations.
      </div>
    )
  }
  const strengthColor = (c) => {
    const a = Math.abs(c)
    return a > 0.7 ? 'var(--green)' : a > 0.4 ? 'var(--amber)' : 'var(--muted)'
  }
  return (
    <div style={{
      background: 'var(--surface)',
      border: '1px solid var(--border)',
      borderRadius: 10,
      overflow: 'hidden',
    }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead>
          <tr style={{ borderBottom: '1px solid var(--border)' }}>
            {['Columns', 'Correlation', 'p-value', 'n'].map(h => (
              <th key={h} style={{
                textAlign: 'left', padding: '9px 16px',
                color: 'var(--muted)', fontWeight: 600,
                fontSize: 11, letterSpacing: '0.06em', textTransform: 'uppercase',
              }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {pairs.map((p, i) => (
            <tr key={`${p.column_a}-${p.column_b}`} style={{ borderBottom: i < pairs.length - 1 ? '1px solid var(--border)' : 'none' }}>
              <td style={{ padding: '9px 16px', color: 'var(--text)', fontWeight: 500 }}>
                {p.column_a} <span style={{ color: 'var(--muted)' }}>×</span> {p.column_b}
              </td>
              <td style={{ padding: '9px 16px', fontFamily: 'var(--ff-mono)', fontWeight: 600, color: strengthColor(p.correlation) }}>
                {p.correlation.toFixed(4)}
              </td>
              <td style={{ padding: '9px 16px', fontFamily: 'var(--ff-mono)', color: 'var(--muted)' }}>
                {p.p_value < 0.0001 ? '<0.0001' : p.p_value.toFixed(4)}
              </td>
              <td style={{ padding: '9px 16px', fontFamily: 'var(--ff-mono)', color: 'var(--muted)' }}>
                {p.n.toLocaleString()}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function Analyze({ session }) {
  const numCols = session.numeric_columns || []
  const [statsCol, setStatsCol] = useState(numCols[0] || '')
  const [stats, setStats] = useState(null)
  const [statsLoading, setStatsLoading] = useState(false)
  const [statsErr, setStatsErr] = useState(null)

  const [xCol, setXCol] = useState(numCols[0] || '')
  const [yCol, setYCol] = useState(numCols[1] || numCols[0] || '')
  const [regression, setRegression] = useState(null)
  const [regLoading, setRegLoading] = useState(false)
  const [regErr, setRegErr] = useState(null)

  const [corrMethod, setCorrMethod] = useState('pearson')
  const [correlations, setCorrelations] = useState(null)
  const [corrLoading, setCorrLoading] = useState(false)
  const [corrErr, setCorrErr] = useState(null)

  async function fetchStats() {
    setStatsErr(null)
    setStatsLoading(true)
    try {
      const data = await getStats(session.session_id, statsCol)
      setStats(data)
    } catch (e) {
      setStatsErr(e.message)
    } finally {
      setStatsLoading(false)
    }
  }

  async function fetchRegression() {
    setRegErr(null)
    setRegLoading(true)
    try {
      const data = await runRegression(session.session_id, xCol, yCol)
      setRegression(data)
    } catch (e) {
      setRegErr(e.message)
    } finally {
      setRegLoading(false)
    }
  }

  async function fetchCorrelations() {
    setCorrErr(null)
    setCorrLoading(true)
    try {
      const data = await getCorrelations(session.session_id, corrMethod)
      setCorrelations(data.pairs)
    } catch (e) {
      setCorrErr(e.message)
    } finally {
      setCorrLoading(false)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 32 }}>
      {/* Statistics */}
      <section>
        <div style={sectionHeader}>Statistics</div>
        <div style={{ display: 'flex', gap: 10, marginBottom: 16, alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <div style={fieldWrap}>
            <label style={labelStyle}>Column</label>
            <select value={statsCol} onChange={e => setStatsCol(e.target.value)} style={selectStyle}>
              {numCols.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
          <button onClick={fetchStats} disabled={statsLoading} style={btnStyle(statsLoading)}>
            {statsLoading ? 'Computing…' : 'Compute stats'}
          </button>
        </div>
        {statsErr && <ErrorBox msg={statsErr} />}
        <StatTable stats={stats} />
      </section>

      {/* Correlations */}
      <section>
        <div style={sectionHeader}>Correlations</div>
        <div style={{ display: 'flex', gap: 10, marginBottom: 16, alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <div style={fieldWrap}>
            <label style={labelStyle}>Method</label>
            <select value={corrMethod} onChange={e => setCorrMethod(e.target.value)} style={selectStyle}>
              <option value="pearson">Pearson (linear)</option>
              <option value="spearman">Spearman (rank)</option>
            </select>
          </div>
          <button onClick={fetchCorrelations} disabled={corrLoading} style={btnStyle(corrLoading)}>
            {corrLoading ? 'Computing…' : 'Compute correlations'}
          </button>
        </div>
        {corrErr && <ErrorBox msg={corrErr} />}
        <CorrelationTable pairs={correlations} />
      </section>

      {/* Regression */}
      <section>
        <div style={sectionHeader}>Linear Regression</div>
        <div style={{ display: 'flex', gap: 10, marginBottom: 16, alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <div style={fieldWrap}>
            <label style={labelStyle}>X (independent)</label>
            <select value={xCol} onChange={e => setXCol(e.target.value)} style={selectStyle}>
              {numCols.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
          <div style={fieldWrap}>
            <label style={labelStyle}>Y (dependent)</label>
            <select value={yCol} onChange={e => setYCol(e.target.value)} style={selectStyle}>
              {numCols.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
          <button onClick={fetchRegression} disabled={regLoading} style={btnStyle(regLoading)}>
            {regLoading ? 'Running…' : 'Run regression'}
          </button>
        </div>
        {regErr && <ErrorBox msg={regErr} />}
        <RegressionResult result={regression} />
      </section>
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
      marginBottom: 12,
    }}>{msg}</div>
  )
}

const sectionHeader = {
  fontSize: 15,
  fontWeight: 600,
  marginBottom: 14,
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