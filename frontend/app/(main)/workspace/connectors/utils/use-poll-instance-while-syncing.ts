'use client';

import { useEffect } from 'react';
import { refreshConnectorInstanceDetails } from './refresh-instance-details';
import { isActiveConnectorSyncStatus } from './sync-progress-view';

const SYNC_POLL_MS = 60_000;

/** Refetch one instance every minute while its backend status is SYNCING / FULL_SYNCING. */
export function usePollInstanceWhileSyncing(
  connectorId: string | undefined,
  status?: string | null
): void {
  useEffect(() => {
    if (!connectorId || !isActiveConnectorSyncStatus(status)) {
      return;
    }

    const intervalId = window.setInterval(() => {
      void refreshConnectorInstanceDetails(connectorId).catch(() => {});
    }, SYNC_POLL_MS);

    return () => {
      window.clearInterval(intervalId);
    };
  }, [connectorId, status]);
}
