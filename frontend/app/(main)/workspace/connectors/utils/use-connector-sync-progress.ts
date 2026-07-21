'use client';

import { useEffect } from 'react';
import useSWR from 'swr';
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
  const activeStatus = isActiveConnectorSyncStatus(status);
  const key = connectorId && enabled ? `connector-sync-progress:${connectorId}` : null;
  const { data, isLoading, mutate } = useSWR<ConnectorSyncProgress | null>(
    key,
    () => ConnectorsApi.getConnectorSyncProgress(connectorId!),
    {
      // SWR shares one cache/in-flight request between the instance card and
      // open Overview panel, preventing duplicate polling for the same connector.
      dedupingInterval: ACTIVE_POLL_MS,
      refreshInterval: (latest) =>
        Boolean(latest?.isActive) || activeStatus ? ACTIVE_POLL_MS : 0,
      refreshWhenHidden: false,
      revalidateOnFocus: false,
      revalidateOnReconnect: true,
    }
  );

  // An optimistic status transition should show progress immediately rather
  // than waiting for the next scheduled refresh. Concurrent consumers dedupe it.
  useEffect(() => {
    if (activeStatus && key) {
      void mutate();
    }
  }, [activeStatus, key, mutate]);

  return { progress: data ?? null, loading: isLoading };
}
