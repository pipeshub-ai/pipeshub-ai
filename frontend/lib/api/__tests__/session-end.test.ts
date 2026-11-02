/**
 * What happens to the last session's work when the session ends (sign-out on
 * the desktop app does not reload the page): its apiClient answers are
 * dropped, its token refresh cannot sign it back in, and its failures cannot
 * sign the next person out. The network is faked at the axios adapter and at
 * `fetch` (the refresh call).
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { AxiosError, AxiosHeaders, type AxiosAdapter, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios';
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

vi.mock('@/lib/utils/jwt', () => ({ getUserIdFromToken: () => 'user-a' }));

const { useAuthStore, ACCESS_TOKEN_STORAGE_KEY, REFRESH_TOKEN_STORAGE_KEY } = await import('@/lib/store/auth-store');
const { useFeatureFlagsStore } = await import('@/lib/store/feature-flags-store');
const { useInitializeUserProfile } = await import('@/lib/hooks/use-initialize-user-profile');
const { useUserStore } = await import('@/lib/store/user-store');
const { renderHook, act } = await import('@testing-library/react');
const { apiClient } = await import('../axios-instance');
const { refreshAccessToken, refreshOrSignOut } = await import('../token-refresh');
const { streamSSERequest } = await import('../streaming');
const scheduler = await import('../token-refresh-scheduler');
const { useToastStore } = await import('@/lib/store/toast-store');

type Reply = { status: number; data?: unknown };

/** Each request waits until the test answers it, so the session can end in between. */
function deferredServer() {
  const pending: Array<{ config: InternalAxiosRequestConfig; answer: (reply: Reply) => void }> = [];
  const adapter: AxiosAdapter = (config) =>
    new Promise<AxiosResponse>((resolve, reject) => {
      pending.push({
        config,
        answer: ({ status, data = {} }) => {
          const response = { data, status, statusText: '', headers: new AxiosHeaders(), config };
          if (status < 400) resolve(response);
          else reject(new AxiosError(`Request failed with status code ${status}`, 'ERR_BAD_RESPONSE', config, null, response));
        },
      });
    });
  apiClient.defaults.adapter = adapter;
  return pending;
}

/** Records whether a promise ever settles. */
function watch<T>(promise: Promise<T>) {
  const seen = { resolved: false, rejected: false, settled: false };
  promise.then(
    () => Object.assign(seen, { resolved: true, settled: true }),
    () => Object.assign(seen, { rejected: true, settled: true }),
  );
  return seen;
}

async function flush() {
  for (let i = 0; i < 10; i += 1) await Promise.resolve();
  await new Promise((r) => setTimeout(r, 0));
}

const signOut = () => useAuthStore.getState().logout();
const signIn = (token = jwtExpiringIn(3600)) =>
  useAuthStore.getState().setTokens(token, 'refresh-b');
const fetchMock = vi.fn<typeof fetch>();

