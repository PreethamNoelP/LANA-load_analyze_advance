/* Shared style tokens for the Clean tab's sections.
 *
 * Extracted verbatim when Clean.jsx was split. Kept as one object rather
 * than divided per component because the sections are deliberately visually
 * identical — a divided copy is how two cards start drifting apart.
 */

export const s = {
  root: { maxWidth: 820 },

  pageHeader: {
    display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between',
    marginBottom: 20,
  },
  h2: { fontSize: 18, fontWeight: 700, color: 'var(--text)', margin: 0 },
  subline: { fontSize: 13, color: 'var(--muted)', margin: '4px 0 0' },
  rescanBtn: {
    fontSize: 12, color: 'var(--accent2)',
    background: 'transparent', border: '1px solid var(--border)',
    borderRadius: 6, padding: '6px 14px', cursor: 'pointer',
    fontFamily: 'var(--ff-ui)', flexShrink: 0, marginTop: 2,
  },

  card: {
    background: 'var(--surface)',
    border: '1px solid var(--border)',
    borderRadius: 12,
    marginBottom: 16,
    overflow: 'hidden',
  },
  cardHeader: {
    display: 'flex', alignItems: 'center', gap: 10,
    padding: '12px 18px',
    borderBottom: '1px solid var(--border)',
    background: 'rgba(255,255,255,0.02)',
  },
  cardIcon: {
    fontSize: 15, width: 24, textAlign: 'center', color: 'var(--accent2)',
  },
  cardTitle: { fontSize: 13, fontWeight: 700, color: 'var(--text)' },
  badge: {
    marginLeft: 'auto', fontSize: 11, fontFamily: 'var(--ff-mono)',
    background: 'rgba(91,108,255,0.12)', color: 'var(--accent2)',
    border: '1px solid rgba(91,108,255,0.25)',
    borderRadius: 20, padding: '2px 10px',
  },
  cardBody: { padding: '16px 18px' },

  row: { display: 'flex', alignItems: 'center', gap: 10 },
  checkRow: { display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', color: 'var(--muted)', fontSize: 13 },

  hint: { fontSize: 12, color: 'var(--muted)', margin: '0 0 14px', lineHeight: 1.5 },

  colList: { display: 'flex', flexDirection: 'column', gap: 12 },
  colRow: {
    display: 'flex', alignItems: 'center', gap: 16,
    padding: '10px 14px',
    background: 'rgba(255,255,255,0.02)',
    border: '1px solid var(--border)',
    borderRadius: 8,
    flexWrap: 'wrap',
  },
  colName: { flex: 1, minWidth: 140 },
  colLabel: { display: 'block', fontSize: 13, fontWeight: 600, color: 'var(--text)', fontFamily: 'var(--ff-mono)' },
  colSub: { display: 'block', fontSize: 11, color: 'var(--muted)', marginTop: 2 },
  colSub2: { fontSize: 11, color: 'var(--muted)', marginLeft: 8 },

  colBlock: {
    padding: '12px 14px',
    background: 'rgba(255,255,255,0.02)',
    border: '1px solid var(--border)',
    borderRadius: 8,
  },

  methodGroup: { display: 'flex', gap: 6, flexWrap: 'wrap' },

  reasonBox: {
    marginTop: 10, paddingTop: 10,
    borderTop: '1px solid var(--border)',
    fontSize: 12, color: 'var(--muted)', lineHeight: 1.55,
  },
  dangerBox: {
    marginTop: 10, padding: '9px 12px',
    background: 'rgba(224,82,82,0.08)',
    border: '1px solid rgba(224,82,82,0.25)',
    borderRadius: 7, fontSize: 12, color: 'var(--red)', lineHeight: 1.5,
  },
  sampleRow: {
    display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center',
    marginTop: 10, fontSize: 11,
  },
  codeChip: {
    fontFamily: 'var(--ff-mono)', fontSize: 11,
    background: 'rgba(255,255,255,0.05)', padding: '1px 5px', borderRadius: 4,
  },

  qualityCard: {
    display: 'flex', alignItems: 'flex-start', gap: 18,
    padding: '16px 20px',
    background: 'var(--surface)',
    border: '1px solid var(--border)',
    borderRadius: 12,
    marginBottom: 16, flexWrap: 'wrap',
  },
  qualityScore: {
    width: 58, height: 58, borderRadius: 12,
    border: '2px solid', display: 'flex', alignItems: 'center',
    justifyContent: 'center', fontSize: 20, fontWeight: 700,
    fontFamily: 'var(--ff-mono)', flexShrink: 0,
  },
  qualityList: {
    margin: '8px 0 0', paddingLeft: 16,
    fontSize: 12, color: 'var(--muted)', lineHeight: 1.6,
  },

  lineageSummary: {
    display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
    fontSize: 12.5, color: 'var(--muted)', fontFamily: 'var(--ff-mono)',
    paddingBottom: 12, borderBottom: '1px solid var(--border)',
  },
  disclosure: {
    background: 'transparent', border: 'none', padding: '10px 0 4px',
    color: 'var(--accent2)', fontSize: 12, cursor: 'pointer',
    fontFamily: 'var(--ff-ui)',
  },
  stepRow: {
    padding: '12px 14px', marginTop: 8,
    background: 'rgba(255,255,255,0.02)',
    border: '1px solid var(--border)', borderRadius: 8,
  },
  stepHead: {
    display: 'flex', alignItems: 'center', gap: 9,
    marginBottom: 7, flexWrap: 'wrap',
  },
  stepIndex: {
    width: 19, height: 19, borderRadius: 5, flexShrink: 0,
    background: 'rgba(91,108,255,0.15)', color: 'var(--accent2)',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    fontSize: 10.5, fontWeight: 700,
  },
  stepMeta: {
    display: 'flex', gap: 14, flexWrap: 'wrap', marginTop: 7,
    fontSize: 11, color: 'var(--muted)', fontFamily: 'var(--ff-mono)',
  },
  pill: {
    fontSize: 11, padding: '4px 11px', borderRadius: 20,
    cursor: 'pointer', fontFamily: 'var(--ff-ui)',
    transition: 'background 0.15s, color 0.15s, border-color 0.15s',
  },

  groupList: { display: 'flex', flexDirection: 'column', gap: 8 },
  groupRow: {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12,
    padding: '8px 12px',
    background: 'rgba(255,255,255,0.02)',
    border: '1px solid var(--border)',
    borderRadius: 8,
    flexWrap: 'wrap',
  },
  variantList: { display: 'flex', gap: 6, flexWrap: 'wrap', flex: 1 },
  variantChip: {
    fontSize: 12, padding: '3px 10px', borderRadius: 6,
    fontFamily: 'var(--ff-mono)', transition: 'background 0.15s, border-color 0.15s',
  },
  select: {
    fontSize: 12, background: 'var(--surface)',
    border: '1px solid var(--border)', borderRadius: 6,
    color: 'var(--text)', padding: '4px 8px',
    fontFamily: 'var(--ff-ui)', cursor: 'pointer',
  },

  miniTable: { width: '100%', borderCollapse: 'collapse', fontSize: 11, fontFamily: 'var(--ff-mono)' },
  th: { padding: '6px 12px', textAlign: 'left', fontWeight: 600, color: '#6b7190', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.04em', whiteSpace: 'nowrap' },
  td: { padding: '6px 12px', color: 'var(--text)', whiteSpace: 'nowrap', maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis' },

  applyBar: {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    padding: '16px 20px',
    background: 'var(--surface)',
    border: '1px solid var(--border)',
    borderRadius: 10,
    marginTop: 4,
  },
  applyBtn: {
    fontSize: 13, fontWeight: 600, color: '#fff',
    padding: '9px 22px', borderRadius: 8, border: 'none',
    fontFamily: 'var(--ff-ui)', transition: 'background 0.15s',
  },

  resultBanner: {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    padding: '14px 20px',
    background: 'rgba(78,199,127,0.08)',
    border: '1px solid rgba(78,199,127,0.25)',
    borderRadius: 10,
    marginBottom: 20,
    flexWrap: 'wrap',
    gap: 12,
  },
  resultLeft: { display: 'flex', alignItems: 'center', gap: 14 },
  resultCheck: {
    width: 32, height: 32, borderRadius: 8,
    background: 'rgba(78,199,127,0.2)', color: 'var(--green)',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    fontSize: 16, fontWeight: 700, flexShrink: 0,
  },
  versionToggle: { display: 'flex', alignItems: 'center', gap: 6 },
  vBtn: {
    fontSize: 12, fontWeight: 600,
    padding: '5px 14px', borderRadius: 6,
    cursor: 'pointer', fontFamily: 'var(--ff-ui)',
    transition: 'background 0.15s, color 0.15s',
  },
  warningsBox: {
    padding: '12px 16px',
    background: 'rgba(240,180,60,0.08)',
    border: '1px solid rgba(240,180,60,0.25)',
    borderRadius: 10,
    marginTop: -8, marginBottom: 20,
    fontSize: 12.5, color: 'var(--amber)',
    lineHeight: 1.5,
  },

  errorBox: {
    background: 'rgba(224,82,82,0.08)', border: '1px solid rgba(224,82,82,0.25)',
    borderRadius: 8, padding: '12px 16px', fontSize: 13,
    color: 'var(--muted)', marginBottom: 16,
  },
  loadingBox: {
    background: 'var(--surface)', border: '1px solid var(--border)',
    borderRadius: 8, padding: '16px 20px', fontSize: 13,
    color: 'var(--muted)', display: 'flex', alignItems: 'center',
    marginBottom: 16,
  },
  cleanCard: {
    background: 'rgba(78,199,127,0.06)', border: '1px solid rgba(78,199,127,0.2)',
    borderRadius: 12, padding: '32px 24px', textAlign: 'center',
    color: 'var(--green)',
  },
}
