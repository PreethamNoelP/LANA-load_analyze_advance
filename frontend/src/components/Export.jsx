import { exportCsvUrl, exportPdfUrl, exportDocxUrl } from '../api.js'

export default function Export({ session }) {
  const sid = session.session_id

  return (
    <div>
      <div style={{
        fontSize: 15,
        fontWeight: 600,
        marginBottom: 20,
        paddingBottom: 12,
        borderBottom: '1px solid var(--border)',
      }}>
        Export Report
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 16 }}>
        <ExportCard
          icon="📊"
          title="CSV Data"
          description="Download the parsed dataset as a clean CSV file."
          label="Download CSV"
          href={exportCsvUrl(sid)}
          color="var(--green)"
        />
        <ExportCard
          icon="📄"
          title="PDF Report"
          description="Dataset summary including data profile and numeric statistics."
          label="Download PDF"
          href={exportPdfUrl(sid)}
          color="var(--red)"
        />
        <ExportCard
          icon="📝"
          title="Word Document"
          description="Editable Word report with the same summary content as the PDF."
          label="Download DOCX"
          href={exportDocxUrl(sid)}
          color="var(--accent)"
        />
      </div>
    </div>
  )
}

function ExportCard({ icon, title, description, label, href, color }) {
  return (
    <div style={{
      background: 'var(--surface)',
      border: '1px solid var(--border)',
      borderRadius: 12,
      padding: '20px',
      display: 'flex',
      flexDirection: 'column',
      gap: 10,
    }}>
      <div style={{ fontSize: 28 }}>{icon}</div>
      <div>
        <div style={{ fontWeight: 600, marginBottom: 4 }}>{title}</div>
        <div style={{ fontSize: 13, color: 'var(--muted)', lineHeight: 1.5 }}>{description}</div>
      </div>
      <a
        href={href}
        download
        style={{
          marginTop: 'auto',
          display: 'inline-block',
          padding: '9px 18px',
          background: color,
          color: '#fff',
          borderRadius: 8,
          fontWeight: 600,
          fontSize: 13,
          textAlign: 'center',
          opacity: 0.9,
          transition: 'opacity 0.15s',
        }}
        onMouseEnter={e => e.currentTarget.style.opacity = 1}
        onMouseLeave={e => e.currentTarget.style.opacity = 0.9}
      >
        {label}
      </a>
    </div>
  )
}