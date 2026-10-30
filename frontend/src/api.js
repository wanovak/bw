export class ApiError extends Error {
  constructor(status, message, detail) {
    super(message)
    this.status = status
    this.detail = detail
  }
}

async function request(method, path, body) {
  let res
  try {
    res = await fetch('/api' + path, {
      method,
      headers: body !== undefined ? { 'Content-Type': 'application/json' } : {},
      body: body !== undefined ? JSON.stringify(body) : undefined,
      credentials: 'same-origin',
    })
  } catch {
    throw new ApiError(0, "Can't reach the server. Check your connection and try again.")
  }
  let data = null
  try {
    data = await res.json()
  } catch {
    /* empty body */
  }
  if (!res.ok) {
    const d = data && data.detail
    const message = typeof d === 'string' ? d : Array.isArray(d) ? 'Please check what you entered and try again.' : 'Something went wrong. Please try again.'
    throw new ApiError(res.status, message, d)
  }
  return data
}

export const api = {
  get: (p) => request('GET', p),
  post: (p, b = {}) => request('POST', p, b),
  put: (p, b) => request('PUT', p, b),
  del: (p, b) => request('DELETE', p, b),
}

export function qs(params) {
  const q = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => {
    if (v !== '' && v !== null && v !== undefined && v !== false) q.set(k, v)
  })
  const s = q.toString()
  return s ? '?' + s : ''
}

export function fmtTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  return d.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

export function pct(v) {
  return v === null || v === undefined ? '–' : Math.round(v * 100) + '%'
}
