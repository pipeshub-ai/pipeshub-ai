import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';
import { isElectron } from '@/lib/electron';
import { allowChatDraftWrites, discardAllChatDrafts } from '@/lib/storage/chat-drafts';
import { allowChatUnreadWrites, discardAllChatUnread } from '@/lib/storage/chat-unread';
import { clearSessionCache } from '@/lib/api/session-cache';
import {
  clearLegacyTokens,
  clearSignedInMark,
  endServerSession as endSessionOnServer,
  forgetRefreshToken,
  keepRefreshToken,
  markSignedIn,
  readLegacyTokens,
  rememberRefreshToken,
  requestAccessToken,
  signedInMark,
} from '@/lib/auth/session-credential';
import {
  clearElectronLogoutServerState,
  persistElectronServerUrlOnLogin,
} from '@/lib/electron/api-base-url-storage';

export interface User {
  id: string;
  phone?: string;
  name?: string;
  email?: string;
  created_at?: string;
  updated_at?: string;
}

interface AuthState {
  /** In memory only (ADR-WEB-02): a reload gets a new one from the session's refresh token. */
  accessToken: string | null;
  user: User | null;
  isAuthenticated: boolean;
  isHydrated: boolean;
}

interface AuthActions {
  /**
   * Signs in with the tokens a sign-in returned. The web app's refresh token is
   * a cookie the page never sees, so `refreshToken` is given only where the
   * server answered with one (the desktop app, a flow that answers in the body).
   */
  setTokens: (accessToken: string, refreshToken?: string) => void;
  setAccessToken: (accessToken: string) => void;
  setUser: (user: User | null) => void;
  logout: () => void;
  setHydrated: (value: boolean) => void;
}

type AuthStore = AuthState & AuthActions;

const initialState: AuthState = {
  accessToken: null,
  user: null,
  isAuthenticated: false,
  isHydrated: false,
};

/**
 * Electron main holds the access token in memory so Local FS keeps syncing;
 * clearing the renderer alone would leave the desktop socket connected after
 * logout. Called through the preload bridge directly rather than via the
 * connectors util, to keep this store free of a feature-module import.
 */
function clearElectronDesktopCredential(): void {
  if (typeof window === 'undefined' || !isElectron()) return;
  const localSync = (window as unknown as {
    electronAPI?: { localSync?: { clearCredentials?: () => Promise<unknown> } };
  }).electronAPI?.localSync;
  void localSync?.clearCredentials?.().catch(() => { /* logout must not block */ });
}

export const useAuthStore = create<AuthStore>()(
  devtools(
    immer((set) => ({
      ...initialState,

      setTokens: (accessToken, refreshToken) => {
        allowChatUnreadWrites();
        allowChatDraftWrites();
        markSignedIn(accessToken);
        persistElectronServerUrlOnLogin();
        set((state) => {
          state.accessToken = accessToken;
          state.isAuthenticated = true;
        });
        if (refreshToken) void rememberRefreshToken(refreshToken);
      },

      setAccessToken: (accessToken) => {
        if (accessToken) markSignedIn(accessToken);
        set((state) => {
          state.accessToken = accessToken;
          state.isAuthenticated = !!accessToken;
        });
      },

      setUser: (user) =>
        set((state) => {
          state.user = user;
        }),

      logout: () => {
        clearSignedInMark();
        clearLegacyTokens();
        forgetRefreshToken();
        clearElectronDesktopCredential();
        set((state) => {
          state.accessToken = null;
          state.user = null;
          state.isAuthenticated = false;
        });
        clearSessionCache();
      },

      setHydrated: (value) =>
        set((state) => {
          state.isHydrated = value;
        }),
    })),
    { name: 'AuthStore' }
  )
);

function legacyAccessTokenUsable(token: string | null): boolean {
  if (!token) return false;
  try {
    const payload = JSON.parse(atob((token.split('.')[1] ?? '').replace(/-/g, '+').replace(/_/g, '/'))) as { exp?: unknown };
    return typeof payload.exp !== 'number' || payload.exp * 1000 > Date.now();
  } catch {
    return false;
  }
}

/**
 * Moves an older build's localStorage tokens out (R41OPS-FU-7): the refresh
 * token goes into the cookie (desktop: the keychain) and both keys are deleted.
 * If the server will not take it but the old access token is still good, the
 * page keeps that one in memory, so nobody is signed out mid-task by the
 * upgrade. The keys stay only when the server could not be reached, to move
 * them on the next load.
 */
async function migrateLegacyTokens(legacy: { accessToken: string | null; refreshToken: string | null }): Promise<string | null> {
  const answer = legacy.refreshToken ? await keepRefreshToken(legacy.refreshToken) : null;
  if (answer?.outcome !== 'unreachable') clearLegacyTokens();
  if (answer?.outcome === 'issued') return answer.accessToken;
  return legacyAccessTokenUsable(legacy.accessToken) ? legacy.accessToken : null;
}

async function restoreFromCredential(): Promise<string | null> {
  const answer = await requestAccessToken();
  if (answer.outcome === 'issued') return answer.accessToken;
  if (answer.outcome === 'refused') clearSignedInMark();
  return null;
}

let restoring: Promise<void> | null = null;

/**
 * Signs this page in from the session it already has: after a reload, in a new
 * tab, or when another tab signs in. Resolves once `isHydrated` is true;
 * concurrent callers share one attempt. With nothing to restore it finishes at
 * once, without a request.
 */
