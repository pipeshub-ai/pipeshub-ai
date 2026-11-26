import { beforeEach, describe, expect, it, vi } from 'vitest';
import { installMemoryStorage, jwtExpiringIn } from '@/lib/api/__tests__/sse-response';

installMemoryStorage();

vi.mock('@/config', async () => {
  const auth = await vi.importActual<typeof import('@/lib/store/auth-store')>('@/lib/store/auth-store');
  return { useAuthStore: auth.useAuthStore, logoutAndRedirect: vi.fn() };
});

// A stand-in for pdf.js's base class: it records what the viewer is given.
vi.mock('pdfjs-dist', () => ({
  PDFDataRangeTransport: class {
    length: number;
    initialData: Uint8Array | null;
    given: Array<{ begin: number; size: number }> = [];
    _rangeListeners: unknown[] = [];
    addRangeListener(l: unknown) {
      this._rangeListeners.push(l);
    }
    constructor(length: number, initialData: Uint8Array | null) {
      this.length = length;
      this.initialData = initialData;
    }
    onDataRange(begin: number, chunk: Uint8Array | null) {
      this.given.push({ begin, size: chunk?.length ?? 0 });
    }
  },
}));

const { useAuthStore } = await import('@/lib/store/auth-store');
const { fakeApi } = await import('@/lib/api/__tests__/fake-api');
const {
  fetchRecordRange,
  openRecordRange,
  shouldOpenByRange,
  RECORD_RANGE_CHUNK_BYTES,
  RECORD_RANGE_MAX_SPAN_BYTES,
  RECORD_RANGE_LOADER_OPTIONS,
  RECORD_RANGE_THRESHOLD_BYTES,
} = await import('../pdf-range-transport');
type RangeRequest = import('../pdf-range-transport').RangeRequest;
type RangeAnswer = import('../pdf-range-transport').RangeAnswer;

const MIB = 1024 * 1024;
const TOTAL = 200 * MIB;
const ETAG = '"v2-abc"';

/** A server that holds a 200 MB document and answers ranges as the stream route does. */
function server(over: { etag?: string; first?: RangeAnswer; later?: (r: RangeRequest) => RangeAnswer | null } = {}) {
  const requests: RangeRequest[] = [];
  const fetcher = async (r: RangeRequest): Promise<RangeAnswer> => {
    requests.push(r);
    if (requests.length === 1 && over.first) return over.first;
    const custom = requests.length > 1 ? over.later?.(r) : null;
    if (custom) return custom;
    return { status: 206, bytes: new Uint8Array(r.last - r.first + 1), total: TOTAL, etag: over.etag ?? ETAG };
  };
  return { requests, fetcher };
}

const settle = () => new Promise((r) => setTimeout(r, 0));