beforeEach(() => {
  storage.clear();
  fetchMock.mockReset();
  logoutAndRedirect.mockReset();
  vi.stubGlobal('fetch', fetchMock);
  useAuthStore.setState({ accessToken: jwtExpiringIn(3600), refreshToken: 'refresh-a', isAuthenticated: true });
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

describe('an apiClient answer that arrives after the session ended', () => {
  it('never reaches the caller when it succeeds', async () => {
    const pending = deferredServer();
    const seen = watch(apiClient.get('/api/v1/users'));
    await flush();

    signOut();
    pending[0].answer({ status: 200, data: { name: 'Ada' } });
    await flush();

    expect(seen.settled).toBe(false);
  });

  it('never reaches the caller, and shows no toast, when it fails', async () => {
    const pending = deferredServer();
    const seen = watch(apiClient.get('/api/v1/users'));
    await flush();

    signOut();
    pending[0].answer({ status: 500 });
    await flush();

    expect(seen.settled).toBe(false);
    expect(useToastStore.getState().toasts).toHaveLength(0);
  });

  it("does not touch the next person's requests", async () => {
    const pending = deferredServer();
    signOut();
    signIn();
    const seen = watch(apiClient.get('/api/v1/users'));
    await flush();

    pending[0].answer({ status: 200 });
    await flush();

    expect(seen.resolved).toBe(true);
  });

  it('neither refreshes nor signs out on a stale 401', async () => {
    const pending = deferredServer();
    watch(apiClient.get('/api/v1/users'));
    await flush();

    signOut();
    signIn();
    pending[0].answer({ status: 401 });
    await flush();

    expect(fetchMock).not.toHaveBeenCalled();
    expect(logoutAndRedirect).not.toHaveBeenCalled();
    expect(useAuthStore.getState().isAuthenticated).toBe(true);
  });

  it('still refreshes and retries a 401 within the session', async () => {
    const pending = deferredServer();
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { accessToken: jwtExpiringIn(3600) }));
    const seen = watch(apiClient.get('/api/v1/users'));
    await flush();

    pending[0].answer({ status: 401 });
    await flush();
    pending[1].answer({ status: 200 });
    await flush();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(seen.resolved).toBe(true);
  });

  it('keeps its session on the retry after a refresh', async () => {
    const pending = deferredServer();
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { accessToken: jwtExpiringIn(3600) }));
    const seen = watch(apiClient.get('/api/v1/users'));
    await flush();
    pending[0].answer({ status: 401 });
    await flush();

    signOut();
    pending[1].answer({ status: 200 });
    await flush();

    expect(seen.settled).toBe(false);
  });

  it('within the session, a server error still rejects and shows its toast', async () => {
    const pending = deferredServer();
    const seen = watch(apiClient.get('/api/v1/users'));
    await flush();

    pending[0].answer({ status: 500 });
    await flush();

    expect(seen.rejected).toBe(true);
    expect(useToastStore.getState().toasts.filter((t) => t.variant === 'error')).toHaveLength(1);
  });
});

describe('the request whose refresh fails, which ends the session', () => {
  it('signs out once and never settles: no toast, no error written into the reset stores', async () => {
    useAuthStore.setState({ accessToken: jwtExpiringIn(20) });
    deferredServer();
    fetchMock.mockResolvedValueOnce(jsonResponse(401, {}));
    const seen = watch(apiClient.get('/api/v1/users'));
    await flush();

    expect(logoutAndRedirect).toHaveBeenCalledTimes(1);
    expect(seen.settled).toBe(false);
    expect(useToastStore.getState().toasts).toHaveLength(0);
  });

  it('leaves the feature flags unloaded, so the next sign-in waits for its own', async () => {
    useAuthStore.setState({ accessToken: jwtExpiringIn(20) });
    deferredServer();
    fetchMock.mockResolvedValueOnce(jsonResponse(401, {}));
    void useFeatureFlagsStore.getState().fetchFlags();
    await flush();

    expect(logoutAndRedirect).toHaveBeenCalledTimes(1);
    expect(useFeatureFlagsStore.getState().flags).toBeNull();
  });

  it('a 401 whose refresh fails signs out once and never settles', async () => {
    const pending = deferredServer();
    fetchMock.mockResolvedValueOnce(jsonResponse(401, {}));
    const seen = watch(apiClient.get('/api/v1/users'));
    await flush();

    pending[0].answer({ status: 401 });
    await flush();

    expect(logoutAndRedirect).toHaveBeenCalledTimes(1);
    expect(seen.settled).toBe(false);
  });

  it('a 401 saying the session is over signs out without refreshing and never settles', async () => {
    const pending = deferredServer();
    const seen = watch(apiClient.get('/api/v1/users'));
    await flush();

    pending[0].answer({ status: 401, data: { error: { message: 'Session expired, please login again' } } });
    await flush();

    expect(fetchMock).not.toHaveBeenCalled();
    expect(logoutAndRedirect).toHaveBeenCalledTimes(1);
    expect(seen.settled).toBe(false);
  });
});

describe('a profile load that overlaps a sign-out', () => {
  it('writes nothing when the same person signs back in while it is out (no blank profile, no full-name prompt)', async () => {
    const pending = deferredServer();
    const { result } = renderHook(() => useInitializeUserProfile());
    void act(() => result.current.initialize(true));
    await flush();
    expect(pending.length).toBeGreaterThan(0);

    signOut();
    signIn();
    // The last session's answers all fail (the server ended that session).
    pending.forEach((p) => p.answer({ status: 401 }));
    await flush();

    expect(useUserStore.getState().profile).toBeNull();
    expect(useUserStore.getState().isLoading).toBe(false);
  });
});

