/**
 * SSE 客户端。
 * 用 fetch + ReadableStream 手写解析，因为 EventSource 不支持 POST 和自定义 Header。
 * 必须自己处理跨 chunk 截断的行 —— 服务端一个事件可能被拆成两个网络包。
 */

import { creds } from '@/api/creds'

export type SSEHandler = (event: string, data: any) => void

export interface SSEOptions {
  url: string
  body: unknown
  onEvent: SSEHandler
  signal?: AbortSignal
  onOpen?: () => void
  onClose?: () => void
  onError?: (err: Error) => void
}

export async function streamSSE(opts: SSEOptions): Promise<void> {
  const { url, body, onEvent, signal, onOpen, onClose, onError } = opts

  let resp: Response
  try {
    resp = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
        ...creds.headers(),
      },
      body: JSON.stringify(body),
      signal,
    })
  } catch (e) {
    if ((e as Error).name === 'AbortError') return
    onError?.(e as Error)
    return
  }

  if (!resp.ok) {
    let msg = `HTTP ${resp.status}`
    try {
      const j = await resp.json()
      msg = j?.error?.message || j?.detail || msg
    } catch {
      /* ignore */
    }
    onError?.(new Error(msg))
    return
  }
  if (!resp.body) {
    onError?.(new Error('响应无流'))
    return
  }

  onOpen?.()

  const reader = resp.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })

      // 按空行切分事件块
      let idx: number
      while ((idx = buffer.indexOf('\n\n')) >= 0) {
        const raw = buffer.slice(0, idx)
        buffer = buffer.slice(idx + 2)
        dispatch(raw, onEvent)
      }
    }
    // 处理残留
    if (buffer.trim()) dispatch(buffer, onEvent)
  } catch (e) {
    if ((e as Error).name !== 'AbortError') onError?.(e as Error)
  } finally {
    onClose?.()
    reader.releaseLock?.()
  }
}

function dispatch(raw: string, onEvent: SSEHandler) {
  if (raw.startsWith(':')) return // 注释/心跳

  let event = 'message'
  const dataLines: string[] = []

  for (const line of raw.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
  }

  if (!dataLines.length) return
  const payload = dataLines.join('\n')
  try {
    onEvent(event, JSON.parse(payload))
  } catch {
    onEvent(event, { raw: payload })
  }
}
