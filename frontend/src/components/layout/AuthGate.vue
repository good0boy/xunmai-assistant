<script setup lang="ts">
/**
 * 访问凭据闸门。
 *
 * 服务端配了 app_passcode（用户入口口令）或 admin_token（管理员令牌）时，
 * 未通过校验的请求一律 401。这里在进入主界面前先让用户填好凭据。
 */
import { onMounted, ref } from 'vue'
import { api, creds } from '@/api/client'

const passcodeRequired = ref(false)
const tokenRequired = ref(false)
const passcode = ref(creds.passcode)
const token = ref(creds.token)
const error = ref('')
const checking = ref(true)
const ready = ref(false)

async function probe() {
  try {
    const cfg = await api.config()
    passcodeRequired.value = !!cfg.passcode_required
    tokenRequired.value = !!cfg.token_required
    error.value = ''
    // 已存的凭据先试一次，能过就直接进
    if (!passcodeRequired.value && !tokenRequired.value) {
      ready.value = true
    } else if (await verify()) {
      ready.value = true
    }
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    checking.value = false
  }
}

async function verify(): Promise<boolean> {
  creds.setPasscode(passcode.value.trim())
  creds.setToken(token.value.trim())
  try {
    await api.health()
    return true
  } catch {
    return false
  }
}

async function submit() {
  error.value = ''
  if (await verify()) {
    ready.value = true
  } else {
    error.value = '校验未通过，请检查口令或令牌'
  }
}

onMounted(probe)
</script>

<template>
  <div v-if="checking" class="gate">正在连接…</div>

  <div v-else-if="!ready" class="gate">
    <div class="card">
      <h2>寻脉问诊助手</h2>
      <p class="hint">该服务已开启访问控制，请输入凭据。</p>

      <label v-if="passcodeRequired">
        <span>入口口令</span>
        <el-input
          v-model="passcode"
          type="password"
          show-password
          placeholder="请输入口令"
          @keyup.enter="submit"
        />
      </label>

      <label v-if="tokenRequired">
        <span>管理令牌</span>
        <el-input
          v-model="token"
          type="password"
          show-password
          placeholder="请输入令牌"
          @keyup.enter="submit"
        />
      </label>

      <p v-if="error" class="err">{{ error }}</p>
      <el-button type="primary" style="width: 100%" @click="submit">进入</el-button>
    </div>
  </div>

  <router-view v-else />
</template>

<style scoped>
.gate {
  display: grid;
  place-items: center;
  min-height: 100vh;
  background: var(--xm-bg, #faf8f4);
  color: var(--xm-text, #2c2823);
}
.card {
  width: 340px;
  padding: 28px;
  background: #fff;
  border: 1px solid var(--xm-border, #e5ded2);
  border-radius: 10px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.card h2 {
  margin: 0;
  font-size: 18px;
}
.hint {
  margin: 0;
  font-size: 13px;
  color: var(--xm-text-muted, #8a8177);
}
label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
}
.err {
  margin: 0;
  font-size: 13px;
  color: var(--xm-danger, #c0392b);
}
</style>