describe('a token refresh that overlaps a sign-out', () => {
  function deferredRefresh() {
    let answer!: (r: Response) => void;
    fetchMock.mockReturnValueOnce(new Promise<Response>((r) => (answer = r)));
    return (r: Response) => answer(r);
  }

  it('does not sign the last person back in when it finishes after the sign-out', async () => {
    const answer = deferredRefresh();
    const refresh = refreshAccessToken();

    signOut();
    answer(jsonResponse(200, { accessToken: jwtExpiringIn(3600) }));

    expect(await refresh).toBe(false);
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
    expect(storage.has(ACCESS_TOKEN_STORAGE_KEY)).toBe(false);
  });

  it('does not sign the next person out when a 401 from before the sign-out fails to refresh after it', async () => {
    const pending = deferredServer();
    const answer = deferredRefresh();
    const seen = watch(apiClient.get('/api/v1/users'));
    await flush();
    pending[0].answer({ status: 401 });
    await flush();

    signOut();
    signIn();
    answer(jsonResponse(401, {}));
    await flush();

    expect(logoutAndRedirect).not.toHaveBeenCalled();
    expect(useAuthStore.getState().isAuthenticated).toBe(true);
    expect(seen.settled).toBe(false);
  });

  it('does not sign the next person out from the refresh before sending an expired token', async () => {
    useAuthStore.setState({ accessToken: jwtExpiringIn(20) });
    deferredServer();
    const answer = deferredRefresh();
    const seen = watch(apiClient.get('/api/v1/users'));
    await flush();

    signOut();
    signIn();
    answer(jsonResponse(401, {}));
    await flush();

    expect(logoutAndRedirect).not.toHaveBeenCalled();
    expect(seen.settled).toBe(false);
  });

  it('does not sign the next person out from a stream', async () => {
    useAuthStore.setState({ accessToken: jwtExpiringIn(20) });
    const answer = deferredRefresh();
    const errors: Error[] = [];
    const stream = streamSSERequest('/s', {}, { onEvent: () => {}, onError: (e) => errors.push(e) });
    await flush();

    signOut();
    signIn();
    answer(jsonResponse(401, {}));
    await stream;

    expect(logoutAndRedirect).not.toHaveBeenCalled();
    expect(useAuthStore.getState().isAuthenticated).toBe(true);
  });

  it("neither signs the next person out from the refresh timer nor stops the next person's timer", async () => {
    vi.useFakeTimers();
    const lastAnswer = deferredRefresh();
    scheduler.scheduleFromToken(jwtExpiringIn(30));
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    signOut();
    signIn(jwtExpiringIn(200));
    scheduler.scheduleFromToken(useAuthStore.getState().accessToken);
    lastAnswer(jsonResponse(401, {}));
    await vi.advanceTimersByTimeAsync(0);
    expect(logoutAndRedirect).not.toHaveBeenCalled();

    // The next person's token is refreshed 90 s before it expires.
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { accessToken: jwtExpiringIn(3600) }));
    await vi.advanceTimersByTimeAsync(115_000);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('does not sign the last person back in when another tab signed out while the refresh was out', async () => {
    storage.set(REFRESH_TOKEN_STORAGE_KEY, 'refresh-a');
    const answer = deferredRefresh();
    const before = useAuthStore.getState().accessToken;
    const refresh = refreshAccessToken();

    // The other tab's sign-out is in storage, but this tab has not handled the event yet.
    storage.delete(REFRESH_TOKEN_STORAGE_KEY);
    answer(jsonResponse(200, { accessToken: jwtExpiringIn(3600) }));

    expect(await refresh).toBe(false);
    expect(useAuthStore.getState().accessToken).toBe(before);
    expect(storage.has(REFRESH_TOKEN_STORAGE_KEY)).toBe(false);
  });

  it("never lets the next person wait on the last person's refresh", async () => {
    deferredRefresh();
    void refreshAccessToken();

    signOut();
    signIn();
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { accessToken: jwtExpiringIn(3600) }));

    expect(await refreshAccessToken()).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('still signs out when the refresh fails within the session', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(401, {}));
    expect(await refreshOrSignOut()).toBe('signed-out');
    expect(logoutAndRedirect).toHaveBeenCalledTimes(1);
  });
});
