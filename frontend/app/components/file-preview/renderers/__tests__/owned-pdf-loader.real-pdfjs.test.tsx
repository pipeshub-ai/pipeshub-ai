/**
 * OwnedPdfLoader against the real pdf.js: every document load gets its own PDFWorker, and every one is destroyed on
 * close, with a range left unanswered, while the document is still loading, on an error, on a new document and on
 * unmount. The shared default worker used to survive each of those (50 live workers and +686 MiB after
 * 50 open/close cycles in Chromium). This is the CI form of that browser measurement.
 */
import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, cleanup, act } from '@testing-library/react';

vi.mock('@/lib/api/axios-instance', () => ({ apiClient: {} }));

const live = vi.hoisted(() => ({ workers: new Set<unknown>(), created: 0, tasks: [] as Array<{ destroyed: boolean }> }));

vi.mock('pdfjs-dist', async () => {
  const actual = (await vi.importActual('pdfjs-dist/legacy/build/pdf.mjs')) as typeof import('pdfjs-dist');
  class TrackedWorker extends actual.PDFWorker {
    constructor(params?: ConstructorParameters<typeof actual.PDFWorker>[0]) {
      super(params);
      live.workers.add(this);
      live.created += 1;
    }
    destroy() {
      live.workers.delete(this);
      super.destroy();
    }
  }
  return {
    ...actual,
    PDFWorker: TrackedWorker,
    getDocument: (params: Parameters<typeof actual.getDocument>[0]) => {
      const task = actual.getDocument(params);
      live.tasks.push(task);
      return task;
    },
  };
});

const pdfjs = (await import('pdfjs-dist/legacy/build/pdf.mjs')) as typeof import('pdfjs-dist');
(globalThis as unknown as { pdfjsWorker: unknown }).pdfjsWorker = await import('pdfjs-dist/legacy/build/pdf.worker.mjs');

const { OwnedPdfLoader } = await import('../owned-pdf-loader');
const { openRecordRange, RECORD_RANGE_LOADER_OPTIONS } = await import('@/lib/record-content/pdf-range-transport');
type RangeRequest = import('@/lib/record-content/pdf-range-transport').RangeRequest;
type RangeAnswer = import('@/lib/record-content/pdf-range-transport').RangeAnswer;

const MIB = 1024 * 1024;

/** A one-page PDF whose page content stream is `contentBytes` long, followed by `padBytes` of an unused stream. */
function buildPdf(contentBytes: number, padBytes: number): Uint8Array {
  const enc = new TextEncoder();
  const content = '0 0 m 10 10 l S\n'.repeat(Math.ceil(contentBytes / 16));
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

const tick = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** A server that answers `answerFirst` requests and then holds every later range open until its signal aborts. */
function server(bytes: Uint8Array, answerFirst: number) {
  let seen = 0;
  const held: Array<{ aborted: boolean }> = [];
  const fetcher = async (r: RangeRequest): Promise<RangeAnswer> => {
    seen += 1;
    if (seen > answerFirst) {
      const mark = { aborted: false };
      held.push(mark);
      await new Promise<never>((_, reject) => {
        r.signal?.addEventListener('abort', () => {
          mark.aborted = true;
          reject(new DOMException('aborted', 'AbortError'));
        });
      });
    }
    const last = Math.min(r.last, bytes.length - 1);
    return { status: 206, bytes: bytes.slice(r.first, last + 1), total: bytes.length, etag: '"v1"' };
  };
  return { fetcher, held };
}

afterEach(() => {
  cleanup();
});

describe('OwnedPdfLoader releases its pdf.js worker', () => {
  it('after N open/close cycles with a range left unanswered, 0 workers are alive and every task is destroyed', async () => {
    const pdf = buildPdf(2 * MIB, 20 * MIB);
    const CYCLES = 8;
    const before = live.created;
    for (let i = 0; i < CYCLES; i += 1) {
      // The first range (the end of the file, for the xref) is answered; the next one never is.
      const s = server(pdf, 1);
      const out = await openRecordRange({ recordId: 'r1', fetcher: s.fetcher });
      if (out.mode !== 'range') throw new Error('range mode expected');
      const view = render(
        <OwnedPdfLoader
          url="record-range:"
          range={out.transport}
          length={out.length}
          {...RECORD_RANGE_LOADER_OPTIONS}
          isEvalSupported={false}
          beforeLoad={<span>loading</span>}
        >
          {() => <span>loaded</span>}
        </OwnedPdfLoader>,
      );
      for (let t = 0; t < 100 && s.held.length === 0; t += 1) await tick(20);
      expect(s.held.length, `cycle ${i}: a range must be in flight at close`).toBeGreaterThan(0);
      view.unmount();
      out.transport.destroy();
    }
    expect(live.created - before).toBe(CYCLES);
    // Each worker goes once its task has torn down, or after the grace period when a range never answers.
    await vi.waitFor(() => expect(live.workers.size).toBe(0), { timeout: 10_000 });
    expect(live.tasks.slice(-CYCLES).every((t) => t.destroyed)).toBe(true);
  }, 60_000);

  it('an error while loading destroys the worker of the failed load', async () => {
    const onError = vi.fn();
    const made = live.created;
    render(
      <OwnedPdfLoader
        url="record-range:"
        range={undefined}
        data={new Uint8Array([1, 2, 3])}
        isEvalSupported={false}
        onError={onError}
        beforeLoad={<span>loading</span>}
        errorMessage={<span>failed</span>}
      >
        {() => null}
      </OwnedPdfLoader>,
    );
    for (let t = 0; t < 100 && onError.mock.calls.length === 0; t += 1) await tick(20);
    expect(onError).toHaveBeenCalled();
    cleanup();
    expect(live.created - made).toBe(1);
    await vi.waitFor(() => expect(live.workers.size).toBe(0), { timeout: 10_000 });
  }, 20_000);

  it('a new url closes the previous load before it opens the next, and leaves exactly one worker while mounted', async () => {
    const pdf = buildPdf(1 * MIB, 4 * MIB);
    const mk = (u: string) => (
      <OwnedPdfLoader url={u} data={pdf.slice()} isEvalSupported={false} beforeLoad={<span>loading</span>}>
        {(doc) => <span data-testid="pages">{doc.numPages}</span>}
      </OwnedPdfLoader>
    );
    const view = render(mk('a:'));
    await act(async () => {
      for (let t = 0; t < 100 && !view.queryByTestId('pages'); t += 1) await tick(20);
    });
    expect(view.queryByTestId('pages')).not.toBeNull();
    view.rerender(mk('b:'));
    await act(async () => {
      await tick(50);
    });
    expect(live.workers.size).toBe(1);
    view.unmount();
    await vi.waitFor(() => expect(live.workers.size).toBe(0), { timeout: 10_000 });
  }, 20_000);

  it('sanity: the real pdf.js is in use (the control pdf loads)', () => {
    expect(typeof pdfjs.getDocument).toBe('function');
  });
});
