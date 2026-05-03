'use client';

import { useEffect, useRef } from 'react';
import { useRouter } from 'next/navigation';
import {
  useServicesHealthStore,
  selectBackgroundCheckFailed,
  selectAppServices,
  selectInfraServices,
  selectInfraServiceNames,
  APP_SERVICE_LABELS,
  formatServiceList,
  type AppServices,
  type InfraServices,
} from '@/lib/store/services-health-store';
import { toast } from '@/lib/store/toast-store';

const CRITICAL_APP_SERVICES = new Set(['query', 'connector']);
const NON_CRITICAL_TOAST_INTERVAL = 60 * 60 * 1000; // 1 hour

function classifyUnhealthyServices(
  appServices: AppServices | null,
  infraServices: InfraServices | null,
  infraServiceNames: Record<string, string> | null,
): { critical: string[]; nonCritical: string[] } {
  const critical: string[] = [];
  const nonCritical: string[] = [];

  if (appServices) {
    for (const [key, status] of Object.entries(appServices)) {
      if (status !== 'unhealthy') continue;
      const label = APP_SERVICE_LABELS[key] || key;
      if (CRITICAL_APP_SERVICES.has(key)) {
        critical.push(label);
      } else {
        nonCritical.push(label);
      }
    }
  }

  if (infraServices) {
    for (const [key, status] of Object.entries(infraServices)) {
      if (status === 'unhealthy') {
        critical.push(infraServiceNames?.[key] || key);
      }
    }
  }

  return { critical, nonCritical };
}

/**
 * Non-blocking health monitor for the authenticated app.
 *
 * Starts a background poll (every 5 s) against the health endpoints.
 * When critical services (query, connector, infra) are unhealthy a
 * persistent warning toast is shown; non-critical services (indexing,
 * docling) trigger a separate auto-dismissing toast throttled to once
 * per hour.  Children are always rendered — individual pages use
 * `<ServiceGate>` to block when the specific services they need are
 * down.
 */
export function HealthGate({ children }: { children: React.ReactNode }) {
  const router = useRouter();

  const startBackgroundPolling = useServicesHealthStore((s) => s.startBackgroundPolling);
  const stopBackgroundPolling = useServicesHealthStore((s) => s.stopBackgroundPolling);
  const backgroundCheckFailed = useServicesHealthStore(selectBackgroundCheckFailed);
  const appServices = useServicesHealthStore(selectAppServices);
  const infraServices = useServicesHealthStore(selectInfraServices);
  const infraServiceNames = useServicesHealthStore(selectInfraServiceNames);

  const criticalToastIdRef = useRef<string | null>(null);
  const lastNonCriticalToastRef = useRef<number>(0);

  // ── Start background polling on mount ────────────────────────────────────
  useEffect(() => {
    startBackgroundPolling();
    return () => {
      stopBackgroundPolling();
      if (criticalToastIdRef.current) {
        toast.dismiss(criticalToastIdRef.current);
        criticalToastIdRef.current = null;
      }
    };
  }, [startBackgroundPolling, stopBackgroundPolling]);

  // ── Show / update / dismiss toasts based on health status ────────────────
  useEffect(() => {
    if (!backgroundCheckFailed) {
      if (criticalToastIdRef.current) {
        toast.dismiss(criticalToastIdRef.current);
        criticalToastIdRef.current = null;
      }
      return;
    }

    const { critical, nonCritical } = classifyUnhealthyServices(
      appServices,
      infraServices,
      infraServiceNames,
    );

    // Critical services → persistent toast
    if (critical.length > 0) {
      const description = `Affected: ${critical.join(', ')}`;
      if (criticalToastIdRef.current === null) {
        criticalToastIdRef.current = toast.error(
          'Some services are unavailable',
          {
            description,
            duration: null,
            action: {
              label: 'View status',
              onClick: () => router.push('/workspace/services'),
            },
          },
        );
      } else {
        toast.update(criticalToastIdRef.current, { description });
      }
    } else if (criticalToastIdRef.current !== null) {
      toast.dismiss(criticalToastIdRef.current);
      criticalToastIdRef.current = null;
    }

    // Non-critical services (indexing, docling) → auto-dismiss toast, once per hour
    if (nonCritical.length > 0) {
      const now = Date.now();
      if (now - lastNonCriticalToastRef.current >= NON_CRITICAL_TOAST_INTERVAL) {
        lastNonCriticalToastRef.current = now;
        toast.warning(
          `${formatServiceList(nonCritical)} ${nonCritical.length === 1 ? 'is' : 'are'} currently unavailable`,
          {
            action: {
              label: 'View status',
              onClick: () => router.push('/workspace/services'),
            },
          },
        );
      }
    }
  }, [backgroundCheckFailed, appServices, infraServices, infraServiceNames, router]);

  // Always render children — never block the app shell.
  return <>{children}</>;
}
