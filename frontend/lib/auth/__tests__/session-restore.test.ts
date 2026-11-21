/**
 * R41OPS-FU-7 (ADR-WEB-02): the access token lives in memory and the refresh
 * token where no script can read it. A reload signs the person back in from
 * the session; an older build's localStorage tokens are moved over once.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { installMemoryStorage, jsonResponse } from '@/lib/api/__tests__/sse-response';

const storage = installMemoryStorage();
installMemoryStorage('sessionStorage');

const electron = { on: false };
vi.mock('@/lib/electron', () => ({
  isElectron: () => electron.on,
  applyElectronOverrides: (c: unknown) => c,
  getElectronApiBaseUrl: () => 'http://server.test',
}));

const fetchMock = vi.fn<typeof fetch>();

function tokenFor(userId: string, secondsFromNow = 3600): string {
  const claims = { exp: Math.floor(Date.now() / 1000) + secondsFromNow, userId, orgId: 'o1' };
  return `header.${btoa(JSON.stringify(claims)).replace(/=+$/, '')}.signature`;
}

/** A fresh page load: the store module runs its restore as it loads. */
async function loadPage() {
  vi.resetModules();
  const store = await import('@/lib/store/auth-store');
  await vi.waitFor(() => expect(store.useAuthStore.getState().isHydrated).toBe(true));
  return store.useAuthStore;
}

const vault = { token: null as string | null, read: vi.fn(), write: vi.fn(), clear: vi.fn() };
const sent = (i = 0) => fetchMock.mock.calls[i][1] as RequestInit & { headers: Record<string, string> };
const nothingSecretStored = (...secrets: string[]) => {
  const stored = [...storage.values()].join('\n');
  for (const secret of secrets) expect(stored).not.toContain(secret);
};

beforeEach(() => {
  storage.clear();
  electron.on = false;
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
  vault.token = null;
  vault.read.mockImplementation(async () => vault.token);
  vault.write.mockImplementation(async (t: string) => { vault.token = t; return true; });
  vault.clear.mockImplementation(async () => { vault.token = null; return true; });
  (window as unknown as { electronAPI?: unknown }).electronAPI = {
    session: { readRefreshToken: vault.read, writeRefreshToken: vault.write, clearRefreshToken: vault.clear },
  };
  vi.spyOn(console, 'log').mockImplementation(() => {});
});

