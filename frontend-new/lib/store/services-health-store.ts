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
}

interface ServicesHealthState {
  loading: boolean;
  healthy: boolean | null;
  infraServices: InfraServices | null;
  appServices: AppServices | null;
  infraServiceNames: Record<string, string> | null;
  lastChecked: number | null;
}

interface ServicesHealthActions {
  checkHealth: () => Promise<void>;
  startPolling: () => void;
  stopPolling: () => void;
  clearCache: () => void;
}

type ServicesHealthStore = ServicesHealthState & ServicesHealthActions;

// ========================================
// Constants
// ========================================

const POLL_INTERVAL = 5000;
const CACHE_KEY = 'healthCheck';

let pollTimer: ReturnType<typeof setInterval> | null = null;

// ========================================
// Store
// ========================================

const initialState: ServicesHealthState = {
  loading: true,
  healthy: null,
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

          set((state) => {
            state.loading = false;
            state.healthy = overallHealthy;
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
          }
        } catch {
          set((state) => {
            state.loading = false;
            state.healthy = false;
            state.infraServices = null;
            state.appServices = null;
            state.lastChecked = Date.now();
          });
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
// Selectors
// ========================================

export const selectHealthy = (s: ServicesHealthStore) => s.healthy;
export const selectLoading = (s: ServicesHealthStore) => s.loading;
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
