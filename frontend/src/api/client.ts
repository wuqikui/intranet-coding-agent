import axios from 'axios'

const api = axios.create({ baseURL: '/api', timeout: 120000 })

// 请求拦截器：附加 JWT
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// 响应拦截器：统一错误处理
api.interceptors.response.use(
  (res) => res.data,
  (err) => {
    const msg = err.response?.data?.error || err.message || '请求失败'
    return Promise.reject(new Error(msg))
  },
)

export default api

// --- SSE 流式聊天 ---
export async function* streamChat(messages: any[], opts: { model?: string; signal?: AbortSignal } = {}) {
  const res = await fetch('/api/agent/generate', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${localStorage.getItem('token')}`,
    },
    body: JSON.stringify({ messages, stream: true, ...opts }),
    signal: opts.signal,
  })
  if (!res.ok) throw new Error(`SSE 连接失败: ${res.status}`)
  const reader = res.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''
    for (const line of lines) {
      if (line.startsWith('data: ')) {
        const data = line.slice(6)
        if (data === '[DONE]') return
        try { yield JSON.parse(data) } catch { /* 跳过非 JSON 行 */ }
      }
    }
  }
}
