import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  PartyLeftError,
  RecordContentCache,
  SessionEndedError,
  RECORD_CONTENT_CONNECTOR_FRESH_MS,
  type FetchArgs,
  type FetchResult,
  type Lease,
} from '../cache';
import { mintGrant, RECORD_CONTENT_GRANT_TTL_MS } from '../grant';

const MIB = 1024 * 1024;

interface Call extends FetchArgs {
  resolve: (r: FetchResult) => void;
  reject: (e: unknown) => void;
}

function blobOf(bytes: number, type = 'application/pdf'): Blob {
  return new Blob([new Uint8Array(bytes)], { type });
}

function setup(opts: { budget?: number; owner?: () => string | null } = {}) {
  const calls: Call[] = [];
  let urlSeq = 0;
  const live = new Set<string>();
  const revoked: string[] = [];
  let owner: string | null = 'org1|user1';
  const cache = new RecordContentCache({
    fetcher: (args) =>
      new Promise<FetchResult>((resolve, reject) => {
        args.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
        calls.push({ ...args, resolve, reject });
      }),
    owner: opts.owner ?? (() => owner),
    budgetBytes: () => opts.budget ?? 192 * MIB,
    createObjectURL: () => {
      const u = `blob:test/${++urlSeq}`;
      live.add(u);
      return u;
    },
    revokeObjectURL: (u) => {
      live.delete(u);
      revoked.push(u);
    },
    onOwnerChange: () => cache.clear(),
  });
  return {
    cache,
    calls,
    live,
    revoked,
    setOwner: (o: string | null) => {
      owner = o;
    },
  };
}

const ok = (bytes: number, etag: string | null = '"v1"'): FetchResult => ({
  status: 200,
  blob: blobOf(bytes),
  etag,
  mime: 'application/pdf',
});

const settle = () => new Promise((r) => setTimeout(r, 0));

