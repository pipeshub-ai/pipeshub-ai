'use client';

/**
 * User Profile Store
 *
 * Pure state container — no API calls here.
 * Data is assembled by the `useInitializeUserProfile` hook (lib/hooks/) and
 * pushed in via the synchronous setters below.
 *
 * Sources resolved by the hook:
 *   1. JWT token  → userId
 *   2. GET /api/v1/users/:userId         → firstName, lastName, fullName, email, hasLoggedIn
 *   3. GET /api/v1/userGroups/users/:id  → isAdmin (group.type === 'admin')
 *   4. GET /api/v1/users/:userId/logo    → avatarUrl (blob → object URL, silent fail)
 *
 * The profile (excluding avatarUrl) is persisted to localStorage for instant
 * avatar initials on reload. The initializer still fetches fresh data on mount.
 * Call `clearProfile()` on logout.
 */

import { create } from 'zustand';
import { devtools, persist, createJSONStorage } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';

// ============================================================
// Types
// ============================================================

export interface UserProfile {
  userId: string;
  firstName: string | null;
  lastName: string | null;
  /** Derived: `${firstName} ${lastName}` or fullName from API */
  fullName: string | null;
  email: string | null;
  /** null = unknown (groups API failed); false = confirmed non-admin; true = confirmed admin */
  isAdmin: boolean | null;
  /** Object URL created from the logo blob, or null if not set */
  avatarUrl: string | null;
  hasLoggedIn: boolean;
}

interface UserProfileState {
  profile: UserProfile | null;
  /** True while the three API calls are in-flight */
  isLoading: boolean;
  /** True once initialization has completed (success or error) */
  isInitialized: boolean;
  error: string | null;
}

interface UserProfileActions {
  /** Called by the hook once all API data is resolved. */
  setProfile: (profile: UserProfile) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  setInitialized: (initialized: boolean) => void;
  /** Partially update fields (e.g. after the user edits their name). */
  updateProfile: (patch: Partial<UserProfile>) => void;
  /** Replace the avatar URL (revokes the previous object URL to avoid memory leaks). */
  setAvatarUrl: (url: string | null) => void;
  /** Clear all profile data — call on logout. */
  clearProfile: () => void;
}

export type UserProfileStore = UserProfileState & UserProfileActions;

// ============================================================
// Initial state
// ============================================================

const initialState: UserProfileState = {
  profile: null,
  isLoading: false,
  isInitialized: false,
  error: null,
};

// ============================================================
// Store
// ============================================================

export const useUserStore = create<UserProfileStore>()(
  devtools(
    persist(
      immer((set, get) => ({
        ...initialState,

        // ----------------------------------------------------------
        setProfile: (profile) =>
          set((state) => {
            // Revoke previous blob URL before replacing the profile to avoid memory leaks
            const prevAvatarUrl = state.profile?.avatarUrl;
            if (
              prevAvatarUrl &&
              prevAvatarUrl !== profile.avatarUrl &&
              prevAvatarUrl.startsWith('blob:')
            ) {
              URL.revokeObjectURL(prevAvatarUrl);
            }
            state.profile = profile;
          }),

        setLoading: (loading) =>
          set((state) => {
            state.isLoading = loading;
          }),

        setError: (error) =>
          set((state) => {
            state.error = error;
          }),

        setInitialized: (initialized) =>
          set((state) => {
            state.isInitialized = initialized;
          }),

        // ----------------------------------------------------------
        updateProfile: (patch) =>
          set((state) => {
            if (!state.profile) return;
            // Route avatarUrl updates through the same revocation logic as setAvatarUrl
            if (Object.prototype.hasOwnProperty.call(patch, 'avatarUrl')) {
              const newAvatarUrl = patch.avatarUrl ?? null;
              const prev = state.profile.avatarUrl;
              if (prev && prev !== newAvatarUrl && prev.startsWith('blob:')) {
                URL.revokeObjectURL(prev);
              }
              state.profile.avatarUrl = newAvatarUrl;
              const { avatarUrl: _ignored, ...rest } = patch;
              Object.assign(state.profile, rest);
            } else {
              Object.assign(state.profile, patch);
            }
          }),

        // ----------------------------------------------------------
        setAvatarUrl: (url) =>
          set((state) => {
            if (!state.profile) return;
            const prev = state.profile.avatarUrl;
            // No-op if URL hasn't changed — avoids revoking a still-valid blob URL
            if (prev === url) return;
            // Revoke previous blob URL to avoid memory leaks
            if (prev?.startsWith('blob:')) URL.revokeObjectURL(prev);
            state.profile.avatarUrl = url;
          }),

        // ----------------------------------------------------------
        clearProfile: () => {
          // Revoke blob URL before clearing
          const prev = get().profile?.avatarUrl;
          if (prev?.startsWith('blob:')) URL.revokeObjectURL(prev);
          set(() => ({ ...initialState }));
        },
      })),
      {
        name: 'user-profile-storage',
        storage: createJSONStorage(() => localStorage),
        partialize: (state) => ({
          // Persist only the profile for instant avatar/initials on reload.
          // Exclude avatarUrl (blob URLs don't survive reloads) and transient flags.
          profile: state.profile
            ? {
                userId: state.profile.userId,
                firstName: state.profile.firstName,
                lastName: state.profile.lastName,
                fullName: state.profile.fullName,
                email: state.profile.email,
                isAdmin: state.profile.isAdmin,
                avatarUrl: null,
                hasLoggedIn: state.profile.hasLoggedIn,
              }
            : null,
        }),
      }
    ),
    { name: 'UserStore' }
  )
);

// ============================================================
// Selectors (stable references — use in components)
// ============================================================

export const selectProfile = (s: UserProfileStore) => s.profile;
export const selectIsAdmin = (s: UserProfileStore) => s.profile?.isAdmin ?? null;
export const selectAvatarUrl = (s: UserProfileStore) => s.profile?.avatarUrl ?? null;
export const selectFullName = (s: UserProfileStore) => s.profile?.fullName ?? null;
export const selectUserEmail = (s: UserProfileStore) => s.profile?.email ?? null;
export const selectIsProfileLoading = (s: UserProfileStore) => s.isLoading;
export const selectIsProfileInitialized = (s: UserProfileStore) => s.isInitialized;
