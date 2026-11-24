// @vitest-environment node
/**
 * RecordRangeTransport driven by the real pdf.js 4.10.38 (legacy build).
 * Attacks: requests over the 8 MiB span at odd boundaries, a short / long / refused answer mid-span,
 * the whole-file fallback, repeated range requests, remounting twice, and a remount while a range is in flight.
 */
import { describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/api/axios-instance', () => ({ apiClient: {} }));
vi.mock('pdfjs-dist', async () => await vi.importActual('pdfjs-dist/legacy/build/pdf.mjs'));

const pdfjs = (await import('pdfjs-dist/legacy/build/pdf.mjs')) as typeof import('pdfjs-dist');
const { openRecordRange, RECORD_RANGE_LOADER_OPTIONS, RECORD_RANGE_MAX_SPAN_BYTES } = await import('../pdf-range-transport');
type RangeRequest = import('../pdf-range-transport').RangeRequest;
type RangeAnswer = import('../pdf-range-transport').RangeAnswer;
type Transport = import('../pdf-range-transport').RecordRangeTransport;

const MIB = 1024 * 1024;
const SEED = 918273645;
let state = SEED;
const rnd = (n: number) => {
  state = (state * 1103515245 + 12345) % 2 ** 31;
  return state % n;
};

/**
 * A one-page PDF: an unused stream of `prePad` bytes (to put the page's objects at odd offsets), the page's content
 * stream of `contentBytes` (a few drawing ops and one long comment, so the operator list stays small), and an unused
 * stream of `postPad` bytes.
 */
function buildPdf(contentBytes: number, prePad: number, postPad: number): Uint8Array {
  const enc = new TextEncoder();
  const ops = '0 0 m 10 10 l S\n';
  const commentLen = Math.max(0, contentBytes - ops.length - 2);
  const parts: Uint8Array[] = [];
  const offsets: number[] = [];
  let pos = 0;
  const push = (s: string | Uint8Array) => {
    const b = typeof s === 'string' ? enc.encode(s) : s;
    parts.push(b);
    pos += b.length;
  };
  push('%PDF-1.4\n');
  offsets[6] = pos;
  push(`6 0 obj\n<< /Length ${prePad} >>\nstream\n`);
  push(new Uint8Array(prePad).fill(0x20));
  push('\nendstream\nendobj\n');
  const obj = (n: number, body: string) => {
    offsets[n] = pos;
    push(`${n} 0 obj\n${body}\nendobj\n`);
  };
  obj(1, '<< /Type /Catalog /Pages 2 0 R >>');
  obj(2, '<< /Type /Pages /Kids [3 0 R] /Count 1 >>');
  obj(3, '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << >> >>');
  const comment = new Uint8Array(commentLen + 2);
  comment[0] = 0x25; // %
  comment.fill(0x78, 1, commentLen + 1);
  comment[commentLen + 1] = 0x0a;
  const contentLen = ops.length + comment.length;
  offsets[4] = pos;
  push(`4 0 obj\n<< /Length ${contentLen} >>\nstream\n`);
  push(ops);
  push(comment);
  push('\nendstream\nendobj\n');
  offsets[5] = pos;
  push(`5 0 obj\n<< /Length ${postPad} >>\nstream\n`);
  push(new Uint8Array(postPad).fill(0x20));
  push('\nendstream\nendobj\n');
  const xref = pos;
  let x = 'xref\n0 7\n0000000000 65535 f \n';
  for (let i = 1; i <= 6; i++) x += `${String(offsets[i]).padStart(10, '0')} 00000 n \n`;
  push(`${x}trailer\n<< /Size 7 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`);
  const out = new Uint8Array(pos);
  let o = 0;
  for (const p of parts) {
    out.set(p, o);
    o += p.length;
  }
  return out;
}

type Fault = 'short' | 'long' | '200' | '416' | 'throw';

function serverFor(bytes: Uint8Array, opts: { delayMs?: number; faultAt?: number; fault?: Fault; begins?: Set<number> } = {}) {
  let faulted = 0;
  const requests: Array<{ first: number; last: number; ifRange: string | null }> = [];
  const fetcher = async (r: RangeRequest): Promise<RangeAnswer> => {
    requests.push({ first: r.first, last: r.last, ifRange: r.ifRange });
    const n = requests.length;
    if (opts.delayMs) await new Promise((res) => setTimeout(res, opts.delayMs));
    if (r.signal.aborted) throw new Error('aborted');
    const last = Math.min(r.last, bytes.length - 1);
    if (opts.faultAt === n || (opts.faultAt === -1 && !opts.begins?.has(r.first) && r.first !== 0 && !faulted && (faulted = r.first) > 0)) {
      if (opts.fault === 'short') return { status: 206, bytes: bytes.slice(r.first, last), total: bytes.length, etag: '"v2-qa"' };
      if (opts.fault === 'long') return { status: 206, bytes: bytes.slice(r.first, last + 2), total: bytes.length, etag: '"v2-qa"' };
      if (opts.fault === '200') return { status: 200 };
      if (opts.fault === '416') return { status: 416 };
      throw new Error('network');
    }
    return { status: 206, bytes: bytes.slice(r.first, last + 1), total: bytes.length, etag: '"v2-qa"' };
  };
  return { requests, fetcher, faulted: () => faulted };
}

function withTimeout<T>(p: Promise<T>, ms: number): Promise<T | 'timeout'> {
  return Promise.race([p, new Promise<'timeout'>((r) => setTimeout(() => r('timeout'), ms))]);
}

function load(transport: Transport, length: number) {
  return pdfjs.getDocument({
    range: transport,
    length,
    ...RECORD_RANGE_LOADER_OPTIONS,
    isEvalSupported: false,
    disableFontFace: true,
  } as Parameters<typeof pdfjs.getDocument>[0]);
}

async function renderPageOne(transport: Transport, length: number, ms = 10_000) {
  const task = load(transport, length);
  const doc = await withTimeout(task.promise, ms).catch((e) => `error:${String(e)}` as const);
  if (typeof doc === 'string') return { result: doc === 'timeout' ? 'document-timeout' : doc, task };
  const page = await withTimeout(doc.getPage(1), ms).catch((e) => `error:${String(e)}` as const);
  if (typeof page === 'string') return { result: page === 'timeout' ? 'page-timeout' : page, task };
  const ops = await withTimeout(page.getOperatorList(), ms).catch((e) => `error:${String(e)}` as const);
  return { result: ops === 'timeout' ? 'ops-timeout' : typeof ops === 'string' ? ops : 'ok', task, page };
}

function trapUnhandled() {
  const seen: unknown[] = [];
  const on = (e: unknown) => seen.push(e);
  process.on('unhandledRejection', on);
  return { seen, off: () => process.off('unhandledRejection', on) };
}

describe('spans over 8 MiB at odd boundaries (real pdf.js)', () => {
  const cases: Array<[string, number, number, number]> = [
    ['8 MiB + 1', 8 * MIB + 1, MIB + 12_345, 3 * MIB + 7],
    ['exactly 16 MiB', 16 * MIB, 2 * MIB - 1, 0],
    ['17 MiB + 3, content at the end of the file', 17 * MIB + 3, 333_333, 0],
    [`random (seed ${SEED})`, 8 * MIB + 1 + rnd(12 * MIB), 1 + rnd(5 * MIB), rnd(5 * MIB)],
  ];
  it.each(cases)('%s renders; every request is at most 8 MiB, inside the file, bound to the tag', async (_n, content, pre, post) => {
    const pdf = buildPdf(content, pre, post);
    const s = serverFor(pdf);
    const fallback = vi.fn();
    const u = trapUnhandled();
    try {
      const out = await openRecordRange({ recordId: 'r1', fetcher: s.fetcher, onFallback: fallback });
      if (out.mode !== 'range') throw new Error('range mode expected');
      const r = await renderPageOne(out.transport, out.length);
      expect(r.result).toBe('ok');
      expect(fallback).not.toHaveBeenCalled();
      expect(u.seen.map(String)).toEqual([]);
      for (const q of s.requests.slice(1)) {
        expect(q.last - q.first + 1).toBeLessThanOrEqual(RECORD_RANGE_MAX_SPAN_BYTES);
        expect(q.last).toBeLessThan(pdf.length);
        expect(q.ifRange).toBe('"v2-qa"');
      }
      expect(out.transport.stats.viewedBytes).toBeLessThanOrEqual(pdf.length);
      await r.task.destroy();
    } finally {
      u.off();
    }
  }, 60_000);
});

describe('a fault in the middle of a split request (real pdf.js)', () => {
  // request 1 is the open; the page then asks for one range > 8 MiB, split into requests 2, 3, 4...
  it.each<[Fault, string]>([
    ['short', 'whole-body'],
    ['long', 'whole-body'],
    ['200', 'changed'],
    ['416', 'unsatisfiable'],
    ['throw', 'whole-body'],
  ])('a %s answer on the second span ends range mode once (%s), hands pdf.js nothing, no unhandled rejection', async (fault, reason) => {
    const pdf = buildPdf(20 * MIB + 5, MIB + 3, MIB);
    const begins = new Set<number>();
    const asked: Array<[number, number]> = [];
    const s = serverFor(pdf, { faultAt: -1, fault, begins });
    const fallback = vi.fn();
    const u = trapUnhandled();
    try {
      const out = await openRecordRange({ recordId: 'r1', fetcher: s.fetcher, onFallback: fallback });
      if (out.mode !== 'range') throw new Error('range mode expected');
      const req = out.transport.requestDataRange.bind(out.transport);
      out.transport.requestDataRange = (b: number, e: number) => {
        begins.add(b);
        asked.push([b, e]);
        req(b, e);
      };
      const given: number[] = [];
      const orig = out.transport.onDataRange.bind(out.transport);
      (out.transport as unknown as { onDataRange: (b: number, c: Uint8Array) => void }).onDataRange = (b, c) => {
        given.push(b);
        orig(b, c);
      };
      const r = await renderPageOne(out.transport, out.length, 3_000);
      expect(r.result).not.toBe('ok');
      expect(fallback).toHaveBeenCalledTimes(1);
      expect(fallback).toHaveBeenCalledWith(reason);
      const at = s.faulted();
      expect(at).toBeGreaterThan(0);
      const victim = asked.find(([b, e]) => b < at && at < e)!;
      expect(victim, 'the faulted span belongs to a pdf.js request').toBeDefined();
      // pdf.js is given nothing of the faulted request, and none of its later spans is fetched
      expect(given).not.toContain(victim[0]);
      expect(s.requests.filter((q) => q.first > at && q.first < victim[1])).toEqual([]);
      expect(u.seen.map(String)).toEqual([]);
    } finally {
      u.off();
    }
  }, 30_000);
});

describe('repeated requests and remounts (real pdf.js)', () => {
  it('a page read twice in one document asks nothing new', async () => {
    const pdf = buildPdf(12 * MIB, 3 * MIB + 1, MIB);
    const s = serverFor(pdf);
    const out = await openRecordRange({ recordId: 'r1', fetcher: s.fetcher });
    if (out.mode !== 'range') throw new Error('range mode expected');
    const r = await renderPageOne(out.transport, out.length);
    expect(r.result).toBe('ok');
    const n = s.requests.length;
    const again = await withTimeout(r.page!.getOperatorList(), 10_000);
    expect(again).not.toBe('timeout');
    expect(s.requests.length).toBe(n);
    await r.task.destroy();
  }, 40_000);

  it('three documents in turn on one transport (remount twice) each render a page over the span', async () => {
    const pdf = buildPdf(12 * MIB + 9, MIB + 1, 2 * MIB);
    const s = serverFor(pdf);
    const fallback = vi.fn();
    const u = trapUnhandled();
    try {
      const out = await openRecordRange({ recordId: 'r1', fetcher: s.fetcher, onFallback: fallback });
      if (out.mode !== 'range') throw new Error('range mode expected');
      const counts: number[] = [];
      for (let i = 0; i < 3; i++) {
        const before = s.requests.length;
        const r = await renderPageOne(out.transport, out.length);
        expect(r.result, `document ${i + 1}`).toBe('ok');
        counts.push(s.requests.length - before);
        await r.task.destroy();
      }
      expect(fallback).not.toHaveBeenCalled();
      expect(u.seen.map(String)).toEqual([]);
    } finally {
      u.off();
    }
  }, 90_000);

  it('a viewer remounted while its first range is still in flight renders and does not fall back to the whole file', async () => {
    const pdf = buildPdf(4 * MIB, 2 * MIB + 11, 4 * MIB);
    const s = serverFor(pdf, { delayMs: 300 });
    const fallback = vi.fn();
    const u = trapUnhandled();
    try {
      const out = await openRecordRange({ recordId: 'r1', fetcher: s.fetcher, onFallback: fallback });
      if (out.mode !== 'range') throw new Error('range mode expected');
      // mount 1 starts loading; it is unmounted (StrictMode, or Retry) while its range requests are in flight
      const t1 = load(out.transport, out.length);
      void t1.promise.then((d) => d.getPage(1)).then((p) => p.getOperatorList()).catch(() => undefined);
      await new Promise((r) => setTimeout(r, 120));
      await t1.destroy();
      const r = await renderPageOne(out.transport, out.length);
      await new Promise((res) => setTimeout(res, 800));
      expect(r.result).toBe('ok');
      expect(fallback).not.toHaveBeenCalled();
      expect(u.seen.map(String)).toEqual([]);
      await r.task.destroy();
    } finally {
      u.off();
    }
  }, 40_000);
});

describe('letting go of a document whose range is unanswered (real pdf.js)', () => {
  it.each([
    ['the server never answers (page left while loading)', 'hang'],
    ['the transport is destroyed first (record page unmount)', 'destroy-first'],
    ['range mode ends on a single-span fault', 'single-fault'],
  ])('%s: the loading task destroys within 5 s', async (_n, mode) => {
    const pdf = buildPdf(12 * MIB, MIB + 3, MIB);
    const base = serverFor(pdf, mode === 'single-fault' ? { faultAt: 3, fault: '200' } : {});
    let n = 0;
    const fetcher = async (r: RangeRequest): Promise<RangeAnswer> => {
      n += 1;
      if (mode !== 'single-fault' && n >= 3) {
        return await new Promise<RangeAnswer>((_res, rej) => r.signal.addEventListener('abort', () => rej(new Error('aborted'))));
      }
      return base.fetcher(r);
    };
    const out = await openRecordRange({ recordId: 'r1', fetcher });
    if (out.mode !== 'range') throw new Error('range mode expected');
    const r = await renderPageOne(out.transport, out.length, 2_000);
    expect(r.result).not.toBe('ok');
    if (mode === 'destroy-first') out.transport.destroy();
    const destroyed = await withTimeout(r.task.destroy().then(() => 'destroyed'), 5_000);
    expect(destroyed).toBe('destroyed');
  }, 30_000);
});

describe('letting go while the page content waits on a range (real pdf.js)', () => {
  // pdf.js itself never settles `destroy()` of a task whose page waits on an unanswered range (see the control below),
  // so what the transport owes is that none of its own requests stays open once it is let go.
  it.each([
    ['its first span is never answered', 'first'],
    ['its second span is never answered', 'second'],
  ])('a page content request over the span: %s; destroying the transport aborts every request still open', async (_n, mode) => {
    const pdf = buildPdf(20 * MIB + 5, MIB + 3, MIB);
    const s = serverFor(pdf);
    const begins = new Set<number>();
    const held: AbortSignal[] = [];
    const fetcher = async (r: RangeRequest): Promise<RangeAnswer> => {
      const big = r.last - r.first + 1 === RECORD_RANGE_MAX_SPAN_BYTES;
      const hold = (mode === 'first' && big && begins.has(r.first)) || (mode !== 'first' && !begins.has(r.first) && r.first !== 0);
      if (hold) {
        held.push(r.signal);
        return await new Promise<RangeAnswer>((_res, rej) => r.signal.addEventListener('abort', () => rej(new Error('aborted'))));
      }
      return s.fetcher(r);
    };
    const out = await openRecordRange({ recordId: 'r1', fetcher });
    if (out.mode !== 'range') throw new Error('range mode expected');
    const req = out.transport.requestDataRange.bind(out.transport);
    out.transport.requestDataRange = (b: number, e: number) => {
      begins.add(b);
      req(b, e);
    };
    const r = await renderPageOne(out.transport, out.length, 2_000);
    expect(r.result).not.toBe('ok');
    expect(held.length).toBeGreaterThan(0);
    expect(held.every((sig) => !sig.aborted)).toBe(true);
    out.transport.destroy();
    expect(held.every((sig) => sig.aborted)).toBe(true);
  }, 30_000);
});

describe('control: a bare pdf.js PDFDataRangeTransport (no product code)', () => {
  it('destroy while the page content waits on an unanswered range', async () => {
    const pdf = buildPdf(20 * MIB + 5, MIB + 3, MIB);
    let held = false;
    class Bare extends (pdfjs.PDFDataRangeTransport as unknown as new (l: number, d: Uint8Array) => {
      onDataRange(b: number, c: Uint8Array): void;
    }) {
      requestDataRange(b: number, e: number) {
        if (e - b > 4 * MIB && !held) {
          held = true;
          return;
        }
        setTimeout(() => this.onDataRange(b, pdf.slice(b, e)), 0);
      }
    }
    const t = new Bare(pdf.length, pdf.slice(0, MIB));
    const r = await renderPageOne(t as unknown as Transport, pdf.length, 2_000);
    expect(r.result).toBe('ops-timeout');
  }, 30_000);
});
