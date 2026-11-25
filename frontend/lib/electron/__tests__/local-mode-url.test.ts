/**
 * Local mode's server URL lives in memory for this launch only: the launcher picks a new port each start, so a URL saved by
 * an earlier launch would point a refresh token at whatever listens on that old port now.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { installMemoryStorage, jsonResponse } from '../../api/__tests__/sse-response';

installMemoryStorage();
installMemoryStorage('sessionStorage');
(window as unknown as { electronAPI?: unknown }).electronAPI = { isElectron: true };

vi.mock('@/config', async () => {
  const auth = await vi.importActual<typeof import('@/lib/store/auth-store')>('@/lib/store/auth-store');
  return {
    useAuthStore: auth.useAuthStore,
    readStoredRefreshToken: auth.readStoredRefreshToken,
    logoutAndRedirect: () => auth.useAuthStore.getState().logout(),
  };
});

const storage = await import('../api-base-url-storage');
const { useAuthStore } = await import('@/lib/store/auth-store');
const { refreshAccessToken } = await import('@/lib/api/token-refresh');

describe('local mode URL', () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
    storage.resetApiBaseUrlModeForTests();
  });

  it('is kept in memory and never saved; an earlier launch saved URL is ignored', () => {
    window.localStorage.setItem('PIPESHUB_API_BASE_URL', 'http://127.0.0.1:41000');
    storage.setLocalModeApiBaseUrl(null);
    expect(storage.getElectronApiBaseUrl()).toBe('');
    storage.setLocalModeApiBaseUrl('http://127.0.0.1:52000');
    expect(storage.getElectronApiBaseUrl()).toBe('http://127.0.0.1:52000');
    expect(window.localStorage.getItem('PIPESHUB_API_BASE_URL')).toBe('http://127.0.0.1:41000');
    expect(window.sessionStorage.length).toBe(0);
  });

  it('a token refresh waits for this launch\'s URL, then goes there and nowhere else', async () => {
    window.localStorage.setItem('PIPESHUB_API_BASE_URL', 'http://127.0.0.1:41000');
    useAuthStore.getState().setTokens('old-access', 'refresh-1');
    const fetchMock = vi.fn(async () => jsonResponse(200, { accessToken: "new-access" }));
    vi.stubGlobal('fetch', fetchMock);
    storage.setLocalModeApiBaseUrl(null);
    const refreshed = refreshAccessToken();
    await new Promise((r) => setTimeout(r, 20));
    expect(fetchMock).not.toHaveBeenCalled();
    storage.setLocalModeApiBaseUrl('http://127.0.0.1:52000');
    expect(await refreshed).toBe(true);
    // Keeping the sign-in's refresh token and the refresh itself both wait, and both go to this launch's server.
    expect(fetchMock).toHaveBeenCalled();
    for (const [url] of fetchMock.mock.calls as unknown as [string][]) {
      expect(String(url)).toMatch(/^http:\/\/127\.0\.0\.1:52000\//);
    }
    vi.unstubAllGlobals();
  });

  it('server mode keeps its saved URL and does not wait', async () => {
    window.localStorage.setItem('PIPESHUB_API_BASE_URL', 'https://pipeshub.example.com');
    storage.markServerMode();
    expect(storage.getElectronApiBaseUrl()).toBe('https://pipeshub.example.com');
    await storage.whenApiBaseUrlReady();
  });
});