export function restoreSession(): Promise<void> {
  if (typeof window === 'undefined') return Promise.resolve();
  if (restoring) return restoring;
  const legacy = readLegacyTokens();
  const hasLegacy = Boolean(legacy.accessToken || legacy.refreshToken);
  const mark = signedInMark();
  const hasSession = hasLegacy || isElectron() || mark !== null;
  if (!hasSession) {
    useAuthStore.getState().setHydrated(true);
    return Promise.resolve();
  }
  const attempt = (async () => {
    try {
      const accessToken = hasLegacy ? await migrateLegacyTokens(legacy) : await restoreFromCredential();
      // Signed in meanwhile (this page or another tab) wins over the restore. So does a sign-out (or another account's
      // sign-in) in another tab while the refresh was out (R48MIGFIX-3): storing its token would mark this browser
      // signed in as the person who just left.
      const signedOutMeanwhile = mark !== null && signedInMark() !== mark;
      if (accessToken && !signedOutMeanwhile && !useAuthStore.getState().isAuthenticated) {
        allowChatUnreadWrites();
        allowChatDraftWrites();
        useAuthStore.getState().setAccessToken(accessToken);
      }
    } catch {
      // Nothing to restore from: the page shows sign-in.
    } finally {
      useAuthStore.getState().setHydrated(true);
    }
  })();
  restoring = attempt;
  void attempt.finally(() => {
    if (restoring === attempt) restoring = null;
  });
  return attempt;
}

/** Kept for callers that hydrate on mount; the restore runs once per page. */
export function hydrateAuthStore(): void {
  if (useAuthStore.getState().isHydrated) return;
  void restoreSession();
}

// Start as soon as the module loads on the client, so the guards wait on one
// restore instead of each starting its own.
if (typeof window !== 'undefined') {
  hydrateAuthStore();
}

/** Dispatched after logout; AuthHydrator listens and runs client-side navigation. */
export const LOGIN_NAVIGATION_EVENT = 'pipeshub:request-login-navigation';

/** Electron: after explicit workspace logout, show server URL screen then sign-in (see AuthHydrator). */
export const ELECTRON_SERVER_URL_NAVIGATION_EVENT = 'pipeshub:electron-goto-server-url-flow';

/**
 * Clears all auth state and redirects the user to the login page.
 * Single source of truth used by the axios interceptor (session expiry / 401).
 *
 * Web: hard navigation via `window.location.href = '/login'` (original behavior).
 * Electron: dispatch a CustomEvent that AuthHydrator consumes to do a soft
 * `router.replace('/login')` — a hard navigation under `app://` reloads into an
 * empty shell.
 */
export function logoutAndRedirect(): void {
  useAuthStore.getState().logout();
  if (typeof window === 'undefined') return;
  if (isElectron()) {
    window.dispatchEvent(new CustomEvent(LOGIN_NAVIGATION_EVENT));
    return;
  }
  window.location.href = '/login';
}

export function requestElectronServerUrlChange(): void {
  if (typeof window === 'undefined' || !isElectron()) return;
  clearElectronLogoutServerState();
  window.dispatchEvent(new CustomEvent(ELECTRON_SERVER_URL_NAVIGATION_EVENT));
}
/**
 * Workspace menu logout: web → same as session-expiry logout; Electron → clear
 * server URL ack (keep last URL for pre-fill), then route through ServerUrlGuard's
 * add-URL screen so the user can confirm or change the server before signing in.
 */
/**
 * Asks the server to end the session, so a copy of the tokens stops working
 * and the cookie is cleared. Fire-and-forget: signing out on this device never
 * waits for or depends on the answer (offline, server down). Not apiClient, so
 * a 401 here can't trigger a refresh or another logout.
 */
function endServerSession(): void {
  try {
    const accessToken = useAuthStore.getState().accessToken;
    if (!accessToken && signedInMark() === null) return;
    void endSessionOnServer(accessToken);
  } catch {
    // Signing out locally must still happen.
  }
}

export function logoutFromWorkspaceMenu(): void {
  // Read the token before anything below clears it (and, on desktop, the server URL).
  endServerSession();
  // Deliberate sign-out: which chats had a new answer must not outlive the
  // session. A session that merely expires keeps them.
  discardAllChatUnread();
  // Same for unsent chat text; an expired session keeps its drafts, so nothing
  // typed is lost.
  discardAllChatDrafts();
  if (typeof window !== 'undefined' && isElectron()) {
    useAuthStore.getState().logout();
    requestElectronServerUrlChange();
    clearElectronLogoutServerState();
    window.dispatchEvent(new CustomEvent(ELECTRON_SERVER_URL_NAVIGATION_EVENT));
    return;
  }
  // Web path is identical to the 401 / session-expiry flow — delegate so the
  // two paths stay in lockstep.
  logoutAndRedirect();
}

/** One workspace per sign-in in this edition, so there is nothing to switch to. */
export function switchToSignedInWorkspace(_orgId: string): boolean {
  return false;
}

// Selectors for common access patterns
export const selectAccessToken = (state: AuthStore) => state.accessToken;
export const selectUser = (state: AuthStore) => state.user;
export const selectIsAuthenticated = (state: AuthStore) => state.isAuthenticated;
export const selectIsHydrated = (state: AuthStore) => state.isHydrated;
