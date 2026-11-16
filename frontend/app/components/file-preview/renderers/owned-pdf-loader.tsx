'use client';

import React, { useEffect, useState } from 'react';
import { GlobalWorkerOptions, PDFWorker, getDocument } from 'pdfjs-dist';
import type { PDFDocumentProxy } from 'pdfjs-dist';

/**
 * Loads one PDF and owns everything pdf.js starts for it: the `PDFWorker` and the loading task.
 *
 * react-pdf-highlighter's `PdfLoader` leaves the default shared worker running when a document is closed while a
 * range is still unanswered (and never destroys a task that has not finished loading), so every such close
 * strands a Web Worker of about 13 MiB. This loader creates a worker per document load and destroys it, and the
 * task, on every exit: close, error, a new document, and unmount. Props other than the ones below go to pdf.js's
 * `getDocument` (as `PdfLoader` did), so a caller can pass the same options.
 */
export interface OwnedPdfLoaderProps {
  /** Loads nothing while empty; pdf.js ignores it when a range transport is given. */
  url: string;
  workerSrc?: string;
  beforeLoad: React.ReactNode;
  errorMessage?: React.ReactElement;
  onError?: (error: Error) => void;
  children: (pdfDocument: PDFDocumentProxy) => React.ReactNode;
  [option: string]: unknown;
}

/** How long a closing task may take to tear down before its worker is terminated anyway. */
export const WORKER_GRACE_MS = 1000;

/** Catches a render error below the loader and shows the loader's own error view, as the library loader did. */
class ChildrenBoundary extends React.Component<
  { errorMessage?: React.ReactElement; onError?: (error: Error) => void; children: React.ReactNode },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error) {
    this.props.onError?.(error);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    const { errorMessage } = this.props;
    return errorMessage ? React.cloneElement(errorMessage as React.ReactElement<{ error?: Error }>, { error }) : null;
  }
}

type State = { doc: PDFDocumentProxy | null; error: Error | null; loadId: number };

export function OwnedPdfLoader({
  url,
  workerSrc,
  beforeLoad,
  errorMessage,
  onError,
  children,
  ...options
}: OwnedPdfLoaderProps) {
  const [state, setState] = useState<State>({ doc: null, error: null, loadId: 0 });
  // The options object is rebuilt every render; a new document is a new url or a new transport.
  const range = options.range;
  const optionsRef = React.useRef(options);
  const onErrorRef = React.useRef(onError);
  // Declared before the loading effect, so it runs first and the load reads the current values.
  useEffect(() => {
    optionsRef.current = options;
    onErrorRef.current = onError;
  });

  useEffect(() => {
    setState((prev) => ({ doc: null, error: null, loadId: prev.loadId + 1 }));
    if (!url) return;
    if (typeof workerSrc === 'string') GlobalWorkerOptions.workerSrc = workerSrc;
    let closed = false;
    const worker = new PDFWorker();
    const task = getDocument({ ...optionsRef.current, url, worker } as Parameters<typeof getDocument>[0]);
    task.promise.then(
      (doc) => {
        if (!closed) setState((prev) => ({ doc, error: null, loadId: prev.loadId }));
      },
      (error: unknown) => {
        if (closed) return;
        const err = error instanceof Error ? error : new Error(String(error));
        onErrorRef.current?.(err);
        setState((prev) => ({ doc: null, error: err, loadId: prev.loadId }));
      },
    );
    return () => {
      closed = true;
      // The task goes first and is awaited: pdf.js cancels its own download only once the worker has answered the
      // task's teardown, so a worker destroyed first would leave the request open. `destroy()` can stay pending
      // while a page waits on an unanswered range, so the worker is destroyed after a short grace period at most.
      let timer: ReturnType<typeof setTimeout> | undefined;
      const grace = new Promise<void>((resolve) => {
        timer = setTimeout(resolve, WORKER_GRACE_MS);
      });
      Promise.race([task.destroy().catch(() => undefined), grace]).finally(() => {
        clearTimeout(timer);
        worker.destroy();
      });
    };
  }, [url, range, workerSrc]);

  if (state.error) {
    return errorMessage ? React.cloneElement(errorMessage as React.ReactElement<{ error?: Error }>, { error: state.error }) : null;
  }
  if (!state.doc) return <>{beforeLoad}</>;
  return (
    <ChildrenBoundary key={state.loadId} errorMessage={errorMessage} onError={onError}>
      {children(state.doc)}
    </ChildrenBoundary>
  );
}
