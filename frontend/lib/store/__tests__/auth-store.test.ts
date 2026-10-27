import { describe, it, expect, vi, beforeEach } from 'vitest';

const isElectronMock = vi.fn();
const clearElectronLogoutServerStateMock = vi.fn();

vi.mock('@/lib/electron', () => ({
  isElectron: () => isElectronMock(),
}));

vi.mock('@/lib/electron/api-base-url-storage', () => ({
  clearElectronLogoutServerState: () => clearElectronLogoutServerStateMock(),
  persistElectronServerUrlOnLogin: vi.fn(),
}));

describe('requestElectronServerUrlChange', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('Electron: clears the server ack and dispatches the navigation event', async () => {
    isElectronMock.mockReturnValue(true);
    const { requestElectronServerUrlChange, ELECTRON_SERVER_URL_NAVIGATION_EVENT } = await import(
      '../auth-store'
    );
    // Assert ordering from inside the listener — dispatch is synchronous, so
    // by the time it fires the ack must already be cleared. A plain
    // post-hoc call-count check would pass even if the event fired first.
    const listener = vi.fn(() => {
      expect(clearElectronLogoutServerStateMock).toHaveBeenCalledTimes(1);
    });
    window.addEventListener(ELECTRON_SERVER_URL_NAVIGATION_EVENT, listener);

    requestElectronServerUrlChange();

    expect(clearElectronLogoutServerStateMock).toHaveBeenCalledTimes(1);
    expect(listener).toHaveBeenCalledTimes(1);

    window.removeEventListener(ELECTRON_SERVER_URL_NAVIGATION_EVENT, listener);
  });

  it('Web: is a no-op — no ack clear and no navigation event dispatched', async () => {
    isElectronMock.mockReturnValue(false);
    const { requestElectronServerUrlChange, ELECTRON_SERVER_URL_NAVIGATION_EVENT } = await import(
      '../auth-store'
    );
    const listener = vi.fn();
    window.addEventListener(ELECTRON_SERVER_URL_NAVIGATION_EVENT, listener);

    requestElectronServerUrlChange();

    expect(clearElectronLogoutServerStateMock).not.toHaveBeenCalled();
    expect(listener).not.toHaveBeenCalled();

    window.removeEventListener(ELECTRON_SERVER_URL_NAVIGATION_EVENT, listener);
  });
});

describe('chat drafts and signing out', () => {
  const DRAFT_KEY = 'pipeshub-chat-draft:u1:-:conv-1';

  beforeEach(async () => {
    vi.clearAllMocks();
    isElectronMock.mockReturnValue(true);
    window.localStorage.clear();
    const { allowChatDraftWrites } = await import('@/lib/storage/chat-drafts');
    allowChatDraftWrites();
  });

  it('removes unsent chat text when the user signs out from the workspace menu', async () => {
    window.localStorage.setItem(DRAFT_KEY, '{"text":"private","savedAt":1}');
    window.localStorage.setItem('theme', 'dark');
    const { logoutFromWorkspaceMenu } = await import('../auth-store');

    logoutFromWorkspaceMenu();

    expect(window.localStorage.getItem(DRAFT_KEY)).toBeNull();
    expect(window.localStorage.getItem('theme')).toBe('dark');
  });

  it('removes drafts before the session is cleared', async () => {
    window.localStorage.setItem(DRAFT_KEY, '{"text":"private","savedAt":1}');
    const { logoutFromWorkspaceMenu, useAuthStore } = await import('../auth-store');
    useAuthStore.getState().setTokens('access', 'refresh');
    let draftAtLogout: string | null | undefined;
    const unsubscribe = useAuthStore.subscribe((state) => {
      if (!state.isAuthenticated) draftAtLogout = window.localStorage.getItem(DRAFT_KEY);
    });

    logoutFromWorkspaceMenu();
    unsubscribe();

    expect(draftAtLogout).toBeNull();
  });

  it('keeps unsent text when the session merely ends', async () => {
    window.localStorage.setItem(DRAFT_KEY, '{"text":"keep me","savedAt":1}');
    const { useAuthStore } = await import('../auth-store');
    useAuthStore.getState().setTokens('access', 'refresh');

    useAuthStore.getState().logout();

    expect(window.localStorage.getItem(DRAFT_KEY)).toBe('{"text":"keep me","savedAt":1}');
  });

  it('does not let a draft be written back after a deliberate sign-out, until the next sign-in', async () => {
    const { logoutFromWorkspaceMenu, useAuthStore } = await import('../auth-store');
    const { saveDraft, loadDraft } = await import('@/chat/utils/composer-draft');

    logoutFromWorkspaceMenu();
    saveDraft(DRAFT_KEY, 'typed as the page unloads');
    expect(loadDraft(DRAFT_KEY)).toBe('');

    useAuthStore.getState().setTokens('access', 'refresh');
    saveDraft(DRAFT_KEY, 'typed after signing in again');
    expect(loadDraft(DRAFT_KEY)).toBe('typed after signing in again');
  });
});

describe('signing out ends the session on the server', () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
    isElectronMock.mockReturnValue(false);
  });

  it('sends the access token it is about to discard to the logout endpoint', async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 200 }));
    const { logoutFromWorkspaceMenu, useAuthStore } = await import('../auth-store');
    useAuthStore.getState().setTokens('old-access', 'old-refresh');

    logoutFromWorkspaceMenu();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/userAccount/logout/manual');
    expect(init.method).toBe('POST');
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer old-access');
    // The page navigates away right after; keepalive lets the request finish.
    expect(init.keepalive).toBe(true);
    expect(useAuthStore.getState().accessToken).toBeNull();
  });

  it('still signs out on this device when the server cannot be reached', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'));
    const { logoutFromWorkspaceMenu, useAuthStore } = await import('../auth-store');
    useAuthStore.getState().setTokens('old-access', 'old-refresh');

    expect(() => logoutFromWorkspaceMenu()).not.toThrow();

    expect(useAuthStore.getState().isAuthenticated).toBe(false);
    expect(window.localStorage.getItem('jwt_refresh_token')).toBeNull();
  });

  it('does not call the server when there is no session to end', async () => {
    const { logoutFromWorkspaceMenu, useAuthStore } = await import('../auth-store');
    useAuthStore.getState().logout();

    logoutFromWorkspaceMenu();

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('does not call the server when the session merely expires', async () => {
    const { useAuthStore } = await import('../auth-store');
    useAuthStore.getState().setTokens('old-access', 'old-refresh');

    useAuthStore.getState().logout();

    expect(fetchMock).not.toHaveBeenCalled();
  });
});
