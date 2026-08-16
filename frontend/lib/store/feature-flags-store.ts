'use client';

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';
import { apiClient } from '@/lib/api/axios-instance';

interface FeatureFlagsState {
  /** null = not yet loaded (treat the same as "disabled" to avoid a flash of gated UI) */
  flags: Record<string, boolean> | null;
  loading: boolean;
}

interface FeatureFlagsActions {
  fetchFlags: () => Promise<void>;
}

type FeatureFlagsStore = FeatureFlagsState & FeatureFlagsActions;

const EFFECTIVE_FLAGS_URL =
  '/api/v1/configurationManager/platform/feature-flags/effective';

let fetchPromise: Promise<void> | null = null;

function parseFeatureFlags(value: unknown): Record<string, boolean> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return {};
  }
  const flags: Record<string, boolean> = {};
  for (const [key, entry] of Object.entries(value as Record<string, unknown>)) {
    if (typeof entry === 'boolean') {
      flags[key] = entry;
    }
  }
  return flags;
}

export const useFeatureFlagsStore = create<FeatureFlagsStore>()(
  devtools(
    immer((set) => ({
      flags: null,
      loading: false,

      fetchFlags: async () => {
        // Every authenticated page mounts through `HealthGate` — dedupe so a
        // fast client-side navigation between pages doesn't refetch.
        if (fetchPromise) return fetchPromise;

        fetchPromise = (async () => {
          set((state) => {
            state.loading = true;
          });
          try {
            const { data } = await apiClient.get<{ featureFlags?: unknown }>(
              EFFECTIVE_FLAGS_URL,
              { suppressErrorToast: true },
            );
            set((state) => {
              state.flags = parseFeatureFlags(data?.featureFlags);
              state.loading = false;
            });
          } catch {
            // Fail closed to "loaded, nothing enabled" rather than leaving
            // `flags` null forever — an indefinite `null` would permanently
            // block flag-gated pages (e.g. MCP redirects) on a transient
            // network hiccup, which is worse than the safe (disabled) default.
            set((state) => {
              state.flags = state.flags ?? {};
              state.loading = false;
            });
          }
        })();

        try {
          await fetchPromise;
        } finally {
          fetchPromise = null;
        }
      },
    })),
    { name: 'FeatureFlagsStore' },
  ),
);

export const selectFeatureFlagsLoaded = (s: FeatureFlagsStore) => s.flags !== null;
/** Treats "not yet loaded" as disabled so gated UI never flashes on then off. */
export const selectMcpEnabled = (s: FeatureFlagsStore) => s.flags?.ENABLE_MCP === true;
