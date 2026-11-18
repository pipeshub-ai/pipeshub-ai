// @vitest-environment node
/**
 * the range transport's request lifecycle on the real pdf.js base
 * class, driven directly (no document): remount with ranges in flight, destroy with ranges pending, a range-mode end
 * while others are pending, a request after destroy, and the first request failing in every way.
 * Every fetch the transport starts must end aborted or answered; nothing stale reaches a listener.
 */
import { describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/api/axios-instance', () => ({ apiClient: {} }));
vi.mock('pdfjs-dist', async () => await vi.importActual('pdfjs-dist/legacy/build/pdf.mjs'));

const { openRecordRange, RECORD_RANGE_CHUNK_BYTES } = await import('../pdf-range-transport');
type RangeRequest = import('../pdf-range-transport').RangeRequest;
type RangeAnswer = import('../pdf-range-transport').RangeAnswer;

const MIB = 1024 * 1024;
const TOTAL = 40 * MIB;
const SEED = Number(process.env.RANGE_LIFECYCLE_SEED ?? 2718281828);

/** A fetcher whose answers the test releases by hand. A pending fetch rejects when its signal aborts. */
function heldServer() {
  const live: Array<{ r: RangeRequest; answer: (a: RangeAnswer) => void; settled: boolean }> = [];
  const fetcher = (r: RangeRequest) =>
    new Promise<RangeAnswer>((resolve, reject) => {
      if (r.first === 0 && r.ifRange === null) {
        return resolve({ status: 206, bytes: new Uint8Array(RECORD_RANGE_CHUNK_BYTES), total: TOTAL, etag: '"v2-qa"' });
      }
      const entry = {
        r,
        settled: false,
        answer: (a: RangeAnswer) => {
          if (entry.settled) return;
          entry.settled = true;
          resolve(a);
        },
      };
      live.push(entry);
      r.signal.addEventListener('abort', () => {
        if (entry.settled) return;
        entry.settled = true;
        reject(new Error('aborted'));
      });
    });
  const ok = (e: { r: RangeRequest }): RangeAnswer => ({ status: 206, bytes: new Uint8Array(e.r.last - e.r.first + 1), total: TOTAL, etag: '"v2-qa"' });
  return { live, fetcher, ok, open: () => live.filter((e) => !e.settled && !e.r.signal.aborted) };
}
const flush = () => new Promise((r) => setTimeout(r, 0));

async function open() {
  const s = heldServer();
  const fallback = vi.fn();
  const out = await openRecordRange({ recordId: 'r1', fetcher: s.fetcher, onFallback: fallback });
  if (out.mode !== 'range') throw new Error('range mode expected');
  return { s, fallback, t: out.transport };
}

describe('the transport request lifecycle', () => {
  it('a remount with ranges in flight aborts them, drops their answers, and destroy aborts the new mount', async () => {
    const { s, fallback, t } = await open();
    const a = vi.fn();
    t.addRangeListener(a);
    t.requestDataRange(2 * MIB, 3 * MIB);
    t.requestDataRange(20 * MIB, 30 * MIB); // two spans
    await flush();
    expect(s.open().length).toBe(2);
    const b = vi.fn();
    t.addRangeListener(b); // remount
    await flush();
    expect(s.open().length, 'requests of the previous viewer still open after a remount').toBe(0);
    for (const e of s.live) e.answer(s.ok(e)); // late answers (already aborted: ignored)
    await flush();
    expect(a).not.toHaveBeenCalled();
    expect(b).not.toHaveBeenCalled();
    t.requestDataRange(5 * MIB, 6 * MIB);
    t.requestDataRange(6 * MIB, 7 * MIB);
    await flush();
    expect(s.open().length).toBe(2);
    t.destroy();
    await flush();
    expect(s.open().length, 'requests still open after destroy').toBe(0);
    expect(fallback, 'a remount or destroy is not a range-mode failure').not.toHaveBeenCalled();
    t.requestDataRange(8 * MIB, 9 * MIB);
    await flush();
    expect(s.live.length, 'a request after destroy reached the server').toBe(4);
  });

  it('an answer that lands just after its mount was aborted (the response raced the abort) is not handed to the next viewer', async () => {
    let release: ((a: RangeAnswer) => void) | null = null;
    const fetcher = (r: RangeRequest) =>
      r.first === 0
        ? Promise.resolve<RangeAnswer>({ status: 206, bytes: new Uint8Array(RECORD_RANGE_CHUNK_BYTES), total: TOTAL, etag: '"v2-qa"' })
        : new Promise<RangeAnswer>((res) => {
            release = res; // ignores the abort: the bytes were already on their way
          });
    const fallback = vi.fn();
    const out = await openRecordRange({ recordId: 'r1', fetcher, onFallback: fallback });
    if (out.mode !== 'range') throw new Error('range mode expected');
    const t = out.transport;
    const a = vi.fn();
    t.addRangeListener(a);
    t.requestDataRange(2 * MIB, 3 * MIB);
    await flush();
    const b = vi.fn();
    t.addRangeListener(b);
    release!({ status: 206, bytes: new Uint8Array(MIB), total: TOTAL, etag: '"v2-qa"' });
    await flush();
    await flush();
    expect(a).not.toHaveBeenCalled();
    expect(b, 'a range the previous viewer asked for was handed to the new one').not.toHaveBeenCalled();
    expect(fallback).not.toHaveBeenCalled();
  });

  it('a range-mode end (416) while other ranges are pending aborts them all and reports once', async () => {
    const { s, fallback, t } = await open();
    const a = vi.fn();
    t.addRangeListener(a);
    t.requestDataRange(2 * MIB, 3 * MIB);
    t.requestDataRange(3 * MIB, 4 * MIB);
    t.requestDataRange(4 * MIB, 5 * MIB);
    await flush();
    s.live[1].answer({ status: 416 });
    await flush();
    await flush();
    expect(fallback).toHaveBeenCalledTimes(1);
    expect(fallback).toHaveBeenCalledWith('unsatisfiable');
    expect(s.open().length).toBe(0);
    expect(a).not.toHaveBeenCalled();
  });

  it(`random interleavings of request / answer / remount / destroy (seed ${SEED}): nothing left open, nothing stale handed over`, async () => {
    let st = SEED;
    const rnd = (n: number) => {
      st = (st * 1103515245 + 12345) % 2 ** 31;
      return st % n;
    };
    for (let world = 0; world < 60; world++) {
      const { s, fallback, t } = await open();
      let gen = 0;
      const handed: Array<{ gen: number; begin: number }> = [];
      const asked = new Set<string>(); // `${begin}@${generation}` of every request
      const listen = (g: number) => (begin: number) => handed.push({ gen: g, begin });
      t.addRangeListener(listen(gen));
      for (let step = 0; step < 40; step++) {
        const op = rnd(10);
        if (op < 4) {
          const b = (1 + rnd(38)) * MIB;
          asked.add(`${b}@${gen}`);
          t.requestDataRange(b, b + (1 + rnd(9)) * MIB);
        } else if (op < 8) {
          const pending = s.open();
          if (pending.length) pending[rnd(pending.length)].answer(s.ok(pending[rnd(pending.length)]));
        } else if (op < 9) {
          gen += 1;
          t.addRangeListener(listen(gen));
        } else {
          const pending = s.open();
          if (pending.length) pending[rnd(pending.length)].answer({ status: 200 });
        }
        await flush();
      }
      t.destroy();
      await flush();
      expect(s.open().length, `world ${world}: open after destroy`).toBe(0);
      for (const h of handed) expect(asked.has(`${h.begin}@${h.gen}`), `world ${world}: ${h.begin} handed to viewer ${h.gen}, which never asked for it`).toBe(true);
      expect(fallback.mock.calls.length).toBeLessThanOrEqual(1);
    }
  });

  it.each([
    ['500', () => Promise.reject(Object.assign(new Error('Request failed with status code 500'), { response: { status: 500 } }))],
    ['network', () => Promise.reject(new Error('Network Error'))],
    ['timeout', () => Promise.reject(Object.assign(new Error('timeout of 30000ms exceeded'), { code: 'ECONNABORTED' }))],
    ['403', () => Promise.reject(Object.assign(new Error('Request failed with status code 403'), { response: { status: 403 } }))],
    ['sync throw', () => {
      throw new Error('sync');
    }],
  ])('the first request fails (%s): whole mode, no throw', async (_name, f) => {
    const out = await openRecordRange({ recordId: 'r1', fetcher: f as never });
    expect(out).toEqual({ mode: 'whole' });
  });
});
