const BASE = '/api'

// Authentication is a cookie the server sets, not a value compiled into this
// bundle.
//
// It used to be `import.meta.env.VITE_LANA_AUTH_TOKEN`, baked in at build
// time. That was not access control: anyone who could load the page could
// read the token straight out of the JavaScript, and rotating it meant
// rebuilding and redeploying the frontend image. The token now goes to
// POST /auth/session once; the server replies with an HttpOnly cookie that
// this code cannot read and an XSS payload cannot steal.
//
// `credentials: 'include'` is what carries that cookie. It is required rather
// than incidental — without it the browser omits the cookie on every
// cross-origin call and every request 401s.
function withAuthHeader(headers) {
  return headers
}

// Nothing here may hang forever. Without a deadline a stalled connection
// leaves a caller's loading state on permanently — during session restore
// that meant a blank screen with no spinner and no way out.
// 20s suits a metadata read: anything that is just looking something up has
// either answered or failed well inside it.
const DEFAULT_TIMEOUT_MS = 20_000
// Work that scales with the size of the dataset does not. Cleaning a large
// frame, rendering a chart server-side, scanning every numeric pair for
// correlation or fitting a regression can all legitimately run past 20s on a
// big upload, and killing them would turn a slow answer into a wrong error
// message. Still bounded, because "slow" must not mean "forever".
const ANALYSIS_TIMEOUT_MS = 120_000
// Parsing and profiling a large upload legitimately takes much longer than a
// metadata read, so that one call gets its own, far more generous deadline.
const UPLOAD_TIMEOUT_MS = 300_000

// Carries the HTTP status so callers can tell "the server answered, and the
// answer was no" from "we never reached the server at all". Session restore
// depends on that distinction: a 404 means the session is genuinely gone and
// the stored id should be forgotten, while a network failure means try again.
export class ApiError extends Error {
  constructor(message, { status = null, timeout = false, offline = false } = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.timeout = timeout
    // True when the request never produced a response: connection refused,
    // DNS failure, offline, or our own deadline firing.
    this.offline = offline || timeout
  }
}

async function request(path, { timeoutMs = DEFAULT_TIMEOUT_MS, headers, ...init } = {}) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    return await fetch(`${BASE}${path}`, {
      ...init,
      headers: withAuthHeader(headers),
      // Sends the HttpOnly auth cookie. Without it the browser omits the
      // cookie on cross-origin calls and every request 401s.
      credentials: 'include',
      signal: controller.signal,
    })
  } catch (e) {
    if (e.name === 'AbortError') {
      throw new ApiError(
        `The server did not respond within ${Math.round(timeoutMs / 1000)}s.`,
        { timeout: true },
      )
    }
    throw new ApiError('Could not reach the LANA backend.', { offline: true })
  } finally {
    clearTimeout(timer)
  }
}

async function ok(res) {
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new ApiError(err.detail || res.statusText, { status: res.status })
  }
  return res.json()
}

export async function uploadFile(file) {
  const form = new FormData()
  form.append('file', file)
  return ok(await request('/upload', {
    method: 'POST',
    body: form,
    timeoutMs: UPLOAD_TIMEOUT_MS,
  }))
}

export async function getSessionInfo(sessionId) {
  return ok(await request(`/session/${encodeURIComponent(sessionId)}`))
}

// Yields events as the model generates its answer, parsing the backend's
// `data: {...}\n\n` SSE frames from /query/stream:
//   { type: 'grounding', grounding }   — which path answered: 'sql' when the
//                                        figures came from a query executed
//                                        against the rows, 'ledger' when they
//                                        came from precomputed facts
//   { type: 'sql', sql }               — the executed statement and its result,
//                                        so the answer's provenance is shown
//                                        rather than asserted
//   { type: 'delta', text }            — the next chunk of answer text
//   { type: 'validation', validation } — the trust verdict, sent once the
//                                        full answer has been checked against
//                                        the facts that produced the context
// Deliberately not routed through request(): a long answer legitimately takes
// minutes to stream, so a fixed overall deadline would cut off healthy
// generation. The connection attempt itself is still guarded.
export async function* streamQuery(sessionId, question) {
  let res
  try {
    res = await fetch(`${BASE}/query/stream`, {
      method: 'POST',
      headers: withAuthHeader({ 'Content-Type': 'application/json' }),
      credentials: 'include',
      body: JSON.stringify({ session_id: sessionId, question }),
    })
  } catch {
    throw new ApiError('Could not reach the LANA backend.', { offline: true })
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new ApiError(err.detail || res.statusText, { status: res.status })
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    let sepIndex
    while ((sepIndex = buffer.indexOf('\n\n')) !== -1) {
      const rawEvent = buffer.slice(0, sepIndex)
      buffer = buffer.slice(sepIndex + 2)
      if (!rawEvent.startsWith('data: ')) continue

      const payload = JSON.parse(rawEvent.slice('data: '.length))
      if (payload.error) throw new Error(payload.error)
      if (payload.done) return
      if (payload.grounding) yield { type: 'grounding', grounding: payload.grounding }
      if (payload.sql) yield { type: 'sql', sql: payload.sql }
      if (payload.delta) yield { type: 'delta', text: payload.delta }
      if (payload.validation) yield { type: 'validation', validation: payload.validation }
    }
  }
}

export async function getStats(sessionId, column) {
  return ok(await request(
    `/stats/${encodeURIComponent(sessionId)}?column=${encodeURIComponent(column)}`))
}

export async function getCorrelations(sessionId, method = 'pearson') {
  return ok(await request(
    `/correlation/${encodeURIComponent(sessionId)}?method=${encodeURIComponent(method)}`,
    { timeoutMs: ANALYSIS_TIMEOUT_MS }))
}

