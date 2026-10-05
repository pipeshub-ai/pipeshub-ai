/**
 * Keeping a person signed in: the axios client's token handling and error
 * toasts, the shared refresh lock, and the proactive refresh timer. The
 * network is faked at the axios adapter and at `fetch` (the refresh call).
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { AxiosError, AxiosHeaders, type AxiosAdapter, type InternalAxiosRequestConfig } from 'axios';
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

const { useAuthStore, ACCESS_TOKEN_STORAGE_KEY, REFRESH_TOKEN_STORAGE_KEY } = await import('@/lib/store/auth-store');
const { apiClient } = await import('../axios-instance');
const { ErrorType } = await import('../api-error');
const { STREAM_ERROR_MESSAGES } = await import('../stream-errors');
const { refreshAccessToken, isTokenExpired, decodeJwtPayload, REFRESH_TOKEN_ENDPOINT } = await import('../token-refresh');
const scheduler = await import('../token-refresh-scheduler');
const { useToastStore } = await import('@/lib/store/toast-store');

interface FakeReply {
  status: number;
  data?: unknown;
  headers?: Record<string, string>;
}

/** Answers each request from `replies` in order and records what was sent. */
function fakeServer(...replies: Array<FakeReply | Error>) {
  const sent: InternalAxiosRequestConfig[] = [];
  const adapter: AxiosAdapter = async (config) => {
    sent.push(config);
    const reply = replies.shift() ?? { status: 200, data: {} };
    if (reply instanceof Error) throw Object.assign(reply, { config, isAxiosError: true });
    const response = {
      data: reply.data ?? {},
      status: reply.status,
      statusText: '',
      headers: new AxiosHeaders(reply.headers ?? {}),
      config,
    };
    if (reply.status >= 200 && reply.status < 300) return response;
    throw new AxiosError(`Request failed with status code ${reply.status}`, 'ERR_BAD_RESPONSE', config, null, response);
  };
  apiClient.defaults.adapter = adapter;
  return sent;
}

const bearer = (config: InternalAxiosRequestConfig) => AxiosHeaders.from(config.headers).get('Authorization');
const errorToasts = () => useToastStore.getState().toasts.filter((t) => t.variant === 'error');
const fetchMock = vi.fn<typeof fetch>();

beforeEach(() => {
  storage.clear();
  fetchMock.mockReset();
  logoutAndRedirect.mockReset();
  vi.stubGlobal('fetch', fetchMock);
  useAuthStore.setState({ accessToken: jwtExpiringIn(3600), refreshToken: 'refresh-1', isAuthenticated: true });
  useToastStore.setState({ toasts: [] });
  vi.spyOn(console, 'log').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  scheduler.cancel();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('reading a token', () => {
  it('decodes a base64url payload and treats a token inside the 90 s buffer as expired', () => {
    expect(decodeJwtPayload(jwtExpiringIn(100))?.exp).toBeTypeOf('number');
    expect(isTokenExpired(jwtExpiringIn(60))).toBe(true);
    expect(isTokenExpired(jwtExpiringIn(600))).toBe(false);
  });

  it('never throws on garbage and treats a token without exp as not expired', () => {
    expect(decodeJwtPayload('not-a-jwt')).toBeNull();
    expect(decodeJwtPayload('a.%%%.c')).toBeNull();
    expect(decodeJwtPayload(null)).toBeNull();
    expect(isTokenExpired(`h.${btoa('{"sub":"u1"}')}.s`)).toBe(false);
  });
});

describe('the shared refresh lock', () => {
  it('collapses concurrent refreshes into one network call and stores the new token', async () => {
    const fresh = jwtExpiringIn(3600);
    let resolve!: (r: Response) => void;
    fetchMock.mockReturnValueOnce(new Promise((r) => (resolve = r)));

    const calls = [refreshAccessToken(), refreshAccessToken(), refreshAccessToken()];
    resolve(jsonResponse(200, { accessToken: fresh }));

    expect(await Promise.all(calls)).toEqual([true, true, true]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe(REFRESH_TOKEN_ENDPOINT);
    expect(useAuthStore.getState().accessToken).toBe(fresh);
    expect(storage.get(ACCESS_TOKEN_STORAGE_KEY)).toBe(fresh);
  });

  it('falls back to the stored refresh token after a reload', async () => {
    useAuthStore.setState({ refreshToken: null });
    storage.set(REFRESH_TOKEN_STORAGE_KEY, 'from-storage');
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { accessToken: jwtExpiringIn(3600) }));
    expect(await refreshAccessToken()).toBe(true);
    expect((fetchMock.mock.calls[0][1]?.headers as Record<string, string>).Authorization).toBe('Bearer from-storage');
  });

  it.each([
    ['no refresh token at all', () => useAuthStore.setState({ refreshToken: null }), undefined],
    ['the server refuses', () => {}, jsonResponse(401, {})],
    ['the reply has no token', () => {}, jsonResponse(200, {})],
    ['the network is down', () => {}, new TypeError('Failed to fetch')],
  ])('reports failure when %s, and lets the next attempt start fresh', async (_label, setup, reply) => {
    setup();
    if (reply instanceof Error) fetchMock.mockRejectedValueOnce(reply);
    else if (reply) fetchMock.mockResolvedValueOnce(reply);
    expect(await refreshAccessToken()).toBe(false);

    useAuthStore.setState({ refreshToken: 'refresh-1' });
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { accessToken: 'next' }));
    expect(await refreshAccessToken()).toBe(true);
  });
});

