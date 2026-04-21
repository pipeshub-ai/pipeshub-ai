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

export interface Deployment {
  kvStoreType: string;
  messageBrokerType: string;
  graphDbType: string;
  vectorDbType?: string;
}

interface ServicesHealthState {
  backgroundCheckFailed: boolean;
  infraServices: InfraServices | null;
  appServices: AppServices | null;
  infraServiceNames: Record<string, string> | null;
  deployment: Deployment | null;
  lastChecked: number | null;
}

interface ServicesHealthActions {
  checkHealth: () => Promise<boolean>;
}

type ServicesHealthStore = ServicesHealthState & ServicesHealthActions;

// ========================================
// Store
// ========================================

const initialState: ServicesHealthState = {
  backgroundCheckFailed: false,
  infraServices: null,
  appServices: null,
  infraServiceNames: null,
  deployment: null,
  lastChecked: null,
};

export const useServicesHealthStore = create<ServicesHealthStore>()(
  devtools(
    immer((set) => ({
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

          const overallHealthy =
            infraData?.status === 'healthy' && servicesData?.status === 'healthy';

          set((state) => {
            state.backgroundCheckFailed = !overallHealthy;
            state.infraServices = infraData?.services ?? null;
            state.appServices = servicesData?.services ?? null;
            state.infraServiceNames = infraData?.serviceNames ?? null;
            state.deployment = infraData?.deployment ?? null;
            state.lastChecked = Date.now();
          });

          return overallHealthy;
        } catch {
          set((state) => {
            state.backgroundCheckFailed = true;
            state.lastChecked = Date.now();
          });
          return false;
        }
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
};

export function formatServiceList(items: string[]): string {
  if (items.length <= 1) return items[0] ?? '';
  if (items.length === 2) return `${items[0]} and ${items[1]}`;
  return `${items.slice(0, -1).join(', ')}, and ${items[items.length - 1]}`;
}

// ========================================
// Selectors
// ========================================

export const selectBackgroundCheckFailed = (s: ServicesHealthStore) => s.backgroundCheckFailed;
export const selectInfraServices = (s: ServicesHealthStore) => s.infraServices;
export const selectAppServices = (s: ServicesHealthStore) => s.appServices;
export const selectInfraServiceNames = (s: ServicesHealthStore) => s.infraServiceNames;
export const selectDeployment = (s: ServicesHealthStore) => s.deployment;
export const selectLastChecked = (s: ServicesHealthStore) => s.lastChecked;