export async function runRegression(sessionId, xCol, yCol) {
  return ok(await request('/regression', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, x_col: xCol, y_col: yCol }),
    timeoutMs: ANALYSIS_TIMEOUT_MS,
  }))
}

export async function getChartBlob(sessionId, column, chartType, xCol) {
  const res = await request('/chart', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, column, chart_type: chartType, x_col: xCol }),
    timeoutMs: ANALYSIS_TIMEOUT_MS,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || res.statusText)
  }
  const blob = await res.blob()
  return URL.createObjectURL(blob)
}

export async function getModels() {
  return ok(await request('/models'))
}

// Liveness, host resource limits, and — critically for the sidebar status
// indicator — whether the configured LLM is actually reachable right now.
export async function getHealth() {
  return ok(await request('/health', { timeoutMs: 8_000 }))
}

// Two LLM calls (propose candidate drivers, then narrate the corrected
// results) bracket real statistical tests against the actual rows, so this
// legitimately takes longer than a single question — the analysis deadline
// applies, not the metadata one.
export async function runInvestigation(sessionId, targetColumn) {
  return ok(await request('/investigate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, target_column: targetColumn }),
    timeoutMs: ANALYSIS_TIMEOUT_MS,
  }))
}

export async function getRecommendations(sessionId) {
  return ok(await request(`/recommendations/${encodeURIComponent(sessionId)}`,
    { timeoutMs: ANALYSIS_TIMEOUT_MS }))
}

export async function getValidatorCapabilities() {
  return ok(await request('/validator/capabilities'))
}

// A plain `<a href={...}>` can't carry the Authorization header, so exports
// are fetched here and handed to the browser as a Blob — the same pattern
// getChartBlob already uses, and the only one that still works once
// LANA_AUTH_TOKEN is enabled.
async function downloadFile(path, filename) {
  const res = await request(path, { timeoutMs: ANALYSIS_TIMEOUT_MS })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new ApiError(err.detail || res.statusText, { status: res.status })
  }
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

export function downloadCsv(sessionId) {
  return downloadFile(`/export/csv/${encodeURIComponent(sessionId)}`, 'lana_data.csv')
}
export function downloadPdf(sessionId) {
  return downloadFile(`/export/pdf/${encodeURIComponent(sessionId)}`, 'lana_report.pdf')
}
export function downloadDocx(sessionId) {
  return downloadFile(`/export/docx/${encodeURIComponent(sessionId)}`, 'lana_report.docx')
}

export async function getCleanPreview(sessionId) {
  return ok(await request(`/clean/preview/${encodeURIComponent(sessionId)}`,
    { timeoutMs: ANALYSIS_TIMEOUT_MS }))
}

export async function applyClean(sessionId, operations) {
  return ok(await request(`/clean/apply/${encodeURIComponent(sessionId)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ operations }),
    timeoutMs: ANALYSIS_TIMEOUT_MS,
  }))
}

export async function switchVersion(sessionId, version) {
  return ok(await request(`/clean/version/${encodeURIComponent(sessionId)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ version }),
    timeoutMs: ANALYSIS_TIMEOUT_MS,
  }))
}

export async function getCleanStatus(sessionId) {
  return ok(await request(`/clean/status/${encodeURIComponent(sessionId)}`))
}


/* ── Authentication ────────────────────────────────────────────────────────
 *
 * Only relevant when the server has LANA_AUTH_TOKEN set. In the default
 * single-user local setup `getAuthStatus()` reports `required: false` and the
 * UI never asks for anything.
 */

export async function getAuthStatus() {
  return ok(await request('/auth/status'))
}

export async function openAuthSession(token) {
  const res = await request('/auth/session', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token }),
  })
  if (res.status === 401) {
    throw new ApiError('That token was not accepted.', { status: 401 })
  }
  return ok(res)
}

// Accounts mode: a real username and password, checked server-side against a
// per-user scrypt hash. The reply sets a session cookie this code cannot read
// and the server can revoke — unlike a stateless token, which stays valid
// until it expires no matter what the server has since learned.
export async function login(username, password) {
  const res = await request('/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  if (res.status === 401) {
    throw new ApiError('That username and password did not match.', { status: 401 })
  }
  return ok(res)
}

export async function closeAuthSession() {
  return ok(await request('/auth/logout', { method: 'POST' }))
}

// Always resolves with the server's generic message, success or not — the
// backend deliberately gives the same response whether or not the username
// exists, so there is nothing more specific for this to distinguish either.
export async function requestPasswordReset(username) {
  return ok(await request('/auth/forgot-password', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username }),
  }))
}

export async function resetPassword(token, newPassword) {
  const res = await request('/auth/reset-password', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token, new_password: newPassword }),
  })
  if (res.status === 400) {
    const err = await res.json().catch(() => ({}))
    throw new ApiError(err.detail || 'That reset link is invalid or has expired.', { status: 400 })
  }
  return ok(res)
}


/* ── Data sources ──────────────────────────────────────────────────────────
 *
 * The picker is rendered from `listSources()` rather than a hardcoded list,
 * so a connector registered on the backend appears here with no change to
 * this file.
 */

export async function listSources() {
  return ok(await request('/sources'))
}

export async function testSource(spec) {
  return ok(await request('/sources/test', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(spec),
  }))
}

export async function previewSource(spec) {
  return ok(await request('/sources/preview', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(spec),
    timeoutMs: ANALYSIS_TIMEOUT_MS,
  }))
}

export async function loadSource(spec) {
  return ok(await request('/sources/load', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(spec),
    // A connector can legitimately spend a long time on a big query or a slow
    // network, so it gets the upload deadline rather than the metadata one.
    timeoutMs: UPLOAD_TIMEOUT_MS,
  }))
}
