'use client';

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';
import { apiClient } from '@/lib/api/axios-instance';
import { onSessionEnd } from '@/lib/api/session-cache';
import { ErrorType, type ProcessedError } from '@/lib/api/api-error';

interface FeatureFlagsState {
  /** null = not yet loaded. Opt-in selectors treat that as off; default-on selectors treat it as on. */
  flags: Record<string, boolean> | null;
  loading: boolean;
}

interface FeatureFlagsActions {
  fetchFlags: () => Promise<void>;
}

export type FeatureFlagsStore = FeatureFlagsState & FeatureFlagsActions;

const EFFECTIVE_FLAGS_URL =
  '/api/v1/configurationManager/platform/feature-flags/effective';

let fetchPromise: Promise<void> | null = null;

/**
 * Waits before asking again when a read got no answer at all (the connection dropped, the network changed). Until
 * the last one fails, the flags stay unknown, so what shows them as they were last time (the rail's Projects row)
 * does not change for a blip. An error the server answered with stands at once.
 */
const NO_ANSWER_RETRY_MS = [300, 1000];

async function readFlags() {
  for (let attempt = 0; ; attempt += 1) {
    try {
      return await apiClient.get<{ featureFlags?: unknown }>(EFFECTIVE_FLAGS_URL, { suppressErrorToast: true });
    } catch (error) {
      const wait = NO_ANSWER_RETRY_MS[attempt];
      // Network errors only, by design: an aborted or timed-out read falls back at once, as a server error does.
      if (wait === undefined || (error as ProcessedError | undefined)?.type !== ErrorType.NETWORK_ERROR) throw error;
      await new Promise((resolve) => setTimeout(resolve, wait));
    }
  }
}

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
            const { data } = await readFlags();
            set((state) => {
              state.flags = parseFeatureFlags(data?.featureFlags);
              state.loading = false;
            });
          } catch {
            // Mark the load as finished rather than leaving `flags` null forever.
            // An indefinite `null` would permanently block pages that wait for
            // flags (MCP, Skills, Actions). An empty map keeps opt-in flags off
            // and leaves default-on flags on.
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

// Flags belong to the org signed in to; the next sign-in (maybe another server on desktop) loads its
// own through HealthGate. A switch to another org keeps the app on screen, so it loads them here.
onSessionEnd(({ refetch }) => {
  fetchPromise = null;
  useFeatureFlagsStore.setState(useFeatureFlagsStore.getInitialState(), true);
  if (refetch) void useFeatureFlagsStore.getState().fetchFlags();
});

export const selectFeatureFlagsLoaded = (s: FeatureFlagsStore) => s.flags !== null;
/** MCP defaults to enabled, so an unloaded or absent flag must not read as disabled. */
export const selectMcpEnabled = (s: FeatureFlagsStore) => s.flags?.ENABLE_MCP !== false;
/** API Toolsets default to disabled (Labs); "not yet loaded" reads as disabled. */
export const selectApiToolsetsEnabled = (s: FeatureFlagsStore) => s.flags?.ENABLE_API_TOOLSETS === true;
/** Actions defaults to enabled, so an unloaded or absent flag must not read as disabled. */
export const selectActionsEnabled = (s: FeatureFlagsStore) => s.flags?.ENABLE_ACTIONS !== false;
/** Vector store rebuild defaults to disabled; admins opt in from Labs. */
export const selectVectorStoreRebuildEnabled = (s: FeatureFlagsStore) =>
  s.flags?.ENABLE_VECTOR_STORE_REBUILD === true;
/** Projects defaults to disabled; admins opt in from Labs. */
export const selectProjectsEnabled = (s: FeatureFlagsStore) =>
  s.flags?.ENABLE_PROJECTS === true;
/** Skills default to enabled (Beta); admins opt out from Labs. */
export const selectSkillsEnabled = (s: FeatureFlagsStore) => s.flags?.ENABLE_SKILLS !== false;
/** The trash ("Move Deleted Records to the Trash") defaults to disabled; admins opt in from Labs. */
export const selectSoftDeleteEnabled = (s: FeatureFlagsStore) =>
  s.flags?.ENABLE_SOFT_DELETE === true;
/** User/org prompt context defaults to enabled; admins opt out from Labs. */
export const selectUserContextEnabled = (s: FeatureFlagsStore) =>
  s.flags?.ENABLE_USER_CONTEXT !== false;

export type FeatureFlagGate = 'mcp' | 'actions' | 'skills';
