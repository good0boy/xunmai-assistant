<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { useChatStore } from '@/stores/chat'
import { api, type AppConfig } from '@/api/client'
import MessageList from '@/components/chat/MessageList.vue'
import Composer from '@/components/chat/Composer.vue'

const store = useChatStore()
const { messages, streaming, error, conversations, conversationId } = storeToRefs(store)
const route = useRoute()
const router = useRouter()

const config = ref<AppConfig | null>(null)
const kbWarning = ref('')

onMounted(async () => {
  store.loadConversations()
  try {
    config.value = await api.config()
    const h = await api.health()
    if (h.kb_status === 'missing') kbWarning.value = '知识库尚未建立，请先执行 make kb'
    else if (h.kb_status === 'stale') kbWarning.value = '知识库版本已过期，建议重建索引'
    else if (h.kb_status === 'unavailable') kbWarning.value = '向量库未连通，请检查 Qdrant 服务'
    if (!h.llm_configured) kbWarning.value = '主模型 API Key 未配置，无法对话'
  } catch (e) {
    kbWarning.value = '后端连接失败：' + (e as Error).message
  }

  const rid = route.params.id as string | undefined
  if (rid) await store.loadConversation(rid)
})

watch(
  () => conversationId.value,
  (id) => {
    if (id && route.params.id !== id) router.replace(`/c/${id}`)
  },
)

// 路由参数变化必须监听：`/` 与 `/c/:id` 共用同一个 ChatView 实例，
// onMounted 只跑一次 —— 没有这个 watch，点击侧栏历史会话只会改 URL，
// 消息永远不会加载（「点击历史会话无法跳转」的根因）。
watch(
  () => route.params.id,
  async (id) => {
    const rid = id as string | undefined
    if (rid && rid !== conversationId.value) await store.loadConversation(rid)
  },
)

async function send(text: string, imageIds: string[]) {
  await store.send(text, imageIds)
}

async function newChat() {
  store.newConversation()
  router.replace('/')
}

async function rename(id: string, old: string) {
  try {
    const { value } = await ElMessageBox.prompt('重命名对话', '', {
      inputValue: old,
      confirmButtonText: '确定',
      cancelButtonText: '取消',
    })
    await api.renameConversation(id, value)
    store.loadConversations()
  } catch {
    /* cancelled */
  }
}

async function removeConv(id: string) {
  try {
    await ElMessageBox.confirm('删除这个对话？', '确认', { type: 'warning' })
    await api.deleteConversation(id)
    if (conversationId.value === id) newChat()
    store.loadConversations()
  } catch {
    /* cancelled */
  }
}
</script>

<template>
  <div class="shell">
    <!-- 侧栏 -->
    <aside class="side">
      <div class="brand">
        <div class="logo">寻脉</div>
        <div class="meta">
          <div class="name">{{ config?.app_title || '寻脉问诊助手' }}</div>
          <div class="sub">{{ config?.app_subtitle || '中医古籍与医案学习参考' }}</div>
        </div>
      </div>

      <button class="new-btn" @click="newChat">＋ 新对话</button>

      <div class="conv-list">
        <div
          v-for="c in conversations"
          :key="c.id"
          class="conv"
          :class="{ active: c.id === conversationId }"
          @click="router.push(`/c/${c.id}`)"
        >
          <div class="conv-title">{{ c.title }}</div>
          <div class="conv-actions">
            <button @click.stop="rename(c.id, c.title)" title="重命名">✎</button>
            <button @click.stop="removeConv(c.id)" title="删除">×</button>
          </div>
        </div>
        <div v-if="!conversations.length" class="empty-side">还没有对话</div>
      </div>

      <div class="foot">
        <router-link to="/knowledge" class="nav">知识库调试</router-link>
      </div>
    </aside>

    <!-- 主区 -->
    <main class="main">
      <div v-if="kbWarning" class="warn">{{ kbWarning }}</div>

      <div v-if="!store.hasMessages" class="welcome">
        <h1>寻脉问诊助手</h1>
        <p class="tagline">以倪海厦经方思维整理的中医古籍与医案</p>
        <div class="cards">
          <div class="card">
            <div class="k">六经辨证</div>
            <div class="v">先辨六经，再选方。太阳、阳明、少阳、太阴、少阴、厥阴。</div>
          </div>
          <div class="card">
            <div class="k">经方原方</div>
            <div class="v">伤寒论 129 条 + 金匮 23 篇，原方剂量与倪师临床剂量对照。</div>
          </div>
          <div class="card">
            <div class="k">1257 例医案</div>
            <div class="v">按病名、方剂检索倪师真实处置与转归。</div>
          </div>
        </div>
        <p class="disclaimer">
          本工具为学习参考，不构成诊断或处方建议。身体不适请找执业中医师面诊。
        </p>
      </div>

      <MessageList v-else :messages="messages" :streaming="streaming" />

      <div v-if="error" class="err">{{ error }}</div>

      <Composer
        :streaming="streaming"
        :vision-enabled="config?.vision_enabled ?? false"
        @send="send"
        @stop="store.abort()"
      />
    </main>
  </div>
