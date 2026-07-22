'use client';

import { useEffect } from 'react';
import useSWR from 'swr';
import { ConnectorsApi } from '../api';
import { isActiveConnectorSyncStatus } from './sync-progress-view';
import type { ConnectorSyncProgress } from '../types';

export { isActiveConnectorSyncStatus } from './sync-progress-view';

const ACTIVE_POLL_MS = 5_000;
// Coverage draining has no run heartbeat; one permanently stuck record would
// otherwise keep the fast poll alive forever, so back off to a slow poll.
const PENDING_COVERAGE_POLL_MS = 30_000;

function hasPendingCoverage(progress: ConnectorSyncProgress | null | undefined): boolean {
  return ((progress?.coverage?.inProgress ?? 0) + (progress?.coverage?.queued ?? 0)) > 0;
}

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
  const id = connectorId && enabled ? connectorId : null;
  const key = id ? `connector-sync-progress:${id}` : null;
  const { data, isLoading, mutate } = useSWR<ConnectorSyncProgress | null>(
    key,
    () => {
      if (!id) throw new Error('Connector sync progress requested without a connector id');
      return ConnectorsApi.getConnectorSyncProgress(id);
    },
    {
      // SWR shares one cache/in-flight request between the instance card and
      // open Overview panel, preventing duplicate polling for the same connector.
      dedupingInterval: ACTIVE_POLL_MS,
      refreshInterval: (latest) => {
        if (Boolean(latest?.isActive) || activeStatus) return ACTIVE_POLL_MS;
        if (hasPendingCoverage(latest)) return PENDING_COVERAGE_POLL_MS;
        return 0;
      },
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
