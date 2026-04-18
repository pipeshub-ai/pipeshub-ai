'use client';

import { useEffect, useRef } from 'react';
import { useServicesHealthStore, isCachedHealthy } from '@/lib/store/services-health-store';
import { toast } from '@/lib/store/toast-store';
import { LoadingScreen } from './auth-guard';

/**
 * Blocks rendering of the authenticated app until critical services
 * (Node.js infra + Query + Connector) are healthy.
 *
 * On first load, checks localStorage cache — if a previous session was healthy,
 * renders children immediately. Otherwise polls `/api/v1/health` and
 * `/api/v1/health/services` every 5 s until both return healthy.
 */
export function HealthGate({ children }: { children: React.ReactNode }) {
  const loading = useServicesHealthStore((s) => s.loading);
  const healthy = useServicesHealthStore((s) => s.healthy);
  const startPolling = useServicesHealthStore((s) => s.startPolling);
  const stopPolling = useServicesHealthStore((s) => s.stopPolling);

  const toastIdRef = useRef<string | null>(null);
  const hasCheckedCache = useRef(false);
  const cachedHealthy = useRef(false);

  useEffect(() => {
    if (!hasCheckedCache.current) {
      hasCheckedCache.current = true;
      cachedHealthy.current = isCachedHealthy();
    }

    if (cachedHealthy.current) {
      useServicesHealthStore.setState({ loading: false, healthy: true });
      return;
    }

    const timer = setTimeout(() => {
      if (!cachedHealthy.current && toastIdRef.current === null) {
        toastIdRef.current = toast.loading('Checking services health. Please wait…');
      }
    }, 1000);

    startPolling();

    return () => {
      clearTimeout(timer);
      stopPolling();
      if (toastIdRef.current) {
        toast.dismiss(toastIdRef.current);
        toastIdRef.current = null;
      }
    };
  }, [startPolling, stopPolling]);

  useEffect(() => {
    if (cachedHealthy.current) return;

    if (healthy === true && toastIdRef.current) {
      toast.update(toastIdRef.current, {
        variant: 'success',
        title: 'Services are healthy',
      });
      toastIdRef.current = null;
    } else if (healthy === false && toastIdRef.current) {
      toast.update(toastIdRef.current, {
        variant: 'loading',
        title: 'Waiting for services to become ready…',
      });
    }
  }, [healthy]);

  if (cachedHealthy.current) {
    return <>{children}</>;
  }

  if (loading || healthy !== true) {
    return <LoadingScreen />;
  }

  return <>{children}</>;
}