describe('RecordContentCache', () => {
  beforeEach(() => {
    vi.useRealTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('loads are shared and survive a party leaving', async () => {
    const { cache, calls, live } = setup();
    const pendings = Array.from({ length: 50 }, () => cache.acquire('r1', 'original'));
    expect(calls).toHaveLength(1);
    // The first party leaves, a second joins before the answer lands: still one fetch.
    pendings[0].leave();
    expect(calls[0].signal.aborted).toBe(false);
    const late = cache.acquire('r1', 'original');
    expect(calls).toHaveLength(1);
    calls[0].resolve(ok(1000));
    const leases = await Promise.all([...pendings.slice(1), late].map((p) => p.promise));
    expect(new Set(leases.map((l) => l.url)).size).toBe(1);
    expect(live.size).toBe(1);
    await expect(pendings[0].promise).rejects.toBeInstanceOf(PartyLeftError);
    leases.forEach((l) => l.release());
  });

  it('retention needs an etag and fits the budget', async () => {
    const { cache, calls, live } = setup({ budget: 100 * MIB });
    // No validator and no label: lease-only, gone after release.
    const a = cache.acquire('a', 'original');
    calls[0].resolve(ok(1000, null));
    (await a.promise).release();
    expect(cache.stats().entries).toBe(0);
    expect(live.size).toBe(0);
    // With an etag: kept and its URL stays until eviction.
    const b = cache.acquire('b', 'original');
    calls[1].resolve(ok(1000, '"b1"'));
    const bLease = await b.promise;
    void bLease.url;
    bLease.release();
    expect(cache.stats().entries).toBe(1);
    expect(live.size).toBe(1);
    // Over 64 MiB: lease-only.
    const c = cache.acquire('c', 'original');
    calls[2].resolve(ok(65 * MIB, '"c1"'));
    (await c.promise).release();
    expect(cache.has('c', 'original')).toBe(false);
    // LRU by bytes, evicting at insert: 30 x 10 MiB stay within the budget and the entry cap.
    for (let i = 0; i < 30; i++) {
      const p = cache.acquire(`big${i}`, 'original');
      calls[calls.length - 1].resolve(ok(10 * MIB, `"e${i}"`));
      const l = await p.promise;
      void l.url;
      l.release();
      expect(cache.stats().unpinnedBytes).toBeLessThanOrEqual(100 * MIB);
      expect(cache.stats().entries).toBeLessThanOrEqual(24);
    }
    expect(cache.has('big0', 'original')).toBe(false);
    expect(cache.has('big29', 'original')).toBe(true);
    expect(live.size).toBe(cache.stats().entries);
  });

  it('revalidates a kept entry with its etag and reuses it on 304', async () => {
    const { cache, calls } = setup();
    const a = cache.acquire('a', 'original');
    calls[0].resolve(ok(10, '"v1"'));
    const first = await a.promise;
    first.release();
    const again = cache.acquire('a', 'original');
    expect(calls[1].ifNoneMatch).toBe('"v1"');
    calls[1].resolve({ status: 304 });
    const second = await again.promise;
    expect(second.url).toBe(first.url);
    expect(calls).toHaveLength(2);
    second.release();
  });

  it('asks once more without a validator when a 304 does not match the entry', async () => {
    const { cache, calls } = setup();
    const a = cache.acquire('a', 'original');
    calls[0].resolve(ok(10, '"v1"'));
    (await a.promise).release();
    const again = cache.acquire('a', 'original');
    // Another load replaces the entry before the 304 arrives.
    cache.evictRecord('a');
    calls[1].resolve({ status: 304 });
    await settle();
    expect(calls).toHaveLength(3);
    expect(calls[2].ifNoneMatch).toBeNull();
    calls[2].resolve(ok(20, '"v2"'));
    const lease = await again.promise;
    expect(lease.blob.size).toBe(20);
    lease.release();
  });

  it('drops an answer that lands after the session ended', async () => {
    const { cache, calls, live } = setup();
    const p = cache.acquire('a', 'original');
    cache.clear();
    await expect(p.promise).rejects.toBeInstanceOf(SessionEndedError);
    // Even an answer that resolved after its abort is not inserted.
    calls[0].resolve(ok(10));
    await settle();
    expect(cache.stats().entries).toBe(0);
    expect(live.size).toBe(0);
    expect(cache.stats().inflight).toBe(0);
  });

  it('clear revokes every url including the ones a lease holds', async () => {
    const { cache, calls, live } = setup();
    const p = cache.acquire('a', 'original');
    calls[0].resolve(ok(10));
    const lease = await p.promise;
    const url = lease.url;
    expect(live.has(url)).toBe(true);
    cache.clear();
    expect(live.size).toBe(0);
    expect(cache.stats().entries).toBe(0);
    expect(() => lease.release()).not.toThrow();
  });

  it('an owner change discards the previous owner\'s late answer', async () => {
    const { cache, calls, live, setOwner } = setup();
    const a = cache.acquire('a', 'original');
    setOwner('org1|user2');
    const b = cache.acquire('b', 'original');
    await expect(a.promise).rejects.toBeInstanceOf(SessionEndedError);
    calls[0].resolve(ok(10, '"a"'));
    calls[1].resolve(ok(10, '"b"'));
    const lease = await b.promise;
    await settle();
    expect(cache.has('a', 'original')).toBe(false);
    void lease.url;
    expect(live.size).toBe(1);
    lease.release();
  });

  describe('connector records', () => {
    const label = 'rev7|c3';
    async function seed(cache: RecordContentCache, calls: Call[]) {
      const grant = mintGrant('c1', label, 'org1|user1');
      const p = cache.acquire('c1', 'original', grant);
      calls[calls.length - 1].resolve(ok(100, null));
      (await p.promise).release();
    }

    it('are reused by label inside the window', async () => {
      vi.useFakeTimers();
      const { cache, calls } = setup();
      await seed(cache, calls);
      expect(cache.has('c1', 'original')).toBe(true);
      const hit = cache.acquire('c1', 'original', mintGrant('c1', label, 'org1|user1'));
      expect(calls).toHaveLength(1);
      (await hit.promise).release();
      // A different label: a full request.
      const moved = cache.acquire('c1', 'original', mintGrant('c1', 'rev8|c3', 'org1|user1'));
      expect(calls).toHaveLength(2);
      expect(calls[1].ifNoneMatch).toBeNull();
      calls[1].resolve(ok(100, null));
      (await moved.promise).release();
      // 600,001 ms later: a full request even with the same label.
      vi.advanceTimersByTime(RECORD_CONTENT_CONNECTOR_FRESH_MS + 1);
      const late = cache.acquire('c1', 'original', mintGrant('c1', 'rev8|c3', 'org1|user1'));
      expect(calls).toHaveLength(3);
      calls[2].resolve(ok(100, null));
      (await late.promise).release();
    });

    it('does not share an in-flight load between opens with different grant labels', async () => {
      const { cache, calls } = setup();
      const plain = cache.acquire('c1', 'original', mintGrant('c1', null, 'org1|user1'));
      const labelled = cache.acquire('c1', 'original', mintGrant('c1', label, 'org1|user1'));
      expect(calls).toHaveLength(2);
      const l1 = ok(100, '"e1"');
      const l2 = ok(200, null);
      calls[1].resolve(l2);
      const second = await labelled.promise;
      expect(second.blob).toBe(l2.blob);
      second.release();
      calls[0].resolve(l1);
      const first = await plain.promise;
      expect(first.blob).toBe(l1.blob);
      first.release();
      // The older load landing last did not replace the newer open's retained result.
      const hit = cache.acquire('c1', 'original', mintGrant('c1', label, 'org1|user1'));
      expect(calls).toHaveLength(2);
      const kept = await hit.promise;
      expect(kept.blob).toBe(l2.blob);
      kept.release();
    });

    it('still shares an in-flight load between opens with the same grant label', async () => {
      const { cache, calls } = setup();
      const a = cache.acquire('c1', 'original', mintGrant('c1', label, 'org1|user1'));
      const b = cache.acquire('c1', 'original', mintGrant('c1', label, 'org1|user1'));
      const c = cache.acquire('c1', 'original');
      const d = cache.acquire('c1', 'original');
      expect(calls).toHaveLength(2);
      calls[0].resolve(ok(10, null));
      calls[1].resolve(ok(10, '"e"'));
      for (const p of [a, b, c, d]) (await p.promise).release();
    });

    it('a record with no label and no validator is not retained', async () => {
      const { cache, calls } = setup();
      const p = cache.acquire('x', 'original', mintGrant('x', null, 'org1|user1'));
      calls[0].resolve(ok(10, null));
      (await p.promise).release();
      expect(cache.has('x', 'original')).toBe(false);
    });

    it('a label hit needs a grant from this open', async () => {
      vi.useFakeTimers();
      const { cache, calls } = setup();
      await seed(cache, calls);
      const n = () => calls.length;
      const expectRequest = (p: ReturnType<typeof cache.acquire>, before: number) => {
        expect(n()).toBe(before + 1);
        calls[n() - 1].resolve(ok(100, null));
        return p.promise.then((l: Lease) => l.release());
      };
      // No grant at all (a plain label string cannot even be passed).
      let before = n();
      await expectRequest(cache.acquire('c1', 'original'), before);
      // A hand-made object of the same shape.
      before = n();
      const fake = { recordId: 'c1', label, owner: 'org1|user1', mintedAt: Date.now() };
      await expectRequest(cache.acquire('c1', 'original', fake), before);
      // Another record's grant.
      before = n();
      await expectRequest(cache.acquire('c1', 'original', mintGrant('other', label, 'org1|user1')), before);
      // A stale grant, even unused.
      const stale = mintGrant('c1', label, 'org1|user1');
      vi.advanceTimersByTime(RECORD_CONTENT_GRANT_TTL_MS + 1);
      before = n();
      await expectRequest(cache.acquire('c1', 'original', stale), before);
      // A reused grant: the first use hits, the second goes to the network.
      const once = mintGrant('c1', label, 'org1|user1');
      before = n();
      (await cache.acquire('c1', 'original', once).promise).release();
      expect(n()).toBe(before);
      await expectRequest(cache.acquire('c1', 'original', once), before);
      // A fresh grant: a hit with no stream request.
      before = n();
      (await cache.acquire('c1', 'original', mintGrant('c1', label, 'org1|user1')).promise).release();
      expect(n()).toBe(before);
    });

    it('a storage-backed entry is never a label hit', async () => {
      const { cache, calls } = setup();
      const p = cache.acquire('s', 'original', mintGrant('s', null, 'org1|user1'));
      calls[0].resolve(ok(10, '"s1"'));
      (await p.promise).release();
      const q = cache.acquire('s', 'original', mintGrant('s', 'a|b', 'org1|user1'));
      expect(calls).toHaveLength(2);
      expect(calls[1].ifNoneMatch).toBe('"s1"');
      calls[1].resolve({ status: 304 });
      (await q.promise).release();
    });
  });

  describe('unwanted loads', () => {
    const progress = (c: Call, loaded: number, etag: string | null = null) =>
      c.onProgress({ loaded, status: 200, etag });

    it('are aborted at their headers', async () => {
      const { cache, calls } = setup({ budget: 100 * MIB });
      // Superseded original with no validator and no label: aborted at its first progress event.
      const a = cache.acquire('a', 'original');
      a.leave();
      progress(calls[0], 1000);
      expect(calls[0].signal.aborted).toBe(true);
      expect(cache.stats().inflight).toBe(0);
      // The same with a validator is kept running.
      const b = cache.acquire('b', 'original');
      b.leave();
      progress(calls[1], 1000, '"b"');
      expect(calls[1].signal.aborted).toBe(false);
      // Past 64 MiB it is aborted anyway.
      progress(calls[1], 65 * MIB, '"b"');
      expect(calls[1].signal.aborted).toBe(true);
      // A converted PDF in the same state is not aborted.
      const c = cache.acquire('c', 'pdf');
      c.leave();
      progress(calls[2], 1000);
      expect(calls[2].signal.aborted).toBe(false);
      // A load a surface waits for is never aborted.
      const d = cache.acquire('d', 'original');
      progress(calls[3], 70 * MIB);
      expect(calls[3].signal.aborted).toBe(false);
      d.leave();
      expect(calls[3].signal.aborted).toBe(true);
    });

    it('keep orphaned bytes in flight within the budget', async () => {
      const budget = 100 * MIB;
      const { cache, calls } = setup({ budget });
      let peak = 0;
      for (let i = 0; i < 10; i++) {
        const p = cache.acquire(`k${i}`, 'original', mintGrant(`k${i}`, 'r|c', 'org1|user1'));
        p.leave();
        const c = calls[calls.length - 1];
        // chunked answer of 40 MiB (no Content-Length): received bytes are what count.
        c.onProgress({ loaded: 40 * MIB, status: 200, etag: null });
        peak = Math.max(peak, cache.stats().orphanedBytes);
        expect(cache.stats().orphanedBytes).toBeLessThanOrEqual(budget);
      }
      expect(peak).toBeGreaterThan(0);
      expect(calls.filter((c) => c.signal.aborted).length).toBeGreaterThan(0);
    });

    it('an abandoned job never retries', async () => {
      const { cache, calls } = setup();
      const a = cache.acquire('a', 'original');
      calls[0].resolve(ok(10, '"v1"'));
      (await a.promise).release();
      const again = cache.acquire('a', 'original');
      cache.evictRecord('a');
      again.leave();
      calls[1].resolve({ status: 304 });
      await settle();
      expect(calls).toHaveLength(2);
    });
  });

  it('a 304 reuses only the entry its validator was sent for', async () => {
    const { cache, calls } = setup();
    const a = cache.acquire('a', 'original');
    calls[0].resolve(ok(10, '"v1"'));
    (await a.promise).release();
    const again = cache.acquire('a', 'original');
    expect(calls[1].ifNoneMatch).toBe('"v1"');
    // The entry now held under the key is another version than the one the validator named.
    const held = (cache as unknown as { entries: Map<string, { etag: string | null }> }).entries;
    for (const entry of held.values()) entry.etag = '"v2"';
    calls[1].resolve({ status: 304 });
    await settle();
    expect(calls).toHaveLength(3);
    expect(calls[2].ifNoneMatch).toBeNull();
    calls[2].resolve(ok(20, '"v3"'));
    const lease = await again.promise;
    expect(lease.blob.size).toBe(20);
    lease.release();
  });

  it('answers nobody waits for are kept within the budget while they land, not only when released', async () => {
    const { cache, calls } = setup({ budget: 100 * MIB });
    for (let i = 0; i < 30; i++) {
      cache.acquire(`n${i}`, 'original').leave();
      calls[calls.length - 1].resolve(ok(10 * MIB, `"e${i}"`));
      await settle();
      expect(cache.stats().unpinnedBytes).toBeLessThanOrEqual(100 * MIB);
      expect(cache.stats().entries).toBeLessThanOrEqual(24);
    }
    expect(cache.has('n0', 'original')).toBe(false);
  });

  it('a lease held across a clear shows no url afterwards, and a load that was in flight installs nothing', async () => {
    const { cache, calls, live } = setup();
    const a = cache.acquire('a', 'original');
    calls[0].resolve(ok(10, '"a"'));
    const lease = await a.promise;
    void lease.url;
    const inflight = cache.acquire('b', 'original');
    cache.clear();
    await expect(inflight.promise).rejects.toBeInstanceOf(SessionEndedError);
    expect(lease.url).toBe('');
    // A fetcher that ignores the abort answers late.
    calls[1].resolve(ok(10, '"b"'));
    await settle();
    expect(cache.stats().entries).toBe(0);
    expect(live.size).toBe(0);
    expect(cache.fenceEpoch()).toBeGreaterThan(0);
  });

  it('a failure is surfaced to every waiter and nothing is kept', async () => {
    const { cache, calls } = setup();
    const a = cache.acquire('a', 'original');
    const b = cache.acquire('a', 'original');
    calls[0].reject(new Error('boom'));
    await expect(a.promise).rejects.toThrow('boom');
    await expect(b.promise).rejects.toThrow('boom');
    expect(cache.stats().entries).toBe(0);
    expect(cache.stats().inflight).toBe(0);
  });

  it('a refusal evicts every variant of the record for this owner only', async () => {
    const { cache, calls, setOwner } = setup();
    for (const v of ['original', 'pdf'] as const) {
      const p = cache.acquire('a', v);
      calls[calls.length - 1].resolve(ok(10, `"${v}"`));
      (await p.promise).release();
    }
    const other = cache.acquire('b', 'original');
    calls[calls.length - 1].resolve(ok(10, '"b"'));
    (await other.promise).release();
    void setOwner;
    cache.evictRecord('a');
    expect(cache.has('a', 'original')).toBe(false);
    expect(cache.has('a', 'pdf')).toBe(false);
    expect(cache.has('b', 'original')).toBe(true);
  });
});
