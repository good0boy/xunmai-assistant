/**
 * useSSE 解析测试。
 *
 * 覆盖两件容易出错的事：
 * 1. 服务端一个 SSE 事件可能被拆到多个网络包（必须靠缓冲拼接）
 * 2. 心跳注释行（": ping"）不能被当成事件
 */
import { describe, expect, it, vi } from 'vitest'
import { streamSSE } from '@/composables/useSSE'

function makeStream(chunks: string[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder()
  let i = 0
  return new ReadableStream({
    pull(controller) {
      if (i >= chunks.length) {
        controller.close()
        return
      }
      controller.enqueue(enc.encode(chunks[i]))
      i += 1
    },
  })
}

function mockFetch(chunks: string[]) {
  const fn = vi.fn(async () => {
    const resp = new Response(makeStream(chunks), {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    })
    return resp
  })
  vi.stubGlobal('fetch', fn)
  return fn
}

describe('useSSE 事件解析', () => {
  it('解析基本事件序列', async () => {
    mockFetch([
      'event: meta\ndata: {"conversation_id":"c1"}\n\n',
      'event: delta\ndata: {"text":"你好"}\n\n',
      'event: done\ndata: {}\n\n',
    ])
    const seen: Array<[string, any]> = []
    await streamSSE({
      url: '/api/v1/chat/stream',
      body: {},
      onEvent: (e, d) => seen.push([e, d]),
    })
    expect(seen.map((s) => s[0])).toEqual(['meta', 'delta', 'done'])
    expect(seen[1][1].text).toBe('你好')
  })

  it('事件被拆到多个网络包时仍能正确拼接', async () => {
    // 故意把一个事件切成 3 段
    mockFetch([
      'event: del',
      'ta\ndata: {"te',
      'xt":"分片"}\n\n',
    ])
    const seen: Array<[string, any]> = []
    await streamSSE({
      url: '/api/v1/chat/stream',
      body: {},
      onEvent: (e, d) => seen.push([e, d]),
    })
    expect(seen).toHaveLength(1)
    expect(seen[0][0]).toBe('delta')
    expect(seen[0][1].text).toBe('分片')
  })

  it('忽略心跳注释行', async () => {
    mockFetch([': ping\n\n', 'event: delta\ndata: {"text":"x"}\n\n', ': ping\n\n'])
    const seen: Array<[string, any]> = []
    await streamSSE({
      url: '/api/v1/chat/stream',
      body: {},
      onEvent: (e, d) => seen.push([e, d]),
    })
    expect(seen).toHaveLength(1)
    expect(seen[0][0]).toBe('delta')
  })

  it('一个网络包里含多个事件时全部解析', async () => {
    mockFetch(['event: delta\ndata: {"text":"a"}\n\nevent: delta\ndata: {"text":"b"}\n\n'])
    const seen: Array<[string, any]> = []
    await streamSSE({
      url: '/api/v1/chat/stream',
      body: {},
      onEvent: (e, d) => seen.push([e, d]),
    })
    expect(seen.map((s) => s[1].text)).toEqual(['a', 'b'])
  })

  it('HTTP 错误时回调 onError', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ error: { message: '模型未配置' } }), { status: 503 })),
    )
    let err = ''
    await streamSSE({
      url: '/api/v1/chat/stream',
      body: {},
      onEvent: () => {},
      onError: (e) => {
        err = e.message
      },
    })
    expect(err).toContain('模型未配置')
  })

  it('正常结束会调用 onClose', async () => {
    mockFetch(['event: done\ndata: {}\n\n'])
    let closed = false
    await streamSSE({
      url: '/api/v1/chat/stream',
      body: {},
      onEvent: () => {},
      onClose: () => {
        closed = true
      },
    })
    expect(closed).toBe(true)
  })
})
