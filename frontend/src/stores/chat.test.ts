/**
 * chat store 的流式行为测试。
 *
 * 重点回归一个真实缺陷：meta 事件曾把消息 id 改写成服务端 id，
 * 导致后续 delta/citation/done 全部找不到目标消息而被丢弃，
 * 界面上一个字都不显示，只能刷新页面从数据库读历史。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const sse: { opts: any } = { opts: null }

vi.mock('@/composables/useSSE', () => ({
  streamSSE: vi.fn(async (opts: any) => {
    sse.opts = opts
  }),
}))

// node 环境没有相对 URL 的 fetch，onClose 里的 loadConversations 会报错，屏蔽掉
vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<any>('@/api/client')
  return {
    ...actual,
    api: {
      ...actual.api,
      listConversations: vi.fn(async () => ({ items: [] })),
      getCitations: vi.fn(async () => ({ items: [] })),
    },
  }
})

const { useChatStore } = await import('@/stores/chat')

const nextFrame = () => new Promise((r) => setTimeout(r, 20))

describe('chat store 流式渲染', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    sse.opts = null
  })

  it('meta 事件不得改写消息 id', async () => {
    const store = useChatStore()
    await store.send('我怕冷没汗')
    const asst = store.messages[1]

    sse.opts.onEvent('meta', {
      conversation_id: 'c1',
      assistant_message_id: 'srv-1',
    })

    // id 被改写会让 MessageList 的 :key 变化、组件重建，且定位失联
    expect(asst.id).not.toBe('srv-1')
    expect(asst.server_id).toBe('srv-1')
    expect(store.conversationId).toBe('c1')
  })

  it('meta 之后的 delta 仍能实时写入正文', async () => {
    const store = useChatStore()
    await store.send('我怕冷没汗')
    const asst = store.messages[1]

    sse.opts.onEvent('meta', {
      conversation_id: 'c1',
      assistant_message_id: 'srv-1',
    })
    sse.opts.onEvent('delta', { text: '我跟你说，' })
    sse.opts.onEvent('delta', { text: '这是太阳伤寒。' })
    await nextFrame()

    expect(asst.content).toBe('我跟你说，这是太阳伤寒。')
  })

  it('done 事件收敛状态并落全量正文', async () => {
    const store = useChatStore()
    await store.send('桂枝汤怎么用')
    const asst = store.messages[1]

    sse.opts.onEvent('meta', { conversation_id: 'c1', assistant_message_id: 'srv-1' })
    sse.opts.onEvent('delta', { text: '桂枝汤' })
    sse.opts.onEvent('done', { issues: ['口头禅不足'] })
    await nextFrame()

    expect(asst.content).toBe('桂枝汤')
    expect(asst.streaming).toBe(false)
    expect(asst.status).toBe('done')
    expect(asst.issues).toEqual(['口头禅不足'])
  })

  it('引用与免责事件能落到消息上', async () => {
    const store = useChatStore()
    await store.send('小柴胡汤什么时候用')
    const asst = store.messages[1]

    sse.opts.onEvent('meta', { conversation_id: 'c1', assistant_message_id: 'srv-1' })
    sse.opts.onEvent('citation', { n: 1, label: '伤寒论·第105条', snippet: '...' })
    sse.opts.onEvent('disclaimer', { level: 'standard', text: '仅供参考' })

    expect(asst.citations).toHaveLength(1)
    expect(asst.citations?.[0].n).toBe(1)
    expect(asst.disclaimer?.text).toBe('仅供参考')
  })

  it('连接关闭后正文落全量、状态置为完成', async () => {
    const store = useChatStore()
    await store.send('麻黄汤禁忌')
    const asst = store.messages[1]

    sse.opts.onEvent('meta', { conversation_id: 'c1', assistant_message_id: 'srv-1' })
    sse.opts.onEvent('delta', { text: '麻黄汤发汗力猛' })
    sse.opts.onClose()

    expect(asst.content).toBe('麻黄汤发汗力猛')
    expect(asst.streaming).toBe(false)
    expect(asst.status).toBe('done')
    expect(store.streaming).toBe(false)
  })

  it('replace 事件整体替换正文', async () => {
    const store = useChatStore()
    await store.send('测试')
    const asst = store.messages[1]

    sse.opts.onEvent('delta', { text: '草稿' })
    sse.opts.onEvent('replace', { text: '修订后的完整答案' })
    await nextFrame()

    expect(asst.content).toBe('修订后的完整答案')
  })
})
