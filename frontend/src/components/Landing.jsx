import { useState, useEffect } from 'react'

const FEATURES = [
  {
    icon: (
      <svg width="20" height="20" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.8">
        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
        <polyline points="17 8 12 3 7 8"/>
        <line x1="12" y1="3" x2="12" y2="15"/>
      </svg>
    ),
    title: 'Any format, drag & drop',
    desc: 'CSV, Excel, or JSON — drop your file and LANA parses it instantly, extracting column types, nulls, and a live preview.',
  },
  {
    icon: (
      <svg width="20" height="20" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.8">
        <circle cx="12" cy="12" r="10"/>
        <path d="M12 16v-4M12 8h.01"/>
      </svg>
    ),
    title: 'Ask in plain English',
    desc: 'Powered by local LLMs via Ollama. Ask anything about your data and get a thoughtful, context-aware answer. No cloud. No API keys.',
  },
  {
    icon: (
      <svg width="20" height="20" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.8">
        <rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/>
        <rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/>
      </svg>
    ),
    title: 'Nine chart types',
    desc: 'Histograms, scatter plots, heatmaps, violin plots and more — rendered server-side and delivered as crisp, publication-ready images.',
  },
  {
    icon: (
      <svg width="20" height="20" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.8">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
        <polyline points="14 2 14 8 20 8"/>
        <line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>
        <polyline points="10 9 9 9 8 9"/>
      </svg>
    ),
    title: 'Export in one click',
    desc: 'Download your data as CSV or generate a full PDF or DOCX report complete with statistics and analysis context.',
  },
]

const STEPS = [
  { num: '01', title: 'Upload', desc: 'Drop any CSV, Excel, or JSON file. LANA reads it in under a second.' },
  { num: '02', title: 'Ask', desc: 'Type a question in plain English. No SQL, no code, no setup.' },
  { num: '03', title: 'Explore', desc: 'Visualize, run regressions, and export polished reports.' },
]

