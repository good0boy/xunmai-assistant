<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { api } from '@/api/client'

const q = ref('')
const loading = ref(false)
const result = ref<any>(null)
const docs = ref<any>(null)
const showTrace = ref(true)

onMounted(async () => {
  try {
    docs.value = await api.kbDocs()
  } catch {
    /* ignore */
  }
})

async function search() {
  if (!q.value.trim()) return
  loading.value = true
  result.value = null
  try {
    result.value = await api.kbSearch(q.value.trim(), true)
  } catch (e) {
    result.value = { error: (e as Error).message }
  } finally {
    loading.value = false
  }
}

const samples = [
  '我怕冷没汗脖子僵',
  '小柴胡汤什么时候用',
  '倪师治肝癌常用什么方',
  '手脚冰凉怎么辨',
]
</script>

<template>
  <div class="kb">
    <header>
      <router-link to="/" class="back">← 返回对话</router-link>
      <h2>知识库调试</h2>
      <p class="hint">看检索每一级的召回与打分，用于调参和排查</p>
    </header>

    <div class="search">
      <input
        v-model="q"
        placeholder="输入一个用户可能会问的问题"
        @keydown.enter="search"
      />
      <button :disabled="loading" @click="search">{{ loading ? '检索中…' : '检索' }}</button>
    </div>

    <div class="samples">
      <button v-for="s in samples" :key="s" @click="(q = s), search()">{{ s }}</button>
    </div>

    <template v-if="result">
      <div v-if="result.error" class="err">{{ result.error }}</div>

      <template v-else>
        <div class="summary">
          <span class="badge" :class="'l' + result.level">L{{ result.level }}</span>
          <span>{{ result.covered ? '已覆盖' : '未覆盖' }}</span>
          <span class="sep">·</span>
          <span>命中 {{ result.hit_count }} 条</span>
          <label class="toggle">
            <input v-model="showTrace" type="checkbox" /> 显示降级轨迹
          </label>
        </div>

        <div v-if="showTrace && result.trace" class="trace">
          <div class="trace-title">降级轨迹</div>
          <div v-for="t in result.trace.traces" :key="t.level" class="tline">
            <span class="tl">L{{ t.level }}</span>
            <span class="tlabel">{{ t.label }}</span>
            <span class="tnum">融合 {{ t.fused }}</span>
            <span class="tnum">重排后 {{ t.after_rerank }}</span>
            <span class="tnum">top {{ t.top_score }}</span>
            <span v-if="t.note" class="tnote">{{ t.note }}</span>
          </div>
          <div class="ents" v-if="result.trace.entities">
            实体：
            <em v-if="result.trace.entities.formulas?.length">
              方 {{ result.trace.entities.formulas.join('/') }}
            </em>
            <em v-if="result.trace.entities.symptoms?.length">
              症 {{ result.trace.entities.symptoms.join('/') }}
            </em>
            <em v-if="result.trace.entities.diseases?.length">
              病 {{ result.trace.entities.diseases.join('/') }}
            </em>
          </div>
        </div>

        <div class="hits">
          <div v-for="h in result.hits" :key="h.chunk_id" class="hit">
            <div class="hit-head">
              <span class="n">{{ h.n }}</span>
              <span class="src">{{ h.source }}</span>
              <span class="type">{{ h.doc_type }}</span>
              <span class="score">
                rrf {{ h.score }}
                <template v-if="h.dense !== null"> · d {{ h.dense }}</template>
                <template v-if="h.sparse !== null"> · s {{ h.sparse }}</template>
                <template v-if="h.boost"> · boost {{ h.boost }}</template>
                <template v-if="h.rerank !== null"> · rerank {{ h.rerank }}</template>
              </span>
            </div>
            <div class="path">{{ h.section }}</div>
            <div class="text">{{ h.text }}</div>
            <div class="ents2" v-if="h.formulas?.length || h.symptoms?.length">
              <span v-if="h.formulas?.length" class="tag">方 {{ h.formulas.join('、') }}</span>
              <span v-if="h.symptoms?.length" class="tag">症 {{ h.symptoms.join('、') }}</span>
              <span v-if="h.diseases?.length" class="tag">病 {{ h.diseases.join('、') }}</span>
            </div>
          </div>
          <div v-if="!result.hits.length" class="none">没有召回结果</div>
        </div>
      </template>
    </template>

    <div v-if="docs && !result" class="doclist">
      <div class="dl-title">已入库 {{ docs.total }} 个知识块</div>
      <table>
        <thead>
          <tr>
            <th>来源</th>
            <th>类型</th>
            <th>块数</th>
            <th>篇号</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="d in docs.items" :key="d.source_file">
            <td>{{ d.source_file }}</td>
            <td>{{ d.doc_type }}</td>
            <td class="num">{{ d.chunks }}</td>
            <td class="num">{{ d.chapters?.length ? d.chapters.join(',') : '-' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.kb {
  max-width: 900px;
  margin: 0 auto;
  padding: 28px 24px 60px;
}
header {
  margin-bottom: 22px;
}
.back {
  font-size: 12.5px;
  color: var(--xm-text-muted);
  text-decoration: none;
}
.back:hover {
  color: var(--xm-accent);
}
h2 {
  font-family: var(--xm-font-serif);
  font-size: 22px;
  margin: 10px 0 4px;
  font-weight: 500;
}
.hint {
  font-size: 12.5px;
  color: var(--xm-text-muted);
  margin: 0;
}

.search {
  display: flex;
  gap: 10px;
}
.search input {
  flex: 1;
  padding: 10px 14px;
  border: 1px solid var(--xm-border-strong);
  border-radius: 9px;
  background: var(--xm-bg-elevated);
  font-size: 14px;
  outline: none;
  font-family: inherit;
}
.search input:focus {
  border-color: var(--xm-accent);
}
.search button {
  padding: 0 22px;
  border: none;
  border-radius: 9px;
  background: var(--xm-accent);
  color: #fff;
  font-size: 13.5px;
  cursor: pointer;
}
.search button:disabled {
  background: var(--xm-border-strong);
}

.samples {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin: 12px 0 24px;
}
.samples button {
  border: 1px solid var(--xm-border);
  background: transparent;
  color: var(--xm-text-secondary);
  font-size: 12px;
  padding: 4px 11px;
  border-radius: 16px;
  cursor: pointer;
}
.samples button:hover {
  border-color: var(--xm-accent);
  color: var(--xm-accent);
}

.summary {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--xm-text-secondary);
  padding: 10px 0;
  border-bottom: 1px solid var(--xm-border);
}
.badge {
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 11.5px;
  font-weight: 600;
  background: var(--xm-bg-sunken);
  color: var(--xm-text-secondary);
}
.badge.l1 {
  background: #e6f2e9;
  color: var(--xm-ok);
}
.badge.l2,
.badge.l3 {
  background: var(--xm-warn-soft);
  color: var(--xm-warn);
}
.badge.l4 {
  background: var(--xm-danger-soft);
  color: var(--xm-danger);
}
.sep {
  color: var(--xm-border-strong);
}
.toggle {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  cursor: pointer;
}

.trace {
  margin: 14px 0;
  padding: 12px 14px;
  background: var(--xm-bg-sunken);
  border-radius: var(--xm-radius-sm);
  font-size: 12.5px;
}
.trace-title {
  font-weight: 600;
  margin-bottom: 8px;
  color: var(--xm-text-secondary);
}
.tline {
  display: flex;
  gap: 10px;
  padding: 3px 0;
  color: var(--xm-text-secondary);
}
.tl {
  font-weight: 600;
  color: var(--xm-accent-strong);
  min-width: 22px;
}
.tlabel {
  min-width: 80px;
}
.tnum {
  color: var(--xm-text-muted);
}
.tnote {
  color: var(--xm-warn);
}
.ents {
  margin-top: 8px;
  color: var(--xm-text-muted);
}
.ents em {
  font-style: normal;
  margin-right: 12px;
  color: var(--xm-accent-strong);
}

.hits {
  margin-top: 18px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.hit {
  border: 1px solid var(--xm-border);
  border-radius: var(--xm-radius-sm);
  background: var(--xm-bg-elevated);
  padding: 12px 14px;
}
.hit-head {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  color: var(--xm-text-muted);
  flex-wrap: wrap;
}
.n {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: var(--xm-accent-soft);
  color: var(--xm-accent-strong);
  display: grid;
  place-items: center;
  font-weight: 600;
  font-size: 11px;
}
.src {
  color: var(--xm-text-secondary);
  font-weight: 500;
}
.type {
  padding: 1px 6px;
  background: var(--xm-bg-sunken);
  border-radius: 3px;
  font-size: 11px;
}
.score {
  margin-left: auto;
  font-family: var(--xm-font-mono);
  font-size: 11px;
}
.path {
  margin: 6px 0;
  font-size: 11.5px;
  color: var(--xm-text-muted);
}
.text {
  font-size: 13px;
  line-height: 1.8;
  color: var(--xm-text);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 220px;
  overflow-y: auto;
}
.ents2 {
  margin-top: 8px;
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.tag {
  font-size: 11px;
  padding: 1px 7px;
  background: var(--xm-accent-soft);
  color: var(--xm-accent-strong);
  border-radius: 3px;
}
.none,
.err {
  padding: 20px;
  text-align: center;
  color: var(--xm-text-muted);
  font-size: 13px;
}
.err {
  color: var(--xm-danger);
}

.doclist {
  margin-top: 20px;
}
.dl-title {
  font-size: 13px;
  color: var(--xm-text-secondary);
  margin-bottom: 10px;
}
.doclist table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
}
.doclist th,
.doclist td {
  text-align: left;
  padding: 6px 10px;
  border-bottom: 1px solid var(--xm-border);
}
.doclist th {
  color: var(--xm-text-muted);
  font-weight: 500;
}
.doclist .num {
  text-align: right;
  font-family: var(--xm-font-mono);
}
</style>
