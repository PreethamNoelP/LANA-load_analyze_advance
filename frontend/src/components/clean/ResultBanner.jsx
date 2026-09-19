import { s } from './styles.js'

export default function ResultBanner({ result, version, onVersionSwitch }) {
  return (
    <div>
      <div style={s.resultBanner}>
        <div style={s.resultLeft}>
          <span style={s.resultCheck}>✓</span>
          <div>
            <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text)' }}>
              Cleaning applied
            </div>
            <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 3, fontFamily: 'var(--ff-mono)' }}>
              {result.rows_removed > 0
                ? `${result.rows_before.toLocaleString()} → ${result.rows_after.toLocaleString()} rows  ·  ${result.rows_removed} removed`
                : `${result.rows_after.toLocaleString()} rows  ·  no rows removed`}
            </div>
          </div>
        </div>
        <div style={s.versionToggle}>
          <span style={{ fontSize: 11, color: 'var(--muted)' }}>View:</span>
          <button
            style={{ ...s.vBtn, background: version === 'original' ? 'var(--surface)' : 'transparent', color: version === 'original' ? 'var(--text)' : 'var(--muted)', border: `1px solid ${version === 'original' ? 'var(--border)' : 'transparent'}` }}
            onClick={() => onVersionSwitch('original')}
          >Original</button>
          <button
            style={{ ...s.vBtn, background: version === 'cleaned' ? 'var(--accent)' : 'transparent', color: version === 'cleaned' ? '#fff' : 'var(--muted)', border: `1px solid ${version === 'cleaned' ? 'var(--accent)' : 'transparent'}` }}
            onClick={() => onVersionSwitch('cleaned')}
          >Cleaned</button>
        </div>
      </div>
      {/* Requested but not applied — a silent no-op is how a user ends up
          believing a column was cleaned when it was not. Per-step caveats
          live in the transformation log rather than being repeated here. */}
      {result.lineage?.summary?.skipped?.length > 0 && (
        <div style={s.warningsBox}>
          <div style={{ fontWeight: 600, marginBottom: 6 }}>Not applied:</div>
          {result.lineage.summary.skipped.map((skip, i) => (
            <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'flex-start', marginTop: i > 0 ? 6 : 0 }}>
              <span style={{ color: 'var(--amber)', flexShrink: 0 }}>⚠</span>
              <span>
                <code>{skip.operation}</code>
                {skip.column ? <> on <code>{skip.column}</code></> : null} — {skip.reason}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/* ── Main component ──────────────────────────────────────────────────────── */
