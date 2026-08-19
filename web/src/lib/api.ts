export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
    this.name = 'ApiError'
  }
}

function formatDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === 'string') return item
        if (item && typeof item === 'object' && 'msg' in item) {
          return String((item as { msg: unknown }).msg)
        }
        return JSON.stringify(item)
      })
      .join('; ')
  }
  if (detail != null) return JSON.stringify(detail)
  return ''
}

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(path, options)
  if (!resp.ok) {
    let msg = `HTTP ${resp.status}`
    try {
      const body: unknown = await resp.json()
      if (body && typeof body === 'object' && 'detail' in body) {
        const formatted = formatDetail((body as { detail: unknown }).detail)
        if (formatted) msg = formatted
      }
    } catch {
      /* 非 JSON 响应 */
    }
    throw new ApiError(resp.status, msg)
  }
  if (resp.status === 204) return null as T
  return (await resp.json()) as T
}

export function fmtTime(ts: number): string {
  return new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false })
}

export function jsonInit(method: string, body?: unknown): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  }
}
