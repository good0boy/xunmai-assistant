import { mkdirSync } from 'node:fs'
import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vitest/config'

// vitest 内部用 os.tmpdir() 建 SSR 模块缓存目录，在受限环境里 %TEMP% 不可写，
// 会抛 EPERM 并导致文件发现阶段只收集到部分用例（表现为「跑一次只跑一半」，
// 给出假通过）。这里把临时目录指到项目内，绕开系统临时目录的写限制。
const localTmp = fileURLToPath(new URL('./node_modules/.vitest-tmp', import.meta.url))
mkdirSync(localTmp, { recursive: true })
process.env.TEMP = localTmp
process.env.TMP = localTmp
process.env.TMPDIR = localTmp

export default defineConfig({
  cacheDir: './node_modules/.vite-vitest-cache',
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
    setupFiles: ['./tests/setup.ts'],
    globals: false,
  },
})
