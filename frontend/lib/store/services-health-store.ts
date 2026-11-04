'use client';

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';
import { apiClient } from '@/lib/api/axios-instance';

// ========================================
// Types
// ========================================

export type ServiceStatus = 'healthy' | 'unhealthy' | 'unknown';

export type InfraServices = Record<string, ServiceStatus>;

export interface AppServices {
  query: ServiceStatus;
  connector: ServiceStatus;
  indexing: ServiceStatus;
  docling: ServiceStatus;
  parsing?: ServiceStatus;
  extraction?: ServiceStatus;
}

interface ServicesHealthState {
  loading: boolean;
  healthy: boolean | null;
  backgroundCheckFailed: boolean;
  apiServerReachable: boolean;
  infraServices: InfraServices | null;
  appServices: AppServices | null;
  infraServiceNames: Record<string, string> | null;
  lastChecked: number | null;
}

interface ServicesHealthActions {
  checkHealth: () => Promise<void>;
  retryServerConnection: () => Promise<void>;
  startPolling: () => void;
  stopPolling: () => void;
  startBackgroundPolling: () => void;
  stopBackgroundPolling: () => void;
  clearCache: () => void;
}

type ServicesHealthStore = ServicesHealthState & ServicesHealthActions;

// ========================================
// Constants
// ========================================

const POLL_INTERVAL = 10 * 60 * 1000;
// After a failed check: services that are starting up are back within a minute,
// and a page blocked by <ServiceGate> should open soon after, not 10 minutes on.
const RECOVERY_POLL_INTERVAL = 10 * 1000;
const CACHE_KEY = 'healthCheck';

let pollTimer: ReturnType<typeof setInterval> | null = null;
let bgPollTimer: ReturnType<typeof setTimeout> | null = null;
// Bumped by every start and stop, so a check still in flight from an earlier
// run does not schedule a second chain of checks.
let bgPollRun = 0;
let bgPolling = false;
let isRetrying = false;

// ========================================
// Store
// ========================================

const initialState: ServicesHealthState = {
  loading: true,
  healthy: null,
  backgroundCheckFailed: false,
  apiServerReachable: true,
  infraServices: null,
  appServices: null,
  infraServiceNames: null,
  lastChecked: null,
};

