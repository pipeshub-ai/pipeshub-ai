'use client';

import { useEffect, useRef } from 'react';
import { useRouter } from 'next/navigation';
import { useTranslation } from 'react-i18next';
import { Flex, Text, Heading } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import {
  useServicesHealthStore,
  selectBackgroundCheckFailed,
  selectApiServerReachable,
  selectAppServices,
  selectInfraServices,
  selectInfraServiceNames,
  APP_SERVICE_LABELS,
  formatServiceList,
  type AppServices,
  type InfraServices,
} from '@/lib/store/services-health-store';
import { toast } from '@/lib/store/toast-store';
import { useUserStore, selectIsAdmin } from '@/lib/store/user-store';

const CRITICAL_APP_SERVICES = new Set(['query', 'connector']);
const NON_CRITICAL_TOAST_INTERVAL = 60 * 60 * 1000; // 1 hour
const SERVER_RETRY_INTERVAL = 10000; // 10 seconds

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

function BackendUnavailableScreen() {
  const { t } = useTranslation();

  return (
    <Flex
      align="center"
      justify="center"
      style={{
        height: '100vh',
        width: '100%',
        backgroundColor: 'var(--olive-2)',
      }}
    >
      <Flex
        direction="column"
        align="center"
        gap="4"
        style={{ maxWidth: 420, textAlign: 'center', padding: '0 24px' }}
      >
        <Flex
          align="center"
          justify="center"
          style={{
            width: 64,
            height: 64,
            borderRadius: 'var(--radius-3)',
            backgroundColor: 'var(--amber-a3)',
          }}
        >
          <MaterialIcon name="cloud_off" size={32} color="var(--amber-9)" />
        </Flex>

        <Heading size="5" style={{ color: 'var(--slate-12)' }}>
          {t('healthGate.title')}
        </Heading>

        <Text size="2" style={{ color: 'var(--slate-10)', lineHeight: 1.6 }}>
          {t('healthGate.description')}
        </Text>

        <Text size="2" style={{ color: 'var(--slate-10)', lineHeight: 1.6 }}>
          {t('healthGate.contactAdmin')}
        </Text>

        <LottieLoader variant="loader" size={48} />
      </Flex>
    </Flex>
  );
}

/**
 * Non-blocking health monitor for the authenticated app, with a blocking
 * gate when the API server itself is unreachable.
 *
 * When the Node.js backend is completely down (all health endpoints fail),
 * a full-page "Waiting for Server" screen is shown and a fast retry loop
 * (every 10 s) attempts to reconnect.  Once the server responds, children
 * are rendered and normal background polling (every 10 min) resumes.
 *
 * When the server IS reachable but some services are unhealthy, persistent
 * or auto-dismissing toasts are shown while children render normally.
 * Individual pages use `<ServiceGate>` to block when the specific services
 * they need are down.
 */
export function HealthGate({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const isAdmin = useUserStore(selectIsAdmin);

  const startBackgroundPolling = useServicesHealthStore((s) => s.startBackgroundPolling);
  const stopBackgroundPolling = useServicesHealthStore((s) => s.stopBackgroundPolling);
  const retryServerConnection = useServicesHealthStore((s) => s.retryServerConnection);
  const apiServerReachable = useServicesHealthStore(selectApiServerReachable);
  const backgroundCheckFailed = useServicesHealthStore(selectBackgroundCheckFailed);
  const appServices = useServicesHealthStore(selectAppServices);
  const infraServices = useServicesHealthStore(selectInfraServices);
  const infraServiceNames = useServicesHealthStore(selectInfraServiceNames);

  const criticalToastIdRef = useRef<string | null>(null);
  const lastNonCriticalToastRef = useRef<number>(0);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const wasUnreachableRef = useRef(false);

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

  // ── Refresh data on server recovery to clear stale state ────────────────
  useEffect(() => {
    if (!apiServerReachable) {
      wasUnreachableRef.current = true;
      return;
    }
    if (wasUnreachableRef.current) {
      wasUnreachableRef.current = false;
      router.refresh();
    }
  }, [apiServerReachable, router]);

  // ── Fast retry when server is unreachable ────────────────────────────────
  useEffect(() => {
    if (apiServerReachable) {
      if (retryTimerRef.current) {
        clearTimeout(retryTimerRef.current);
        retryTimerRef.current = null;
      }
      return;
    }

    const scheduleRetry = () => {
      retryTimerRef.current = setTimeout(async () => {
        await retryServerConnection();
        scheduleRetry();
      }, SERVER_RETRY_INTERVAL);
    };
    scheduleRetry();

    return () => {
      if (retryTimerRef.current) {
        clearTimeout(retryTimerRef.current);
        retryTimerRef.current = null;
      }
    };
  }, [apiServerReachable, retryServerConnection]);

  // ── Show / update / dismiss toasts based on health status ────────────────
  useEffect(() => {
    if (!apiServerReachable) {
      if (criticalToastIdRef.current) {
        toast.dismiss(criticalToastIdRef.current);
        criticalToastIdRef.current = null;
      }
      return;
    }

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
      const description = isAdmin === false
        ? `Affected: ${critical.join(', ')}. Please contact your administrator for assistance.`
        : `Affected: ${critical.join(', ')}`;
      if (criticalToastIdRef.current === null) {
        criticalToastIdRef.current = toast.error(
          'Some services are unavailable',
          {
            description,
            duration: null,
            ...(isAdmin === true && {
              action: {
                label: 'View status',
                onClick: () => router.push('/workspace/services'),
              },
            }),
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
            ...(isAdmin === true && {
              action: {
                label: 'View status',
                onClick: () => router.push('/workspace/services'),
              },
            }),
          },
        );
      }
    }
  }, [apiServerReachable, backgroundCheckFailed, appServices, infraServices, infraServiceNames, isAdmin, router]);

  if (!apiServerReachable) {
    return <BackendUnavailableScreen />;
  }

  return <>{children}</>;
}