export default function Landing({ onTry }) {
  const [scrolled, setScrolled] = useState(false)
  const [hoverCta, setHoverCta] = useState(false)

  useEffect(() => {
    const fn = () => setScrolled(window.scrollY > 24)
    window.addEventListener('scroll', fn, { passive: true })
    return () => window.removeEventListener('scroll', fn)
  }, [])

  return (
    <div style={{ background: 'var(--bg)', color: 'var(--text)', fontFamily: 'var(--ff-ui)', overflowX: 'hidden' }}>

      {/* ── Nav ───────────────────────────────────────────────────────────── */}
      <nav style={{
        position: 'fixed', top: 0, left: 0, right: 0, zIndex: 200,
        height: 60,
        display: 'flex', alignItems: 'center',
        padding: '0 clamp(20px, 5vw, 56px)',
        gap: 32,
        background: scrolled ? 'rgba(11,12,16,0.88)' : 'transparent',
        backdropFilter: scrolled ? 'blur(14px)' : 'none',
        borderBottom: scrolled ? '1px solid var(--border)' : '1px solid transparent',
        transition: 'background 0.25s, border-color 0.25s',
      }}>
        <span style={{ fontWeight: 800, fontSize: 17, letterSpacing: '-0.03em', userSelect: 'none' }}>
          L<span style={{ color: 'var(--accent2)' }}>A</span>NA
        </span>
        <div style={{ flex: 1 }} />
        <NavLink href="#features">Features</NavLink>
        <NavLink href="#how-it-works">How it works</NavLink>
        <button onClick={onTry} style={styles.navCta}>Try LANA</button>
      </nav>

      {/* ── Hero ──────────────────────────────────────────────────────────── */}
      <section style={{
        minHeight: '100vh',
        display: 'flex', flexDirection: 'column',
        alignItems: 'center', justifyContent: 'center',
        textAlign: 'center',
        padding: 'clamp(100px, 14vh, 140px) clamp(20px, 5vw, 40px) clamp(60px, 8vh, 80px)',
        position: 'relative', overflow: 'hidden',
      }}>
        {/* Background glow */}
        <div style={{
          position: 'absolute', top: '38%', left: '50%',
          transform: 'translate(-50%, -50%)',
          width: 700, height: 700, borderRadius: '50%',
          background: 'radial-gradient(circle, rgba(91,108,255,0.10) 0%, transparent 68%)',
          pointerEvents: 'none',
        }} />

        {/* Status pill */}
        <div style={styles.statusPill}>
          <span style={styles.statusDot} />
          Runs fully local · No cloud required
        </div>

        {/* Headline */}
        <h1 style={{
          fontSize: 'clamp(40px, 6.5vw, 80px)',
          fontWeight: 800,
          letterSpacing: '-0.035em',
          lineHeight: 1.06,
          maxWidth: 820,
          marginBottom: 24,
          textWrap: 'balance',
        }}>
          Ask your data{' '}
          <span style={{
            background: 'linear-gradient(130deg, #5b6cff 0%, #9ba8ff 100%)',
            WebkitBackgroundClip: 'text',
            WebkitTextFillColor: 'transparent',
            backgroundClip: 'text',
          }}>anything.</span>
        </h1>

        {/* Subhead */}
        <p style={{
          fontSize: 'clamp(15px, 2vw, 19px)',
          color: '#9499b8',
          maxWidth: 540,
          lineHeight: 1.75,
          marginBottom: 44,
          textWrap: 'balance',
        }}>
          Upload any spreadsheet or CSV, ask questions in plain English, and get AI-powered insights, charts, and reports — in seconds.
        </p>

        {/* CTAs */}
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', justifyContent: 'center', marginBottom: 72 }}>
          <button
            onClick={onTry}
            style={{
              ...styles.ctaPrimary,
              background: hoverCta ? '#6b7dff' : 'var(--accent)',
              transform: hoverCta ? 'translateY(-1px)' : 'translateY(0)',
              boxShadow: hoverCta ? '0 8px 32px rgba(91,108,255,0.35)' : '0 4px 16px rgba(91,108,255,0.2)',
            }}
            onMouseEnter={() => setHoverCta(true)}
            onMouseLeave={() => setHoverCta(false)}
          >
            Try LANA for free →
          </button>
          <a href="#how-it-works" style={styles.ctaGhost}>See how it works</a>
        </div>

        {/* Mockup window */}
        <div style={{
          width: '100%', maxWidth: 740,
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          borderRadius: 18,
          overflow: 'hidden',
          boxShadow: '0 40px 100px rgba(0,0,0,0.55), 0 0 0 1px rgba(255,255,255,0.04)',
        }}>
          {/* Window chrome */}
          <div style={{
            height: 42, background: '#0e1017',
            borderBottom: '1px solid var(--border)',
            display: 'flex', alignItems: 'center',
            padding: '0 16px', gap: 6,
          }}>
            {['#e05252','#d8a44e','#4ec77f'].map(c => (
              <span key={c} style={{ width: 11, height: 11, borderRadius: '50%', background: c, opacity: 0.75 }} />
            ))}
            <span style={{ marginLeft: 14, fontSize: 11, color: '#5a5f78', fontFamily: 'var(--ff-mono)' }}>
              LANA · CPU-GPU-Final.xlsx · 200 rows · 7 cols
            </span>
          </div>
          {/* KPI row */}
          <div style={{ display: 'flex', borderBottom: '1px solid var(--border)', padding: '14px 20px', gap: 12 }}>
            {[
              { label: 'TOTAL ROWS', val: '200', color: 'var(--accent2)' },
              { label: 'COLUMNS', val: '7', color: 'var(--green)' },
              { label: 'NUMERIC COLS', val: '6', color: 'var(--amber)' },
            ].map(k => (
              <div key={k.label} style={{
                flex: 1, background: 'var(--bg)', border: '1px solid var(--border)',
                borderRadius: 10, padding: '12px 16px',
              }}>
                <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.08em', color: '#5a5f78', marginBottom: 4 }}>{k.label}</div>
                <div style={{ fontSize: 22, fontWeight: 700, color: k.color, fontFamily: 'var(--ff-mono)' }}>{k.val}</div>
              </div>
            ))}
          </div>
          {/* Chat */}
          <div style={{ padding: '20px 24px', textAlign: 'left' }}>
            <MockMsg
              q="What are the key trends in this dataset?"
              a="CPU frequency stays constant at 2611 MHz across all 200 processes — no frequency scaling is happening. Execution time grows linearly with Task Intensity (R²≈0.94). CPU Change averages +5.9 with variance increasing at higher intensities."
            />
            <div style={{
              display: 'flex', alignItems: 'center', gap: 10,
              background: 'rgba(255,255,255,0.03)',
              border: '1px solid var(--border)', borderRadius: 10, padding: '10px 14px',
            }}>
              <span style={{ color: '#5a5f78', fontSize: 13 }}>Ask anything about your data…</span>
              <div style={{ flex: 1 }} />
              <div style={{
                width: 28, height: 28, borderRadius: 7, background: 'var(--accent)',
                display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#fff', fontSize: 13,
              }}>→</div>
            </div>
          </div>
        </div>
      </section>

      {/* ── Features ──────────────────────────────────────────────────────── */}
      <section id="features" style={styles.section}>
        <EyebrowLabel>Features</EyebrowLabel>
        <h2 style={styles.sectionTitle}>Everything you need to understand your data</h2>

        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))',
          gap: 16, marginTop: 52, maxWidth: 960, width: '100%',
        }}>
          {FEATURES.map(f => <FeatureCard key={f.title} feature={f} />)}
        </div>
      </section>

      {/* ── How it works ──────────────────────────────────────────────────── */}
      <section id="how-it-works" style={{
        ...styles.section,
        borderTop: '1px solid var(--border)',
        borderBottom: '1px solid var(--border)',
        background: '#0d0f14',
      }}>
        <EyebrowLabel>How it works</EyebrowLabel>
        <h2 style={styles.sectionTitle}>Up and running in three steps</h2>

        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
          gap: 0, marginTop: 56, maxWidth: 780, width: '100%',
        }}>
          {STEPS.map((step, i) => (
            <div key={step.num} style={{
              textAlign: 'center', padding: '0 36px',
              borderRight: i < STEPS.length - 1 ? '1px solid var(--border)' : 'none',
            }}>
              <div style={{
                fontSize: 11, fontWeight: 700, fontFamily: 'var(--ff-mono)',
                color: 'var(--accent)', letterSpacing: '0.1em', marginBottom: 18,
              }}>{step.num}</div>
              <div style={{ fontSize: 18, fontWeight: 700, marginBottom: 10 }}>{step.title}</div>
              <div style={{ fontSize: 13, color: '#7a7f99', lineHeight: 1.65 }}>{step.desc}</div>
            </div>
          ))}
        </div>
      </section>

      {/* ── CTA Banner ────────────────────────────────────────────────────── */}
      <section style={{
        ...styles.section,
        background: 'linear-gradient(160deg, rgba(91,108,255,0.12) 0%, rgba(11,12,16,1) 60%)',
        borderBottom: '1px solid var(--border)',
      }}>
        <h2 style={{
          fontSize: 'clamp(28px, 4vw, 52px)',
          fontWeight: 800, letterSpacing: '-0.03em',
          textAlign: 'center', marginBottom: 16, textWrap: 'balance',
        }}>Ready to talk to your data?</h2>
        <p style={{ color: '#7a7f99', fontSize: 16, marginBottom: 40, textAlign: 'center' }}>
          Free, local, and open. No account needed.
        </p>
        <button onClick={onTry} style={{ ...styles.ctaPrimary, fontSize: 16, padding: '14px 36px' }}>
          Open LANA →
        </button>
      </section>

      {/* ── Footer ────────────────────────────────────────────────────────── */}
      <footer style={{
        padding: '28px clamp(20px, 5vw, 56px)',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        flexWrap: 'wrap', gap: 12,
      }}>
        <span style={{ fontWeight: 800, fontSize: 16, letterSpacing: '-0.03em' }}>
          L<span style={{ color: 'var(--accent2)' }}>A</span>NA
        </span>
        <span style={{ fontSize: 12, color: '#4a4f66' }}>Load · Analyze · Advance</span>
      </footer>
    </div>
  )
}

