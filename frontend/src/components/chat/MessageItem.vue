<script setup lang="ts">
import { ref } from 'vue'
import MarkdownBody from '@/components/markdown/MarkdownBody.vue'
import DisclaimerBar from '@/components/safety/DisclaimerBar.vue'
import SourceDrawer from '@/components/citation/SourceDrawer.vue'
import type { ChatMessage } from '@/api/client'

const props = defineProps<{ message: ChatMessage }>()

const drawer = ref(false)
const activeN = ref(1)

function openCite(n: number) {
  activeN.value = n
  drawer.value = true
}

function imgUrl(id: string) {
  return `/api/v1/files/${id}`
}
</script>

<template>
  <div class="row" :class="message.role">
    <div class="avatar">{{ message.role === 'user' ? '我' : '倪' }}</div>

    <div class="main">
      <!-- 用户消息 -->
      <template v-if="message.role === 'user'">
        <div class="images" v-if="message.image_ids?.length">
          <el-image
            v-for="id in message.image_ids"
            :key="id"
            :src="imgUrl(id)"
            :preview-src-list="message.image_ids.map(imgUrl)"
            fit="cover"
            class="thumb"
            preview-teleported
          />
        </div>
        <div class="bubble user">{{ message.content }}</div>
      </template>

      <!-- 助手消息 -->
      <template v-else>
        <div class="answer">
          <MarkdownBody
            :content="message.content"
            :citations="message.citations"
            :streaming="message.streaming"
            @cite="openCite"
          />
          <span v-if="message.streaming" class="caret" />
        </div>

        <!-- 引用条 -->
        <div class="cites" v-if="message.citations?.length">
          <span class="cites-label">出处</span>
          <button
            v-for="c in message.citations"
            :key="c.n"
            class="cite-chip"
            :title="c.snippet"
            @click="openCite(c.n)"
          >
            {{ c.n }} · {{ c.label }}
          </button>
        </div>

        <DisclaimerBar
          v-if="message.disclaimer"
          :level="message.disclaimer.level"
          :text="message.disclaimer.text"
        />

        <div class="badges">
          <el-tag
            v-if="message.status === 'partial'"
            size="small"
            type="warning"
          >
            已中断
          </el-tag>
          <el-tag v-if="message.status === 'error'" size="small" type="danger">
            生成失败
          </el-tag>
          <el-tag
            v-if="message.retrieval?.degradation_level >= 3"
            size="small"
            type="info"
          >
            知识库覆盖有限
          </el-tag>
        </div>
      </template>
    </div>

    <SourceDrawer
      :visible="drawer"
      :citations="message.citations || []"
      :active-n="activeN"
      @close="drawer = false"
    />
  </div>
</template>

<style scoped>
.row {
  display: flex;
  gap: 12px;
  padding: 20px 0;
  max-width: 860px;
  margin: 0 auto;
  width: 100%;
}
.row.user {
  flex-direction: row-reverse;
}

.avatar {
  flex: 0 0 auto;
  width: 30px;
  height: 30px;
  border-radius: 8px;
  display: grid;
  place-items: center;
  font-size: 13px;
  font-weight: 600;
  font-family: var(--xm-font-serif);
}
.user .avatar {
  background: var(--xm-accent);
  color: #fff;
}
.assistant .avatar {
  background: var(--xm-accent-soft);
  color: var(--xm-accent-strong);
  border: 1px solid var(--xm-border-strong);
}

.main {
  flex: 1;
  min-width: 0;
}

.bubble.user {
  display: inline-block;
  float: right;
  max-width: 100%;
  padding: 10px 14px;
  background: var(--xm-accent-soft);
  color: var(--xm-accent-strong);
  border-radius: 10px 2px 10px 10px;
  font-size: 14.5px;
  line-height: 1.75;
  white-space: pre-wrap;
  word-break: break-word;
}
.user .main {
  text-align: right;
}
.user .main::after {
  content: '';
  display: block;
  clear: both;
}

.images {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  margin-bottom: 8px;
  flex-wrap: wrap;
}
.thumb {
  width: 88px;
  height: 88px;
  border-radius: 8px;
  border: 1px solid var(--xm-border);
  cursor: pointer;
}

.answer {
  font-size: 15px;
}

.caret {
  display: inline-block;
  width: 7px;
  height: 16px;
  background: var(--xm-accent);
  vertical-align: text-bottom;
  margin-left: 2px;
  animation: blink 1.05s steps(2, start) infinite;
}
@keyframes blink {
  to {
    visibility: hidden;
  }
}

.cites {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px dashed var(--xm-border);
}
.cites-label {
  font-size: 12px;
  color: var(--xm-text-muted);
  margin-right: 2px;
}
.cite-chip {
  border: 1px solid var(--xm-border);
  background: var(--xm-bg-elevated);
  color: var(--xm-text-secondary);
  font-size: 12px;
  padding: 3px 9px;
  border-radius: 20px;
  cursor: pointer;
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  transition: all 0.15s;
}
.cite-chip:hover {
  border-color: var(--xm-accent);
  color: var(--xm-accent-strong);
  background: var(--xm-accent-soft);
}

.badges {
  display: flex;
  gap: 6px;
  margin-top: 8px;
}
.badges:empty {
  display: none;
}
</style>