describe('RecordRangeTransport', () => {
  it('opens with one request for the first 1 MiB and nothing else', async () => {
    const s = server();
    const out = await openRecordRange({ recordId: 'r1', fetcher: s.fetcher });
    expect(out.mode).toBe('range');
    if (out.mode !== 'range') return;
    expect(s.requests).toHaveLength(1);
    expect([s.requests[0].first, s.requests[0].last, s.requests[0].ifRange]).toEqual([0, RECORD_RANGE_CHUNK_BYTES - 1, null]);
    expect(out.length).toBe(TOTAL);
    expect((out.transport as unknown as { initialData: Uint8Array }).initialData.length).toBe(RECORD_RANGE_CHUNK_BYTES);
    await settle();
    expect(s.requests).toHaveLength(1); // no background fetch
    expect(out.transport.stats.viewedBytes).toBe(RECORD_RANGE_CHUNK_BYTES);
  });

  it('answers a viewer request with one range bound to the version', async () => {
    const s = server();
    const out = await openRecordRange({ recordId: 'r1', fetcher: s.fetcher });
    if (out.mode !== 'range') throw new Error('range mode expected');
    out.transport.requestDataRange(50 * MIB, 51 * MIB);
    await settle();
    expect(s.requests).toHaveLength(2);
    expect(s.requests[1]).toMatchObject({ first: 50 * MIB, last: 51 * MIB - 1, ifRange: ETAG });
    expect((out.transport as unknown as { given: unknown[] }).given).toEqual([{ begin: 50 * MIB, size: MIB }]);
    // a range asked for again is answered again: pdf.js waits for one answer per request
    out.transport.requestDataRange(50 * MIB, 51 * MIB);
    await settle();
    expect(s.requests).toHaveLength(3);
    expect((out.transport as unknown as { given: unknown[] }).given).toHaveLength(2);
  });

  it('splits a viewer request larger than the server serves into pieces of at most 8 MiB', async () => {
    const s = server();
    const out = await openRecordRange({ recordId: 'r1', fetcher: s.fetcher });
    if (out.mode !== 'range') throw new Error('range mode expected');
    out.transport.requestDataRange(10 * MIB, 30 * MIB);
    await settle();
    const spans = s.requests.slice(1).map((r) => r.last - r.first + 1);
    expect(spans).toEqual([RECORD_RANGE_MAX_SPAN_BYTES, RECORD_RANGE_MAX_SPAN_BYTES, 4 * MIB]);
    expect(spans.every((n) => n <= RECORD_RANGE_MAX_SPAN_BYTES)).toBe(true);
    // the viewer is given the whole range once, as pdf.js requires
    expect((out.transport as unknown as { given: unknown[] }).given).toEqual([{ begin: 10 * MIB, size: 20 * MIB }]);
  });

  it('a short answer ends range mode instead of leaving the viewer waiting', async () => {
    const fallback = vi.fn();
    const out = await openRecordRange({
      recordId: 'r1',
      onFallback: fallback,
      fetcher: async (r) => ({
        status: 206,
        bytes: new Uint8Array(r.first === 0 ? r.last + 1 : 10),
        total: TOTAL,
        etag: ETAG,
      }),
    });
    if (out.mode !== 'range') throw new Error('range mode expected');
    out.transport.requestDataRange(20 * MIB, 21 * MIB);
    await settle();
    expect(fallback).toHaveBeenCalledWith('whole-body');
    expect((out.transport as unknown as { given: unknown[] }).given).toEqual([]);
  });

  it.each([
    ['a 200 (the server sent the whole body)', { status: 200 } as RangeAnswer, 'whole-body'],
    ['a weak tag', { status: 206, bytes: new Uint8Array(4), total: TOTAL, etag: 'W/"x"' } as RangeAnswer, 'whole-body'],
    ['a 416', { status: 416 } as RangeAnswer, 'whole-body'],
    ['no tag', { status: 206, bytes: new Uint8Array(4), total: TOTAL, etag: null } as RangeAnswer, 'whole-body'],
  ])('the first answer is %s: whole-body mode, no transport', async (_name, first, _reason) => {
    const s = server({ first });
    const out = await openRecordRange({ recordId: 'r1', fetcher: s.fetcher });
    expect(out.mode).toBe('whole');
    expect(s.requests).toHaveLength(1);
  });

  it.each([
    ['an If-Range mismatch (a 200)', { status: 200 } as RangeAnswer, 'changed'],
    ['a refused range (416)', { status: 416 } as RangeAnswer, 'unsatisfiable'],
  ])('%s later ends range mode once and asks for no more', async (_name, answer, reason) => {
    const s = server({ later: () => answer });
    const fallback = vi.fn();
    const out = await openRecordRange({ recordId: 'r1', fetcher: s.fetcher, onFallback: fallback });
    if (out.mode !== 'range') throw new Error('range mode expected');
    out.transport.requestDataRange(20 * MIB, 21 * MIB);
    await settle();
    out.transport.requestDataRange(30 * MIB, 31 * MIB);
    await settle();
    expect(fallback).toHaveBeenCalledTimes(1);
    expect(fallback).toHaveBeenCalledWith(reason);
    expect(s.requests).toHaveLength(2);
  });

  it('a transport failure ends range mode as well', async () => {
    let n = 0;
    const fallback = vi.fn();
    const out = await openRecordRange({
      recordId: 'r1',
      onFallback: fallback,
      fetcher: async (r) => {
        if (++n > 1) throw new Error('network');
        return { status: 206, bytes: new Uint8Array(r.last - r.first + 1), total: TOTAL, etag: ETAG };
      },
    });
    if (out.mode !== 'range') throw new Error('range mode expected');
    out.transport.requestDataRange(20 * MIB, 21 * MIB);
    await settle();
    expect(fallback).toHaveBeenCalledWith('whole-body');
  });

  it('a second viewer on the transport replaces the first one\'s listener', async () => {
    const s = server();
    const out = await openRecordRange({ recordId: 'r1', fetcher: s.fetcher });
    if (out.mode !== 'range') throw new Error('range mode expected');
    const t = out.transport as unknown as { _rangeListeners: unknown[]; addRangeListener(l: unknown): void };
    t.addRangeListener(() => {});
    t.addRangeListener(() => {});
    expect(t._rangeListeners).toHaveLength(1);
  });

  it.each([
    ['a 5xx', Object.assign(new Error('Request failed with status code 500'), { statusCode: 500 })],
    ['a network error', new Error('Network Error')],
    ['a timeout', Object.assign(new Error('timeout of 30000ms exceeded'), { code: 'ECONNABORTED' })],
  ])('%s on the first request opens the whole file instead of failing', async (_name, failure) => {
    const out = await openRecordRange({
      recordId: 'r1',
      fetcher: async () => {
        throw failure;
      },
    });
    expect(out).toEqual({ mode: 'whole' });
  });

  it('a viewer that throws when given bytes ends range mode instead of leaving the page waiting', async () => {
    const s = server();
    const fallback = vi.fn();
    const out = await openRecordRange({ recordId: 'r1', fetcher: s.fetcher, onFallback: fallback });
    if (out.mode !== 'range') throw new Error('range mode expected');
    (out.transport as unknown as { onDataRange: () => void }).onDataRange = () => {
      throw new Error('pdf.js: unexpected range');
    };
    out.transport.requestDataRange(20 * MIB, 21 * MIB);
    await settle();
    expect(fallback).toHaveBeenCalledWith('whole-body');
  });

  it('a remount with a range in flight drops its answer and does not leave range mode', async () => {
    let release: (() => void) | undefined;
    const fallback = vi.fn();
    let n = 0;
    const signals: AbortSignal[] = [];
    const out = await openRecordRange({
      recordId: 'r1',
      onFallback: fallback,
      fetcher: async (r) => {
        signals.push(r.signal);
        if (++n === 2) await new Promise<void>((res) => (release = res));
        if (r.signal.aborted) throw new Error('aborted');
        return { status: 206, bytes: new Uint8Array(r.last - r.first + 1), total: TOTAL, etag: ETAG };
      },
    });
    if (out.mode !== 'range') throw new Error('range mode expected');
    const t = out.transport as unknown as { given: unknown[]; addRangeListener(l: unknown): void };
    t.addRangeListener(() => {});
    out.transport.requestDataRange(20 * MIB, 21 * MIB);
    await settle();
    t.addRangeListener(() => {});
    expect(signals[1].aborted).toBe(true);
    release?.();
    await settle();
    expect(t.given).toEqual([]);
    expect(fallback).not.toHaveBeenCalled();
    // The new viewer's own request is answered.
    out.transport.requestDataRange(30 * MIB, 31 * MIB);
    await settle();
    expect(t.given).toEqual([{ begin: 30 * MIB, size: MIB }]);
  });

  it('destroy aborts a range request that is waiting for an answer', async () => {
    let seen: AbortSignal | undefined;
    let n = 0;
    const out = await openRecordRange({
      recordId: 'r1',
      fetcher: async (r) => {
        if (++n === 2) {
          seen = r.signal;
          return await new Promise<RangeAnswer>(() => undefined);
        }
        return { status: 206, bytes: new Uint8Array(r.last - r.first + 1), total: TOTAL, etag: ETAG };
      },
    });
    if (out.mode !== 'range') throw new Error('range mode expected');
    out.transport.requestDataRange(20 * MIB, 21 * MIB);
    await settle();
    expect(seen?.aborted).toBe(false);
    out.transport.destroy();
    expect(seen?.aborted).toBe(true);
  });

  it('destroy stops later requests', async () => {
    const s = server();
    const out = await openRecordRange({ recordId: 'r1', fetcher: s.fetcher });
    if (out.mode !== 'range') throw new Error('range mode expected');
    out.transport.destroy();
    out.transport.requestDataRange(20 * MIB, 21 * MIB);
    await settle();
    expect(s.requests).toHaveLength(1);
    expect(s.requests[0].signal.aborted).toBe(true);
  });
});

