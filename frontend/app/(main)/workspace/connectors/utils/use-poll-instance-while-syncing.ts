'use client';

import { useEffect } from 'react';
import { CONNECTOR_INSTANCE_STATUS } from '../constants';
import { refreshConnectorInstanceDetails } from './refresh-instance-details';

const SYNC_POLL_MS = 60_000;

function isSyncInProgressStatus(status?: string | null): boolean {
  const normalized = (status ?? CONNECTOR_INSTANCE_STATUS.IDLE).toUpperCase();
  return (
    normalized === CONNECTOR_INSTANCE_STATUS.SYNCING ||
    normalized === CONNECTOR_INSTANCE_STATUS.FULL_SYNCING
  );
}

/** Refetch one instance every minute while its backend status is SYNCING / FULL_SYNCING. */
export function usePollInstanceWhileSyncing(
  connectorId: string | undefined,
  status?: string | null
): void {
  useEffect(() => {
    if (!connectorId || !isSyncInProgressStatus(status)) {
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