describe('apiClient requests', () => {
  it('sends the bearer token and a request id', async () => {
    const sent = fakeServer({ status: 200, data: { ok: true } });
    const { data } = await apiClient.get('/api/v1/users');
    expect(data).toEqual({ ok: true });
    expect(bearer(sent[0])).toBe(`Bearer ${useAuthStore.getState().accessToken}`);
    expect(AxiosHeaders.from(sent[0].headers).get('x-request-id')).toBeTruthy();
  });

  it('keeps a token the caller set explicitly', async () => {
    const sent = fakeServer({ status: 200 });
    await apiClient.get('/api/v1/users', { headers: { Authorization: 'Bearer caller-token' } });
    expect(bearer(sent[0])).toBe('Bearer caller-token');
  });

  it('refreshes a token about to expire before sending', async () => {
    useAuthStore.setState({ accessToken: jwtExpiringIn(20) });
    const fresh = jwtExpiringIn(3600);
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { accessToken: fresh }));
    const sent = fakeServer({ status: 200 });

    await apiClient.get('/api/v1/users');

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(bearer(sent[0])).toBe(`Bearer ${fresh}`);
  });

  it('never runs the token check on the refresh endpoint itself', async () => {
    useAuthStore.setState({ accessToken: jwtExpiringIn(20) });
    const sent = fakeServer({ status: 200 });
    await apiClient.post(REFRESH_TOKEN_ENDPOINT);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(bearer(sent[0])).toBeUndefined();
  });

  it('logs the person out when the token cannot be refreshed before sending', async () => {
    useAuthStore.setState({ accessToken: jwtExpiringIn(20) });
    fetchMock.mockResolvedValueOnce(jsonResponse(401, {}));
    const sent = fakeServer();

    // This used to reject with "Cannot read properties of undefined (reading
    // 'suppressErrorToast')", which callers then showed as the error text.
    await expect(apiClient.get('/api/v1/users')).rejects.toEqual({
      type: ErrorType.AUTHENTICATION_ERROR,
      message: STREAM_ERROR_MESSAGES.sessionExpired,
    });

    expect(sent).toHaveLength(0);
    expect(logoutAndRedirect).toHaveBeenCalledTimes(1);
    expect(useAuthStore.getState().accessToken).toBeNull();
    expect(errorToasts()).toHaveLength(0);
  });
});

