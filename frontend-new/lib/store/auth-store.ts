import { create } from 'zustand';
import { devtools, persist, createJSONStorage } from 'zustand/middleware';
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

const initialState: AuthState = {
  accessToken: null,
  refreshToken: null,
  user: null,
  isAuthenticated: false,
  isHydrated: false,
};

export const useAuthStore = create<AuthStore>()(
  devtools(
    persist(
      immer((set) => ({
        ...initialState,

        setTokens: (accessToken, refreshToken) =>
          set((state) => {
            state.accessToken = accessToken;
            state.refreshToken = refreshToken;
            state.isAuthenticated = true;
          }),

        setAccessToken: (accessToken) =>
          set((state) => {
            state.accessToken = accessToken;
            state.isAuthenticated = !!accessToken;
          }),

        setUser: (user) =>
          set((state) => {
            state.user = user;
          }),

        logout: () =>
          set((state) => {
            state.accessToken = null;
            state.refreshToken = null;
            state.user = null;
            state.isAuthenticated = false;
          }),

        setHydrated: (value) =>
          set((state) => {
            state.isHydrated = value;
          }),
      })),
      {
        name: 'auth-storage',
        storage: createJSONStorage(() => localStorage),
        partialize: (state) => ({
          accessToken: state.accessToken,
          refreshToken: state.refreshToken,
        }),
        onRehydrateStorage: () => (state) => {
          if (state) {
            if (state.accessToken) {
              // Use setAccessToken so the update goes through Zustand's set()
              // and isAuthenticated is properly reactive for all subscribers
              state.setAccessToken(state.accessToken);
            }
            state.setHydrated(true);
          }
        },
      }
    ),
    { name: 'AuthStore' }
  )
);

/**
 * Clears all auth state and redirects the user to the login page.
 * Single source of truth used by both the axios interceptor and UI buttons.
 */
export function logoutAndRedirect(): void {
  useAuthStore.getState().logout();
  if (typeof window !== 'undefined') {
    window.location.href = '/login';
  }
}

// Selectors for common access patterns
export const selectAccessToken = (state: AuthStore) => state.accessToken;
export const selectRefreshToken = (state: AuthStore) => state.refreshToken;
export const selectUser = (state: AuthStore) => state.user;
export const selectIsAuthenticated = (state: AuthStore) => state.isAuthenticated;
export const selectIsHydrated = (state: AuthStore) => state.isHydrated;
