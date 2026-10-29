<script setup lang="ts">
import { computed } from 'vue'
import { useMarkdown } from '@/composables/useMarkdown'
import type { Citation } from '@/api/client'

const props = defineProps<{
  content: string
  citations?: Citation[]
  streaming?: boolean
}>()

const emit = defineEmits<{ (e: 'cite', n: number): void }>()

const { render } = useMarkdown()

const html = computed(() => {
  let text = props.content || ''
  // 流式中可能有未闭合的表格/代码，先降级为纯文本，避免闪烁
  if (props.streaming && /(^|\n)\|.*$/.test(text) && !/\|\s*$/m.test(text)) {
    // 仍在输出表格最后一列，不强渲染
  }
  return render(text, props.citations || [])
})

function onClick(e: MouseEvent) {
  const el = (e.target as HTMLElement).closest('.cite') as HTMLElement | null
  if (el?.dataset.n) emit('cite', Number(el.dataset.n))
}
</script>

<template>
  <div class="md-body" @click="onClick" v-html="html" />
</template>

<style>
.md-body {
  font-size: 15px;
  line-height: 1.82;
  color: var(--xm-text);
  word-break: break-word;
}
.md-body > *:first-child {
  margin-top: 0;
}
.md-body > *:last-child {
  margin-bottom: 0;
}

.md-body h1,
.md-body h2,
.md-body h3,
.md-body h4 {
  font-family: var(--xm-font-serif);
  font-weight: 600;
  margin: 1.4em 0 0.6em;
  line-height: 1.4;
}
.md-body h1 {
  font-size: 20px;
}
.md-body h2 {
  font-size: 18px;
}
.md-body h3 {
  font-size: 16px;
}
.md-body h4 {
  font-size: 15px;
}

.md-body p {
  margin: 0.7em 0;
}

.md-body strong {
  color: var(--xm-accent-strong);
  font-weight: 600;
}

.md-body ul,
.md-body ol {
  padding-left: 1.4em;
  margin: 0.6em 0;
}
.md-body li {
  margin: 0.3em 0;
}

.md-body .table-wrap {
  overflow-x: auto;
  margin: 1em 0;
  border: 1px solid var(--xm-border);
  border-radius: var(--xm-radius-sm);
}

.md-body table {
  border-collapse: collapse;
  width: 100%;
  font-size: 14px;
}

.md-body th,
.md-body td {
  border-bottom: 1px solid var(--xm-border);
  border-right: 1px solid var(--xm-border);
  padding: 8px 12px;
  text-align: left;
  vertical-align: top;
}
.md-body th:last-child,
.md-body td:last-child {
  border-right: none;
}
.md-body tr:last-child td {
  border-bottom: none;
}

.md-body th {
  background: var(--xm-bg-sunken);
  font-weight: 600;
  white-space: nowrap;
}

.md-body tbody tr:nth-child(even) {
  background: #fbf8f3;
}

.md-body blockquote.quote {
  margin: 0.9em 0;
  padding: 0.5em 0 0.5em 14px;
  border-left: 3px solid var(--xm-border-strong);
  color: var(--xm-text-secondary);
  background: transparent;
}

/* 经方条文卡片 */
.md-body blockquote.formula-card {
  margin: 1em 0;
  padding: 14px 16px;
  border: 1px solid var(--xm-border-strong);
  border-left: 3px solid var(--xm-accent);
  border-radius: var(--xm-radius-sm);
  background: #fdfaf5;
  font-size: 14px;
  line-height: 1.9;
}
.md-body blockquote.formula-card p {
  margin: 0.25em 0;
}

.md-body code {
  background: var(--xm-bg-sunken);
  padding: 1px 5px;
  border-radius: 4px;
  font-family: var(--xm-font-mono);
  font-size: 13px;
}

.md-body pre {
  background: #f4efe6;
  padding: 14px;
  border-radius: var(--xm-radius-sm);
  overflow-x: auto;
  font-size: 13px;
  line-height: 1.6;
}
.md-body pre code {
  background: none;
  padding: 0;
}

.md-body hr {
  border: none;
  border-top: 1px solid var(--xm-border);
  margin: 1.4em 0;
}

/* 引用角标 */
.md-body sup.cite {
  display: inline-block;
  min-width: 16px;
  height: 16px;
  line-height: 16px;
  padding: 0 4px;
  margin: 0 2px;
  border-radius: 8px;
  background: var(--xm-accent-soft);
  color: var(--xm-accent-strong);
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  vertical-align: super;
  transition: background 0.15s;
  user-select: none;
}
.md-body sup.cite:hover {
  background: var(--xm-accent);
  color: #fff;
}
</style>
