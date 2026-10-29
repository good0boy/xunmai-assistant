// node 环境没有 requestAnimationFrame，store 的按帧合并依赖它
if (typeof globalThis.requestAnimationFrame !== 'function') {
  globalThis.requestAnimationFrame = ((cb: (t: number) => void) =>
    setTimeout(() => cb(Date.now()), 0) as unknown as number) as never
  globalThis.cancelAnimationFrame = ((id: number) =>
    clearTimeout(id as unknown as NodeJS.Timeout)) as never
}

// node 环境没有 localStorage，访问凭据模块依赖它
if (typeof globalThis.localStorage === 'undefined') {
  const store = new Map<string, string>()
  globalThis.localStorage = {
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    setItem: (k: string, v: string) => void store.set(k, String(v)),
    removeItem: (k: string) => void store.delete(k),
    clear: () => store.clear(),
    key: (i: number) => Array.from(store.keys())[i] ?? null,
    get length() {
      return store.size
    },
  } as unknown as Storage
}
