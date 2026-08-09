/**
 * Node exposes a stub `localStorage` global (from `--localstorage-file`, which
 * Vitest passes without a path) that shadows jsdom's implementation and has no
 * methods, so any module reading storage at import time throws. Install a real
 * in-memory Storage when the ambient one is unusable.
 */
function memoryStorage(): Storage {
  const data = new Map<string, string>();
  return {
    get length() {
      return data.size;
    },
    clear: () => data.clear(),
    getItem: (key: string) => (data.has(key) ? (data.get(key) as string) : null),
    key: (index: number) => Array.from(data.keys())[index] ?? null,
    removeItem: (key: string) => void data.delete(key),
    setItem: (key: string, value: string) => void data.set(key, String(value)),
  } as Storage;
}

function ensureStorage(name: 'localStorage' | 'sessionStorage'): void {
  const current = (globalThis as Record<string, unknown>)[name] as Storage | undefined;
  if (current && typeof current.getItem === 'function') return;
  const storage = memoryStorage();
  Object.defineProperty(globalThis, name, { configurable: true, value: storage });
  if (typeof window !== 'undefined') {
    Object.defineProperty(window, name, { configurable: true, value: storage });
  }
}

ensureStorage('localStorage');
ensureStorage('sessionStorage');
