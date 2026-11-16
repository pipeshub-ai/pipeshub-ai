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

type State = { doc: PDFDocumentProxy | null; error: Error | null };

export function OwnedPdfLoader({
  url,
  workerSrc,
  beforeLoad,
  errorMessage,
  onError,
  children,
  ...options
}: OwnedPdfLoaderProps) {
  const [state, setState] = useState<State>({ doc: null, error: null });
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
    setState({ doc: null, error: null });
    if (!url) return;
    if (typeof workerSrc === 'string') GlobalWorkerOptions.workerSrc = workerSrc;
    let closed = false;
    const worker = new PDFWorker();
    const task = getDocument({ ...optionsRef.current, url, worker } as Parameters<typeof getDocument>[0]);
    task.promise.then(
      (doc) => {
        if (!closed) setState({ doc, error: null });
      },
      (error: unknown) => {
        if (closed) return;
        const err = error instanceof Error ? error : new Error(String(error));
        onErrorRef.current?.(err);
        setState({ doc: null, error: err });
      },
    );
    return () => {
      closed = true;
      // `destroy()` can stay pending while a page waits on an unanswered range (pdf.js itself), so it is not
      // awaited; the worker is terminated at once, which is what frees the thread.
      task.destroy().catch(() => undefined);
      worker.destroy();
    };
  }, [url, range, workerSrc]);

  if (state.error) {
    return errorMessage ? React.cloneElement(errorMessage as React.ReactElement<{ error?: Error }>, { error: state.error }) : null;
  }
  return <>{state.doc ? children(state.doc) : beforeLoad}</>;
}
