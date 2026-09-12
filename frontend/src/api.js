const BASE = '/api'

// Nothing here may hang forever. Without a deadline a stalled connection
// leaves a caller's loading state on permanently — during session restore
// that meant a blank screen with no spinner and no way out.
const DEFAULT_TIMEOUT_MS = 20_000
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

async function request(path, { timeoutMs = DEFAULT_TIMEOUT_MS, ...init } = {}) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    return await fetch(`${BASE}${path}`, { ...init, signal: controller.signal })
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
      headers: { 'Content-Type': 'application/json' },
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
    `/correlation/${encodeURIComponent(sessionId)}?method=${encodeURIComponent(method)}`))
}

export async function runRegression(sessionId, xCol, yCol) {
  return ok(await request('/regression', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, x_col: xCol, y_col: yCol }),
  }))
}

export async function getChartBlob(sessionId, column, chartType, xCol) {
  const res = await request('/chart', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, column, chart_type: chartType, x_col: xCol }),
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

export async function getRecommendations(sessionId) {
  return ok(await request(`/recommendations/${encodeURIComponent(sessionId)}`))
}

export async function getValidatorCapabilities() {
  return ok(await request('/validator/capabilities'))
}

export function exportCsvUrl(sessionId)  { return `${BASE}/export/csv/${sessionId}` }
export function exportPdfUrl(sessionId)  { return `${BASE}/export/pdf/${sessionId}` }
export function exportDocxUrl(sessionId) { return `${BASE}/export/docx/${sessionId}` }

export async function getCleanPreview(sessionId) {
  return ok(await request(`/clean/preview/${encodeURIComponent(sessionId)}`))
}

export async function applyClean(sessionId, operations) {
  return ok(await request(`/clean/apply/${encodeURIComponent(sessionId)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ operations }),
  }))
}

export async function switchVersion(sessionId, version) {
  return ok(await request(`/clean/version/${encodeURIComponent(sessionId)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ version }),
  }))
}

export async function getCleanStatus(sessionId) {
  return ok(await request(`/clean/status/${encodeURIComponent(sessionId)}`))
}