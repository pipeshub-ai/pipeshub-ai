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

// One attempt per refresh here; the retry cycle has its own tests in session-check-retry.test.ts.
vi.mock('../refresh-retry', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../refresh-retry')>()),
  REFRESH_MAX_TOTAL_WAIT_MS: 0,
}));

const { useAuthStore } = await import('@/lib/store/auth-store');
const { SESSION_HINT_KEY } = await import('@/lib/auth/session-credential');
const { apiClient } = await import('../axios-instance');
const { ErrorType } = await import('../api-error');
const { sessionUnavailableMessage } = await import('../session-notice');
const { REFRESH_MAX_COOLDOWN_CYCLES } = await import('../refresh-retry');
const { refreshAccessToken, refreshOrSignOut, isTokenExpired, decodeJwtPayload, REFRESH_TOKEN_ENDPOINT } = await import(
  '../token-refresh'
);
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

/** An unsigned JWT for `userId` in org `o1`. */
function tokenFor(userId: string, secondsFromNow = 3600): string {
  const claims = { exp: Math.floor(Date.now() / 1000) + secondsFromNow, userId, orgId: 'o1' };
  return `header.${btoa(JSON.stringify(claims)).replace(/=+$/, '')}.signature`;
}

/** Whether a request settles within a few ticks; one that ended the session is dropped and never does. */
async function settlesSoon(request: Promise<unknown>): Promise<boolean> {
  let settled = false;
  request.then(
    () => (settled = true),
    () => (settled = true),
  );
  await new Promise((resolve) => setTimeout(resolve, 10));
  return settled;
}

const bearer = (config: InternalAxiosRequestConfig) => AxiosHeaders.from(config.headers).get('Authorization');
const errorToasts = () => useToastStore.getState().toasts.filter((t) => t.variant === 'error');
const fetchMock = vi.fn<typeof fetch>();

/** The next refresh succeeds and issues the returned token, which differs from the one stored before. */
const refreshIssues = (token = jwtExpiringIn(3600, { refreshed: true })) => {
  fetchMock.mockResolvedValueOnce(jsonResponse(200, { accessToken: token }));
  return token;
};

// What the Node error middleware sends for a 401 a handler throws (UnauthorizedError): not about the session.
const wrongPassword = { error: { code: 'UNAUTHORIZED', message: 'Current password is incorrect.' } };
const notYourUser = { error: { code: 'UNAUTHORIZED', message: 'Unauthorized to update the user' } };
const sessionOver = { error: { message: 'Session expired, please login again' } };

/** A JSON body as a download receives it. jsdom's Blob has no text(); every browser's does. */
const jsonBlob = (body: unknown) => {
  const json = JSON.stringify(body);
  return Object.defineProperty(new Blob([json], { type: 'application/json' }), 'text', { value: async () => json });
};

