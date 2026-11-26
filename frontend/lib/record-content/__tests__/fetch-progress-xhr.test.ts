/**
 * Aborts and the in-flight bound, measured on RECEIVED bytes through axios's real `xhr` adapter.
 *
 * Only `XMLHttpRequest` is faked: it emits `progress` events on fake timers, so axios's own
 * `progressEventReducer` and its ~333 ms throttle are in the path, as are `apiClient`'s interceptors and
 * `fetchRecordContent`. The bound is the budget plus, per orphaned load, the bytes one throttle window can
 * carry (rate x 333 ms). A last case shows that term is needed.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { installMemoryStorage, jwtExpiringIn } from '@/lib/api/__tests__/sse-response';

installMemoryStorage();
installMemoryStorage('sessionStorage');

vi.mock('@/config', async () => {
  const auth = await vi.importActual<typeof import('@/lib/store/auth-store')>('@/lib/store/auth-store');
  return { useAuthStore: auth.useAuthStore, logoutAndRedirect: vi.fn() };
});

const { useAuthStore } = await import('@/lib/store/auth-store');
const { apiClient } = await import('@/lib/api/axios-instance');
const { RecordContentCache, RECORD_CONTENT_CACHE_MAX_ENTRY_BYTES } = await import('../cache');
const { fetchRecordContent } = await import('../fetch');
const { mintGrant } = await import('../grant');

const MIB = 1024 * 1024;
const OWNER = 'o1|u1';
const THROTTLE_MS = 1000 / 3;

interface Script {
  etag: string | null;
  /** Bytes the server sends in total (events only; no Blob of that size is built). */
  total: number;
  chunk: number;
  everyMs: number;
}

const instances: FakeXhr[] = [];
let script: (recordId: string, variant: string) => Script = () => ({ etag: null, total: 1, chunk: 1, everyMs: 10 });

class FakeXhr {
  onloadend: ((e: unknown) => void) | null = null;
  onabort: (() => void) | null = null;
  onerror: (() => void) | null = null;
  ontimeout: (() => void) | null = null;
  timeout = 0;
  responseType = '';
  withCredentials = false;
  status = 0;
  statusText = '';
  responseURL = '';
  response: Blob | null = null;
  received = 0;
  aborted = false;
  finished = false;
  url = '';
  recordId = '';
  variant = 'original';
  conf!: Script;
  private listeners: ((e: unknown) => void)[] = [];
  private timer: ReturnType<typeof setTimeout> | null = null;
  constructor() {
    instances.push(this);
  }
  open(_method: string, url: string) {
    this.url = url;
    this.recordId = decodeURIComponent(/stream\/record\/([^?]+)/.exec(url)?.[1] ?? '');
    this.variant = url.includes('convertTo') ? 'pdf' : 'original';
  }
  setRequestHeader() {}
  addEventListener(type: string, fn: (e: unknown) => void) {
    if (type === 'progress') this.listeners.push(fn);
  }
  getAllResponseHeaders() {
    return this.conf?.etag ? `etag: ${this.conf.etag}\r\n` : '';
  }
  getResponseHeader(name: string) {
    return name.toLowerCase() === 'etag' ? (this.conf?.etag ?? null) : null;
  }
  send() {
    this.conf = script(this.recordId, this.variant);
    this.timer = setTimeout(() => {
      this.status = 200;
      this.step();
    }, 20); // headers
  }
  private step() {
    if (this.aborted) return;
    this.received = Math.min(this.conf.total, this.received + this.conf.chunk);
    const event = { type: 'progress', loaded: this.received, total: 0, lengthComputable: false, target: this };
    for (const l of this.listeners) l(event);
    if (this.received >= this.conf.total) {
      this.finished = true;
      this.response = new Blob([new Uint8Array(8)], { type: 'application/pdf' });
      this.onloadend?.({ target: this });
      return;
    }
    this.timer = setTimeout(() => this.step(), this.conf.everyMs);
  }
  abort() {
    this.aborted = true;
    if (this.timer) clearTimeout(this.timer);
  }
}

function setup(budget: number) {
  const cache = new RecordContentCache({
    fetcher: fetchRecordContent,
    owner: () => OWNER,
    budgetBytes: () => budget,
    createObjectURL: () => 'blob:x',
    revokeObjectURL: () => {},
    onOwnerChange: () => cache.clear(),
  });
  return cache;
}

