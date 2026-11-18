/**
 * Loading a large PDF by byte ranges, for pdf.js (`getDocument({ range })`).
 *
 * pdf.js asks for the byte ranges a page needs; each is one `Range` request to the stream route, bound to the
 * version the first answer named (`If-Range`). Nothing else is fetched: the viewer is told to turn off its
 * background fetch and its streaming, so a 200 MB document costs the pages the user looks at.
 *
 * pdf.js 4.x waits for exactly one `onDataRange(begin, chunk)` per `requestDataRange(begin, end)` and asserts on any
 * further call for a `begin` it no longer waits on. A request larger than the span the server answers is therefore
 * fetched in spans and handed over once, whole.
 *
 * Whole-body mode is the way out: a server that does not answer 206 (an old server, a connector record, a flag
 * that is off), a range it refuses (416), a document that changed (the answer to `If-Range` is a 200) or a first
 * request that fails (a 5xx, the network, a timeout) ends range mode, and the owner opens the whole record through
 * the content cache instead. Only a failure of that whole-file load is shown as an error.
 */
import type { PDFDataRangeTransport } from 'pdfjs-dist';
import { apiClient } from '@/lib/api/axios-instance';
import {
  RECORD_RANGE_CHUNK_BYTES,
  RECORD_RANGE_MAX_SPAN_BYTES,
  RECORD_RANGE_THRESHOLD_BYTES,
} from './range-options';

export {
  RECORD_RANGE_CHUNK_BYTES,
  RECORD_RANGE_LOADER_OPTIONS,
  RECORD_RANGE_MAX_SPAN_BYTES,
  RECORD_RANGE_THRESHOLD_BYTES,
} from './range-options';

const STREAM_PATH = '/api/v1/knowledgeBase/stream/record';

export type RangeAnswer =
  | { status: 206; bytes: Uint8Array; total: number | null; etag: string | null }
  /** The server did not answer with a partial response: the whole body, a refusal of the range, or a changed document. */
  | { status: 200 | 416 };

export interface RangeRequest {
  recordId: string;
  /** Inclusive first and last byte. */
  first: number;
  last: number;
  /** The strong tag of the version this document is read at; null on the first request. */
  ifRange: string | null;
  signal: AbortSignal;
}

export type RangeFetcher = (request: RangeRequest) => Promise<RangeAnswer>;

/** One `Range` request through `apiClient` (base URL, token refresh, request ids and timeouts apply). */
export const fetchRecordRange: RangeFetcher = async ({ recordId, first, last, ifRange, signal }) => {
  const controller = new AbortController();
  signal.addEventListener('abort', () => controller.abort(), { once: true });
  let wholeBody = false;
  const response = await apiClient.get<ArrayBuffer>(`${STREAM_PATH}/${encodeURIComponent(recordId)}`, {
    responseType: 'arraybuffer',
    headers: {
      Range: `bytes=${first}-${last}`,
      'X-Record-Content-Open': '1',
      ...(ifRange ? { 'If-Range': ifRange } : {}),
    },
    signal: controller.signal,
    suppressErrorToast: true,
    validateStatus: (s) => (s >= 200 && s < 300) || s === 416,
    // A 200 is the whole body: stop reading it as soon as its status is known.
    onDownloadProgress: (event) => {
      const status = (event.event as { target?: XMLHttpRequest } | undefined)?.target?.status;
      if (status === 200 && !wholeBody) {
        wholeBody = true;
        controller.abort();
      }
    },
  }).catch((error: unknown) => {
    if (wholeBody) return null;
    throw error;
  });
  if (response === null) return { status: 200 };
  if (response.status === 416) return { status: 416 };
  if (response.status !== 206) return { status: 200 };
  const contentRange = String(response.headers?.['content-range'] ?? '');
  const total = /\/(\d+)$/.exec(contentRange)?.[1];
  const etag = (response.headers?.etag as string | undefined) ?? null;
  return { status: 206, bytes: new Uint8Array(response.data), total: total ? Number(total) : null, etag };
};

export interface RecordRangeStats {
  /** Range requests made, the first included. */
  requests: number;
  /** Bytes of the document the viewer has been given. */
  viewedBytes: number;
}

export interface RecordRangeOptions {
  recordId: string;
  fetcher?: RangeFetcher;
  /** Called once when range mode ends (a 200, a 416 or a changed document); the owner opens the whole record. */
  onFallback?: (reason: 'whole-body' | 'unsatisfiable' | 'changed') => void;
  /** The viewer was given more bytes: the pinned size of a range document is what has been viewed. */
  onProgress?: (viewedBytes: number) => void;
}

export interface RecordRangeTransport extends PDFDataRangeTransport {
  readonly stats: RecordRangeStats;
  /** Stops every request. The viewer's own document is destroyed by its loader. */
  destroy(): void;
}

export type RangeOpen = { mode: 'range'; transport: RecordRangeTransport; etag: string; length: number } | { mode: 'whole' };

type TransportClass = new (length: number, initialData: Uint8Array | null) => PDFDataRangeTransport;

