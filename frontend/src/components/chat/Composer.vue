<script setup lang="ts">
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '@/api/client'

const props = defineProps<{
  streaming: boolean
  visionEnabled: boolean
}>()

const emit = defineEmits<{
  (e: 'send', text: string, imageIds: string[]): void
  (e: 'stop'): void
}>()

const text = ref('')
const imageIds = ref<string[]>([])
const uploading = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

const canSend = computed(
  () => (text.value.trim().length > 0 || imageIds.value.length > 0) && !props.streaming,
)

function submit() {
  if (!canSend.value) return
  emit('send', text.value, [...imageIds.value])
  text.value = ''
  imageIds.value = []
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    submit()
  }
}

async function pick(e: Event) {
  const files = (e.target as HTMLInputElement).files
  if (!files?.length) return
  await upload(Array.from(files))
  ;(e.target as HTMLInputElement).value = ''
}

async function upload(files: File[]) {
  uploading.value = true
  for (const f of files.slice(0, 2)) {
    try {
      const res = await api.uploadImage(f)
      imageIds.value.push(res.image_id)
      if (!props.visionEnabled) {
        ElMessage.info('图片已收到。当前未启用图像分析，请用文字补充舌象/面色。')
      }
    } catch (err) {
      ElMessage.error((err as Error).message)
    }
  }
  uploading.value = false
}

function onPaste(e: ClipboardEvent) {
  const items = e.clipboardData?.items
  if (!items) return
  const imgs: File[] = []
  for (const it of items) {
    if (it.type.startsWith('image/')) {
      const f = it.getAsFile()
      if (f) imgs.push(f)
    }
  }
  if (imgs.length) {
    e.preventDefault()
    upload(imgs)
  }
}

function imgUrl(id: string) {
  return `/api/v1/files/${id}`
}

const quick = [
  '我感冒了，怕冷没汗，脖子后面僵',
  '最近老是睡不着，心里烦',
  '小柴胡汤什么时候用',
  '倪师怎么看糖尿病',
]

function useQuick(q: string) {
  text.value = q
}
</script>

<template>
  <div class="composer" @paste="onPaste">
    <div class="quick" v-if="!streaming">
      <button v-for="q in quick" :key="q" class="qchip" @click="useQuick(q)">{{ q }}</button>
    </div>

    <div class="box">
      <div class="thumbs" v-if="imageIds.length">
        <div v-for="id in imageIds" :key="id" class="thumb">
          <img :src="imgUrl(id)" alt="" />
          <button class="del" @click="imageIds = imageIds.filter((x) => x !== id)">×</button>
        </div>
      </div>

      <textarea
        v-model="text"
        class="input"
        rows="1"
        placeholder="说说你的情况，比如：怕冷、没汗、脖子疼……"
        :disabled="streaming"
        @keydown="onKeydown"
      />

      <div class="bar">
        <button class="icon-btn" title="上传舌象/面色照片" @click="fileInput?.click()">
          <span v-if="uploading">…</span>
          <span v-else>＋</span>
        </button>
        <input
          ref="fileInput"
          type="file"
          accept="image/jpeg,image/png,image/webp"
          multiple
          hidden
          @change="pick"
        />

        <span class="hint">Enter 发送 · Shift+Enter 换行</span>

        <button v-if="streaming" class="send stop" @click="emit('stop')">停止</button>
        <button v-else class="send" :disabled="!canSend" @click="submit">发送</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.composer {
  padding: 12px 24px 20px;
  max-width: 860px;
  margin: 0 auto;
  width: 100%;
}

.quick {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}
.qchip {
  border: 1px solid var(--xm-border);
  background: var(--xm-bg-elevated);
  color: var(--xm-text-secondary);
  font-size: 12.5px;
  padding: 5px 12px;
  border-radius: 20px;
  cursor: pointer;
  transition: all 0.15s;
}
.qchip:hover {
  border-color: var(--xm-accent);
  color: var(--xm-accent-strong);
  background: var(--xm-accent-soft);
}

.box {
  border: 1px solid var(--xm-border-strong);
  background: var(--xm-bg-elevated);
  border-radius: 14px;
  padding: 10px 12px 8px;
  box-shadow: var(--xm-shadow);
  transition: border-color 0.15s;
}
.box:focus-within {
  border-color: var(--xm-accent);
}

.thumbs {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
}
.thumb {
  position: relative;
  width: 56px;
  height: 56px;
}
.thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  border-radius: 6px;
  border: 1px solid var(--xm-border);
}
.del {
  position: absolute;
  top: -6px;
  right: -6px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  border: none;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
}

.input {
  width: 100%;
  border: none;
  outline: none;
  resize: none;
  font-family: inherit;
  font-size: 14.5px;
  line-height: 1.7;
  background: transparent;
  color: var(--xm-text);
  max-height: 160px;
  min-height: 44px;
  padding: 4px 2px;
}

.bar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 4px;
}
.icon-btn {
  width: 30px;
  height: 30px;
  border: 1px solid var(--xm-border);
  border-radius: 8px;
  background: transparent;
  color: var(--xm-text-secondary);
  font-size: 15px;
  cursor: pointer;
  transition: all 0.15s;
}
.icon-btn:hover {
  border-color: var(--xm-accent);
  color: var(--xm-accent);
}
.hint {
  flex: 1;
  font-size: 11.5px;
  color: var(--xm-text-muted);
}
.send {
  border: none;
  background: var(--xm-accent);
  color: #fff;
  font-size: 13.5px;
  padding: 7px 20px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s;
}
.send:hover:not(:disabled) {
  background: var(--xm-accent-strong);
}
.send:disabled {
  background: var(--xm-border-strong);
  cursor: not-allowed;
}
.send.stop {
  background: var(--xm-danger);
}
</style>
