/**
 * The open path end to end, through the real details wrapper, the real cache and `apiClient` over a
 * fake backend: grants, refusals, and the retention switch.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { installMemoryStorage, jwtExpiringIn } from '@/lib/api/__tests__/sse-response';

installMemoryStorage();

vi.mock('@/config', async () => {
  const auth = await vi.importActual<typeof import('@/lib/store/auth-store')>('@/lib/store/auth-store');
  return { useAuthStore: auth.useAuthStore, logoutAndRedirect: vi.fn() };
});

const { useAuthStore } = await import('@/lib/store/auth-store');
const { useFeatureFlagsStore } = await import('@/lib/store/feature-flags-store');
const { fakeApi } = await import('@/lib/api/__tests__/fake-api');
const { KnowledgeBaseApi } = await import('@/app/(main)/knowledge-base/api');
const { openRecordContent, clearRecordContent } = await import('../index');
const { getRecordContentCache } = await import('../cache');

const KB = '/api/v1/knowledgeBase';
const pdf = () => new Blob(['%PDF'], { type: 'application/pdf' });

beforeEach(() => {
  useAuthStore.setState({ accessToken: jwtExpiringIn(3600, { orgId: 'o1', userId: 'u1' }), refreshToken: 'r' });
  useFeatureFlagsStore.setState({ flags: null });
  let n = 0;
  URL.createObjectURL = vi.fn(() => `blob:o/${++n}`);
  URL.revokeObjectURL = vi.fn();
  clearRecordContent();
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

const connectorDetails = (rev: string) => ({ status: 200, data: { record: { origin: 'CONNECTOR', externalRevisionId: rev, contentRev: 'c1' } } });

describe('connector records by label', () => {
  it('a second open with the same label makes no stream request, and a moved label does', async () => {
    const api = fakeApi({
      [`GET ${KB}/record/c1`]: [connectorDetails('7'), connectorDetails('7'), connectorDetails('8')],
      [`GET ${KB}/stream/record/c1`]: { status: 200, data: pdf() },
    });
    const open = async () => {
      const { grant } = await KnowledgeBaseApi.getRecordDetailsForOpen('c1');
      const lease = await openRecordContent('c1', 'original', grant ?? undefined).promise;
      lease.release();
    };
    await open();
    await open();
    expect(api.count(`GET ${KB}/stream/record/c1`)).toBe(1);
    await open();
    expect(api.count(`GET ${KB}/stream/record/c1`)).toBe(2);
  });

  it('every open sends the content-open header, and the plain details call cannot mint a grant', async () => {
    const api = fakeApi({
      [`GET ${KB}/record/c1`]: connectorDetails('7'),
      [`GET ${KB}/stream/record/c1`]: { status: 200, data: pdf() },
    });
    const { grant } = await KnowledgeBaseApi.getRecordDetailsForOpen('c1');
    expect(api.sent[0].config.headers['X-Record-Content-Open']).toBe('1');
    (await openRecordContent('c1', 'original', grant ?? undefined).promise).release();

    // A details object from the plain call carries no grant, so nothing can be passed.
    await KnowledgeBaseApi.getRecordDetails('c1');
    expect(api.sent[2].config.headers['X-Record-Content-Open']).toBeUndefined();
    const lease = await openRecordContent('c1', 'original').promise;
    lease.release();
    expect(api.count(`GET ${KB}/stream/record/c1`)).toBe(2);
  });
});

describe('a details refusal', () => {
  it.each([403, 404, 409])('on %i evicts every variant of the record and nothing else', async (status) => {
    fakeApi({
      [`GET ${KB}/stream/record/r1`]: { status: 200, data: pdf(), headers: { etag: '"a"' } },
      [`GET ${KB}/stream/record/r2`]: { status: 200, data: pdf(), headers: { etag: '"b"' } },
      [`GET ${KB}/record/r1`]: { status, data: { message: 'refused' } },
    });
    for (const [id, v] of [['r1', 'original'], ['r1', 'pdf'], ['r2', 'original']] as const) {
      (await openRecordContent(id, v).promise).release();
    }
    const cache = getRecordContentCache();
    expect(cache.has('r1', 'original') && cache.has('r1', 'pdf') && cache.has('r2', 'original')).toBe(true);

    await expect(KnowledgeBaseApi.getRecordDetailsForOpen('r1', { suppressErrorToast: true })).rejects.toBeTruthy();

    expect(cache.has('r1', 'original')).toBe(false);
    expect(cache.has('r1', 'pdf')).toBe(false);
    expect(cache.has('r2', 'original')).toBe(true);
  });

  it('on 500 keeps what it has', async () => {
    fakeApi({
      [`GET ${KB}/stream/record/r1`]: { status: 200, data: pdf(), headers: { etag: '"a"' } },
      [`GET ${KB}/record/r1`]: { status: 500 },
    });
    (await openRecordContent('r1', 'original').promise).release();
    await expect(KnowledgeBaseApi.getRecordDetailsForOpen('r1', { suppressErrorToast: true })).rejects.toBeTruthy();
    expect(getRecordContentCache().has('r1', 'original')).toBe(true);
  });
});

describe('the ENABLE_RECORD_PREVIEW_CACHE flag', () => {
  it('off: nothing is retained and no validator is sent', async () => {
    useFeatureFlagsStore.setState({ flags: { ENABLE_RECORD_PREVIEW_CACHE: false } });
    const api = fakeApi({ [`GET ${KB}/stream/record/r1`]: { status: 200, data: pdf(), headers: { etag: '"a"' } } });
    (await openRecordContent('r1', 'original').promise).release();
    (await openRecordContent('r1', 'original').promise).release();
    expect(api.count(`GET ${KB}/stream/record/r1`)).toBe(2);
    expect(api.sent.every((r) => r.config.headers['If-None-Match'] === undefined)).toBe(true);
  });

  it('on (default): the second open is conditional and a 304 reuses the entry', async () => {
    const api = fakeApi({
      [`GET ${KB}/stream/record/r1`]: [
        { status: 200, data: pdf(), headers: { etag: '"a"' } },
        { status: 304 },
      ],
    });
    const first = await openRecordContent('r1', 'original').promise;
    first.release();
    const second = await openRecordContent('r1', 'original').promise;
    expect(api.sent[1].config.headers['If-None-Match']).toBe('"a"');
    expect(second.blob).toBe(first.blob);
    second.release();
  });
});

describe('the label scope', () => {
  const record = (over: Record<string, unknown>) => ({ status: 200, data: { record: { externalRevisionId: '7', contentRev: 'c1', ...over } } });
  const reopenStreams = async (over: Record<string, unknown>) => {
    const api = fakeApi({
      [`GET ${KB}/record/s1`]: record(over),
      [`GET ${KB}/stream/record/s1`]: { status: 200, data: pdf() },
    });
    for (let i = 0; i < 2; i += 1) {
      const { grant } = await KnowledgeBaseApi.getRecordDetailsForOpen('s1');
      (await openRecordContent('s1', 'original', grant ?? undefined).promise).release();
    }
    return api.count(`GET ${KB}/stream/record/s1`);
  };

  it.each([
    ['an uploaded record', { origin: 'UPLOAD' }],
    ['an artifact record', { origin: 'CONNECTOR', recordType: 'ARTIFACT' }],
    ['a chat attachment', { origin: 'CONNECTOR', connectorName: 'ATTACHMENTS' }],
    ['a sandbox file', { origin: 'CONNECTOR', connectorName: 'CODING_SANDBOX' }],
  ])('%s never gets a label, so every open reaches the stream route', async (_name, over) => {
    expect(await reopenStreams(over)).toBe(2);
  });

  it('an ordinary connector record does get one (control)', async () => {
    expect(await reopenStreams({ origin: 'CONNECTOR', recordType: 'FILE', connectorName: 'Google Drive' })).toBe(1);
  });
});

describe('the grant owner', () => {
  it('is the owner when the question was asked: an answer landing after an account change mints nothing', async () => {
    fakeApi({
      [`GET ${KB}/record/c1`]: async () => {
        useAuthStore.setState({ accessToken: jwtExpiringIn(3600, { orgId: 'o1', userId: 'u2' }), refreshToken: 'r' });
        return connectorDetails('7');
      },
    });
    const { grant } = await KnowledgeBaseApi.getRecordDetailsForOpen('c1');
    expect(grant).toBeNull();
  });
});

describe('a refused stream', () => {
  it.each([403, 404])('on %i evicts every variant of the record', async (status) => {
    const api = fakeApi({
      [`GET ${KB}/stream/record/r1`]: [
        { status: 200, data: pdf(), headers: { etag: '"a"' } },
        { status, data: { message: 'refused' } },
      ],
    });
    (await openRecordContent('r1', 'original').promise).release();
    expect(getRecordContentCache().has('r1', 'original')).toBe(true);
    await expect(openRecordContent('r1', 'original').promise).rejects.toBeTruthy();
    expect(getRecordContentCache().has('r1', 'original')).toBe(false);
    expect(api.count(`GET ${KB}/stream/record/r1`)).toBe(2);
  });
});

describe('the flag off is the base request', () => {
  it('the details call carries no content-open header and the stream asks for no validator', async () => {
    useFeatureFlagsStore.setState({ flags: { ENABLE_RECORD_PREVIEW_CACHE: false } });
    const api = fakeApi({
      [`GET ${KB}/record/c1`]: connectorDetails('7'),
      [`GET ${KB}/stream/record/c1`]: { status: 200, data: pdf() },
    });
    const { grant } = await KnowledgeBaseApi.getRecordDetailsForOpen('c1');
    (await openRecordContent('c1', 'original', grant ?? undefined).promise).release();
    expect(api.sent.map((r) => r.config.headers['X-Record-Content-Open'])).toEqual([undefined, undefined]);
  });

  it('on, both carry it', async () => {
    const api = fakeApi({
      [`GET ${KB}/record/c1`]: connectorDetails('7'),
      [`GET ${KB}/stream/record/c1`]: { status: 200, data: pdf() },
    });
    const { grant } = await KnowledgeBaseApi.getRecordDetailsForOpen('c1');
    (await openRecordContent('c1', 'original', grant ?? undefined).promise).release();
    expect(api.sent.map((r) => r.config.headers['X-Record-Content-Open'])).toEqual(['1', '1']);
  });
});
