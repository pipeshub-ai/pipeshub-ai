'use client';

import { useEffect, useRef } from 'react';
import { useRouter } from 'next/navigation';
import {
  useServicesHealthStore,
  isCachedHealthy,
  selectBackgroundCheckFailed,
} from '@/lib/store/services-health-store';
import { toast } from '@/lib/store/toast-store';
import { LoadingScreen } from './auth-guard';

/**
 * Blocks rendering of the authenticated app until critical services
 * (Node.js infra + Query + Connector) are healthy.
 *
 * Two modes:
 *
 * 1. **Cached-healthy** (localStorage `healthCheck === 'true'`):
 *    Children render immediately. A silent background poll runs every 5 s.
 *    If the background check fails the user is notified via a persistent
 *    warning toast (non-blocking). The toast is dismissed automatically
 *    once services recover.
 *
 * 2. **Not cached** (first load or after a failure):
 *    Children are blocked by `LoadingScreen` until both health endpoints
 *    report healthy, with a loading toast and 5 s polling.
 */
export function HealthGate({ children }: { children: React.ReactNode }) {
  const router = useRouter();

  const loading = useServicesHealthStore((s) => s.loading);
  const healthy = useServicesHealthStore((s) => s.healthy);
  const startPolling = useServicesHealthStore((s) => s.startPolling);
  const stopPolling = useServicesHealthStore((s) => s.stopPolling);
  const startBackgroundPolling = useServicesHealthStore((s) => s.startBackgroundPolling);
  const stopBackgroundPolling = useServicesHealthStore((s) => s.stopBackgroundPolling);
  const backgroundCheckFailed = useServicesHealthStore(selectBackgroundCheckFailed);

  const toastIdRef = useRef<string | null>(null);
  const hasCheckedCache = useRef(false);
  const cachedHealthy = useRef(false);

  // Determine cache status once on mount.
  if (!hasCheckedCache.current) {
    hasCheckedCache.current = true;
    cachedHealthy.current = isCachedHealthy();
  }

  // ── Cached path: start silent background polling ──────────────────────────
  useEffect(() => {
    if (!cachedHealthy.current) return;

    startBackgroundPolling();
    return () => {
      stopBackgroundPolling();
      if (toastIdRef.current) {
        toast.dismiss(toastIdRef.current);
        toastIdRef.current = null;
      }
    };
  }, [startBackgroundPolling, stopBackgroundPolling]);

  // ── Cached path: react to background check results ────────────────────────
  useEffect(() => {
    if (!cachedHealthy.current) return;

    if (backgroundCheckFailed && toastIdRef.current === null) {
      toastIdRef.current = toast.warning(
        'Some services appear to be unavailable. Your session may be affected.',
        {
          duration: null,
          action: {
            label: 'View status',
            onClick: () => router.push('/workspace/services'),
          },
        },
      );
    } else if (!backgroundCheckFailed && toastIdRef.current !== null) {
      toast.dismiss(toastIdRef.current);
      toastIdRef.current = null;
    }
  }, [backgroundCheckFailed, router]);

  // ── Non-cached path: blocking poll + toasts ───────────────────────────────
  useEffect(() => {
    if (cachedHealthy.current) return;

    const timer = setTimeout(() => {
      if (toastIdRef.current === null) {
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

  // ── Non-cached path: update loading toast on health changes ───────────────
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

  // ── Render ─────────────────────────────────────────────────────────────────

  // Cached: always show children — background check is non-blocking.
  if (cachedHealthy.current) {
    return <>{children}</>;
  }

  // Non-cached: block until healthy.
  if (loading || healthy !== true) {
    return <LoadingScreen />;
  }

  return <>{children}</>;
}
