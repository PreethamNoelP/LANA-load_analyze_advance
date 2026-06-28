import { useState, useRef } from 'react'
import { uploadFile } from '../api.js'

export default function Upload({ onUpload }) {
  const [dragging, setDragging] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const inputRef = useRef()

  async function handleFile(file) {
    if (!file) return
    setError(null)
    setLoading(true)
    try {
      const data = await uploadFile(file)
      onUpload(data)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{
      flex: 1,
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      padding: 40,
    }}>
      {/* Hero */}
      <div style={{ textAlign: 'center', marginBottom: 48 }}>
        <div style={{
          fontSize: 13,
          fontWeight: 600,
          letterSpacing: '0.12em',
          textTransform: 'uppercase',
          color: 'var(--accent2)',
          marginBottom: 14,
        }}>
          LANA · Load, Analyze, Advance
        </div>
        <h1 style={{
          fontSize: 42,
          fontWeight: 700,
          letterSpacing: '-0.035em',
          lineHeight: 1.15,
          marginBottom: 12,
        }}>
          Drop a dataset.<br />Ask anything.
        </h1>
        <p style={{
          fontSize: 16,
          color: 'var(--muted)',
          maxWidth: 400,
          lineHeight: 1.6,
        }}>
          Upload a CSV, Excel, or JSON file and get instant AI-powered insights, charts, and analysis.
        </p>
      </div>

      {/* Drop zone */}
      <div
        style={{
          width: '100%',
          maxWidth: 520,
          border: `2px dashed ${dragging ? 'var(--accent)' : 'var(--border)'}`,
          borderRadius: 16,
          padding: '48px 32px',
          textAlign: 'center',
          transition: 'border-color 0.2s, background 0.2s',
          background: dragging ? 'rgba(91,108,255,0.06)' : 'var(--surface)',
          cursor: 'pointer',
        }}
        onClick={() => inputRef.current?.click()}
        onDragOver={e => { e.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={e => {
          e.preventDefault()
          setDragging(false)
          handleFile(e.dataTransfer.files[0])
        }}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".csv,.xlsx,.xls,.json"
          style={{ display: 'none' }}
          onChange={e => handleFile(e.target.files[0])}
        />

        {loading ? (
          <div style={{ color: 'var(--accent2)', fontSize: 15 }}>
            <div style={{ marginBottom: 8 }}>Parsing dataset…</div>
            <div style={{
              width: 180,
              height: 2,
              background: 'var(--border)',
              borderRadius: 2,
              margin: '0 auto',
              overflow: 'hidden',
            }}>
              <div style={{
                height: '100%',
                background: 'var(--accent)',
                width: '60%',
                borderRadius: 2,
                animation: 'slide 1.2s ease-in-out infinite',
              }} />
            </div>
          </div>
        ) : (
          <>
            <div style={{ fontSize: 36, marginBottom: 12 }}>⇑</div>
            <div style={{ fontWeight: 600, fontSize: 15, marginBottom: 6 }}>
              Drop a file here
            </div>
            <div style={{ fontSize: 13, color: 'var(--muted)' }}>
              or click to browse — CSV · Excel · JSON
            </div>
          </>
        )}
      </div>

      {error && (
        <div style={{
          marginTop: 16,
          padding: '10px 16px',
          background: 'rgba(224,82,82,0.1)',
          border: '1px solid rgba(224,82,82,0.3)',
          borderRadius: 8,
          color: 'var(--red)',
          fontSize: 13,
          maxWidth: 520,
          width: '100%',
        }}>
          {error}
        </div>
      )}

      {/* Format hints */}
      <div style={{
        display: 'flex',
        gap: 10,
        marginTop: 24,
        flexWrap: 'wrap',
        justifyContent: 'center',
      }}>
        {['CSV', 'Excel (.xlsx)', 'JSON'].map(f => (
          <span key={f} style={{
            fontSize: 12,
            color: 'var(--muted)',
            background: 'var(--surface)',
            border: '1px solid var(--border)',
            borderRadius: 20,
            padding: '4px 12px',
            fontFamily: 'var(--ff-mono)',
          }}>{f}</span>
        ))}
      </div>

      <style>{`@keyframes slide { 0%{transform:translateX(-100%)} 100%{transform:translateX(300%)} }`}</style>
    </div>
  )
}