describe('a 401 from the server', () => {
  it('refreshes once and retries the request with the new token', async () => {
    const fresh = jwtExpiringIn(3600);
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { accessToken: fresh }));
    const sent = fakeServer({ status: 401 }, { status: 200, data: { name: 'Ada' } });

    const { data } = await apiClient.get('/api/v1/users/me');

    expect(data).toEqual({ name: 'Ada' });
    expect(sent).toHaveLength(2);
    expect(bearer(sent[1])).toBe(`Bearer ${fresh}`);
    expect(logoutAndRedirect).not.toHaveBeenCalled();
  });

  it('shares one refresh between requests that fail together', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { accessToken: jwtExpiringIn(3600) }));
    fakeServer({ status: 401 }, { status: 401 }, { status: 200 }, { status: 200 });
    await Promise.all([apiClient.get('/a'), apiClient.get('/b')]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('does not retry forever when the new token is refused too', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { accessToken: jwtExpiringIn(3600) }));
    const sent = fakeServer({ status: 401 }, { status: 401 });
    await expect(apiClient.get('/a')).rejects.toMatchObject({ type: ErrorType.AUTHENTICATION_ERROR });
    expect(sent).toHaveLength(2);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('logs out without trying to refresh when the server says the session is over', async () => {
    storage.set('workspace_session_token', 'x');
    fakeServer({ status: 401, data: { error: { message: 'Session expired, please login again' } } });

    await expect(apiClient.get('/a')).rejects.toMatchObject({ type: ErrorType.AUTHENTICATION_ERROR });

    expect(fetchMock).not.toHaveBeenCalled();
    expect(logoutAndRedirect).toHaveBeenCalledTimes(1);
    expect(storage.has('workspace_session_token')).toBe(false);
  });

  it('logs out when the refresh fails', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(401, {}));
    fakeServer({ status: 401 });
    await expect(apiClient.get('/a')).rejects.toMatchObject({ type: ErrorType.AUTHENTICATION_ERROR });
    expect(logoutAndRedirect).toHaveBeenCalledTimes(1);
  });
});

describe('error toasts', () => {
  it("shows the server's own words with the reference an admin can look up", async () => {
    fakeServer({ status: 500, data: { error: { message: 'The export could not be created. Try again in a minute.', requestId: 'req-42' } } });
    await expect(apiClient.post('/export')).rejects.toMatchObject({
      type: ErrorType.SERVER_ERROR,
      requestId: 'req-42',
    });
    expect(errorToasts().map((t) => [t.title, t.description])).toEqual([
      ['Server Error', 'The export could not be created. Try again in a minute. Reference: req-42'],
    ]);
  });

  it('replaces technical server text with a sentence that says what to do', async () => {
    fakeServer({ status: 500, data: { message: "KeyError: 'org_id'" } });
    await expect(apiClient.get('/x')).rejects.toBeDefined();
    expect(errorToasts()[0].description).toMatch(/try again in a moment/i);
  });

  it('calls a busy server a reason to retry, not a server error', async () => {
    fakeServer({ status: 503, headers: { 'retry-after': '4' } });
    await expect(apiClient.get('/x')).rejects.toBeDefined();
    expect(errorToasts().map((t) => [t.title, t.description])).toEqual([
      ['Please try again shortly', 'PipesHub is busy right now. Please try again in 4 seconds.'],
    ]);
  });

  it('shows one toast for a burst of the same kind of failure', async () => {
    fakeServer({ status: 500 }, { status: 500 }, { status: 500 });
    await Promise.allSettled([apiClient.get('/a'), apiClient.get('/b'), apiClient.get('/c')]);
    expect(errorToasts()).toHaveLength(1);
  });

  it('says to check the connection when the request never reached the server', async () => {
    fakeServer(new Error('Network Error'));
    await expect(apiClient.get('/x')).rejects.toMatchObject({ type: ErrorType.NETWORK_ERROR });
    expect(errorToasts()[0].title).toBe('Connection Error');
  });

  it('stays quiet for a cancelled request', async () => {
    fakeServer(Object.assign(new Error('canceled'), { code: 'ERR_CANCELED' }));
    await expect(apiClient.get('/x')).rejects.toMatchObject({ type: ErrorType.REQUEST_CANCELLED });
    expect(errorToasts()).toHaveLength(0);
  });

  it('lets a caller suppress the toast, all at once or only for failures it shows itself', async () => {
    fakeServer({ status: 404 }, { status: 404 }, { status: 409 });
    await expect(apiClient.get('/a', { suppressErrorToast: true })).rejects.toBeDefined();
    const onlyNotFound = (e: { type: string }) => e.type === ErrorType.NOT_FOUND;
    await expect(apiClient.get('/b', { suppressErrorToast: onlyNotFound })).rejects.toBeDefined();
    expect(errorToasts()).toHaveLength(0);
    await expect(apiClient.get('/c', { suppressErrorToast: onlyNotFound })).rejects.toMatchObject({ type: ErrorType.CONFLICT });
    expect(errorToasts().map((t) => t.title)).toEqual(['Action Required']);
  });

  it('reads the error out of a Blob body from a download request', async () => {
    const json = JSON.stringify({ message: 'You can only download files you have access to.' });
    const body = new Blob([json], { type: 'application/json' });
    // jsdom's Blob has no text(); every browser's does.
    Object.defineProperty(body, 'text', { value: async () => json });
    fakeServer({ status: 403, data: body });
    await expect(apiClient.get('/file', { responseType: 'blob' })).rejects.toMatchObject({
      type: ErrorType.AUTHORIZATION_ERROR,
      message: 'You can only download files you have access to.',
    });
  });
});

