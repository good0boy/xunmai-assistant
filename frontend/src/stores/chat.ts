import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { api, type ChatMessage, type Citation, type ConversationSummary } from '@/api/client'
import { streamSSE } from '@/composables/useSSE'

export const useChatStore = defineStore('chat', () => {
  const messages = ref<ChatMessage[]>([])
  const conversationId = ref<string | null>(null)
  const streaming = ref(false)
  const error = ref('')
  const conversations = ref<ConversationSummary[]>([])
  const retrievalInfo = ref<any>(null)
  const visionInfo = ref<string>('')

  let controller: AbortController | null = null

  const hasMessages = computed(() => messages.value.length > 0)

  async function loadConversations() {
    try {
      const res = await api.listConversations()
      conversations.value = res.items
    } catch (e) {
      console.warn('会话列表加载失败', e)
    }
  }

  async function loadConversation(id: string) {
    abort()
    error.value = ''
    try {
      const data = await api.getConversation(id)
      conversationId.value = data.id
      messages.value = data.messages.map((m) => ({
        ...m,
        citations: [],
        streaming: false,
      }))
      // 引用按需加载
      for (const m of messages.value) {
        if (m.role === 'assistant' && m.id) {
          api
            .getCitations(m.id)
            .then((r) => {
              const target = messages.value.find((x) => x.id === m.id)
              if (target && r.items.length) target.citations = r.items
            })
            .catch(() => {})
        }
      }
    } catch (e) {
      error.value = (e as Error).message
    }
  }

  function newConversation() {
    abort()
    conversationId.value = null
    messages.value = []
    error.value = ''
    retrievalInfo.value = null
    visionInfo.value = ''
  }

  async function send(content: string, imageIds: string[] = []) {
    const text = content.trim()
    if ((!text && !imageIds.length) || streaming.value) return

    error.value = ''
    retrievalInfo.value = null
    visionInfo.value = ''

    const localUserId = `local-u-${Date.now()}`
    const localAsstId = `local-a-${Date.now()}`

    messages.value.push({
      id: localUserId,
      role: 'user',
      content: text || '（图片）',
      image_ids: imageIds,
    })
    messages.value.push({
      id: localAsstId,
      role: 'assistant',
      content: '',
      citations: [],
      streaming: true,
    })
    // 取回 reactive proxy：直接改这个对象，Vue 才能追踪到变化。
    // 不能用 messages.value 里的原始对象（那是 raw，不是 proxy）。
    const asstMsg = messages.value[messages.value.length - 1] as ChatMessage

    streaming.value = true
    controller = new AbortController()

    // 定位助手消息：始终用不变的本地 id。
    // 服务端 id 记在 server_id，绝不覆盖 id —— 一旦改写 id，
    // MessageList 的 :key="m.id" 会重建组件，这里也会再也定位不到这条消息。
    const target = () => messages.value.find((m) => m.id === localAsstId) ?? asstMsg

    let buffer = ''
    let rafId = 0

    const flush = () => {
      rafId = 0
      const m = target()
      if (m) m.content = buffer
    }
    // 按帧合并：token 速率可能高于渲染帧率，逐 token 重渲染 Markdown 会卡
    const scheduleFlush = () => {
      if (rafId) return
      rafId = requestAnimationFrame(flush)
    }

    await streamSSE({
      url: '/api/v1/chat/stream',
      body: {
        conversation_id: conversationId.value,
        content: text,
        image_ids: imageIds,
        options: { with_citations: true },
      },
      signal: controller.signal,
      onEvent: (event, data) => {
        const m = target()
        switch (event) {
          case 'meta':
            conversationId.value = data.conversation_id
            if (m) m.server_id = data.assistant_message_id
            break
          case 'retrieval':
            retrievalInfo.value = data
            if (m) m.retrieval = data
            break
          case 'vision':
            visionInfo.value = data.block || ''
            break
          case 'delta':
            buffer += data.text || ''
            scheduleFlush()
            break
          case 'replace':
            buffer = data.text || buffer
            scheduleFlush()
            break
          case 'citation':
            if (m) m.citations = [...(m.citations || []), data as Citation]
            break
          case 'disclaimer':
            if (m) m.disclaimer = { level: data.level, text: data.text }
            break
          case 'done':
            if (rafId) {
              cancelAnimationFrame(rafId)
              rafId = 0
            }
            if (m) {
              m.content = buffer
              m.streaming = false
              m.status = 'done'
              m.issues = data.issues
            }
            break
          case 'error':
            error.value = data.message || '生成失败'
            if (m) {
              m.content = buffer
              m.streaming = false
              m.status = 'error'
            }
            break
        }
      },
      onError: (e) => {
        error.value = e.message
        if (rafId) {
          cancelAnimationFrame(rafId)
          rafId = 0
        }
        const m = target()
        if (m) {
          m.content = buffer
          m.streaming = false
        }
      },
      onClose: () => {
        if (rafId) {
          cancelAnimationFrame(rafId)
          rafId = 0
        }
        streaming.value = false
        const m = target()
        if (m) {
          m.content = buffer
          m.streaming = false
          if (!m.status || m.status === 'streaming') m.status = 'done'
        }
        loadConversations()
      },
    })

    streaming.value = false
  }

  function abort() {
    if (controller) {
      controller.abort()
      controller = null
    }
    streaming.value = false
    const last = messages.value[messages.value.length - 1]
    if (last && last.streaming) last.streaming = false
  }

  return {
    messages,
    conversationId,
    streaming,
    error,
    conversations,
    retrievalInfo,
    visionInfo,
    hasMessages,
    loadConversations,
    loadConversation,
    newConversation,
    send,
    abort,
  }
})
