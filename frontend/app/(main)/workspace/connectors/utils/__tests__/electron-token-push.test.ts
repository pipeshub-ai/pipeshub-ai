/**
 * The desktop's main process holds the access token and the server it belongs to. In local mode the server's port changes
 * when the launcher restarts (a crash, then Retry), so a new URL must reach main even when the token did not change.
 */
import { describe, it, expect, vi } from 'vitest';
import { installMemoryStorage } from '@/lib/api/__tests__/sse-response';

installMemoryStorage();
installMemoryStorage('sessionStorage');
const setAccessToken = vi.fn(async () => ({ ok: true }));
(window as unknown as { electronAPI?: unknown }).electronAPI = { isElectron: true, localSync: { setAccessToken } };

vi.mock('@/config', async () => {
  const auth = await vi.importActual<typeof import('@/lib/store/auth-store')>('@/lib/store/auth-store');
  return { useAuthStore: auth.useAuthStore, logoutAndRedirect: () => auth.useAuthStore.getState().logout() };
});

const storage = await import('@/lib/electron/api-base-url-storage');
const { useAuthStore } = await import('@/lib/store/auth-store');
const { pushElectronDesktopAccessToken, startElectronDesktopTokenBridge } = await import('../electron-local-sync');

describe('the desktop token bridge', () => {
  it('pushes the token again when the local server comes back on a new port', async () => {
    storage.setLocalModeApiBaseUrl('http://127.0.0.1:41000');
    useAuthStore.getState().setTokens('tok', 'refresh');
    startElectronDesktopTokenBridge();
    await pushElectronDesktopAccessToken();
    await pushElectronDesktopAccessToken();
    expect(setAccessToken.mock.calls).toEqual([['tok', 'http://127.0.0.1:41000']]);

    storage.setLocalModeApiBaseUrl(null);            // crashed; Retry is starting it again
    storage.setLocalModeApiBaseUrl('http://127.0.0.1:52000');
    await vi.waitFor(() => expect(setAccessToken).toHaveBeenCalledTimes(2));
    expect(setAccessToken.mock.calls[1]).toEqual(['tok', 'http://127.0.0.1:52000']);
  });

  it('pushes again when the server comes back on the same port (main forgot the token when it went away)', async () => {
    setAccessToken.mockClear();
    storage.setLocalModeApiBaseUrl(null);
    storage.setLocalModeApiBaseUrl('http://127.0.0.1:52000');
    await vi.waitFor(() => expect(setAccessToken).toHaveBeenCalledTimes(1));
    expect(setAccessToken.mock.calls[0]).toEqual(['tok', 'http://127.0.0.1:52000']);
  });
});
