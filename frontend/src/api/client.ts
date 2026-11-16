/** 统一请求封装：拼后端地址、带操作身份头、抛网络错误、给页脚留一句可读的说明。 */
const API_BASE = import.meta.env.VITE_API_BASE ?? ''

// 操作身份由会话 store 在启动时写入；放在独立模块里避免与 pinia store 形成循环依赖。
let operatorId = 'readonly-01'

export function setOperatorId(id: string) {
  operatorId = id
}

export function authHeaders(extra: HeadersInit = {}): HeadersInit {
  return { 'X-Operator-Id': operatorId, ...extra }
}

export function request(path: string, init?: RequestInit): Promise<Response> {
  const url = path.startsWith('http') ? path : `${API_BASE}${path}`
  return fetch(url, {
    headers: { 'Content-Type': 'application/json', ...authHeaders(), ...(init?.headers ?? {}) },
    ...init,
  }).catch((error: unknown) => {
    const detail = error instanceof Error ? error.message : '请求未送达'
    throw new Error(`接口请求失败：${detail}`)
  })
}

export async function fetchJson<T>(path: string): Promise<T> {
  const response = await request(path)
  if (!response.ok) {
    throw new Error(`接口返回 ${response.status}，数据未更新`)
  }
  return (await response.json()) as T
}

/** 把后端结构化错误（{detail:{code,message}}）翻译成可直接展示的中文原因。 */
export async function errorMessageOf(response: Response, fallback: string): Promise<string> {
  try {
    const payload = await response.json()
    const detail = payload?.detail
    if (detail && typeof detail === 'object' && detail.message) {
      return detail.message as string
    }
    if (typeof detail === 'string') {
      return detail
    }
    if (payload?.message) {
      return payload.message as string
    }
  } catch {
    // 响应体不是 JSON 时退回兜底文案
  }
  return fallback
}
