/**
 * "The server can't check the session right now" is temporary, not a sign-out.
 *
 * The refresh call is retried while its answer is known to be temporary (no
 * network, a 5xx, a 408 or a 429), with capped backoff that honours
 * Retry-After. If the session still cannot be checked when the cycle's time is
 * spent, only what needed the token fails, with a message that says so, and a
 * "Reconnecting…" notice stays up; nobody is signed out. Every final answer
 * (401/403, 400, 404, a 2xx without a token) still signs out as before.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { AxiosHeaders, type AxiosAdapter, type InternalAxiosRequestConfig } from 'axios';
import { installMemoryStorage, jsonResponse, jwtExpiringIn } from './sse-response';

const storage = installMemoryStorage();
installMemoryStorage('sessionStorage');

const logoutAndRedirect = vi.fn();
vi.mock('@/config', async () => {
  const auth = await vi.importActual<typeof import('@/lib/store/auth-store')>('@/lib/store/auth-store');
  return {
    useAuthStore: auth.useAuthStore,
    logoutAndRedirect: () => {
      logoutAndRedirect();
      auth.useAuthStore.getState().logout();
    },
  };
});

const { useAuthStore } = await import('@/lib/store/auth-store');
const { SESSION_HINT_KEY } = await import('@/lib/auth/session-credential');
const { apiClient } = await import('../axios-instance');
const { ErrorType } = await import('../api-error');
const { refreshOrSignOut } = await import('../token-refresh');
const { streamSSERequest } = await import('../streaming');
const scheduler = await import('../token-refresh-scheduler');
const { useToastStore } = await import('@/lib/store/toast-store');
const { i18n } = await import('@/lib/i18n');

const fetchMock = vi.fn<typeof fetch>();

function fakeServer() {
  const sent: InternalAxiosRequestConfig[] = [];
  const adapter: AxiosAdapter = async (config) => {
    sent.push(config);
    return { data: {}, status: 200, statusText: '', headers: new AxiosHeaders(), config };
  };
  apiClient.defaults.adapter = adapter;
  return sent;
}

const loadingToasts = () => useToastStore.getState().toasts.filter((t) => t.variant === 'loading' && !t.isExiting);
const bearer = (config: InternalAxiosRequestConfig) => AxiosHeaders.from(config.headers).get('Authorization');

beforeEach(() => {
  storage.clear();
  storage.set(SESSION_HINT_KEY, 'u1:o1');
  fetchMock.mockReset();
  logoutAndRedirect.mockReset();
  vi.stubGlobal('fetch', fetchMock);
  useAuthStore.setState({ accessToken: jwtExpiringIn(20), isAuthenticated: true });
  useToastStore.setState({ toasts: [] });
  vi.spyOn(console, 'log').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.useFakeTimers();
});

afterEach(() => {
  scheduler.cancel();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('a refresh the server cannot answer right now', () => {
  it.each([
    ['the network is down', () => fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'))],
    ['the server answers 503', () => fetchMock.mockResolvedValueOnce(jsonResponse(503, {}))],
    ['the server answers 502', () => fetchMock.mockResolvedValueOnce(jsonResponse(502, {}))],
    ['the server is rate limiting (429)', () => fetchMock.mockResolvedValueOnce(jsonResponse(429, {}))],
    ['the request timed out (408)', () => fetchMock.mockResolvedValueOnce(jsonResponse(408, {}))],
  ])('is retried when %s, and the request then goes through with the new token', async (_label, fail) => {
    fail();
    fail();
    const fresh = jwtExpiringIn(3600);
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { accessToken: fresh }));
    const sent = fakeServer();

    const request = apiClient.get('/api/v1/users');
    await vi.advanceTimersByTimeAsync(10_000);
    await request;

    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(sent).toHaveLength(1);
    expect(bearer(sent[0])).toBe(`Bearer ${fresh}`);
    expect(logoutAndRedirect).not.toHaveBeenCalled();
  });

  it('honours Retry-After before the next attempt', async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(429, {}, { 'Retry-After': '20' }))
      .mockResolvedValueOnce(jsonResponse(200, { accessToken: jwtExpiringIn(3600) }));
    const outcome = refreshOrSignOut();
    await vi.advanceTimersByTimeAsync(19_000);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(2_000);
    expect(await outcome).toBe('refreshed');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('fails only the request, says why, keeps the person signed in and shows Reconnecting…', async () => {
    fetchMock.mockResolvedValue(jsonResponse(503, {}));
    const sent = fakeServer();
    const token = useAuthStore.getState().accessToken;

    const request = apiClient.get('/api/v1/users').catch((e: unknown) => e);
    await vi.advanceTimersByTimeAsync(60_000);
    const error = (await request) as { type?: string; message?: string };

    expect(error.type).toBe(ErrorType.SERVER_ERROR);
    expect(error.message).toBe(i18n.t('errorToast.sessionUnavailable'));
    expect(sent).toHaveLength(0);
    expect(logoutAndRedirect).not.toHaveBeenCalled();
    expect(useAuthStore.getState().accessToken).toBe(token);
    expect(storage.get(SESSION_HINT_KEY)).toBe('u1:o1');
    expect(loadingToasts()).toHaveLength(1);
    expect(loadingToasts()[0].title).toBe(i18n.t('errorToast.reconnecting'));
  });

  it('clears Reconnecting… once a refresh works again', async () => {
    fetchMock.mockResolvedValue(jsonResponse(503, {}));
    const first = refreshOrSignOut();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(await first).toBe('unreachable');
    expect(loadingToasts()).toHaveLength(1);

    fetchMock.mockReset();
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { accessToken: jwtExpiringIn(3600) }));
    expect(await refreshOrSignOut()).toBe('refreshed');
    expect(loadingToasts()).toHaveLength(0);
  });

  it('stops retrying when the person signs out meanwhile, and stores no token', async () => {
    fetchMock.mockResolvedValue(jsonResponse(503, {}));
    const outcome = refreshOrSignOut();
    await vi.advanceTimersByTimeAsync(1_500);
    const calls = fetchMock.mock.calls.length;
    useAuthStore.getState().logout();
    storage.delete(SESSION_HINT_KEY);
    await vi.advanceTimersByTimeAsync(60_000);

    expect(await outcome).toBe('session-ended');
    expect(fetchMock.mock.calls.length).toBeLessThanOrEqual(calls + 1);
    expect(useAuthStore.getState().accessToken).toBeNull();
    expect(logoutAndRedirect).not.toHaveBeenCalled();
    expect(loadingToasts()).toHaveLength(0);
  });

  it('fails a stream with the same message instead of signing out', async () => {
    fetchMock.mockResolvedValue(jsonResponse(503, {}));
    const errors: Error[] = [];
    const stream = streamSSERequest('/api/v1/conversations/stream', {}, {
      onEvent: () => {},
      onError: (e) => errors.push(e),
    });
    await vi.advanceTimersByTimeAsync(60_000);
    await stream;

    expect(logoutAndRedirect).not.toHaveBeenCalled();
    expect(errors).toHaveLength(1);
    expect(errors[0].message).toBe(i18n.t('errorToast.sessionUnavailable'));
  });

  it('the timer tries again later instead of signing out', async () => {
    fetchMock.mockResolvedValue(jsonResponse(503, {}));
    scheduler.scheduleFromToken(jwtExpiringIn(30));
    await vi.advanceTimersByTimeAsync(60_000);
    expect(logoutAndRedirect).not.toHaveBeenCalled();
    const afterFirstCycle = fetchMock.mock.calls.length;

    fetchMock.mockReset();
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { accessToken: jwtExpiringIn(3600) }));
    await vi.advanceTimersByTimeAsync(61_000);
    expect(afterFirstCycle).toBeGreaterThan(1);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(useAuthStore.getState().isAuthenticated).toBe(true);
    expect(logoutAndRedirect).not.toHaveBeenCalled();
  });
});

describe('a final answer still signs out', () => {
  it.each([
    ['401', jsonResponse(401, {})],
    ['403', jsonResponse(403, {})],
    ['400 (locked account)', jsonResponse(400, {})],
    ['404 (no credentials)', jsonResponse(404, {})],
    ['200 without a token', jsonResponse(200, {})],
  ])('%s, after one attempt', async (_label, reply) => {
    fetchMock.mockResolvedValueOnce(reply);
    const outcome = refreshOrSignOut();
    await vi.advanceTimersByTimeAsync(0);
    expect(await outcome).toBe('signed-out');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(logoutAndRedirect).toHaveBeenCalledTimes(1);
  });
});
