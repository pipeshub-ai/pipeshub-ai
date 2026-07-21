'use client';

import { useEffect, useRef, useState } from 'react';
import { ConnectorsApi } from '../api';
import {
  countByType,
  isActiveConnectorSyncStatus,
  reconcileSyncActivity,
} from './sync-progress-view';
import type { Connector } from '../types';

const POLL_MS = 5_000;

/**
 * Per-connector-type count of instances that are actively syncing OR indexing,
 * for the catalog "N of M syncing" cue. Unlike a status-only derivation, this
 * follows an instance through the post-discovery indexing phase (when
 * apps.status is already IDLE) by consulting the run-scoped sync-progress store.
 *
 * Polling is bounded: on each effect run it probes enabled instances once to
 * catch runs already in flight (e.g. page loaded mid-indexing), then only keeps
 * polling instances with an active run/status and stops entirely when idle.
 */
export function useConnectorSyncActivity(
  instances: Connector[]
): Record<string, number> {
  const [activeIdToType, setActiveIdToType] = useState<Record<string, string>>({});

  const instancesRef = useRef(instances);
  instancesRef.current = instances;
  const trackedRef = useRef<string[]>([]);

  // Restart the poll loop when the enabled set or the status-syncing set changes.
  const enabledSignature = instances
    .filter((i) => i._key && i.isActive)
    .map((i) => i._key)
    .sort()
    .join(',');
  const syncingSignature = instances
    .filter((i) => i._key && isActiveConnectorSyncStatus(i.status))
    .map((i) => i._key)
    .sort()
    .join(',');

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    let first = true;

    const mapped = () =>
      (instancesRef.current || [])
        .filter((i) => Boolean(i._key))
        .map((i) => ({ id: i._key as string, type: i.type, status: i.status }));

    const tick = async (): Promise<void> => {
      const raw = (instancesRef.current || []).filter((i) => Boolean(i._key));
      const toFetch = new Set<string>(trackedRef.current);
      for (const i of raw) {
        if (isActiveConnectorSyncStatus(i.status)) toFetch.add(i._key as string);
      }
      if (first) {
        // One-shot probe of enabled instances to catch runs already indexing.
        for (const i of raw) {
          if (i.isActive) toFetch.add(i._key as string);
        }
        first = false;
      }

      const ids = Array.from(toFetch);
      if (ids.length === 0) {
        if (!cancelled) {
          trackedRef.current = [];
          setActiveIdToType({});
        }
        return;
      }

      const results = await Promise.all(
        ids.map(async (id) => {
          try {
            const p = await ConnectorsApi.getConnectorSyncProgress(id);
            return [id, Boolean(p?.isActive)] as const;
          } catch {
            return [id, false] as const;
          }
        })
      );
      if (cancelled) return;

      const runActiveById: Record<string, boolean> = {};
      for (const [id, active] of results) runActiveById[id] = active;

      const { activeIdToType: nextActive, trackedIds } = reconcileSyncActivity(
        mapped(),
        runActiveById,
        ids
      );
      trackedRef.current = trackedIds;
      setActiveIdToType(nextActive);

      if (!cancelled && trackedIds.length > 0) {
        timer = window.setTimeout(() => {
          void tick();
        }, POLL_MS);
      }
    };

    void tick();

    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabledSignature, syncingSignature]);

  return countByType(activeIdToType);
}
