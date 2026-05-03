import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';

export interface User {
  id: string;
  phone?: string;
  name?: string;
  email?: string;
  created_at?: string;
  updated_at?: string;
}

interface AuthState {
  accessToken: string | null;
  refreshToken: string | null;
  user: User | null;
  isAuthenticated: boolean;
  isHydrated: boolean;
}

interface AuthActions {
  setTokens: (accessToken: string, refreshToken: string) => void;
  setAccessToken: (accessToken: string) => void;
  setUser: (user: User | null) => void;
  logout: () => void;
  setHydrated: (value: boolean) => void;
}

type AuthStore = AuthState & AuthActions;

/** localStorage keys (shared with the legacy frontend so tokens interop). */
export const ACCESS_TOKEN_STORAGE_KEY = 'jwt_access_token';
export const REFRESH_TOKEN_STORAGE_KEY = 'jwt_refresh_token';

const initialState: AuthState = {
  accessToken: null,
  refreshToken: null,
  user: null,
  isAuthenticated: false,
  isHydrated: false,
};

function writeAccessToken(accessToken: string | null): void {
  if (typeof window === 'undefined') return;
  if (accessToken) {
    window.localStorage.setItem(ACCESS_TOKEN_STORAGE_KEY, accessToken);
  } else {
    window.localStorage.removeItem(ACCESS_TOKEN_STORAGE_KEY);
  }
}

function writeRefreshToken(refreshToken: string | null): void {
  if (typeof window === 'undefined') return;
  if (refreshToken) {
    window.localStorage.setItem(REFRESH_TOKEN_STORAGE_KEY, refreshToken);
  } else {
    window.localStorage.removeItem(REFRESH_TOKEN_STORAGE_KEY);
  }
}

export const useAuthStore = create<AuthStore>()(
  devtools(
    immer((set) => ({
      ...initialState,

      setTokens: (accessToken, refreshToken) => {
        writeAccessToken(accessToken);
        writeRefreshToken(refreshToken);
        set((state) => {
          state.accessToken = accessToken;
          state.refreshToken = refreshToken;
          state.isAuthenticated = true;
        });
      },

      setAccessToken: (accessToken) => {
        writeAccessToken(accessToken);
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
        writeAccessToken(null);
        writeRefreshToken(null);
        set((state) => {
          state.accessToken = null;
          state.refreshToken = null;
          state.user = null;
          state.isAuthenticated = false;
        });
      },

      setHydrated: (value) =>
        set((state) => {
          state.isHydrated = value;
        }),
    })),
    { name: 'AuthStore' }
  )
);

/**
 * Hydrates the auth store from localStorage on the client. Safe to call
 * multiple times — subsequent calls are a no-op once hydrated.
 *
 * This must run in a client context (e.g. from a `'use client'` effect
 * or via the `AuthHydrator` provider mounted in the root layout).
 */
export function hydrateAuthStore(): void {
  if (typeof window === 'undefined') return;
  const api = useAuthStore.getState();
  if (api.isHydrated) return;

  const accessToken = window.localStorage.getItem(ACCESS_TOKEN_STORAGE_KEY);
  const refreshToken = window.localStorage.getItem(REFRESH_TOKEN_STORAGE_KEY);

  // Use the store's own actions so mutations go through the immer wrapper
  // exactly like a normal login would, guaranteeing subscribers are
  // notified with the updated state.
  if (accessToken && refreshToken) {
    api.setTokens(accessToken, refreshToken);
  } else if (accessToken) {
    api.setAccessToken(accessToken);
  }
  useAuthStore.getState().setHydrated(true);
}

// Hydrate synchronously as soon as the module loads on the client.
// This guarantees `isHydrated` is true before any component subscribes
// to the store, removing the race between the `AuthHydrator` effect
// and the auth/guest guards.
if (typeof window !== 'undefined') {
  // Remove the legacy zustand-persist JSON blob to avoid confusion in DevTools.
  try {
    window.localStorage.removeItem('auth-storage');
  } catch {
    // ignore storage access errors (private mode, etc.)
  }
  hydrateAuthStore();
}

/** Dispatched after logout; AuthHydrator listens and runs client-side navigation. */
export const LOGIN_NAVIGATION_EVENT = 'pipeshub:request-login-navigation';

/**
 * Clears all auth state and redirects the user to the login page.
 * Single source of truth used by both the axios interceptor and UI buttons.
 *
 * Uses a custom event + Next.js router (see AuthHydrator) instead of
 * `window.location.href` so Electron (`app://`) does not full-reload into an empty
 * shell, and so the App Router stays consistent with soft navigation.
 */
export function logoutAndRedirect(): void {
  useAuthStore.getState().logout();
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent(LOGIN_NAVIGATION_EVENT));
}

// Selectors for common access patterns
export const selectAccessToken = (state: AuthStore) => state.accessToken;
export const selectRefreshToken = (state: AuthStore) => state.refreshToken;
export const selectUser = (state: AuthStore) => state.user;
export const selectIsAuthenticated = (state: AuthStore) => state.isAuthenticated;
export const selectIsHydrated = (state: AuthStore) => state.isHydrated;