</template>

<style scoped>
.shell {
  display: flex;
  height: 100%;
  background: var(--xm-bg);
}

/* ---------- 侧栏 ---------- */
.side {
  width: 250px;
  flex: 0 0 250px;
  border-right: 1px solid var(--xm-border);
  background: var(--xm-bg-sunken);
  display: flex;
  flex-direction: column;
  padding: 16px 12px 12px;
}
.brand {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 0 6px 16px;
}
.logo {
  width: 34px;
  height: 34px;
  border-radius: 9px;
  background: var(--xm-accent);
  color: #fff;
  font-family: var(--xm-font-serif);
  font-size: 13px;
  display: grid;
  place-items: center;
  letter-spacing: 0.5px;
}
.meta .name {
  font-size: 14px;
  font-weight: 600;
  font-family: var(--xm-font-serif);
}
.meta .sub {
  font-size: 11px;
  color: var(--xm-text-muted);
  margin-top: 1px;
}

.new-btn {
  width: 100%;
  padding: 9px;
  border: 1px dashed var(--xm-border-strong);
  border-radius: 8px;
  background: transparent;
  color: var(--xm-accent-strong);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s;
  margin-bottom: 14px;
}
.new-btn:hover {
  background: var(--xm-bg-elevated);
  border-style: solid;
}

.conv-list {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.conv {
  position: relative;
  padding: 7px 8px;
  border-radius: 7px;
  font-size: 13px;
  color: var(--xm-text-secondary);
  cursor: pointer;
  transition: background 0.12s;
}
.conv:hover {
  background: var(--xm-bg-elevated);
}
.conv.active {
  background: var(--xm-bg-elevated);
  color: var(--xm-accent-strong);
  font-weight: 500;
  box-shadow: inset 2px 0 0 var(--xm-accent);
}
.conv-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.conv-actions {
  position: absolute;
  right: 6px;
  top: 50%;
  transform: translateY(-50%);
  display: none;
  gap: 2px;
}
.conv:hover .conv-actions {
  display: flex;
}
.conv-actions button {
  border: none;
  background: transparent;
  color: var(--xm-text-muted);
  font-size: 13px;
  cursor: pointer;
  padding: 2px 4px;
  border-radius: 4px;
}
.conv-actions button:hover {
  background: var(--xm-bg-sunken);
  color: var(--xm-accent);
}
.empty-side {
  font-size: 12px;
  color: var(--xm-text-muted);
  padding: 12px 8px;
}

.foot {
  padding-top: 10px;
  border-top: 1px solid var(--xm-border);
}
.nav {
  font-size: 12.5px;
  color: var(--xm-text-muted);
  text-decoration: none;
}
.nav:hover {
  color: var(--xm-accent);
}

/* ---------- 主区 ---------- */
.main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  background: var(--xm-bg);
}

.warn {
  margin: 10px 24px 0;
  padding: 8px 12px;
  background: var(--xm-warn-soft);
  border: 1px solid #edd9b0;
  border-radius: var(--xm-radius-sm);
  color: #7a5210;
  font-size: 12.5px;
}

.err {
  margin: 0 24px;
  padding: 8px 12px;
  background: var(--xm-danger-soft);
  border-radius: var(--xm-radius-sm);
  color: var(--xm-danger);
  font-size: 12.5px;
}

.welcome {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px 24px;
  text-align: center;
}
.welcome h1 {
  font-family: var(--xm-font-serif);
  font-size: 30px;
  letter-spacing: 3px;
  margin: 0 0 8px;
  font-weight: 500;
}
.tagline {
  color: var(--xm-text-secondary);
  font-size: 13.5px;
  margin: 0 0 36px;
}
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 12px;
  max-width: 660px;
  width: 100%;
}
.card {
  text-align: left;
  padding: 14px 16px;
  background: var(--xm-bg-elevated);
  border: 1px solid var(--xm-border);
  border-radius: var(--xm-radius);
}
.card .k {
  font-size: 13px;
  font-weight: 600;
  color: var(--xm-accent-strong);
  margin-bottom: 5px;
}
.card .v {
  font-size: 12.5px;
  color: var(--xm-text-secondary);
  line-height: 1.65;
}
.disclaimer {
  margin-top: 32px;
  font-size: 11.5px;
  color: var(--xm-text-muted);
  max-width: 480px;
  line-height: 1.7;
}
</style>