/* ── Sub-components ─────────────────────────────────────────────────────────── */

function NavLink({ href, children }) {
  const [hover, setHover] = useState(false)
  return (
    <a href={href} style={{ fontSize: 13, color: hover ? 'var(--text)' : '#7a7f99', transition: 'color 0.15s', textDecoration: 'none' }}
      onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}>
      {children}
    </a>
  )
}

function EyebrowLabel({ children }) {
  return (
    <div style={{
      fontSize: 11, fontWeight: 700, letterSpacing: '0.1em',
      textTransform: 'uppercase', color: 'var(--accent2)', marginBottom: 14,
    }}>{children}</div>
  )
}

function FeatureCard({ feature: f }) {
  const [hover, setHover] = useState(false)
  return (
    <div
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        background: 'var(--surface)',
        border: `1px solid ${hover ? 'rgba(91,108,255,0.4)' : 'var(--border)'}`,
        borderRadius: 14, padding: '28px 24px',
        transition: 'border-color 0.2s, transform 0.2s',
        transform: hover ? 'translateY(-2px)' : 'translateY(0)',
      }}
    >
      <div style={{
        width: 42, height: 42, borderRadius: 11,
        background: 'rgba(91,108,255,0.1)', border: '1px solid rgba(91,108,255,0.2)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        color: 'var(--accent2)', marginBottom: 18,
      }}>{f.icon}</div>
      <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 8 }}>{f.title}</div>
      <div style={{ fontSize: 13, color: '#7a7f99', lineHeight: 1.7 }}>{f.desc}</div>
    </div>
  )
}

