/** One automatic retry of idempotent requests after a transport error, faked at the axios adapter. */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { AxiosError, AxiosHeaders, type AxiosAdapter, type InternalAxiosRequestConfig } from 'axios';
import { installMemoryStorage, jwtExpiringIn } from './sse-response';

installMemoryStorage();
installMemoryStorage('sessionStorage');

vi.mock('@/config', async () => {
  const auth = await vi.importActual<typeof import('@/lib/store/auth-store')>('@/lib/store/auth-store');
  return { useAuthStore: auth.useAuthStore, logoutAndRedirect: vi.fn() };
});

const { useAuthStore } = await import('@/lib/store/auth-store');
const { apiClient } = await import('../axios-instance');
const { ErrorType } = await import('../api-error');
const { useToastStore } = await import('@/lib/store/toast-store');

type Step = 'network' | 'timeout' | 'ok' | 401;

function fake(...steps: Step[]) {
  const sent: InternalAxiosRequestConfig[] = [];
  const adapter: AxiosAdapter = async (config) => {
    sent.push(config);
    const step = steps.shift() ?? 'ok';
    if (step === 'network') throw new AxiosError('Network Error', 'ERR_NETWORK', config);
    if (step === 'timeout') throw new AxiosError('timeout of 90000ms exceeded', 'ECONNABORTED', config);
    const response = {
      data: {},
      status: step === 'ok' ? 200 : step,
      statusText: '',
      headers: new AxiosHeaders(),
      config,
    };
    if (step === 'ok') return response;
    throw new AxiosError('Request failed', 'ERR_BAD_RESPONSE', config, null, response);
  };
  apiClient.defaults.adapter = adapter;
  return sent;
}

beforeEach(() => {
  vi.useFakeTimers();
  useAuthStore.setState({ accessToken: jwtExpiringIn(3600), refreshToken: 'r', isAuthenticated: true });
  useToastStore.setState({ toasts: [] });
  vi.spyOn(console, 'warn').mockImplementation(() => {});
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('retry after a transport error', () => {
  it('retries a GET once and resolves with the second answer', async () => {
    const sent = fake('network', 'ok');
    const p = apiClient.get('/api/v1/x');
    await vi.advanceTimersByTimeAsync(800);
    expect((await p).status).toBe(200);
    expect(sent).toHaveLength(2);
    expect(useToastStore.getState().toasts).toHaveLength(0);
  });

  it('waits between 250 and 750 ms', async () => {
    const sent = fake('network', 'ok');
    const p = apiClient.get('/api/v1/x');
    await vi.advanceTimersByTimeAsync(240);
    expect(sent).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(520);
    expect(sent).toHaveLength(2);
    await p;
  });

  it('does not retry a POST', async () => {
    const sent = fake('network', 'ok');
    await expect(apiClient.post('/api/v1/x', {})).rejects.toMatchObject({ type: ErrorType.NETWORK_ERROR });
    expect(sent).toHaveLength(1);
  });

  it('does not retry a timeout', async () => {
    const sent = fake('timeout', 'ok');
    await expect(apiClient.get('/api/v1/x')).rejects.toMatchObject({ type: ErrorType.TIMEOUT_ERROR });
    expect(sent).toHaveLength(1);
  });

  it('respects retryOnNetworkError: false', async () => {
    const sent = fake('network', 'ok');
    await expect(apiClient.get('/api/v1/x', { retryOnNetworkError: false })).rejects.toMatchObject({
      type: ErrorType.NETWORK_ERROR,
    });
    expect(sent).toHaveLength(1);
  });

  it('rejects as cancelled, without a second call, when aborted during the wait', async () => {
    const sent = fake('network', 'ok');
    const ctrl = new AbortController();
    const p = apiClient.get('/api/v1/x', { signal: ctrl.signal });
    const assertion = expect(p).rejects.toMatchObject({ type: ErrorType.REQUEST_CANCELLED });
    await vi.advanceTimersByTimeAsync(100);
    ctrl.abort();
    await vi.advanceTimersByTimeAsync(1000);
    await assertion;
    expect(sent).toHaveLength(1);
  });

  it('surfaces the error when the retry fails too, with no third call', async () => {
    const sent = fake('network', 'network', 'ok');
    const p = apiClient.get('/api/v1/x');
    const assertion = expect(p).rejects.toMatchObject({ type: ErrorType.NETWORK_ERROR });
    await vi.advanceTimersByTimeAsync(2000);
    await assertion;
    expect(sent).toHaveLength(2);
    expect(useToastStore.getState().toasts.filter((t) => t.variant === 'error')).toHaveLength(1);
  });

  it('keeps the 401 refresh path independent of the network retry mark', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ accessToken: jwtExpiringIn(3600) }), { status: 200 }),
    );
    vi.stubGlobal('fetch', fetchMock);
    const sent = fake('network', 401, 'ok');
    const p = apiClient.get('/api/v1/x');
    await vi.advanceTimersByTimeAsync(2000);
    expect((await p).status).toBe(200);
    expect(sent).toHaveLength(3);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    vi.unstubAllGlobals();
  });
});