/** A refresh the server gave no verdict on: the session may still be good. */
const noAnswer: Array<[string, () => void]> = [
  ['500', () => fetchMock.mockResolvedValueOnce(jsonResponse(500, {}))],
  ['502', () => fetchMock.mockResolvedValueOnce(jsonResponse(502, {}))],
  ['504', () => fetchMock.mockResolvedValueOnce(jsonResponse(504, {}))],
  ['503', () => fetchMock.mockResolvedValueOnce(jsonResponse(503, {}))],
  ['429', () => fetchMock.mockResolvedValueOnce(jsonResponse(429, {}))],
  ['a network failure', () => fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'))],
];

beforeEach(() => {
  storage.clear();
  fetchMock.mockReset();
  logoutAndRedirect.mockReset();
  vi.stubGlobal('fetch', fetchMock);
  useAuthStore.setState({ accessToken: jwtExpiringIn(3600), isAuthenticated: true });
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

    expect(await Promise.all(calls)).toEqual(['refreshed', 'refreshed', 'refreshed']);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe(REFRESH_TOKEN_ENDPOINT);
    expect(useAuthStore.getState().accessToken).toBe(fresh);
    // ADR-WEB-02: the new token stays in memory; storage keeps only which account is signed in.
    expect([...storage.values()].join('\n')).not.toContain(fresh);
  });

  it('asks with the HttpOnly session cookie, never with a token the page holds', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { accessToken: jwtExpiringIn(3600) }));
    expect(await refreshAccessToken()).toBe('refreshed');
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.credentials).toBe('include');
    expect((init.headers as Record<string, string>).Authorization).toBeUndefined();
  });

  it.each([
    ['the session cookie is gone', () => {}, jsonResponse(401, {}), 'refused'],
    ['the server refuses', () => {}, jsonResponse(401, {}), 'refused'],
    ['the account is locked', () => {}, jsonResponse(400, {}), 'refused'],
    ['the origin check refuses', () => {}, jsonResponse(403, {}), 'refused'],
    ['a 4xx above 429 comes back', () => {}, jsonResponse(431, {}), 'refused'],
    ['a request timeout (408) comes back', () => {}, jsonResponse(408, {}), 'unreachable'],
    ['the reply has no token', () => {}, jsonResponse(200, {}), 'refused'],
    ['the network is down', () => {}, new TypeError('Failed to fetch'), 'unreachable'],
    ['the server fails', () => {}, jsonResponse(500, {}), 'unreachable'],
    ['the rate limiter answers', () => {}, jsonResponse(429, {}), 'unreachable'],
  ])('reports failure when %s, and lets the next attempt start fresh', async (_label, setup, reply, expected) => {
    setup();
    if (reply instanceof Error) fetchMock.mockRejectedValueOnce(reply);
    else if (reply) fetchMock.mockResolvedValueOnce(reply);
    expect(await refreshAccessToken()).toBe(expected);

    fetchMock.mockResolvedValueOnce(jsonResponse(200, { accessToken: 'next' }));
    expect(await refreshAccessToken()).toBe('refreshed');
  });

  it('keeps the session when the refresh throws before the server answers', async () => {
    // The cross-tab lock is aborted (for example the page is unloading): the server never gave a verdict.
    vi.stubGlobal('navigator', { locks: { request: () => Promise.reject(new DOMException('aborted', 'AbortError')) } });
    expect(await refreshOrSignOut()).toBe('unreachable');
    expect(fetchMock).not.toHaveBeenCalled();
    expect(logoutAndRedirect).not.toHaveBeenCalled();
    expect(useAuthStore.getState().isAuthenticated).toBe(true);
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

    // The session is over, so the request is dropped: its caller writes nothing into the reset stores.
    expect(await settlesSoon(apiClient.get('/api/v1/users'))).toBe(false);

    expect(sent).toHaveLength(0);
    expect(logoutAndRedirect).toHaveBeenCalledTimes(1);
    expect(useAuthStore.getState().accessToken).toBeNull();
    expect(errorToasts()).toHaveLength(0);
  });

  it.each(noAnswer)('keeps the session and sends with the token it has when the refresh before sending gets %s', async (_label, arrange) => {
    const old = jwtExpiringIn(20);
    useAuthStore.setState({ accessToken: old });
    arrange();
    const sent = fakeServer({ status: 200, data: { ok: 1 } });

    const { data } = await apiClient.get('/api/v1/users');

    expect(data).toEqual({ ok: 1 });
    expect(sent).toHaveLength(1);
    expect(bearer(sent[0])).toBe(`Bearer ${old}`);
    expect(logoutAndRedirect).not.toHaveBeenCalled();
  });

  it('keeps a token the caller set explicitly when the refresh before sending gets no answer', async () => {
    const callerToken = jwtExpiringIn(20, { who: 'caller' });
    fetchMock.mockResolvedValueOnce(jsonResponse(503, {}));
    const sent = fakeServer({ status: 200 });
    await apiClient.get('/api/v1/users', { headers: { Authorization: `Bearer ${callerToken}` } });
    expect(bearer(sent[0])).toBe(`Bearer ${callerToken}`);
  });
});