/** Merges `[begin, end)` into a sorted list of disjoint intervals. */
function addInterval(intervals: Array<[number, number]>, begin: number, end: number): void {
  intervals.push([begin, end]);
  intervals.sort((a, b) => a[0] - b[0]);
  const merged: Array<[number, number]> = [];
  for (const [b, e] of intervals) {
    const last = merged[merged.length - 1];
    if (last && b <= last[1]) last[1] = Math.max(last[1], e);
    else merged.push([b, e]);
  }
  intervals.splice(0, intervals.length, ...merged);
}

function concat(pieces: Uint8Array[], size: number): Uint8Array {
  const out = new Uint8Array(size);
  let at = 0;
  for (const piece of pieces) {
    out.set(piece, at);
    at += piece.length;
  }
  return out;
}

/**
 * Opens a record by ranges: asks for the first chunk and learns the document's length and version from the answer.
 * Returns whole-body mode when the server does not answer with a partial response; the caller then opens the
 * record through the content cache.
 */
export async function openRecordRange(options: RecordRangeOptions): Promise<RangeOpen> {
  const { recordId, fetcher = fetchRecordRange, onFallback, onProgress } = options;
  const controller = new AbortController();
  let first: RangeAnswer;
  try {
    first = await fetcher({ recordId, first: 0, last: RECORD_RANGE_CHUNK_BYTES - 1, ifRange: null, signal: controller.signal });
  } catch {
    // Ranges are an optimisation: whatever went wrong, the whole-file load decides whether the record opens.
    return { mode: 'whole' };
  }
  if (first.status !== 206 || first.total === null || first.etag === null || first.etag.startsWith('W/')) {
    // No usable partial answer: a weak or missing tag cannot pin later ranges to this version.
    return { mode: 'whole' };
  }
  const etag = first.etag;
  const total = first.total;
  const { PDFDataRangeTransport: Base } = (await import('pdfjs-dist')) as { PDFDataRangeTransport: TransportClass };
  const stats: RecordRangeStats = { requests: 1, viewedBytes: first.bytes.length };
  const have: Array<[number, number]> = [[0, first.bytes.length]];
  let ended = false;
  /** Requests of the viewer that holds the transport now; a remount aborts those of the viewer it follows. */
  let mount = new AbortController();
  controller.signal.addEventListener('abort', () => mount.abort(), { once: true });

  const end = (reason: 'whole-body' | 'unsatisfiable' | 'changed'): void => {
    if (ended) return;
    ended = true;
    controller.abort();
    onFallback?.(reason);
  };

  class Transport extends Base implements RecordRangeTransport {
    readonly stats = stats;

    /** One viewer holds the transport at a time: a remounted viewer replaces the listener of the one it follows. */
    addRangeListener(listener: (begin: number, chunk: Uint8Array) => void): void {
      // The answers to what the previous viewer asked for belong to a document that is gone: handing them to this
      // one would trip pdf.js, so they are dropped (and their requests aborted); this viewer asks for what it needs.
      mount.abort();
      mount = new AbortController();
      (this as unknown as { _rangeListeners: unknown[] })._rangeListeners.length = 0;
      super.addRangeListener(listener);
    }

    requestDataRange(begin: number, endExclusive: number): void {
      void this.fetchRange(begin, Math.min(endExclusive, total), mount.signal);
    }

    private async fetchRange(begin: number, endExclusive: number, signal: AbortSignal): Promise<void> {
      const pieces: Uint8Array[] = [];
      for (let from = begin; from < endExclusive && !ended && !signal.aborted; ) {
        const to = Math.min(from + RECORD_RANGE_MAX_SPAN_BYTES, endExclusive);
        stats.requests += 1;
        let answer: RangeAnswer;
        try {
          answer = await fetcher({ recordId, first: from, last: to - 1, ifRange: etag, signal });
        } catch {
          if (!ended && !signal.aborted) end('whole-body');
          return;
        }
        if (ended || signal.aborted) return;
        if (answer.status === 416) return end('unsatisfiable');
        if (answer.status !== 206) return end(answer.status === 200 ? 'changed' : 'whole-body');
        // A short answer would leave pdf.js waiting for the rest: treat it as a failure of range mode.
        if (answer.bytes.length !== to - from) return end('whole-body');
        pieces.push(answer.bytes);
        from = to;
      }
      if (ended || signal.aborted || pieces.length === 0) return;
      const whole = pieces.length === 1 ? pieces[0] : concat(pieces, endExclusive - begin);
      addInterval(have, begin, endExclusive);
      stats.viewedBytes = have.reduce((n, [b, e]) => n + (e - b), 0);
      try {
        this.onDataRange(begin, whole);
      } catch {
        end('whole-body');
        return;
      }
      onProgress?.(stats.viewedBytes);
    }

    destroy(): void {
      ended = true;
      controller.abort();
      mount.abort();
    }
  }

  const transport = new Transport(total, first.bytes);
  onProgress?.(stats.viewedBytes);
  return { mode: 'range', transport, etag, length: total };
}

/** Whether a record of this type and size is opened by ranges. */
export function shouldOpenByRange(record: { mimeType?: string | null; recordName?: string | null; sizeInBytes?: number | null }): boolean {
  const size = record.sizeInBytes ?? 0;
  if (size <= RECORD_RANGE_THRESHOLD_BYTES) return false;
  const mime = (record.mimeType ?? '').toLowerCase();
  return mime === 'application/pdf' || (!mime && /\.pdf$/i.test(record.recordName ?? ''));
}
