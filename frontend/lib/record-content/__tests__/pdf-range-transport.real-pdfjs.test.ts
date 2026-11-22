// @vitest-environment node
/**
 * RecordRangeTransport driven by the real pdf.js (not a stand-in base class).
 *
 * pdf.js resolves one range reader per `requestDataRange(begin, end)` on the FIRST `onDataRange(begin, ...)`
 * and asserts on any `onDataRange` whose begin matches no reader. A request larger than the 8 MiB span the
 * server answers is fetched in spans and must still reach pdf.js as one `onDataRange`. A remounted viewer on the
 * same transport must load as well.
 */
import { describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/api/axios-instance', () => ({ apiClient: {} }));
vi.mock('pdfjs-dist', async () => await vi.importActual('pdfjs-dist/legacy/build/pdf.mjs'));

const pdfjs = (await import('pdfjs-dist/legacy/build/pdf.mjs')) as typeof import('pdfjs-dist');
const { openRecordRange, RECORD_RANGE_LOADER_OPTIONS } = await import('../pdf-range-transport');
type RangeRequest = import('../pdf-range-transport').RangeRequest;
type RangeAnswer = import('../pdf-range-transport').RangeAnswer;

const MIB = 1024 * 1024;

/** A one-page PDF whose page content stream is `contentBytes` long, followed by `padBytes` of an unused stream. */
function buildPdf(contentBytes: number, padBytes: number): Uint8Array {
  const enc = new TextEncoder();
  const op = '0 0 m 10 10 l S\n';
  const content = op.repeat(Math.ceil(contentBytes / op.length));
  const parts: Uint8Array[] = [];
  const offsets: number[] = [];
  let pos = 0;
  const push = (s: string | Uint8Array) => {
    const b = typeof s === 'string' ? enc.encode(s) : s;
    parts.push(b);
    pos += b.length;
  };
  push('%PDF-1.4\n');
  const obj = (n: number, body: string) => {
    offsets[n] = pos;
    push(`${n} 0 obj\n${body}\nendobj\n`);
  };
  obj(1, '<< /Type /Catalog /Pages 2 0 R >>');
  obj(2, '<< /Type /Pages /Kids [3 0 R] /Count 1 >>');
  obj(3, '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << >> >>');
  offsets[4] = pos;
  push(`4 0 obj\n<< /Length ${content.length} >>\nstream\n`);
  push(content);
  push('\nendstream\nendobj\n');
  offsets[5] = pos;
  push(`5 0 obj\n<< /Length ${padBytes} >>\nstream\n`);
  push(new Uint8Array(padBytes).fill(0x20));
  push('\nendstream\nendobj\n');
  const xref = pos;
  let x = 'xref\n0 6\n0000000000 65535 f \n';
  for (let i = 1; i <= 5; i++) x += `${String(offsets[i]).padStart(10, '0')} 00000 n \n`;
  push(`${x}trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`);
  const out = new Uint8Array(pos);
  let o = 0;
  for (const p of parts) {
    out.set(p, o);
    o += p.length;
  }
  return out;
}

function serverFor(bytes: Uint8Array) {
  const requests: Array<{ first: number; last: number }> = [];
  const fetcher = async (r: RangeRequest): Promise<RangeAnswer> => {
    requests.push({ first: r.first, last: r.last });
    const last = Math.min(r.last, bytes.length - 1);
    return { status: 206, bytes: bytes.slice(r.first, last + 1), total: bytes.length, etag: '"v2-qa"' };
  };
  return { requests, fetcher };
}

function withTimeout<T>(p: Promise<T>, ms: number): Promise<T | 'timeout'> {
  return Promise.race([p, new Promise<'timeout'>((r) => setTimeout(() => r('timeout'), ms))]);
}

async function renderPageOne(transport: import('../pdf-range-transport').RecordRangeTransport, length: number) {
  const task = pdfjs.getDocument({
    range: transport,
    length,
    ...RECORD_RANGE_LOADER_OPTIONS,
    isEvalSupported: false,
    disableFontFace: true,
  } as Parameters<typeof pdfjs.getDocument>[0]);
  const doc = await withTimeout(task.promise, 10_000);
  if (doc === 'timeout') return { result: 'document-timeout' as const, task };
  const page = await withTimeout(doc.getPage(1), 10_000);
  if (page === 'timeout') return { result: 'page-timeout' as const, task };
  const ops = await withTimeout(page.getOperatorList(), 10_000);
  return { result: ops === 'timeout' ? ('ops-timeout' as const) : ('ok' as const), task, doc };
}

describe('range transport against the real pdf.js', () => {
  it('control: a page whose content stream is 6 MiB renders by ranges', async () => {
    const pdf = buildPdf(6 * MIB, 40 * MIB);
    const s = serverFor(pdf);
    const out = await openRecordRange({ recordId: 'r1', fetcher: s.fetcher });
    if (out.mode !== 'range') throw new Error('range mode expected');
    const r = await renderPageOne(out.transport, out.length);
    expect(r.result).toBe('ok');
    await r.task.destroy();
  }, 40_000);

  it('a page whose content stream is 12 MiB (one pdf.js request over the 8 MiB span) renders by ranges', async () => {
    const pdf = buildPdf(12 * MIB, 40 * MIB);
    const s = serverFor(pdf);
    const unhandled: unknown[] = [];
    const onUnhandled = (e: unknown) => unhandled.push(e);
    process.on('unhandledRejection', onUnhandled);
    try {
      const out = await openRecordRange({ recordId: 'r1', fetcher: s.fetcher });
      if (out.mode !== 'range') throw new Error('range mode expected');
      const r = await renderPageOne(out.transport, out.length);
      expect(r.result).toBe('ok');
      expect(unhandled.map(String)).toEqual([]);
      await r.task.destroy();
    } finally {
      process.off('unhandledRejection', onUnhandled);
    }
  }, 40_000);

  it('the same transport given to a second document (a remounted viewer) still renders page 1', async () => {
    const pdf = buildPdf(2 * MIB, 40 * MIB);
    const s = serverFor(pdf);
    const out = await openRecordRange({ recordId: 'r1', fetcher: s.fetcher });
    if (out.mode !== 'range') throw new Error('range mode expected');
    const first = await renderPageOne(out.transport, out.length);
    expect(first.result).toBe('ok');
    await first.task.destroy();
    const second = await renderPageOne(out.transport, out.length);
    expect(second.result).toBe('ok');
    await second.task.destroy();
  }, 60_000);
});