describe('when a record is opened by range', () => {
  it('only a PDF over 32 MiB is', () => {
    const big = 40 * MIB;
    expect(shouldOpenByRange({ mimeType: 'application/pdf', sizeInBytes: big })).toBe(true);
    expect(shouldOpenByRange({ recordName: 'a.PDF', sizeInBytes: big })).toBe(true);
    expect(shouldOpenByRange({ mimeType: 'application/pdf', sizeInBytes: RECORD_RANGE_THRESHOLD_BYTES })).toBe(false);
    expect(shouldOpenByRange({ mimeType: 'application/pdf' })).toBe(false);
    expect(shouldOpenByRange({ mimeType: 'image/png', sizeInBytes: big })).toBe(false);
  });

  it('asks pdf.js for no background fetch, no streaming and 1 MiB chunks', () => {
    expect(RECORD_RANGE_LOADER_OPTIONS).toEqual({ disableAutoFetch: true, disableStream: true, rangeChunkSize: MIB });
  });
});

describe('fetchRecordRange (through apiClient)', () => {
  beforeEach(() => {
    useAuthStore.setState({ accessToken: jwtExpiringIn(3600, { orgId: 'o1', userId: 'u1' }), refreshToken: 'r' });
  });
  const path = '/api/v1/knowledgeBase/stream/record/r1';
  const request = (over: Partial<RangeRequest> = {}): RangeRequest => ({
    recordId: 'r1',
    first: 0,
    last: 1023,
    ifRange: null,
    signal: new AbortController().signal,
    ...over,
  });

  it('sends Range, If-Range and the content-open header, and reads the length and tag of a 206', async () => {
    const api = fakeApi({
      [`GET ${path}`]: {
        status: 206,
        data: new ArrayBuffer(1024),
        headers: { 'content-range': `bytes 0-1023/${TOTAL}`, etag: ETAG },
      },
    });
    const out = await fetchRecordRange(request({ ifRange: ETAG }));
    expect(out).toMatchObject({ status: 206, total: TOTAL, etag: ETAG });
    const headers = api.sent[0].config.headers;
    expect([headers['Range'], headers['If-Range'], headers['X-Record-Content-Open']]).toEqual(['bytes=0-1023', ETAG, '1']);
  });

  it('a 416 is an answer, not a failure', async () => {
    fakeApi({ [`GET ${path}`]: { status: 416, headers: { 'content-range': `bytes */${TOTAL}` } } });
    expect(await fetchRecordRange(request())).toEqual({ status: 416 });
  });

  it('a 200 is reported as the whole body', async () => {
    fakeApi({ [`GET ${path}`]: { status: 200, data: new ArrayBuffer(8) } });
    expect(await fetchRecordRange(request())).toEqual({ status: 200 });
  });

  it('a failure is an error, not an answer', async () => {
    fakeApi({ [`GET ${path}`]: { status: 500 } });
    await expect(fetchRecordRange(request())).rejects.toBeTruthy();
  });
});