describe('a 401 from the server', () => {
  it('refreshes once and retries the request with the new token', async () => {
    const fresh = refreshIssues();
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

  it("shows a 401 that comes back with the new token as a refusal in the server's words, and keeps the session", async () => {
    refreshIssues();
    const sent = fakeServer({ status: 401, data: notYourUser }, { status: 401, data: notYourUser });

    const reason = await apiClient.get('/api/v1/users/u2').catch((e) => e);

    expect(reason).toMatchObject({ type: ErrorType.AUTHORIZATION_ERROR, statusCode: 401, message: 'Unauthorized to update the user' });
    expect(reason.originalError?.response?.status).toBe(401);
    expect(sent).toHaveLength(2);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(errorToasts().map((t) => t.description)).toEqual([expect.stringContaining('Unauthorized to update the user')]);
    expect(logoutAndRedirect).not.toHaveBeenCalled();
  });

  it('lets a caller suppress the toast for a 401 that comes back with the new token', async () => {
    refreshIssues();
    fakeServer({ status: 401, data: notYourUser }, { status: 401, data: notYourUser });
    await expect(apiClient.get('/a', { suppressErrorToast: true })).rejects.toMatchObject({ type: ErrorType.AUTHORIZATION_ERROR });
    expect(errorToasts()).toHaveLength(0);
  });

  it('keeps the server reference and words a 401 with no message as a refusal, never "session expired"', async () => {
    refreshIssues();
    const body = { requestId: 'req-1', details: { limit: 5 } };
    fakeServer({ status: 401, data: body }, { status: 401, data: body });
    await expect(apiClient.get('/a')).rejects.toMatchObject({
      requestId: 'req-1',
      details: { limit: 5 },
      message: 'You do not have permission to perform this action.',
    });
    expect(errorToasts()[0]?.title).toMatch(/access denied/i);
  });

  it("shows a Python service's 401 ({detail:{message}}) with its message", async () => {
    refreshIssues();
    const python = { detail: { reasonCode: 'user_not_found', message: 'Your account is still being set up.' } };
    fakeServer({ status: 401, data: python }, { status: 401, data: python });
    await expect(apiClient.get('/api/v1/agents/a1')).rejects.toMatchObject({ message: 'Your account is still being set up.' });
  });

  it.each(['head', 'options'] as const)('replays a %s once after the refresh, like a GET', async (method) => {
    refreshIssues();
    const sent = fakeServer({ status: 401 }, { status: 200 });
    await apiClient.request({ method, url: '/a' });
    expect(sent.map((c) => c.method)).toEqual([method, method]);
  });

  it('signs out when the replayed request is answered "the session is over"', async () => {
    refreshIssues();
    fakeServer({ status: 401, data: notYourUser }, { status: 401, data: sessionOver });
    expect(await settlesSoon(apiClient.get('/a'))).toBe(false);
    expect(logoutAndRedirect).toHaveBeenCalledTimes(1);
  });

  it('signs out when a replayed download says so in a Blob body', async () => {
    refreshIssues();
    fakeServer({ status: 401, data: jsonBlob(notYourUser) }, { status: 401, data: jsonBlob(sessionOver) });
    expect(await settlesSoon(apiClient.get('/file', { responseType: 'blob' }))).toBe(false);
    expect(logoutAndRedirect).toHaveBeenCalledTimes(1);
  });

  it('does not sign out on a 403 that carries the "session is over" text', async () => {
    fakeServer({ status: 403, data: sessionOver });
    await expect(apiClient.get('/a')).rejects.toMatchObject({ type: ErrorType.AUTHORIZATION_ERROR });
    expect(logoutAndRedirect).not.toHaveBeenCalled();
  });

  it('logs out without trying to refresh when the server says the session is over', async () => {
    storage.set('workspace_session_token', 'x');
    sessionStorage.setItem('draft', 'x');
    fakeServer({ status: 401, data: sessionOver });

    expect(await settlesSoon(apiClient.get('/a'))).toBe(false);

    expect(fetchMock).not.toHaveBeenCalled();
    expect(logoutAndRedirect).toHaveBeenCalledTimes(1);
    expect(storage.has('workspace_session_token')).toBe(false);
    expect(sessionStorage.getItem('draft')).toBeNull();
  });

  // 400 = locked account, 403 = foreign origin, 404 = no credentials (the refresh handler in userAccount.controller.ts).
  it.each([400, 401, 403, 404])('logs out when the refresh is refused with %s', async (status) => {
    fetchMock.mockResolvedValueOnce(jsonResponse(status, {}));
    fakeServer({ status: 401 });
    expect(await settlesSoon(apiClient.get('/a'))).toBe(false);
    expect(logoutAndRedirect).toHaveBeenCalledTimes(1);
  });

  it.each(noAnswer)('keeps the session, does not replay, and says to try again shortly when the refresh gets %s', async (_label, arrange) => {
    arrange();
    const token = useAuthStore.getState().accessToken;
    const sent = fakeServer({ status: 401 });

    await expect(apiClient.get('/api/v1/users/me')).rejects.toMatchObject({
      type: ErrorType.SERVER_ERROR,
      statusCode: 503,
      message: sessionUnavailableMessage(),
    });

    expect(sent).toHaveLength(1);
    expect(logoutAndRedirect).not.toHaveBeenCalled();
    expect(useAuthStore.getState().accessToken).toBe(token);
    expect(errorToasts()).toHaveLength(1);
    expect(errorToasts()[0].title).toMatch(/try again shortly/i);
  });

  it('lets a caller suppress the toast when the refresh gets no answer', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(503, {}));
    fakeServer({ status: 401 });
    await expect(apiClient.get('/a', { suppressErrorToast: true })).rejects.toMatchObject({ statusCode: 503 });
    expect(errorToasts()).toHaveLength(0);
  });

  it('shares one refresh between 401s during an outage, and rejects them all', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(503, {}));
    fakeServer({ status: 401 }, { status: 401 });
    const results = await Promise.allSettled([apiClient.get('/a'), apiClient.get('/b')]);
    expect(results.map((r) => r.status)).toEqual(['rejected', 'rejected']);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(logoutAndRedirect).not.toHaveBeenCalled();
  });

  it('leaves nothing behind after an outage: the next 401 refreshes and replays with the new token', async () => {
    const fresh = jwtExpiringIn(3600);
    fetchMock.mockResolvedValueOnce(jsonResponse(503, {})).mockResolvedValueOnce(jsonResponse(200, { accessToken: fresh }));
    const sent = fakeServer({ status: 401 }, { status: 401 }, { status: 200, data: { ok: 1 } });

    await expect(apiClient.get('/a')).rejects.toMatchObject({ statusCode: 503 });
    const { data } = await apiClient.get('/b');

    expect(data).toEqual({ ok: 1 });
    expect(sent).toHaveLength(3);
    expect(bearer(sent[2])).toBe(`Bearer ${fresh}`);
    expect(logoutAndRedirect).not.toHaveBeenCalled();
  });

  describe('a write (never sent twice: the server may have acted on it before it answered 401)', () => {
    it.each(['post', 'put', 'patch', 'delete'] as const)(
      "rejects a %s with the server's words after the refresh, sent once, and keeps the session",
      async (method) => {
        const fresh = refreshIssues();
        const sent = fakeServer({ status: 401, data: wrongPassword }, { status: 200 });

        await expect(apiClient.request({ method, url: '/api/v1/x', data: { a: 1 } })).rejects.toMatchObject({
          type: ErrorType.AUTHORIZATION_ERROR,
          statusCode: 401,
          message: 'Current password is incorrect.',
        });

        expect(sent.map((c) => c.method)).toEqual([method]);
        expect(errorToasts().map((t) => t.description)).toEqual([expect.stringContaining('Current password is incorrect.')]);
        expect(logoutAndRedirect).not.toHaveBeenCalled();
        expect(useAuthStore.getState().accessToken).toBe(fresh);
      },
    );

    it("lets the caller's suppress predicate see the refusal, and shows no toast", async () => {
      refreshIssues();
      fakeServer({ status: 401, data: wrongPassword });
      const seen: unknown[] = [];
      await expect(
        apiClient.post('/api/v1/x', {}, { suppressErrorToast: (e) => (seen.push(e), true) }),
      ).rejects.toMatchObject({ type: ErrorType.AUTHORIZATION_ERROR, statusCode: 401 });
      expect(seen).toEqual([expect.objectContaining({ type: ErrorType.AUTHORIZATION_ERROR })]);
      expect(errorToasts()).toHaveLength(0);
    });

    it('signs out and is dropped when the refresh is refused', async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse(401, {}));
      const sent = fakeServer({ status: 401 });
      expect(await settlesSoon(apiClient.post('/api/v1/x'))).toBe(false);
      expect(sent).toHaveLength(1);
      expect(logoutAndRedirect).toHaveBeenCalledTimes(1);
    });

    it('gets the busy error, is sent once and keeps the session when the refresh gets no answer', async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse(503, {}));
      const sent = fakeServer({ status: 401, data: notYourUser }, { status: 200 });
      await expect(apiClient.post('/api/v1/x', { a: 1 })).rejects.toMatchObject({ type: ErrorType.SERVER_ERROR, statusCode: 503 });
      expect(sent).toHaveLength(1);
      expect(logoutAndRedirect).not.toHaveBeenCalled();
    });

    it('is dropped with no toast, sent once, when the session ends while the refresh is out', async () => {
      let answer!: (r: Response) => void;
      fetchMock.mockReturnValueOnce(new Promise<Response>((resolve) => (answer = resolve)));
      const sent = fakeServer({ status: 401, data: notYourUser }, { status: 200 });
      const request = apiClient.post('/api/v1/x', { a: 1 });
      await new Promise((resolve) => setTimeout(resolve, 10));
      useAuthStore.getState().logout();
      answer(jsonResponse(200, { accessToken: jwtExpiringIn(3600) }));
      expect(await settlesSoon(request)).toBe(false);
      expect(sent).toHaveLength(1);
      expect(errorToasts()).toHaveLength(0);
    });
  });

  it('signs out when the server refuses the refresh after an outage', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(503, {})).mockResolvedValueOnce(jsonResponse(401, {}));
    fakeServer({ status: 401 }, { status: 401 });
    await expect(apiClient.get('/a')).rejects.toMatchObject({ statusCode: 503 });
    expect(logoutAndRedirect).not.toHaveBeenCalled();
    expect(await settlesSoon(apiClient.get('/b'))).toBe(false);
    expect(logoutAndRedirect).toHaveBeenCalledTimes(1);
  });
});

