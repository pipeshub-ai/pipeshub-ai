/**
 * OwnedPdfLoader contract, with pdf.js replaced by a recorder (no real worker):
 *  - the options the renderer passes (isEvalSupported:false, the range transport, length, no auto-fetch) reach getDocument;
 *  - every load gets its own worker, handed to getDocument; every exit destroys the task first, then that worker;
 *  - a render error below the loader shows the loader's error view, and the worker is still destroyed;
 *  - a load that settles after the close does not render.
 */
import React from 'react';
import { describe, it, expect, afterEach, vi, beforeEach } from 'vitest';
import { render, cleanup, act, screen } from '@testing-library/react';

const pdf = vi.hoisted(() => ({
  workers: [] as Array<{ destroyed: boolean }>,
  calls: [] as Array<Record<string, unknown>>,
  order: [] as string[],
  tasks: [] as Array<{ destroyed: boolean; settleDestroy?: () => void; resolve: (d: unknown) => void; reject: (e: unknown) => void }>,
}));

vi.mock('pdfjs-dist', () => {
  class PDFWorker {
    destroyed = false;
    constructor() {
      pdf.workers.push(this);
    }
    destroy() {
      this.destroyed = true;
      pdf.order.push('worker');
    }
  }
  return {
    GlobalWorkerOptions: { workerSrc: '' },
    PDFWorker,
    getDocument: (params: Record<string, unknown>) => {
      pdf.calls.push(params);
      let resolve!: (d: unknown) => void;
      let reject!: (e: unknown) => void;
      const promise = new Promise((res, rej) => {
        resolve = res;
        reject = rej;
      });
      const task = {
        destroyed: false,
        promise,
        resolve,
        reject,
        settleDestroy: undefined as undefined | (() => void),
        destroy() {
          task.destroyed = true;
          pdf.order.push('task');
          // pdf.js: can stay pending while a range is unanswered
          return new Promise<void>((res) => {
            task.settleDestroy = res;
          });
        },
      };
      pdf.tasks.push(task);
      return task;
    },
  };
});

import { OwnedPdfLoader, WORKER_GRACE_MS } from '@/app/components/file-preview/renderers/owned-pdf-loader';

const doc = (n: number) => ({ numPages: n });

