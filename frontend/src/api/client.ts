const BASE = '/api/v1'

export { creds } from './creds'

import { creds } from './creds'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...creds.headers(), ...(init?.headers || {}) },
    ...init,
  })
  if (!res.ok) {
    let msg = `HTTP ${res.status}`
    try {
      const j = await res.json()
      msg = j?.error?.message || j?.detail || msg
    } catch {
      /* ignore */
    }
    throw new Error(msg)
  }
  return res.json() as Promise<T>
}

export interface Health {
  status: string
  kb_status: 'ready' | 'stale' | 'missing' | 'unavailable'
  kb_points: number
  build_version: string | null
  llm_configured: boolean
  vision_enabled: boolean
}

export interface AppConfig {
  vision_enabled: boolean
  passcode_required: boolean
  token_required?: boolean
  model: string
  app_title: string
  app_subtitle: string
  max_upload_mb: number
}

export interface ConversationSummary {
  id: string
  title: string
  updated_at: string
  preview: string
}

/** 判语出处：可回放核对的倪师原话位置 */
export interface JudgeCite {
  bvid: string
  ep: string
  section: string
  t_hms: string
  start_sec: string
  url: string
}

export interface Citation {
  n: number
  chunk_id: string
  label: string
  snippet: string
  source?: string
  section?: string
  /** nihai 倪师解读 / classics 经典原文 */
  source_group?: string
  origin?: string
  judge_cites?: JudgeCite[]
}

export interface ChatMessage {
  id: string
  /** 服务端消息 id。流式过程中本地 id 保持不变，避免 :key 变化导致组件重建 */
  server_id?: string
  role: 'user' | 'assistant'
  content: string
  status?: string
  image_ids?: string[]
  citations?: Citation[]
  disclaimer?: { level: string; text: string }
  issues?: string[]
  retrieval?: any
  streaming?: boolean
}

export const api = {
  health: () => request<Health>('/health'),
  config: () => request<AppConfig>('/config'),

  listConversations: () =>
    request<{ items: ConversationSummary[] }>('/conversations?limit=50'),
  createConversation: (title = '新对话') =>
    request<{ id: string }>('/conversations', {
      method: 'POST',
      body: JSON.stringify({ title }),
    }),
  getConversation: (id: string) =>
    request<{
      id: string
      title: string
      messages: ChatMessage[]
    }>(`/conversations/${id}`),
  renameConversation: (id: string, title: string) =>
    request<{ ok: boolean }>(`/conversations/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ title }),
    }),
  deleteConversation: (id: string) =>
    request<{ ok: boolean }>(`/conversations/${id}`, { method: 'DELETE' }),

  getCitations: (messageId: string) =>
    request<{ items: Citation[] }>(`/messages/${messageId}/citations`),

  kbSearch: (q: string, debug = true) =>
    request<any>(`/kb/search?q=${encodeURIComponent(q)}&debug=${debug ? 1 : 0}`),
  kbDocs: () => request<any>('/kb/docs'),
  kbChunk: (chunkId: string) =>
    request<any>(`/kb/chunks/${encodeURIComponent(chunkId)}?with_neighbors=1`),

  uploadImage: async (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    const res = await fetch(`${BASE}/files/images`, { method: 'POST', body: fd })
    if (!res.ok) {
      const j = await res.json().catch(() => ({}))
      throw new Error(j?.error?.message || `上传失败 HTTP ${res.status}`)
    }
    return res.json() as Promise<{
      image_id: string
      url: string
      vision: { available: boolean; status: string }
    }>
  },

  feedback: (message_id: string, rating: number, reason?: string) =>
    request<{ ok: boolean }>('/feedback', {
      method: 'POST',
      body: JSON.stringify({ message_id, rating, reason }),
    }),
}