function MockMsg({ q, a }) {
  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ display: 'flex', gap: 10, marginBottom: 10, alignItems: 'flex-start' }}>
        <div style={{
          width: 22, height: 22, borderRadius: 4, background: 'var(--accent)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 11, fontWeight: 700, color: '#fff', flexShrink: 0, marginTop: 1,
        }}>Q</div>
        <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--text)', lineHeight: 1.5 }}>{q}</div>
      </div>
      <div style={{
        borderLeft: '2px solid var(--accent)', paddingLeft: 16, marginLeft: 32,
        fontSize: 13, color: '#9499b8', lineHeight: 1.7,
      }}>
        {a}
        <div style={{ marginTop: 6, fontSize: 11, color: '#4a4f66', fontFamily: 'var(--ff-mono)' }}>— LANA AI</div>
      </div>
    </div>
  )
}

/* ── Styles ─────────────────────────────────────────────────────────────────── */
const styles = {
  section: {
    padding: 'clamp(64px, 9vw, 108px) clamp(20px, 5vw, 40px)',
    display: 'flex', flexDirection: 'column', alignItems: 'center',
  },
  sectionTitle: {
    fontSize: 'clamp(24px, 3.5vw, 40px)',
    fontWeight: 800, letterSpacing: '-0.025em',
    textAlign: 'center', lineHeight: 1.15,
    maxWidth: 520, textWrap: 'balance',
  },
  statusPill: {
    display: 'inline-flex', alignItems: 'center', gap: 8,
    fontSize: 11, fontWeight: 700, color: 'var(--accent2)',
    background: 'rgba(91,108,255,0.1)', border: '1px solid rgba(91,108,255,0.22)',
    borderRadius: 20, padding: '5px 14px',
    letterSpacing: '0.06em', textTransform: 'uppercase',
    marginBottom: 30,
  },
  statusDot: {
    width: 7, height: 7, borderRadius: '50%', background: 'var(--green)', display: 'inline-block',
  },
  ctaPrimary: {
    padding: '13px 30px',
    background: 'var(--accent)',
    color: '#fff',
    border: 'none', borderRadius: 10,
    fontWeight: 700, fontSize: 15, cursor: 'pointer',
    fontFamily: 'var(--ff-ui)',
    transition: 'background 0.15s, transform 0.15s, box-shadow 0.15s',
    display: 'inline-flex', alignItems: 'center', letterSpacing: '-0.01em',
  },
  ctaGhost: {
    padding: '13px 30px',
    background: 'transparent',
    color: '#9499b8',
    border: '1px solid var(--border)', borderRadius: 10,
    fontWeight: 600, fontSize: 15, cursor: 'pointer',
    fontFamily: 'var(--ff-ui)',
    display: 'inline-flex', alignItems: 'center',
    textDecoration: 'none', letterSpacing: '-0.01em',
  },
  navCta: {
    padding: '7px 18px',
    background: 'var(--accent)', color: '#fff',
    border: 'none', borderRadius: 8,
    fontWeight: 700, fontSize: 13, cursor: 'pointer',
    fontFamily: 'var(--ff-ui)', letterSpacing: '-0.01em',
  },
}