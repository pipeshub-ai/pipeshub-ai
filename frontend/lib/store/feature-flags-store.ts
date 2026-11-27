'use client';

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';
import { apiClient } from '@/lib/api/axios-instance';
import { onSessionEnd } from '@/lib/api/session-cache';

/** The server's harness rollout decision for this org; the UI never recomputes it. */
export interface AgentHarnessRollout {
  /** New turns may be submitted to the durable harness (the server may still route one to legacy). */
  useHarness: boolean;
  harnessMajor: number;
  reason: string;
  /** Durable runs already admitted can be reattached, even under the kill switch. */
  resumeInFlightRuns: boolean;
  /**
   * The harness is on for this org (ADR-OPS-08): the server switch and the org's Labs switch. Readiness, the kill
   * switch and operator stops do not clear it: they stop new runs, not the product behaviour that came with them.
   */
  harnessOn: boolean;
}

interface FeatureFlagsState {
  /** null = not yet loaded. Opt-in selectors treat that as off; default-on selectors treat it as on. */
  flags: Record<string, boolean> | null;
  agentHarness: AgentHarnessRollout | null;
  /** The deployment flag `HARNESS_RECONCILE` (environment, not Labs): unknown effects are first checked by a read. */
  harnessReconcile: boolean;
  /** A worker that runs engine runs (coding agents) was seen lately: what personal AI accounts serve (UX3). */
  engineRuns: boolean;
  /** The server serves "Continue privately" (SS3): the durable harness infrastructure is ready. */
  continuePrivately: boolean;
  loading: boolean;
  /**
   * The last read failed, so `flags` is a stand-in (`{}`, or the last good answer) and says nothing about what the
   * org has on. Classic pages carry on with it; a route that would send the person away on an off flag waits.
   */
  readFailed: boolean;
  /** A failed read is being tried again (backoff); false once a read answered or every retry failed. */
  retrying: boolean;
}

interface FeatureFlagsActions {
  fetchFlags: () => Promise<void>;
}

export type FeatureFlagsStore = FeatureFlagsState & FeatureFlagsActions;

const EFFECTIVE_FLAGS_URL =
  '/api/v1/configurationManager/platform/feature-flags/effective';

let fetchPromise: Promise<void> | null = null;

/**
 * The waits before each retry of a failed read (UIADOPT1-FU-1): a transient network error on first load, such as
 * `ERR_NETWORK_CHANGED`, must not read as "every opt-in flag is off".
 */
export const FLAG_RETRY_DELAYS_MS: ReadonlyArray<number> = [500, 1000, 2000, 4000];
let attempt = 0;
let retryTimer: ReturnType<typeof setTimeout> | null = null;
/** Bumped at session end, so a retry the last session scheduled never writes into the next one. */
let generation = 0;

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

function parseAgentHarnessRollout(value: unknown): AgentHarnessRollout | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const v = value as Record<string, unknown>;
  return {
    useHarness: v.useHarness === true,
    harnessMajor: typeof v.harnessMajor === 'number' ? v.harnessMajor : 1,
    reason: typeof v.reason === 'string' ? v.reason : 'unknown',
    resumeInFlightRuns: v.resumeInFlightRuns === true,
    harnessOn: v.harnessOn === true,
  };
}