describe('the proactive refresh timer', () => {
  it('refreshes 90 s before the token expires', async () => {
    vi.useFakeTimers();
    const token = jwtExpiringIn(600);
    useAuthStore.setState({ accessToken: token });
    fetchMock.mockResolvedValue(jsonResponse(200, { accessToken: jwtExpiringIn(3600) }));

    scheduler.scheduleFromToken(token);
    await vi.advanceTimersByTimeAsync(500_000);
    expect(fetchMock).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(15_000);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('refreshes at once for a token already inside the buffer, and logs out if that fails', async () => {
    vi.useFakeTimers();
    fetchMock.mockResolvedValueOnce(jsonResponse(401, {}));
    scheduler.scheduleFromToken(jwtExpiringIn(30));
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(logoutAndRedirect).toHaveBeenCalledTimes(1);
  });

  it('re-arms a very long-lived token instead of firing early', async () => {
    vi.useFakeTimers();
    const token = jwtExpiringIn(60 * 60 * 24 * 30); // 30 days, past setTimeout's 24.8-day limit
    useAuthStore.setState({ accessToken: token });
    scheduler.scheduleFromToken(token);
    await vi.advanceTimersByTimeAsync(2_000_000_000 + 1);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('does nothing for a token without an expiry or after cancel', async () => {
    vi.useFakeTimers();
    scheduler.scheduleFromToken(`h.${btoa('{"sub":"u1"}')}.s`);
    scheduler.scheduleFromToken(jwtExpiringIn(200));
    scheduler.cancel();
    await vi.advanceTimersByTimeAsync(10_000_000);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  describe('once initialised', () => {
    beforeEach(() => {
      scheduler.initTokenRefreshScheduler();
    });

    it('adopts a token another tab refreshed, without refreshing itself', () => {
      const fromOtherTab = jwtExpiringIn(3600);
      window.dispatchEvent(new StorageEvent('storage', { key: ACCESS_TOKEN_STORAGE_KEY, newValue: fromOtherTab }));
      expect(useAuthStore.getState().accessToken).toBe(fromOtherTab);
      expect(fetchMock).not.toHaveBeenCalled();
    });

    it('signs out here when another tab signs out', () => {
      window.dispatchEvent(new StorageEvent('storage', { key: ACCESS_TOKEN_STORAGE_KEY, newValue: null }));
      expect(useAuthStore.getState().accessToken).toBeNull();
    });

    it('ignores storage changes to other keys', () => {
      const before = useAuthStore.getState().accessToken;
      window.dispatchEvent(new StorageEvent('storage', { key: 'theme', newValue: 'dark' }));
      expect(useAuthStore.getState().accessToken).toBe(before);
    });

    it('refreshes when the tab comes back after the timer slept through the expiry', async () => {
      vi.useFakeTimers();
      useAuthStore.setState({ accessToken: jwtExpiringIn(3600) });
      // A laptop lid closed for an hour: the clock moves on, the timer never fires.
      vi.setSystemTime(Date.now() + 3_550_000);
      expect(fetchMock).not.toHaveBeenCalled();

      fetchMock.mockResolvedValueOnce(jsonResponse(200, { accessToken: jwtExpiringIn(3600) }));
      Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
      document.dispatchEvent(new Event('visibilitychange'));
      await vi.advanceTimersByTimeAsync(0);

      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(logoutAndRedirect).not.toHaveBeenCalled();
    });
  });
});
