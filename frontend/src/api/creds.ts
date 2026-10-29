/**
 * 访问凭据存储。
 *
 * 单独成模块，不依赖 api/client —— 否则 useSSE 引入 client 会把
 * localStorage 依赖带进流式层，node 环境测试直接炸。
 */

export const creds = {
  get token() {
    return localStorage.getItem('xm_admin_token') || ''
  },
  get passcode() {
    return localStorage.getItem('xm_passcode') || ''
  },
  setToken(v: string) {
    if (v) localStorage.setItem('xm_admin_token', v)
    else localStorage.removeItem('xm_admin_token')
  },
  setPasscode(v: string) {
    if (v) localStorage.setItem('xm_passcode', v)
    else localStorage.removeItem('xm_passcode')
  },
  headers(): Record<string, string> {
    const h: Record<string, string> = {}
    if (creds.token) h['X-Admin-Token'] = creds.token
    if (creds.passcode) h['X-App-Passcode'] = creds.passcode
    return h
  },
}