export const useServicesHealthStore = create<ServicesHealthStore>()(
  devtools(
    immer((set, get) => ({
      ...initialState,

      checkHealth: async () => {
        try {
          const [infraResp, servicesResp] = await Promise.allSettled([
            apiClient.get('/api/v1/health', { suppressErrorToast: true }),
            apiClient.get('/api/v1/health/services', { suppressErrorToast: true }),
          ]);

          const infraData =
            infraResp.status === 'fulfilled' ? infraResp.value.data : null;
          const servicesData =
            servicesResp.status === 'fulfilled' ? servicesResp.value.data : null;

          const infraOk = infraData?.status === 'healthy';
          const servicesOk = servicesData?.status === 'healthy';
          const overallHealthy = infraOk && servicesOk;
          const serverReachable =
            infraResp.status === 'fulfilled' || servicesResp.status === 'fulfilled';

          set((state) => {
            state.loading = false;
            state.healthy = overallHealthy;
            state.apiServerReachable = serverReachable;
            state.infraServices = infraData?.services ?? null;
            state.appServices = servicesData?.services ?? null;
            state.infraServiceNames = infraData?.serviceNames ?? null;
            state.lastChecked = Date.now();
          });

          if (overallHealthy) {
            try {
              localStorage.setItem(CACHE_KEY, 'true');
            } catch {}
            get().stopPolling();
          } else {
            try {
              localStorage.removeItem(CACHE_KEY);
            } catch {}
          }
        } catch {
          set((state) => {
            state.loading = false;
            state.healthy = false;
            state.apiServerReachable = false;
            state.infraServices = null;
            state.appServices = null;
            state.lastChecked = Date.now();
          });
        }
      },

      retryServerConnection: async () => {
        if (isRetrying) return;
        isRetrying = true;
        try {
          await apiClient.get('/api/v1/health', {
            suppressErrorToast: true,
            timeout: 10000,
          });
          set((state) => { state.apiServerReachable = true; });
          get().stopBackgroundPolling();
          get().startBackgroundPolling();
        } catch {
          // Still unreachable
        } finally {
          isRetrying = false;
        }
      },

      startPolling: () => {
        if (pollTimer) return;
        get().checkHealth();
        pollTimer = setInterval(() => {
          get().checkHealth();
        }, POLL_INTERVAL);
      },

      stopPolling: () => {
        if (pollTimer) {
          clearInterval(pollTimer);
          pollTimer = null;
        }
      },

      startBackgroundPolling: () => {
        if (bgPolling) return;
        bgPolling = true;
        const run = ++bgPollRun;

        const runBackgroundCheck = async (): Promise<boolean> => {
          try {
            const [infraResp, servicesResp] = await Promise.allSettled([
              apiClient.get('/api/v1/health', { suppressErrorToast: true }),
              apiClient.get('/api/v1/health/services', { suppressErrorToast: true }),
            ]);
            // Polling restarted while this check was out; a newer one owns the state.
            if (run !== bgPollRun) return false;

            const infraData =
              infraResp.status === 'fulfilled' ? infraResp.value.data : null;
            const servicesData =
              servicesResp.status === 'fulfilled' ? servicesResp.value.data : null;

            const overallHealthy =
              infraData?.status === 'healthy' && servicesData?.status === 'healthy';
            const serverReachable =
              infraResp.status === 'fulfilled' || servicesResp.status === 'fulfilled';

            set((state) => {
              state.loading = false;
              state.apiServerReachable = serverReachable;
              state.backgroundCheckFailed = !overallHealthy;
              state.infraServices = infraData?.services ?? null;
              state.appServices = servicesData?.services ?? null;
              state.infraServiceNames = infraData?.serviceNames ?? null;
              state.lastChecked = Date.now();
            });

            if (overallHealthy) {
              try {
                localStorage.setItem(CACHE_KEY, 'true');
              } catch {}
            } else {
              try {
                localStorage.removeItem(CACHE_KEY);
              } catch {}
            }
            return overallHealthy;
          } catch {
            if (run !== bgPollRun) return false;
            set((state) => {
              state.loading = false;
              state.apiServerReachable = false;
              state.backgroundCheckFailed = true;
              state.lastChecked = Date.now();
            });
            try {
              localStorage.removeItem(CACHE_KEY);
            } catch {}
            return false;
          }
        };

        const poll = async () => {
          const healthy = await runBackgroundCheck();
          if (run !== bgPollRun) return;
          bgPollTimer = setTimeout(poll, healthy ? POLL_INTERVAL : RECOVERY_POLL_INTERVAL);
        };

        void poll();
      },

      stopBackgroundPolling: () => {
        bgPolling = false;
        bgPollRun++;
        if (bgPollTimer) {
          clearTimeout(bgPollTimer);
          bgPollTimer = null;
        }
      },

      clearCache: () => {
        try {
          localStorage.removeItem(CACHE_KEY);
        } catch {}
      },
    })),
    { name: 'ServicesHealthStore' },
  ),
);

// ========================================
// Constants (shared by HealthGate + ServiceGate)
// ========================================

export const APP_SERVICE_LABELS: Record<string, string> = {
  query: 'Query Service',
  connector: 'Connector Service',
  indexing: 'Indexing Service',
  docling: 'Docling Service',
  parsing: 'Parsing Service',
  extraction: 'Extraction Service',
};

// ========================================
// Selectors
// ========================================

export const selectHealthy = (s: ServicesHealthStore) => s.healthy;
export const selectLoading = (s: ServicesHealthStore) => s.loading;
export const selectBackgroundCheckFailed = (s: ServicesHealthStore) => s.backgroundCheckFailed;
export const selectApiServerReachable = (s: ServicesHealthStore) => s.apiServerReachable;
export const selectInfraServices = (s: ServicesHealthStore) => s.infraServices;
export const selectAppServices = (s: ServicesHealthStore) => s.appServices;
export const selectInfraServiceNames = (s: ServicesHealthStore) => s.infraServiceNames;
export const selectLastChecked = (s: ServicesHealthStore) => s.lastChecked;

/**
 * Returns true if cached health check exists in localStorage.
 */
export function isCachedHealthy(): boolean {
  try {
    return localStorage.getItem(CACHE_KEY) === 'true';
  } catch {
    return false;
  }
}
