import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

const md = new MarkdownIt({
  html: false, // 不信任模型输出里的 HTML
  linkify: true,
  breaks: true,
  typographer: false,
})

// 表格外包一层，便于横向滚动
const defaultTableOpen =
  md.renderer.rules.table_open || ((tokens, idx, options, _env, self) => self.renderToken(tokens, idx, options))
md.renderer.rules.table_open = (tokens, idx, options, env, self) =>
  `<div class="table-wrap">${defaultTableOpen(tokens, idx, options, env, self)}`
md.renderer.rules.table_close = () => '</table></div>'

// 引用块识别为条文卡片
const defaultBlockquote =
  md.renderer.rules.blockquote_open ||
  ((tokens, idx, options, _env, self) => self.renderToken(tokens, idx, options))
md.renderer.rules.blockquote_open = (tokens, idx, options, env, self) => {
  const next = tokens[idx + 1]
  const isCard = next && next.type === 'inline' && /📜/.test(next.content)
  return `<blockquote class="${isCard ? 'formula-card' : 'quote'}">`
}

/** 把【n】转成可点击上标。必须在代码块之外做，所以先渲染再处理文本节点。 */
function renderCitations(html: string, validN: Set<number>): string {
  return html.replace(/(?<!<code[^>]*>)(?:[^<]*)【(\d{1,2})】/g, (match, n) => {
    const num = Number(n)
    if (!validN.size || validN.has(num)) {
      return match.replace(
        `【${n}】`,
        `<sup class="cite" data-n="${num}" role="button" tabindex="0">${num}</sup>`,
      )
    }
    return match.replace(`【${n}】`, '') // 越界角标直接剥掉
  })
}

export function useMarkdown() {
  function render(src: string, citations: { n: number }[] = []): string {
    const validN = new Set(citations.map((c) => c.n))
    const raw = md.render(src || '')
    const withCite = renderCitations(raw, validN)
    return DOMPurify.sanitize(withCite, {
      ADD_ATTR: ['data-n', 'role', 'tabindex'],
      ADD_TAGS: ['sup'],
    })
  }

  return { render }
}
