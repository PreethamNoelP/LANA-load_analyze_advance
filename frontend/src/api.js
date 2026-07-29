const BASE = '/api'

async function ok(res) {
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || res.statusText)
  }
  return res.json()
}

export async function uploadFile(file) {
  const form = new FormData()
  form.append('file', file)
  return ok(await fetch(`${BASE}/upload`, { method: 'POST', body: form }))
}

export async function getSessionInfo(sessionId) {
  return ok(await fetch(`${BASE}/session/${sessionId}`))
}

export async function queryAI(sessionId, question) {
  return ok(await fetch(`${BASE}/query`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, question }),
  }))
}

// Yields answer text incrementally as the model generates it, parsing the
// backend's `data: {...}\n\n` SSE frames from /query/stream.
export async function* streamQuery(sessionId, question) {
  const res = await fetch(`${BASE}/query/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, question }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || res.statusText)
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
      if (payload.delta) yield payload.delta
    }
  }
}

export async function getStats(sessionId, column) {
  return ok(await fetch(`${BASE}/stats/${sessionId}?column=${encodeURIComponent(column)}`))
}

export async function getCorrelations(sessionId, method = 'pearson') {
  return ok(await fetch(`${BASE}/correlation/${sessionId}?method=${encodeURIComponent(method)}`))
}

export async function runRegression(sessionId, xCol, yCol) {
  return ok(await fetch(`${BASE}/regression`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, x_col: xCol, y_col: yCol }),
  }))
}

export async function getChartBlob(sessionId, column, chartType, xCol) {
  const res = await fetch(`${BASE}/chart`, {
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
  return ok(await fetch(`${BASE}/models`))
}

export function exportCsvUrl(sessionId)  { return `${BASE}/export/csv/${sessionId}` }
export function exportPdfUrl(sessionId)  { return `${BASE}/export/pdf/${sessionId}` }
export function exportDocxUrl(sessionId) { return `${BASE}/export/docx/${sessionId}` }

export async function getCleanPreview(sessionId) {
  return ok(await fetch(`${BASE}/clean/preview/${sessionId}`))
}

export async function applyClean(sessionId, operations) {
  return ok(await fetch(`${BASE}/clean/apply/${sessionId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ operations }),
  }))
}

export async function switchVersion(sessionId, version) {
  return ok(await fetch(`${BASE}/clean/version/${sessionId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ version }),
  }))
}

export async function getCleanStatus(sessionId) {
  return ok(await fetch(`${BASE}/clean/status/${sessionId}`))
}