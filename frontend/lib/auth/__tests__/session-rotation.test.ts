/**
 * R43AUTH-FU-1 (ADR-WEB-02 D9): every refresh replaces the refresh token, and a replaced token presented again ends
 * the session. The desktop keeps the replacement; refreshes of several tabs or windows go one at a time, so none
 * presents a token another has already replaced.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { installMemoryStorage, jsonResponse } from '@/lib/api/__tests__/sse-response';

installMemoryStorage();
installMemoryStorage('sessionStorage');

const electron = { on: false };
vi.mock('@/lib/electron', () => ({
  isElectron: () => electron.on,
  applyElectronOverrides: (c: unknown) => c,
  getElectronApiBaseUrl: () => 'http://server.test',
}));

const fetchMock = vi.fn<typeof fetch>();
const vault = { token: null as string | null, read: vi.fn(), write: vi.fn(), clear: vi.fn() };

/** A Web Locks stand-in: one holder per name, the others queue (what every current browser does). */
function installLocks() {
  const tails = new Map<string, Promise<unknown>>();
  const request = vi.fn((name: string, _options: unknown, work: () => Promise<unknown>) => {
    const run = (tails.get(name) ?? Promise.resolve()).then(() => work());
    tails.set(name, run.catch(() => undefined));
    return run;
  });
  Object.defineProperty(navigator, 'locks', { value: { request }, configurable: true });
  return request;
}

beforeEach(() => {
  electron.on = false;
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
  vault.token = null;
  for (const fn of [vault.read, vault.write, vault.clear]) fn.mockReset();
  vault.read.mockImplementation(async () => vault.token);
  vault.write.mockImplementation(async (t: string) => {
    vault.token = t;
    return true;
  });
  (window as unknown as { electronAPI?: unknown }).electronAPI = {
    session: { readRefreshToken: vault.read, writeRefreshToken: vault.write, clearRefreshToken: vault.clear },
  };
});