beforeEach(() => {
  pdf.workers.length = 0;
  pdf.calls.length = 0;
  pdf.tasks.length = 0;
  pdf.order.length = 0;
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

/** Lets the closing task settle, so the worker destroy that waits on it runs. */
const settleClose = async () => {
  await act(async () => {
    pdf.tasks.forEach((t) => t.settleDestroy?.());
  });
};

describe('OwnedPdfLoader', () => {
  it('forwards the renderer options to getDocument with its own worker', () => {
    const transport = { tag: 'transport' };
    render(
      <OwnedPdfLoader url="record-range:" workerSrc="/w.mjs" isEvalSupported={false} range={transport} length={123} disableAutoFetch disableStream rangeChunkSize={1048576} beforeLoad={<span>loading</span>}>
        {() => null}
      </OwnedPdfLoader>,
    );
    expect(pdf.calls).toHaveLength(1);
    const p = pdf.calls[0];
    expect(p.isEvalSupported).toBe(false);
    expect(p.range).toBe(transport);
    expect(p.length).toBe(123);
    expect(p.disableAutoFetch).toBe(true);
    expect(p.disableStream).toBe(true);
    expect(p.worker).toBe(pdf.workers[0]);
    expect(screen.getByText('loading')).toBeTruthy();
  });

  it('a new range transport for the same url is a new document: the old task and worker go first', async () => {
    const view = render(<OwnedPdfLoader url="record-range:" range={{ n: 1 }} beforeLoad={null}>{() => null}</OwnedPdfLoader>);
    view.rerender(<OwnedPdfLoader url="record-range:" range={{ n: 2 }} beforeLoad={null}>{() => null}</OwnedPdfLoader>);
    expect(pdf.calls).toHaveLength(2);
    expect(pdf.tasks[0].destroyed).toBe(true);
    await settleClose();
    expect(pdf.workers[0].destroyed).toBe(true);
    expect(pdf.workers[1].destroyed).toBe(false);
    view.unmount();
    await settleClose();
    expect(pdf.workers.every((w) => w.destroyed)).toBe(true);
    expect(pdf.tasks.every((t) => t.destroyed)).toBe(true);
  });

  it('a re-render with the same url and transport does not reload', () => {
    const range = { n: 1 };
    const view = render(<OwnedPdfLoader url="u" range={range} isEvalSupported={false} beforeLoad={null}>{() => null}</OwnedPdfLoader>);
    for (let i = 0; i < 5; i += 1) view.rerender(<OwnedPdfLoader url="u" range={range} isEvalSupported={false} beforeLoad={null}>{() => null}</OwnedPdfLoader>);
    expect(pdf.calls).toHaveLength(1);
  });

  it('a load failure shows errorMessage with the error and calls onError; the worker is destroyed on close', async () => {
    const onError = vi.fn();
    const Err = ({ error }: { error?: Error }) => <span>failed: {error?.message}</span>;
    const view = render(<OwnedPdfLoader url="u" onError={onError} errorMessage={<Err />} beforeLoad={<span>loading</span>}>{() => null}</OwnedPdfLoader>);
    await act(async () => pdf.tasks[0].reject(new Error('Invalid PDF structure.')));
    expect(screen.getByText('failed: Invalid PDF structure.')).toBeTruthy();
    expect(onError).toHaveBeenCalledTimes(1);
    view.unmount();
    await settleClose();
    expect(pdf.workers[0].destroyed).toBe(true);
  });

  it('the task is destroyed first and awaited; the worker is destroyed only after it settles', async () => {
    const view = render(<OwnedPdfLoader url="u" beforeLoad={null}>{() => null}</OwnedPdfLoader>);
    view.unmount();
    expect(pdf.order).toEqual(['task']);
    expect(pdf.workers[0].destroyed).toBe(false);
    await settleClose();
    expect(pdf.order).toEqual(['task', 'worker']);
  });

  it('a task that never finishes closing does not keep the worker alive past the grace period', async () => {
    vi.useFakeTimers();
    const view = render(<OwnedPdfLoader url="u" beforeLoad={null}>{() => null}</OwnedPdfLoader>);
    view.unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(WORKER_GRACE_MS - 1);
    });
    expect(pdf.workers[0].destroyed).toBe(false);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2);
    });
    expect(pdf.order).toEqual(['task', 'worker']);
  });

  it('a document that resolves after the close is never rendered', async () => {
    const child = vi.fn(() => <span>doc</span>);
    const view = render(<OwnedPdfLoader url="u" beforeLoad={<span>loading</span>}>{child}</OwnedPdfLoader>);
    view.unmount();
    await act(async () => pdf.tasks[0].resolve(doc(3)));
    expect(child).not.toHaveBeenCalled();
  });

  it('a new url never hands the previous (destroyed) document to the children after its task is destroyed', async () => {
    const seen: Array<{ n: number; destroyedAtRender: boolean }> = [];
    const child = (d: { numPages: number }) => {
      seen.push({ n: d.numPages, destroyedAtRender: pdf.tasks[d.numPages === 3 ? 0 : 1]?.destroyed ?? false });
      return <span>pages {d.numPages}</span>;
    };
    const view = render(<OwnedPdfLoader url="a" beforeLoad={<span>loading</span>}>{child as never}</OwnedPdfLoader>);
    await act(async () => pdf.tasks[0].resolve(doc(3)));
    expect(screen.getByText('pages 3')).toBeTruthy();
    seen.length = 0;
    await act(async () => {
      view.rerender(<OwnedPdfLoader url="b" beforeLoad={<span>loading</span>}>{child as never}</OwnedPdfLoader>);
    });
    expect(seen.filter((s) => s.destroyedAtRender)).toEqual([]);
    expect(screen.getByText('loading')).toBeTruthy();
  });

  it('a render error below the loader shows the loader error view, reports it, and still destroys the worker', async () => {
    const Boom = () => {
      throw new Error('getPageView of undefined');
    };
    const Err = ({ error }: { error?: Error }) => <span>loader error view: {error?.message}</span>;
    const onError = vi.fn();
    const spy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const view = render(
      <OwnedPdfLoader url="u" onError={onError} beforeLoad={<span>loading</span>} errorMessage={<Err />}>
        {() => <Boom />}
      </OwnedPdfLoader>,
    );
    await act(async () => pdf.tasks[0].resolve(doc(2)));
    spy.mockRestore();
    expect(screen.getByText('loader error view: getPageView of undefined')).toBeTruthy();
    expect(onError).toHaveBeenCalledTimes(1);
    view.unmount();
    await settleClose();
    expect(pdf.workers[0].destroyed).toBe(true);
    expect(pdf.tasks[0].destroyed).toBe(true);
  });

  it('after a render error, a new url loads and renders again', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const Maybe = ({ n }: { n: number }) => {
      if (n === 2) throw new Error('boom');
      return <span>pages {n}</span>;
    };
    const el = (url: string) => (
      <OwnedPdfLoader url={url} beforeLoad={<span>loading</span>} errorMessage={<span>loader error view</span>}>
        {(d) => <Maybe n={(d as unknown as { numPages: number }).numPages} />}
      </OwnedPdfLoader>
    );
    const view = render(el('a'));
    await act(async () => pdf.tasks[0].resolve(doc(2)));
    expect(screen.getByText('loader error view')).toBeTruthy();
    view.rerender(el('b'));
    await act(async () => pdf.tasks[1].resolve(doc(3)));
    spy.mockRestore();
    expect(screen.getByText('pages 3')).toBeTruthy();
  });
});
