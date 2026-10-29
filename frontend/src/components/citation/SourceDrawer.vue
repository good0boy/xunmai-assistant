<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api, type Citation } from '@/api/client'

const props = defineProps<{
  visible: boolean
  citations: Citation[]
  activeN: number
}>()

const emit = defineEmits<{ (e: 'close'): void }>()

const chunk = ref<any>(null)
const loading = ref(false)

const current = computed(() => props.citations.find((c) => c.n === props.activeN))

const groupLabel = computed(() => {
  const g = current.value?.source_group
  if (g === 'classics') return '经典原文'
  if (g === 'judges') return '倪师判语'
  return '倪师解读'
})

watch(
  () => [props.visible, props.activeN] as const,
  async ([vis, n]) => {
    if (!vis || !n) return
    const item = props.citations.find((c) => c.n === n)
    if (!item) return
    loading.value = true
    chunk.value = null
    try {
      chunk.value = await api.kbChunk(item.chunk_id)
    } catch {
      chunk.value = null
    } finally {
      loading.value = false
    }
  },
  { immediate: true },
)
</script>

<template>
  <el-drawer
    :model-value="visible"
    title="原文出处"
    direction="rtl"
    size="460px"
    @update:model-value="(v: boolean) => !v && emit('close')"
  >
    <div v-if="loading" class="loading">
      <el-skeleton :rows="6" animated />
    </div>

    <template v-else-if="chunk?.payload">
      <div class="meta">
        <div class="label">{{ current?.label }}</div>
        <div class="path" v-if="chunk.payload.section_path_str">
          {{ chunk.payload.section_path_str }}
        </div>
        <div class="tags">
          <el-tag
            size="small"
            :type="current?.source_group === 'classics' ? 'warning' : 'primary'"
          >
            {{ groupLabel }}
          </el-tag>
          <el-tag size="small" type="info">{{ chunk.payload.doc_type }}</el-tag>
          <el-tag v-if="chunk.payload.article_no" size="small" type="warning">
            条文 {{ chunk.payload.article_no }}
          </el-tag>
          <el-tag v-if="chunk.payload.case_serial" size="small">
            医案 #{{ chunk.payload.case_serial }}
          </el-tag>
          <el-tag
            v-if="chunk.payload.tag_quality === 'fixed'"
            size="small"
            type="danger"
          >
            标签已纠正
          </el-tag>
        </div>
      </div>

      <!-- 判语出处：可回放核对的倪师原话 -->
      <div v-if="current?.judge_cites?.length" class="judge">
        <div class="sec-title">倪师原话出处</div>
        <a
          v-for="(jc, i) in current.judge_cites"
          :key="i"
          class="judge-link"
          :href="jc.url"
          target="_blank"
          rel="noopener noreferrer"
        >
          ▶ {{ jc.t_hms }} · 第 {{ jc.ep }} 集
        </a>
        <div class="judge-note">字幕转录，可能有识别误差</div>
      </div>

      <div class="notice" v-if="chunk.payload.article_no_note">
        {{ chunk.payload.article_no_note }}
      </div>

      <div class="body">{{ chunk.payload.text }}</div>

      <div v-if="chunk.payload.formula_names?.length" class="entities">
        <span class="k">方剂</span>
        <el-tag
          v-for="f in chunk.payload.formula_names"
          :key="f"
          size="small"
          class="chip"
        >
          {{ f }}
        </el-tag>
      </div>
      <div v-if="chunk.payload.diseases?.length" class="entities">
        <span class="k">病名</span>
        <el-tag
          v-for="d in chunk.payload.diseases"
          :key="d"
          size="small"
          type="success"
          class="chip"
        >
          {{ d }}
        </el-tag>
      </div>

      <div v-if="chunk.neighbors?.length" class="neighbors">
        <div class="sec-title">相邻内容</div>
        <div v-for="(nb, i) in chunk.neighbors" :key="i" class="nb">
          {{ nb.text }}
        </div>
      </div>
    </template>

    <el-empty v-else description="未找到原文" :image-size="80" />
  </el-drawer>
</template>

<style scoped>
.loading {
  padding: 8px;
}
.meta .label {
  font-size: 13px;
  font-weight: 600;
  color: var(--xm-accent-strong);
  margin-bottom: 6px;
}
.meta .path {
  font-size: 12px;
  color: var(--xm-text-muted);
  line-height: 1.6;
}
.meta .tags {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: 10px;
}
.notice {
  margin-top: 10px;
  padding: 6px 10px;
  background: var(--xm-bg-sunken);
  border-radius: 4px;
  font-size: 12px;
  color: var(--xm-text-muted);
}
.body {
  margin-top: 14px;
  padding: 14px;
  background: #fdfaf5;
  border: 1px solid var(--xm-border);
  border-radius: var(--xm-radius-sm);
  font-size: 14px;
  line-height: 1.9;
  white-space: pre-wrap;
  word-break: break-word;
}
.entities {
  margin-top: 12px;
  display: flex;
  gap: 6px;
  align-items: center;
  flex-wrap: wrap;
}
.entities .k {
  font-size: 12px;
  color: var(--xm-text-muted);
}
.chip {
  margin: 0;
}
.judge {
  margin-top: 14px;
  padding: 10px 12px;
  background: var(--xm-bg-sunken);
  border-radius: var(--xm-radius-sm);
}
.judge .sec-title {
  font-size: 12px;
  color: var(--xm-text-muted);
  margin-bottom: 6px;
}
.judge-link {
  display: block;
  font-size: 13px;
  color: var(--xm-accent-strong);
  text-decoration: none;
  margin-bottom: 4px;
}
.judge-link:hover {
  text-decoration: underline;
}
.judge-note {
  font-size: 11px;
  color: var(--xm-text-muted);
  margin-top: 4px;
}
.neighbors {
  margin-top: 22px;
}
.sec-title {
  font-size: 12px;
  color: var(--xm-text-muted);
  margin-bottom: 8px;
}
.nb {
  padding: 10px 12px;
  border-left: 2px solid var(--xm-border-strong);
  margin-bottom: 8px;
  font-size: 13px;
  line-height: 1.8;
  color: var(--xm-text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
