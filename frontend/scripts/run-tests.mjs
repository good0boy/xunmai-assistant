/**
 * 测试运行包装器。
 *
 * 背景：本机在受限环境（WorkBuddy 沙箱只放行部分写路径）下，vitest transform
 * 模块时写 SSR 缓存文件会被判越权，报 EPERM 并中断整个 run —— 表现为
 * 「只跑了一半用例」并给出假通过。缓存生成之后再次运行就正常，
 * 所以规律是：任何文件改动后的第一次运行必挂。
 *
 * 对策：
 *   1. 显式扫描出全部测试文件逐个传给 vitest，不依赖它的自动发现；
 *   2. 临时目录指到工作区内，减少 EPERM 发生概率；
 *   3. 若本次失败确因 EPERM（缓存写入），自动重试一次；
 *      真实断言失败不会被重试掩盖。
 */
import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, readdirSync } from 'node:fs'
import { dirname, join, relative, sep } from 'node:path'
import { fileURLToPath, URL } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const root = join(here, '..')
const srcDir = join(root, 'src')
// 一律用正斜杠：受限环境的 fs 代理对反斜杠路径的规范化不可靠
const tmp = join(root, 'node_modules', '.vitest-tmp').replace(/\\/g, '/')

mkdirSync(tmp, { recursive: true })

/** 递归收集 src 下的 *.test.ts */
function collect(dir, acc = []) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name)
    if (entry.isDirectory()) collect(full, acc)
    else if (entry.name.endsWith('.test.ts')) acc.push(full)
  }
  return acc
}

const files = existsSync(srcDir) ? collect(srcDir).sort() : []
if (!files.length) {
  console.error('未找到测试文件')
  process.exit(1)
}

const rel = files.map((f) => relative(root, f).split(sep).join('/'))
console.log(`[test] 发现 ${rel.length} 个测试文件：${rel.join(', ')}`)

// Windows 上 process.env 大小写不敏感，展开成普通对象后可能同时存在
// 'Temp' 与 'TEMP' 两个键，spawn 合并时原值会胜出 —— 先清干净再设。
const env = { ...process.env }
for (const k of Object.keys(env)) {
  const lk = k.toLowerCase()
  if (lk === 'temp' || lk === 'tmp' || lk === 'tmpdir') delete env[k]
}
env.TEMP = tmp
env.TMP = tmp
env.TMPDIR = tmp

const args = [join(root, 'node_modules', 'vitest', 'vitest.mjs'), 'run', ...rel, ...process.argv.slice(2)]

function runVitest() {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, args, { cwd: root, env, stdio: ['ignore', 'pipe', 'pipe'] })
    let out = ''
    const tap = (buf) => {
      const s = buf.toString()
      out += s
      process.stdout.write(s)
    }
    child.stdout.on('data', tap)
    child.stderr.on('data', tap)
    child.on('exit', (code) => resolve({ code: code ?? 1, out }))
  })
}

const { code, out } = await runVitest()
const isFsPerm = /EPERM|operation not permitted/i.test(out)

// 沙箱对这次写缓存的判定是间歇性的，多试几次通常能过。
// 只在确实是 EPERM 时重试，真实断言失败不会被掩盖。
if (code !== 0 && isFsPerm) {
  let last = { code, out }
  for (let attempt = 2; attempt <= 4; attempt++) {
    console.log(`\n[test] 第 ${attempt - 1} 次因沙箱写缓存受限失败，重试…\n`)
    await new Promise((r) => setTimeout(r, 400))
    last = await runVitest()
    if (last.code === 0) {
      process.exit(0)
    }
    if (!/EPERM|operation not permitted/i.test(last.out)) {
      break
    }
  }
  process.exit(last.code)
}

process.exit(code)