describe('a refresh that hands over another account', () => {
  // Someone else signed in to this browser while the refresh was out: the cookie now names them.
  const assign = vi.fn();
  beforeEach(() => {
    assign.mockReset();
    vi.stubGlobal('location', { ...window.location, assign });
  });

  it('keeps the token out of the store, reloads the page and answers account-changed', async () => {
    const mine = tokenFor('u1', 20);
    useAuthStore.setState({ accessToken: mine });
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { accessToken: tokenFor('u2') }));
    expect(await refreshOrSignOut()).toBe('account-changed');
    expect(useAuthStore.getState().accessToken).toBe(mine);
    expect(assign).toHaveBeenCalledWith('/chat/');
    expect(logoutAndRedirect).not.toHaveBeenCalled();
  });

  it('does not send a request made as one account with the token of another', async () => {
    const mine = tokenFor('u1', 20);
    useAuthStore.setState({ accessToken: mine });
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { accessToken: tokenFor('u2') }));
    const sent = fakeServer({ status: 200 });
    await expect(apiClient.get('/api/v1/users')).rejects.toMatchObject({ type: ErrorType.REQUEST_CANCELLED });
    expect(sent).toHaveLength(0);
    expect(useAuthStore.getState().accessToken).toBe(mine);
    expect(assign).toHaveBeenCalledWith('/chat/');
    expect(errorToasts()).toHaveLength(0);
    expect(logoutAndRedirect).not.toHaveBeenCalled();
  });

  it('does not replay a 401 as another account, and the next request is not sent as them either', async () => {
    const mine = tokenFor('u1');
    useAuthStore.setState({ accessToken: mine });
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { accessToken: tokenFor('u2') }));
    const sent = fakeServer({ status: 401 }, { status: 200 });
    await expect(apiClient.get('/api/v1/users')).rejects.toMatchObject({ type: ErrorType.REQUEST_CANCELLED });
    expect(sent).toHaveLength(1);
    expect(useAuthStore.getState().accessToken).toBe(mine);
    expect(assign).toHaveBeenCalledWith('/chat/');
    expect(errorToasts()).toHaveLength(0);
  });

  it('answers account-changed to a caller whose request was made as another account than the store holds now', async () => {
    const now = tokenFor('u2', 20);
    useAuthStore.setState({ accessToken: now });
    const fresh = tokenFor('u2', 7200);
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { accessToken: fresh }));
    expect(await refreshOrSignOut(tokenFor('u1'))).toBe('account-changed');
    // The store's own account did not change: its token is kept and nothing reloads.
    expect(useAuthStore.getState().accessToken).toBe(fresh);
    expect(assign).not.toHaveBeenCalled();
  });

  it('still sends and replays for the same account', async () => {
    useAuthStore.setState({ accessToken: tokenFor('u1') });
    const fresh = tokenFor('u1', 7200);
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { accessToken: fresh }));
    const sent = fakeServer({ status: 401 }, { status: 200 });
    await expect(apiClient.get('/api/v1/users')).resolves.toMatchObject({ status: 200 });
    expect(sent).toHaveLength(2);
    expect(bearer(sent[1])).toBe(`Bearer ${fresh}`);
    expect(assign).not.toHaveBeenCalled();
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
      ['Server error', 'The export could not be created. Try again in a minute. Reference: req-42'],
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
    expect(errorToasts()[0].title).toBe('Connection error');
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
    expect(errorToasts().map((t) => t.title)).toEqual(['Action required']);
  });

  it('reads the error out of a Blob body from a download request', async () => {
    fakeServer({ status: 403, data: jsonBlob({ message: 'You can only download files you have access to.' }) });
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

  it('keeps the session when that refresh gets no answer, and does not retry in a loop', async () => {
    vi.useFakeTimers();
    fetchMock.mockResolvedValue(jsonResponse(503, {}));
    scheduler.scheduleFromToken(jwtExpiringIn(30));
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    // A bounded number of later tries, then it waits for activity (a request, the tab, the network).
    await vi.advanceTimersByTimeAsync(600_000);
    expect(fetchMock).toHaveBeenCalledTimes(1 + REFRESH_MAX_COOLDOWN_CYCLES);
    await vi.advanceTimersByTimeAsync(600_000);
    expect(fetchMock).toHaveBeenCalledTimes(1 + REFRESH_MAX_COOLDOWN_CYCLES);
    expect(logoutAndRedirect).not.toHaveBeenCalled();
  });

  it('tries at once when the tab comes back while it waits to try again', async () => {
    vi.useFakeTimers();
    vi.spyOn(Math, 'random').mockReturnValue(1);
    fetchMock.mockResolvedValueOnce(jsonResponse(503, {})).mockResolvedValueOnce(jsonResponse(503, {}));
    // A 401 got no answer from its refresh; the token in the store is not due, so only the retry would refresh.
    const sent = fakeServer({ status: 401 });
    await expect(apiClient.get('/api/v1/users')).rejects.toMatchObject({ statusCode: 503 });
    expect(sent).toHaveLength(1);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    scheduler.initTokenRefreshScheduler();
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    document.dispatchEvent(new Event('visibilitychange'));
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(logoutAndRedirect).not.toHaveBeenCalled();
  });

  it('refreshes once when the tab comes back while the proactive timer is still pending', async () => {
    vi.useFakeTimers();
    vi.spyOn(Math, 'random').mockReturnValue(1);
    const token = jwtExpiringIn(600);
    useAuthStore.setState({ accessToken: token });
    scheduler.initTokenRefreshScheduler();
    scheduler.scheduleFromToken(token);
    // A 401 whose refresh got no answer: a retry is waiting, and the proactive timer (at 510 s) is still armed.
    fetchMock.mockResolvedValueOnce(jsonResponse(503, {}));
    fakeServer({ status: 401 });
    await expect(apiClient.get('/api/v1/users')).rejects.toMatchObject({ statusCode: 503 });
    expect(fetchMock).toHaveBeenCalledTimes(1);

    fetchMock.mockResolvedValue(jsonResponse(200, { accessToken: jwtExpiringIn(3600) }));
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    document.dispatchEvent(new Event('visibilitychange'));
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledTimes(2);

    // Past the old timer's 510 s; the new token's timer is not due until 3510 s.
    await vi.advanceTimersByTimeAsync(600_000);
    expect(fetchMock).toHaveBeenCalledTimes(2);
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

    const hintEvent = (newValue: string | null, oldValue: string | null = 'user-a:o1') =>
      window.dispatchEvent(new StorageEvent('storage', { key: SESSION_HINT_KEY, newValue, oldValue }));

    it('starts over on a fresh chat page when another tab signs in as someone else', () => {
      const originalLocation = window.location;
      const assign = vi.fn();
      Object.defineProperty(window, 'location', { value: { assign }, configurable: true });
      try {
        useAuthStore.setState({ accessToken: tokenFor('user-a') });

        hintEvent('user-a:o1', null);
        expect(assign).not.toHaveBeenCalled();

        hintEvent('user-b:o1');
        // A page load drops every cache and store of the previous account, and the
        // chat it had open, which belongs to that account.
        expect(assign).toHaveBeenCalledWith('/chat/');
        expect(fetchMock).not.toHaveBeenCalled();
      } finally {
        Object.defineProperty(window, 'location', { value: originalLocation, configurable: true });
      }
    });

    it('signs out here when another tab signs out', () => {
      hintEvent(null);
      expect(useAuthStore.getState().accessToken).toBeNull();
    });

    it('signs in here from the session when another tab signs in', async () => {
      useAuthStore.getState().logout();
      const fresh = tokenFor('user-a');
      storage.set(SESSION_HINT_KEY, 'user-a:o1');
      fetchMock.mockResolvedValueOnce(jsonResponse(200, { accessToken: fresh }));
      hintEvent('user-a:o1', null);
      await vi.waitFor(() => expect(useAuthStore.getState().accessToken).toBe(fresh));
      expect((fetchMock.mock.calls[0][1] as RequestInit).credentials).toBe('include');
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