export const useFeatureFlagsStore = create<FeatureFlagsStore>()(
  devtools(
    immer((set) => ({
      flags: null,
      agentHarness: null,
      harnessReconcile: false,
      continuePrivately: false,
      engineRuns: false,
      loading: false,
      readFailed: false,
      retrying: false,

      fetchFlags: async () => {
        // Every authenticated page mounts through `HealthGate` — dedupe so a
        // fast client-side navigation between pages doesn't refetch.
        if (fetchPromise) return fetchPromise;

        fetchPromise = (async () => {
          const mine = generation;
          set((state) => {
            state.loading = true;
          });
          try {
            const { data } = await apiClient.get<{
              featureFlags?: unknown;
              agentHarness?: unknown;
              harnessReconcile?: unknown;
              continuePrivately?: unknown;
              engineRuns?: unknown;
            }>(
              EFFECTIVE_FLAGS_URL,
              { suppressErrorToast: true },
            );
            set((state) => {
              state.flags = parseFeatureFlags(data?.featureFlags);
              state.agentHarness = parseAgentHarnessRollout(data?.agentHarness);
              state.harnessReconcile = data?.harnessReconcile === true;
              state.continuePrivately = data?.continuePrivately === true;
              state.engineRuns = data?.engineRuns === true;
              state.loading = false;
              state.readFailed = false;
              state.retrying = false;
            });
            attempt = 0;
          } catch {
            // Mark the load as finished rather than leaving `flags` null forever.
            // An indefinite `null` would permanently block pages that wait for
            // flags (MCP, Skills, Actions). An empty map keeps opt-in flags off
            // and leaves default-on flags on.
            const delay = FLAG_RETRY_DELAYS_MS[attempt];
            set((state) => {
              state.flags = state.flags ?? {};
              state.loading = false;
              state.readFailed = true;
              state.retrying = delay !== undefined;
            });
            if (delay !== undefined && mine === generation) {
              attempt += 1;
              if (retryTimer) clearTimeout(retryTimer);
              retryTimer = setTimeout(() => {
                retryTimer = null;
                if (mine === generation) void useFeatureFlagsStore.getState().fetchFlags();
              }, delay);
            } else {
              attempt = 0;
            }
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
  generation += 1;
  attempt = 0;
  if (retryTimer) clearTimeout(retryTimer);
  retryTimer = null;
  useFeatureFlagsStore.setState(useFeatureFlagsStore.getInitialState(), true);
  if (refetch) void useFeatureFlagsStore.getState().fetchFlags();
});

export const selectFeatureFlagsLoaded = (s: FeatureFlagsStore) => s.flags !== null;
/**
 * The flags are the org's real answer: a read succeeded. Only then may a route send someone away because a flag is
 * off; `selectFeatureFlagsLoaded` is also true after a failed read, so classic pages are never stuck.
 */
export const selectFeatureFlagsKnown = (s: Pick<FeatureFlagsState, 'flags' | 'readFailed'>) => s.flags !== null && !s.readFailed;
/** Every retry of a failed read failed too: the person may ask for another. */
export const selectFeatureFlagsReadFailed = (s: Pick<FeatureFlagsState, 'flags' | 'readFailed' | 'retrying'>) =>
  s.flags !== null && s.readFailed && !s.retrying;
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
/** Chat "Compare models" defaults to disabled; admins opt in from Labs. */
export const selectChatCompareEnabled = (s: FeatureFlagsStore) =>
  s.flags?.ENABLE_CHAT_COMPARE === true;
/** Skills default to enabled (Beta); admins opt out from Labs. */
export const selectSkillsEnabled = (s: FeatureFlagsStore) => s.flags?.ENABLE_SKILLS !== false;
/** The trash ("Move Deleted Records to the Trash") defaults to disabled; admins opt in from Labs. */
export const selectSoftDeleteEnabled = (s: FeatureFlagsStore) =>
  s.flags?.ENABLE_SOFT_DELETE === true;
/** User/org prompt context defaults to enabled; admins opt out from Labs. */
export const selectUserContextEnabled = (s: FeatureFlagsStore) =>
  s.flags?.ENABLE_USER_CONTEXT !== false;
/**
 * Whether a new turn is admitted to the durable harness now (`useHarness`): what starting a run keys on. The server
 * routes each turn. Pages, navigation and settings key on `selectAgentHarnessOn` instead (ADR-OPS-08).
 */
export const selectAgentHarnessEnabled = (s: Pick<FeatureFlagsState, 'agentHarness'>) =>
  s.agentHarness?.useHarness === true;
/**
 * The harness is on for this org (ADR-OPS-08): every surface that changes what a person sees or does keys on it.
 * `useHarness` (selectAgentHarnessEnabled) additionally says whether a new turn is admitted now.
 */
export const selectAgentHarnessOn = (s: Pick<FeatureFlagsState, 'agentHarness'>) =>
  s.agentHarness?.harnessOn === true;
/**
 * The harness is on for the org but a new run is refused now (the kill switch, an operator stop or infrastructure
 * not ready): its surfaces stay and say they are paused.
 */
export const selectAgentHarnessPaused = (s: Pick<FeatureFlagsState, 'agentHarness'>) =>
  selectAgentHarnessOn(s) && !selectAgentHarnessEnabled(s);
/** Whether to look for and reattach to an in-flight durable run. */
export const selectAgentHarnessResumeEnabled = (s: Pick<FeatureFlagsState, 'agentHarness'>) =>
  s.agentHarness?.resumeInFlightRuns === true;

/**
 * Labs `ENABLE_SCHEDULED_RUNS` (P8a) alone (ADR-SCH-02 D1): a firing runs on the harness where the org has it and as
 * a classic chat turn where it does not, so the agent harness plays no part in whether an org has schedules.
 */
export const selectScheduledRunsEnabled = (s: Pick<FeatureFlagsState, 'flags'>) =>
  s.flags?.ENABLE_SCHEDULED_RUNS === true;
/** Labs `ENABLE_KNOWLEDGE_PUBLICATIONS` (P2.13): off unless an admin turned it on. */
export const selectKnowledgePublicationsEnabled = (s: FeatureFlagsStore) =>
  s.flags?.ENABLE_KNOWLEDGE_PUBLICATIONS === true;

/** Personal AI accounts are used by engine runs only (UX3, R1 F16). */
export const selectEngineRunsEnabled = (s: Pick<FeatureFlagsState, 'engineRuns'>) => s.engineRuns === true;

/** Whether this deployment serves "Continue privately" on shared conversations (SS3). */
export const selectContinuePrivatelyEnabled = (s: Pick<FeatureFlagsState, 'continuePrivately'>) =>
  s.continuePrivately === true;

/** Reusing previewed record content defaults to enabled; admins opt out from Labs. */
export const selectRecordPreviewCacheEnabled = (s: FeatureFlagsStore) =>
  s.flags?.ENABLE_RECORD_PREVIEW_CACHE !== false;

export type FeatureFlagGate =
  | 'mcp'
  | 'actions'
  | 'skills'
  | 'scheduledRuns'
  | 'agentHarness'
  | 'agentHarnessResume'
  | 'engineRuns';

/**
 * Whether the default chat's Agent mode runs on the agent harness: it comes with
 * the harness (CTL-5), so it is the org's harness on. The server decides each turn again.
 */
export const selectDurableAssistantAgentOffered = (s: Pick<FeatureFlagsState, 'flags' | 'agentHarness'>) =>
  selectAgentHarnessEnabled(s);