afterEach(() => {
  delete (window as unknown as { electronAPI?: unknown }).electronAPI;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('a reload on the web', () => {
  it('with nobody signed in, finishes at once without a request', async () => {
    const auth = await loadPage();
    expect(auth.getState().isHydrated).toBe(true);
    expect(auth.getState().isAuthenticated).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('signs the person back in from the HttpOnly cookie and keeps the token in memory only', async () => {
    const access = tokenFor('user-a');
    storage.set('pipeshub_signed_in', 'user-a:o1');
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { accessToken: access }));
    const auth = await loadPage();
    expect(auth.getState()).toMatchObject({ accessToken: access, isAuthenticated: true, isHydrated: true });
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/userAccount/refresh/token');
    expect(sent().credentials).toBe('include');
    expect(sent().headers.Authorization).toBeUndefined();
    nothingSecretStored(access);
  });

  it('a session the server ended reads as signed out, and the next load asks nothing', async () => {
    storage.set('pipeshub_signed_in', 'user-a:o1');
    fetchMock.mockResolvedValueOnce(jsonResponse(401, {}));
    const auth = await loadPage();
    expect(auth.getState()).toMatchObject({ isAuthenticated: false, isHydrated: true });
    expect(storage.has('pipeshub_signed_in')).toBe(false);
  });

  it('a sign-out in another tab while the restore was out wins: the late token is dropped and the mark stays gone (R48MIGFIX-3)', async () => {
    storage.set('pipeshub_signed_in', 'user-a:o1');
    let answer: (r: Response) => void = () => {};
    fetchMock.mockImplementationOnce(() => new Promise<Response>((resolve) => { answer = resolve; }));
    vi.resetModules();
    const store = await import('@/lib/store/auth-store');
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    storage.delete('pipeshub_signed_in');
    answer(jsonResponse(200, { accessToken: tokenFor('user-a') }));
    await vi.waitFor(() => expect(store.useAuthStore.getState().isHydrated).toBe(true));
    expect(store.useAuthStore.getState()).toMatchObject({ accessToken: null, isAuthenticated: false });
    expect(storage.get('pipeshub_signed_in')).toBeUndefined();
  });

  it('a server that cannot be reached keeps the mark, to try again on the next load', async () => {
    storage.set('pipeshub_signed_in', 'user-a:o1');
    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    const auth = await loadPage();
    expect(auth.getState()).toMatchObject({ isAuthenticated: false, isHydrated: true });
    expect(storage.get('pipeshub_signed_in')).toBe('user-a:o1');
  });

  it('a sign-in stores no token in the page storage, only which account is signed in', async () => {
    const auth = await loadPage();
    const access = tokenFor('user-a');
    auth.getState().setTokens(access);
    expect(storage.get('pipeshub_signed_in')).toBe('user-a:o1');
    nothingSecretStored(access);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe('moving an older build’s localStorage tokens (the migration)', () => {
  it('moves the refresh token into the cookie, then deletes both keys', async () => {
    const fresh = tokenFor('user-a');
    storage.set('jwt_access_token', tokenFor('user-a', 10));
    storage.set('jwt_refresh_token', 'legacy-refresh');
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { accessToken: fresh }));
    const auth = await loadPage();
    expect(sent().headers.Authorization).toBe('Bearer legacy-refresh');
    expect(sent().headers['x-pipeshub-session']).toBe('cookie');
    expect(sent().credentials).toBe('include');
    expect(auth.getState()).toMatchObject({ accessToken: fresh, isAuthenticated: true });
    expect(storage.has('jwt_access_token')).toBe(false);
    expect(storage.has('jwt_refresh_token')).toBe(false);
    nothingSecretStored('legacy-refresh', fresh);
  });

  it('keeps a still-good old access token for this page when the server will not take the refresh token', async () => {
    const old = tokenFor('user-a');
    storage.set('jwt_access_token', old);
    storage.set('jwt_refresh_token', 'legacy-refresh');
    fetchMock.mockResolvedValueOnce(jsonResponse(401, {}));
    const auth = await loadPage();
    expect(auth.getState()).toMatchObject({ accessToken: old, isAuthenticated: true });
    expect(storage.has('jwt_refresh_token')).toBe(false);
    expect(storage.has('jwt_access_token')).toBe(false);
  });

  it('drops an expired old access token', async () => {
    storage.set('jwt_access_token', tokenFor('user-a', -60));
    const auth = await loadPage();
    expect(auth.getState().isAuthenticated).toBe(false);
    expect(storage.has('jwt_access_token')).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('leaves the keys for the next load when the server cannot be reached', async () => {
    storage.set('jwt_access_token', tokenFor('user-a', -60));
    storage.set('jwt_refresh_token', 'legacy-refresh');
    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    await loadPage();
    expect(storage.get('jwt_refresh_token')).toBe('legacy-refresh');
  });

  it('sign-out removes any old key left behind', async () => {
    const auth = await loadPage();
    storage.set('jwt_refresh_token', 'left-over');
    auth.getState().logout();
    expect(storage.has('jwt_refresh_token')).toBe(false);
  });
});

describe('the desktop app keeps its refresh token in the OS keychain', () => {
  it('a sign-in seals the refresh token through the bridge, never in localStorage', async () => {
    electron.on = true;
    fetchMock.mockResolvedValue(jsonResponse(200, { accessToken: tokenFor('user-a') }));
    const auth = await loadPage();
    const calls = fetchMock.mock.calls.length;
    auth.getState().setTokens(tokenFor('user-a'), 'desktop-refresh');
    await vi.waitFor(() => expect(vault.write).toHaveBeenCalledWith('desktop-refresh'));
    nothingSecretStored('desktop-refresh');
    expect(fetchMock.mock.calls.length, 'the sign-in already gave the access token').toBe(calls);
  });

  it('a restart restores from the keychain with a bearer request', async () => {
    electron.on = true;
    vault.token = 'desktop-refresh';
    const access = tokenFor('user-a');
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { accessToken: access }));
    const auth = await loadPage();
    expect(sent().headers.Authorization).toBe('Bearer desktop-refresh');
    expect(auth.getState().accessToken).toBe(access);
  });

  it('moves an older build’s token into the keychain and deletes the keys', async () => {
    electron.on = true;
    storage.set('jwt_refresh_token', 'legacy-refresh');
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { accessToken: tokenFor('user-a') }));
    const auth = await loadPage();
    expect(vault.token).toBe('legacy-refresh');
    expect(storage.has('jwt_refresh_token')).toBe(false);
    expect(auth.getState().isAuthenticated).toBe(true);
  });

  it('sign-out clears the keychain', async () => {
    electron.on = true;
    vault.token = 'desktop-refresh';
    fetchMock.mockResolvedValue(jsonResponse(200, { accessToken: tokenFor('user-a') }));
    const auth = await loadPage();
    auth.getState().logout();
    await vi.waitFor(() => expect(vault.clear).toHaveBeenCalled());
  });
});

describe('signing in asks for the cookie', () => {
  it('the web app sends the delivery header; the desktop does not', async () => {
    const { sessionDeliveryHeaders } = await import('@/lib/auth/session-credential');
    expect(sessionDeliveryHeaders()).toEqual({ 'x-pipeshub-session': 'cookie' });
    electron.on = true;
    expect(sessionDeliveryHeaders()).toEqual({});
  });
});
