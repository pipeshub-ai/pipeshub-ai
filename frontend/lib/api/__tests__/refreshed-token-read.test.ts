/**
 * The token a request or stream goes out with is the one read right after the refresh, and the
 * account check runs on that same read: the store can change between `refreshOrSignOut`'s own check
 * and the moment the caller resumes (another `setTokens` in between).
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { AxiosHeaders } from 'axios';
import { installMemoryStorage, jwtExpiringIn, sseFrame, sseResponse } from './sse-response';

installMemoryStorage();

vi.mock('@/config', async () => {
  const auth = await vi.importActual<typeof import('@/lib/store/auth-store')>('@/lib/store/auth-store');
  return { useAuthStore: auth.useAuthStore, logoutAndRedirect: vi.fn() };
});

/** The token the store holds when the caller resumes after the refresh. */
let storeOnResume: string | null = null;
vi.mock('../token-refresh', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../token-refresh')>();
  const { useAuthStore } = await import('@/lib/store/auth-store');
  return {
    ...actual,
    // The refresh answered for the caller's account; the store then changes before the caller resumes.
    refreshOrSignOut: vi.fn(async () => {
      queueMicrotask(() => useAuthStore.setState({ accessToken: storeOnResume }));
      return 'refreshed' as const;
    }),
  };
});

const { useAuthStore } = await import('@/lib/store/auth-store');
const { ErrorType } = await import('../api-error');
const { fakeApi } = await import('./fake-api');
const { apiClient } = await import('../axios-instance');
const { streamSSERequest } = await import('../streaming');

const as = (userId: string, secs = 3600) => jwtExpiringIn(secs, { userId, orgId: 'o1' });
const fetchMock = vi.fn<typeof fetch>();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
  vi.spyOn(console, 'warn').mockImplementation(() => {});
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('the token read after a refresh', () => {
  it('apiClient does not send a request made as u1 when the store holds u2 by the time it resumes', async () => {
    useAuthStore.setState({ accessToken: as('u1', 20) });
    storeOnResume = as('u2');
    const api = fakeApi({ 'GET /api/v1/users': { status: 200, data: {} } });
    await expect(apiClient.get('/api/v1/users', { suppressErrorToast: true })).rejects.toMatchObject({
      type: ErrorType.REQUEST_CANCELLED,
    });
    expect(api.sent).toHaveLength(0);
  });

  it('apiClient does not replay a 401 made as u1 when the store holds u2 by the time it resumes', async () => {
    useAuthStore.setState({ accessToken: as('u1') });
    storeOnResume = as('u2');
    const api = fakeApi({ 'GET /api/v1/users': [{ status: 401, data: {} }, { status: 200, data: {} }] });
    await expect(apiClient.get('/api/v1/users', { suppressErrorToast: true })).rejects.toMatchObject({
      type: ErrorType.REQUEST_CANCELLED,
    });
    expect(api.sent).toHaveLength(1);
  });

  it('apiClient sends the token it read, for the same account', async () => {
    useAuthStore.setState({ accessToken: as('u1', 20) });
    const fresh = as('u1', 7200);
    storeOnResume = fresh;
    const api = fakeApi({ 'GET /api/v1/users': { status: 200, data: {} } });
    await apiClient.get('/api/v1/users');
    expect(AxiosHeaders.from(api.sent[0].config.headers).get('Authorization')).toBe(`Bearer ${fresh}`);
  });

  it('a stream made as u1 is not requested when the store holds u2 by the time it resumes', async () => {
    useAuthStore.setState({ accessToken: as('u1', 20) });
    storeOnResume = as('u2');
    const onError = vi.fn();
    await streamSSERequest('/s', {}, { onEvent: vi.fn(), onError });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
  });

  it('a stream goes out with the token it read, for the same account', async () => {
    useAuthStore.setState({ accessToken: as('u1', 20) });
    const fresh = as('u1', 7200);
    storeOnResume = fresh;
    fetchMock.mockResolvedValueOnce(sseResponse([sseFrame('ok', {})]));
    await streamSSERequest('/s', {}, { onEvent: vi.fn(), onError: vi.fn() });
    expect((fetchMock.mock.calls[0][1]?.headers as Record<string, string>).Authorization).toBe(`Bearer ${fresh}`);
  });
});
