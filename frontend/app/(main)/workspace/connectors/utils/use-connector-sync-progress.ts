'use client';

import { useEffect, useRef, useState } from 'react';
import { ConnectorsApi } from '../api';
import { isActiveConnectorSyncStatus } from './sync-progress-view';
import type { ConnectorSyncProgress } from '../types';

export { isActiveConnectorSyncStatus } from './sync-progress-view';

const ACTIVE_POLL_MS = 5_000;

/**
 * Fetch run-scoped sync progress for one connector instance, polling every few
 * seconds while the run is active (either the instance status is SYNCING or the
 * indexing phase is still draining after discovery closed). Stops once settled.
 */
export function useConnectorSyncProgress(
  connectorId: string | undefined,
  status?: string | null,
  enabled = true
): { progress: ConnectorSyncProgress | null; loading: boolean } {
  const [progress, setProgress] = useState<ConnectorSyncProgress | null>(null);
  const [loading, setLoading] = useState(false);

  // Keep the latest status visible to the running poll chain without resetting it.
  const statusRef = useRef(status);
  statusRef.current = status;

  useEffect(() => {
    if (!connectorId || !enabled) {
      return undefined;
    }

    let cancelled = false;
    let timer: number | undefined;

    const tick = async (): Promise<void> => {
      try {
        const data = await ConnectorsApi.getConnectorSyncProgress(connectorId);
        if (cancelled) return;
        setProgress(data);
        const keepPolling =
          Boolean(data?.isActive) || isActiveConnectorSyncStatus(statusRef.current);
        if (keepPolling && !cancelled) {
          timer = window.setTimeout(() => {
            void tick();
          }, ACTIVE_POLL_MS);
        }
      } catch {
        // Best-effort: on transient errors, stop polling until deps change.
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    setLoading(true);
    void tick();

    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [connectorId, status, enabled]);

  return { progress, loading };
}
