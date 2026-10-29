<script setup lang="ts">
import { ref, nextTick, watch } from 'vue'
import MessageItem from './MessageItem.vue'
import type { ChatMessage } from '@/api/client'

const props = defineProps<{
  messages: ChatMessage[]
  streaming: boolean
}>()

const scroller = ref<HTMLElement | null>(null)
const autoStick = ref(true)

function onScroll() {
  const el = scroller.value
  if (!el) return
  autoStick.value = el.scrollHeight - el.scrollTop - el.clientHeight < 80
}

function toBottom() {
  const el = scroller.value
  if (!el) return
  el.scrollTop = el.scrollHeight
}

watch(
  () => props.messages.map((m) => m.content.length).join(','),
  async () => {
    if (!autoStick.value) return
    await nextTick()
    toBottom()
  },
)
</script>

<template>
  <div ref="scroller" class="list" @scroll="onScroll">
    <div class="inner">
      <MessageItem v-for="m in messages" :key="m.id" :message="m" />
    </div>
  </div>
</template>

<style scoped>
.list {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  scroll-behavior: smooth;
}
.inner {
  padding: 8px 24px 32px;
}
</style>
