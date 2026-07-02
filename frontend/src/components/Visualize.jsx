import { useState } from 'react'
import { getChartBlob } from '../api.js'

const CHART_TYPES = [
  'Histogram', 'Line Plot', 'Bar Chart', 'Scatter Plot',
  'Box Plot', 'Heatmap', 'Violin Plot', 'Pie Chart', 'Area Plot',
]

export default function Visualize({ session }) {
  const [col, setCol] = useState(session.numeric_columns?.[0] || '')
  const [xCol, setXCol] = useState(session.numeric_columns?.[0] || '')
  const [chartType, setChartType] = useState('Histogram')
  const [imgUrl, setImgUrl] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const needsX = chartType === 'Scatter Plot'

  async function generate() {
    setError(null)
    setLoading(true)
    try {
      const url = await getChartBlob(
        session.session_id,
        col,
        chartType,
        needsX ? xCol : undefined,
      )
      setImgUrl(url)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', gap: 12, marginBottom: 20, flexWrap: 'wrap', alignItems: 'flex-end' }}>
        <div style={fieldWrap}>
          <label style={labelStyle}>Chart type</label>
          <select value={chartType} onChange={e => setChartType(e.target.value)} style={selectStyle}>
            {CHART_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>

        <div style={fieldWrap}>
          <label style={labelStyle}>{needsX ? 'Y column' : 'Column'}</label>
          <select value={col} onChange={e => setCol(e.target.value)} style={selectStyle}>
            {session.numeric_columns.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>

        {needsX && (
          <div style={fieldWrap}>
            <label style={labelStyle}>X column</label>
            <select value={xCol} onChange={e => setXCol(e.target.value)} style={selectStyle}>
              {session.numeric_columns.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
        )}

        <button onClick={generate} disabled={loading} style={{
          padding: '9px 20px',
          background: loading ? 'var(--border)' : 'var(--accent)',
          color: '#fff',
          border: 'none',
          borderRadius: 8,
          fontWeight: 600,
          fontSize: 13,
          cursor: loading ? 'not-allowed' : 'pointer',
          transition: 'background 0.15s',
        }}>
          {loading ? 'Generating…' : 'Generate chart'}
        </button>
      </div>

      {error && (
        <div style={{
          padding: '10px 16px',
          background: 'rgba(224,82,82,0.1)',
          border: '1px solid rgba(224,82,82,0.3)',
          borderRadius: 8,
          color: 'var(--red)',
          fontSize: 13,
          marginBottom: 16,
        }}>
          {error}
        </div>
      )}

      {imgUrl ? (
        <div style={{
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          borderRadius: 12,
          overflow: 'hidden',
        }}>
          <img src={imgUrl} alt="chart" style={{ maxWidth: '100%', display: 'block', margin: '0 auto' }} />
        </div>
      ) : !loading && (
        <div style={{
          height: 280,
          background: 'var(--surface)',
          border: '1px dashed var(--border)',
          borderRadius: 12,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: 'var(--muted)',
          fontSize: 14,
        }}>
          Select a chart type and column, then click Generate
        </div>
      )}
    </div>
  )
}

const fieldWrap = { display: 'flex', flexDirection: 'column', gap: 6 }
const labelStyle = {
  fontSize: 11, fontWeight: 600, color: 'var(--muted)',
  letterSpacing: '0.06em', textTransform: 'uppercase',
}
const selectStyle = { fontSize: 13 }