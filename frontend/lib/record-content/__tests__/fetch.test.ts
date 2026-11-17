import { beforeEach, describe, expect, it, vi } from 'vitest';
import { installMemoryStorage, jwtExpiringIn } from '@/lib/api/__tests__/sse-response';

installMemoryStorage();

const toast = vi.hoisted(() => ({ showErrorToast: vi.fn() }));
vi.mock('@/lib/api/error-toast', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api/error-toast')>()),
  showErrorToast: toast.showErrorToast,
}));
vi.mock('@/config', async () => {
  const auth = await vi.importActual<typeof import('@/lib/store/auth-store')>('@/lib/store/auth-store');
  return { useAuthStore: auth.useAuthStore, logoutAndRedirect: vi.fn() };
});

const { useAuthStore } = await import('@/lib/store/auth-store');
const { fakeApi } = await import('@/lib/api/__tests__/fake-api');
const { clearSessionCache } = await import('@/lib/api/session-cache');
const { fetchRecordContent } = await import('../fetch');
const { apiClient } = await import('@/lib/api/axios-instance');

const STREAM = '/api/v1/knowledgeBase/stream/record/r1';
const args = (over: Partial<Parameters<typeof fetchRecordContent>[0]> = {}) => ({
  recordId: 'r1',
  variant: 'original' as const,
  ifNoneMatch: null,
  signal: new AbortController().signal,
  onProgress: vi.fn(),
  ...over,
});

beforeEach(() => {
  useAuthStore.setState({ accessToken: jwtExpiringIn(3600, { orgId: 'o1', userId: 'u1' }), refreshToken: 'r' });
  toast.showErrorToast.mockClear();
});

describe('fetchRecordContent', () => {
  it('sends the validator and asks for the conversion when the variant says so', async () => {
    const api = fakeApi({ [`GET ${STREAM}`]: { status: 200, data: new Blob(['x'], { type: 'application/pdf' }), headers: { etag: '"v1"' } } });
    const result = await fetchRecordContent(args({ variant: 'pdf', ifNoneMatch: '"v0"' }));
    expect(result).toMatchObject({ status: 200, etag: '"v1"' });
    const sent = api.sent[0].config;
    expect(sent.params).toEqual({ convertTo: 'application/pdf' });
    expect(sent.headers['If-None-Match']).toBe('"v0"');
    expect(sent.responseType).toBe('blob');
  });

  it('a 304 is a success: no body, no toast, no error', async () => {
    fakeApi({ [`GET ${STREAM}`]: { status: 304 } });
    await expect(fetchRecordContent(args({ ifNoneMatch: '"v1"' }))).resolves.toEqual({ status: 304 });
    expect(toast.showErrorToast).not.toHaveBeenCalled();
  });

  it('inherits what apiClient does for every request', async () => {
    const api = fakeApi({ [`GET ${STREAM}`]: { status: 200, data: new Blob(['x']) } });
    await fetchRecordContent(args());
    const config = api.sent[0].config;
    expect(config.headers['x-request-id']).toBeTruthy();
    expect(config.headers['client-name']).toBe('pipeshub-ai');
    expect(config.headers.Authorization).toMatch(/^Bearer /);
    expect(config.timeout).toBe(apiClient.defaults.timeout);
  });

  it('shows a refusal like any other apiClient call', async () => {
    fakeApi({ [`GET ${STREAM}`]: { status: 403, data: { message: 'no' } } });
    await expect(fetchRecordContent(args())).rejects.toMatchObject({ statusCode: 403 });
    expect(toast.showErrorToast).toHaveBeenCalledTimes(1);
  });

  it('drops a stale 401 that arrives after the session ended: no refresh, no toast', async () => {
    let answer!: () => void;
    const gate = new Promise<void>((r) => (answer = r));
    fakeApi({
      [`GET ${STREAM}`]: async () => {
        await gate;
        return { status: 401, data: { message: 'expired' } };
      },
    });
    const settled = vi.fn();
    void fetchRecordContent(args()).then(settled, settled);
    await Promise.resolve();
    clearSessionCache();
    answer();
    await new Promise((r) => setTimeout(r, 20));
    expect(settled).not.toHaveBeenCalled();
    expect(toast.showErrorToast).not.toHaveBeenCalled();
  });
});