afterEach(() => {
  delete (window as unknown as { electronAPI?: unknown }).electronAPI;
  delete (navigator as unknown as { locks?: unknown }).locks;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('refresh-token rotation in the page', () => {
  it('the desktop seals the replacement the server answered with, and sends it next time', async () => {
    electron.on = true;
    vault.token = 'first';
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, { accessToken: 'a1', refreshToken: 'second' }))
      .mockResolvedValueOnce(jsonResponse(200, { accessToken: 'a2', refreshToken: 'third' }));
    const { requestAccessToken } = await import('@/lib/auth/session-credential');
    expect(await requestAccessToken()).toMatchObject({ outcome: 'issued', accessToken: 'a1' });
    expect(vault.write).toHaveBeenCalledWith('second');
    await requestAccessToken();
    const headers = (fetchMock.mock.calls[1][1] as RequestInit & { headers: Record<string, string> }).headers;
    expect(headers.Authorization).toBe('Bearer second');
    expect(vault.token).toBe('third');
  });

  it('two tabs refreshing at once go one after the other under one lock', async () => {
    const request = installLocks();
    let inFlight = 0;
    let most = 0;
    fetchMock.mockImplementation(async () => {
      inFlight += 1;
      most = Math.max(most, inFlight);
      await new Promise((resolve) => setTimeout(resolve, 5));
      inFlight -= 1;
      return jsonResponse(200, { accessToken: 'a' });
    });
    const { requestAccessToken, REFRESH_LOCK_NAME } = await import('@/lib/auth/session-credential');
    const answers = await Promise.all([requestAccessToken(), requestAccessToken()]);
    expect(answers.every((a) => a.outcome === 'issued')).toBe(true);
    expect(most, 'never two refreshes out at once').toBe(1);
    expect(request).toHaveBeenCalledTimes(2);
    expect(request.mock.calls[0][0]).toBe(REFRESH_LOCK_NAME);
  });

  it('the desktop writes the replacement before the next window may read the vault', async () => {
    electron.on = true;
    installLocks();
    vault.token = 't0';
    let n = 0;
    fetchMock.mockImplementation(async (_url, init) => {
      const sentToken = (init as RequestInit & { headers: Record<string, string> }).headers.Authorization;
      n += 1;
      return jsonResponse(200, { accessToken: `a${n}`, refreshToken: `t${n}`, sentToken });
    });
    const { requestAccessToken } = await import('@/lib/auth/session-credential');
    await Promise.all([requestAccessToken(), requestAccessToken()]);
    const sentTokens = fetchMock.mock.calls.map(
      (c) => (c[1] as RequestInit & { headers: Record<string, string> }).headers.Authorization,
    );
    expect(sentTokens).toEqual(['Bearer t0', 'Bearer t1']);
    expect(vault.token).toBe('t2');
  });

  it('the desktop keeps the replacement when the body of a yes arrives after the attempt limit', async () => {
    vi.useFakeTimers();
    try {
      electron.on = true;
      vault.token = 'first';
      // Like fetch: the headers are in, the body is still coming, and an abort of the request errors the body read.
      fetchMock.mockImplementationOnce(async (_url, init) => {
        const body = new ReadableStream<Uint8Array>({
          start(controller) {
            let aborted = false;
            init?.signal?.addEventListener('abort', () => {
              aborted = true;
              controller.error(new DOMException('The user aborted a request.', 'AbortError'));
            });
            setTimeout(() => {
              if (aborted) return;
              controller.enqueue(new TextEncoder().encode(JSON.stringify({ accessToken: 'a1', refreshToken: 'second' })));
              controller.close();
            }, 15_000);
          },
        });
        return new Response(body, { status: 200, headers: { 'Content-Type': 'application/json' } });
      });
      const { requestAccessToken } = await import('@/lib/auth/session-credential');
      const answer = requestAccessToken();
      await vi.advanceTimersByTimeAsync(15_000);
      expect(await answer).toMatchObject({ outcome: 'issued', accessToken: 'a1' });
      expect(vault.token).toBe('second');
    } finally {
      vi.useRealTimers();
    }
  });

  /** A yes whose body arrives only when `release` is called (never, if it is not). */
  function stalledYes(body: unknown) {
    let release = () => {};
    fetchMock.mockImplementationOnce(async () => {
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          release = () => {
            controller.enqueue(new TextEncoder().encode(JSON.stringify(body)));
            controller.close();
          };
        },
      });
      return new Response(stream, { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    return () => release();
  }

  it('a yes whose body stalls settles as no answer, frees the lock, and keeps a replacement that arrives later', async () => {
    vi.useFakeTimers();
    try {
      installLocks();
      electron.on = true;
      vault.token = 'first';
      const release = stalledYes({ accessToken: 'a1', refreshToken: 'second' });
      const { requestAccessToken } = await import('@/lib/auth/session-credential');
      const { REFRESH_BODY_TIMEOUT_MS } = await import('@/lib/api/refresh-retry');
      const answer = requestAccessToken();
      await vi.advanceTimersByTimeAsync(REFRESH_BODY_TIMEOUT_MS);
      expect(await answer).toEqual({ outcome: 'unreachable' });
      expect(vault.clear).not.toHaveBeenCalled();
      expect(vault.token).toBe('first');
      release();
      await vi.advanceTimersByTimeAsync(0);
      expect(vault.token).toBe('second');
    } finally {
      vi.useRealTimers();
    }
  });

  it('a late replacement does not overwrite the token a later refresh kept', async () => {
    vi.useFakeTimers();
    try {
      installLocks();
      electron.on = true;
      vault.token = 'first';
      const release = stalledYes({ accessToken: 'a1', refreshToken: 'stale' });
      fetchMock.mockResolvedValueOnce(jsonResponse(200, { accessToken: 'a2', refreshToken: 'newer' }));
      const { requestAccessToken } = await import('@/lib/auth/session-credential');
      const { REFRESH_BODY_TIMEOUT_MS } = await import('@/lib/api/refresh-retry');
      const first = requestAccessToken();
      await vi.advanceTimersByTimeAsync(REFRESH_BODY_TIMEOUT_MS);
      expect(await first).toEqual({ outcome: 'unreachable' });
      expect(await requestAccessToken()).toMatchObject({ outcome: 'issued', accessToken: 'a2' });
      release();
      await vi.advanceTimersByTimeAsync(0);
      expect(vault.token).toBe('newer');
    } finally {
      vi.useRealTimers();
    }
  });

  it('a web answer carries no refresh token for the page to keep', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { accessToken: 'a1' }));
    const { requestAccessToken } = await import('@/lib/auth/session-credential');
    const answer = await requestAccessToken();
    expect(answer).toEqual({ outcome: 'issued', accessToken: 'a1' });
    expect(vault.write).not.toHaveBeenCalled();
  });
});