const grant = (rid: string) => mintGrant(rid, `rev-${rid}|c`, OWNER);
const tick = (ms: number) => vi.advanceTimersByTimeAsync(ms);
const open = (instance: string) => instances.filter((x) => x.recordId === instance);

beforeEach(() => {
  instances.length = 0;
  vi.useFakeTimers();
  vi.stubGlobal('XMLHttpRequest', FakeXhr);
  apiClient.defaults.adapter = ['xhr'];
  useAuthStore.setState({ accessToken: jwtExpiringIn(3600, { orgId: 'o1', userId: 'u1' }), refreshToken: 'r' });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('aborts and the in-flight bound through the real xhr adapter', () => {
  it('a superseded original with no validator and no label is aborted at its first progress event', async () => {
    script = () => ({ etag: null, total: 50 * MIB, chunk: 4 * MIB, everyMs: 100 });
    const cache = setup(100 * MIB);
    const first = cache.acquire('a', 'original');
    await tick(30);
    first.leave();
    await tick(2000);
    const [x] = open('a');
    expect(x.aborted).toBe(true);
    expect(x.received).toBeLessThanOrEqual(4 * MIB);
  });

  it('one whose received bytes pass the entry limit is aborted then, even with a label', async () => {
    script = () => ({ etag: null, total: 200 * MIB, chunk: 8 * MIB, everyMs: 100 });
    const cache = setup(1024 * MIB);
    const p = cache.acquire('big', 'original', grant('big'));
    await tick(30);
    p.leave();
    await tick(5000);
    const [x] = open('big');
    expect(x.aborted).toBe(true);
    expect(x.received).toBeGreaterThan(RECORD_CONTENT_CACHE_MAX_ENTRY_BYTES);
    // axios reports at most every 333 ms, so the abort lands a window after the limit, not at the limit
    expect(x.received).toBeLessThanOrEqual(RECORD_CONTENT_CACHE_MAX_ENTRY_BYTES + 8 * MIB * Math.ceil(THROTTLE_MS / 100) + 8 * MIB);
  });

  it('a converted PDF in the same state is not aborted', async () => {
    script = () => ({ etag: null, total: 12 * MIB, chunk: 4 * MIB, everyMs: 100 });
    const cache = setup(100 * MIB);
    const p = cache.acquire('conv', 'pdf');
    await tick(30);
    p.leave();
    await tick(2000);
    const [x] = open('conv');
    expect(x.aborted).toBe(false);
    expect(x.finished).toBe(true);
  });

  /** 10 quick switches between chunked, labelled 60 MiB connector originals; budget 100 MiB. */
  async function quickSwitches() {
    script = () => ({ etag: null, total: 60 * MIB, chunk: 4 * MIB, everyMs: 100 });
    const cache = setup(100 * MIB);
    const rate = (4 * MIB) / 100; // bytes per ms
    let overshoot = -Infinity; // received by orphans minus (budget + one window each)
    let overBudget = -Infinity; // received by orphans minus the budget alone
    let prev: ReturnType<typeof cache.acquire> | null = null;
    const measure = () => {
      const orphans = new Set(cache.snapshot().jobs.filter((j) => j.waiters === 0 && !j.aborted).map((j) => j.key));
      const live = instances.filter((x) => !x.aborted && !x.finished && orphans.has(`${OWNER}|${x.recordId}|original`));
      const sum = live.reduce((n, x) => n + x.received, 0);
      overshoot = Math.max(overshoot, sum - (100 * MIB + live.length * rate * THROTTLE_MS));
      overBudget = Math.max(overBudget, sum - 100 * MIB);
    };
    for (let i = 0; i < 10; i++) {
      const p = cache.acquire(`R${i}`, 'original', grant(`R${i}`));
      prev?.leave();
      prev = p;
      for (let t = 0; t < 100; t += 20) {
        await tick(20);
        measure();
      }
    }
    for (let t = 0; t < 3000; t += 20) {
      await tick(20);
      measure();
    }
    return { overshoot, overBudget };
  }

  it('received bytes of orphaned loads stay within the budget plus one throttle window each', async () => {
    const { overshoot } = await quickSwitches();
    expect(overshoot).toBeLessThanOrEqual(0);
  });

  it('without the throttle term the same run exceeds the bound, so the term is needed', async () => {
    const { overBudget } = await quickSwitches();
    expect(overBudget).toBeGreaterThan(0);
  });
});
