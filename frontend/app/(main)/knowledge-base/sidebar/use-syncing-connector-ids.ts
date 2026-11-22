'use client';

import { useEffect, useState } from 'react';
import { ConnectorsApi } from '@/app/(main)/workspace/connectors/api';
import { CONNECTOR_INSTANCE_STATUS } from '@/app/(main)/workspace/connectors/constants';
import type { Connector } from '@/app/(main)/workspace/connectors/types';

// Often enough to catch a sync starting or ending; a dot needs no more.
const POLL_WHILE_SYNCING_MS = 15_000;
const POLL_AT_REST_MS = 60_000;

const RUNNING: ReadonlySet<string> = new Set([
  CONNECTOR_INSTANCE_STATUS.SYNCING,
  CONNECTOR_INSTANCE_STATUS.FULL_SYNCING,
]);

export function syncingIds(connectors: Connector[]): Set<string> {
  return new Set(
    connectors
      .filter((c) => c._key && c.isActive && RUNNING.has((c.status ?? '').toUpperCase()))
      .map((c) => c._key as string),
  );
}

async function fetchSyncingIds(): Promise<Set<string>> {
  const lists = await Promise.all(
    (['team', 'personal'] as const).map((scope) =>
      ConnectorsApi.getActiveConnectors(scope, 1, 100, { suppressErrorToast: true })
        .then((res) => res.connectors ?? [])
        .catch(() => [] as Connector[]),
    ),
  );
  return syncingIds(lists.flat());
}

/** Ids of the connectors syncing now, among those the viewer can see; polled while mounted and visible. */
export function useSyncingConnectorIds(): ReadonlySet<string> {
  const [ids, setIds] = useState<ReadonlySet<string>>(new Set());

  useEffect(() => {
    let cancelled = false;
    let timeoutId: number | undefined;
    const resume = () => {
      document.removeEventListener('visibilitychange', resume);
      if (!cancelled) void check();
    };
    const check = async () => {
      const found = await fetchSyncingIds();
      if (cancelled) return;
      setIds(found);
      timeoutId = window.setTimeout(() => {
        if (document.visibilityState === 'hidden') {
          document.addEventListener('visibilitychange', resume);
          return;
        }
        void check();
      }, found.size ? POLL_WHILE_SYNCING_MS : POLL_AT_REST_MS);
    };
    void check();
    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
      document.removeEventListener('visibilitychange', resume);
    };
  }, []);

  return ids;
}
