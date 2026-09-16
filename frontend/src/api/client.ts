/** 后端 API 客户端：fetch 封装 + 统一错误处理。走 vite 代理 /api → localhost:8000。 */
import type { AudioUploadResp } from './types'

/** 构造带可选 API Token 的请求头（供 SSE 等未走 request() 的直连 fetch 复用）。 */
export function authHeaders(extra?: Record<string, string>): Record<string, string> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json', ...(extra || {}) }
  const token = import.meta.env.VITE_API_TOKEN as string | undefined
  if (token) headers['Authorization'] = `Bearer ${token}`
  return headers
}

/**
 * 本地后端绝对 URL 转相对路径走 vite 代理（/api、/static）——避免浏览器从 5175 页面
 * 直接 fetch localhost:8000 触发 CORS 拦截。非本地 URL（外网图片等）原样返回。
 */
export function toProxyUrl(url: string | undefined | null): string {
  if (!url) return ''
  if (/^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?\//i.test(url)) {
    const m = url.match(/^https?:\/\/[^/]+(\/.*)$/)
    return m ? m[1] : url
  }
  return url
}

/** 请求超时（毫秒）：防止弱网/后端挂起时 fetch 永久悬挂；调用方传入 signal 时不叠加。 */
const REQUEST_TIMEOUT_MS = 60_000

/**
 * 从后端错误响应体提取可读消息。
 * 关键：FastAPI 校验失败（422）时 detail 是数组 [{loc,msg,type},...]，
 * 若直接把数组塞进 Error，控制台/UI 会显示成 '[object Object]'。
 * 这里统一归一化为字符串。
 */
export function extractErrorMessage(j: unknown, status: number): string {
  if (j && typeof j === 'object') {
    const obj = j as Record<string, unknown>
    if (typeof obj.message === 'string' && obj.message) return obj.message
    if (Array.isArray(obj.detail) && obj.detail.length > 0) {
      const first = obj.detail[0] as (Record<string, unknown> | undefined)
      const why = first && (typeof first.msg === 'string' ? first.msg : typeof first.message === 'string' ? first.message : '')
      if (why) return String(status) + ': ' + why
      return String(status) + ': ' + JSON.stringify(obj.detail)
    }
    if (typeof obj.detail === 'string' && obj.detail) return obj.detail
  }
  return String(status) + ': ' + (typeof j === 'string' ? j : JSON.stringify(j ?? null))
}


/** 读取 File 为 base64（去掉 data:...;base64, 前缀），供各上传接口复用。 */
async function fileToBase64(file: File): Promise<string> {
  return await new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = reader.result as string
      const commaIdx = result.indexOf(',')
      resolve(commaIdx >= 0 ? result.slice(commaIdx + 1) : result)
    }
    reader.onerror = () => reject(new Error('文件读取失败'))
    reader.readAsDataURL(file)
  })
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const { headers: optHeaders, ...rest } = options || {}
  const headers = authHeaders(optHeaders as Record<string, string>)
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  try {
    const res = await fetch(`/api${path}`, {
      ...rest,
      headers,
      // 调用方已有 signal 时优先用调用方的（不叠加超时）
      signal: rest.signal ?? controller.signal,
    })
    if (!res.ok) {
      let msg = `HTTP ${res.status}`
      try {
        const j = await res.json()
        msg = extractErrorMessage(j, res.status)
      } catch {
        /* 非 JSON 响应 */
      }
      throw new Error(msg)
    }
    if (res.status === 204) return undefined as T
    return res.json() as Promise<T>
  } finally {
    clearTimeout(timeoutId)
  }
}

export const api = {
  get: <T>(p: string) => request<T>(p),
  post: <T>(p: string, body?: unknown) =>
    request<T>(p, { method: 'POST', body: body ? JSON.stringify(body) : undefined }),
  put: <T>(p: string, body?: unknown) =>
    request<T>(p, { method: 'PUT', body: body ? JSON.stringify(body) : undefined }),
  patch: <T>(p: string, body?: unknown) =>
    request<T>(p, { method: 'PATCH', body: body ? JSON.stringify(body) : undefined }),
  del: <T>(p: string) => request<T>(p, { method: 'DELETE' }),
}

/** 上传音频文件（base64 编码），返回静态 URL。 */
export async function uploadAudio(file: File): Promise<string> {
  const res = await request<AudioUploadResp>('/uploads/audio', {
    method: 'POST',
    body: JSON.stringify({ filename: file.name, data_base64: await fileToBase64(file) }),
  })
  return res.url
}

/** 上传图片文件（base64 编码，png/jpg/webp 等），返回静态 URL（分镜初始帧等通用场景）。 */
export async function uploadImage(file: File): Promise<string> {
  const res = await request<AudioUploadResp>('/uploads/image', {
    method: 'POST',
    body: JSON.stringify({ filename: file.name, data_base64: await fileToBase64(file) }),
  })
  return res.url
}

/** 上传图片作为资产封面/主图（角色/场景/道具通用），返回更新后的资产对象。 */
export async function uploadAssetImage<T>(assetId: string, file: File): Promise<T> {
  return request<T>(`/assets/${assetId}/upload-image`, {
    method: 'POST',
    body: JSON.stringify({ filename: file.name, data_base64: await fileToBase64(file) }),
  })
}

/** 上传 AI 视频页签参考素材文件（图片/视频，base64），返回静态 URL（全局独立功能）。 */
export async function uploadVideoLabFile(file: File): Promise<string> {
  const res = await request<AudioUploadResp>('/video-drafts/upload', {
    method: 'POST',
    body: JSON.stringify({ filename: file.name, data_base64: await fileToBase64(file) }),
  })
  return res.url
}
