'use client';

import { useEffect, useState } from 'react';
import { ConnectorsApi } from '../api';
import { nextPollMs, type ConnectorProgress } from './connector-progress';

/**
 * Poll a connector's progress at the pace its state calls for (`nextPollMs`).
 * `refreshKey` restarts the loop at once, e.g. when the instance's status changes
 * after the user clicks Sync. Errors keep the last answer and retry at the slow pace.
 */
export function useConnectorProgress(
  connectorId: string | undefined,
  detail: 'summary' | 'full' = 'summary',
  refreshKey?: unknown,
): ConnectorProgress | null {
  const [view, setView] = useState<ConnectorProgress | null>(null);

  useEffect(() => {
    if (!connectorId) return;
    let cancelled = false;
    let timeoutId: number | undefined;
    const resume = () => {
      document.removeEventListener('visibilitychange', resume);
      if (!cancelled) void load();
    };
    const load = async () => {
      let next: number | null = 30_000;
      try {
        const fresh = await ConnectorsApi.getConnectorProgress(connectorId, detail);
        if (cancelled) return;
        setView(fresh);
        next = nextPollMs(fresh);
      } catch {
        // Progress is a view; a failed read must not break the card around it.
      }
      // Hidden tabs keep their last answer instead of polling.
      if (!cancelled && next != null) {
        timeoutId = window.setTimeout(() => {
          if (document.visibilityState === 'hidden') {
            document.addEventListener('visibilitychange', resume);
            return;
          }
          void load();
        }, next);
      }
    };
    void load();
    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
      document.removeEventListener('visibilitychange', resume);
    };
  }, [connectorId, detail, refreshKey]);

  // Never another connector's answer while this one's first read is pending or failed.
  return view && view.connectorId === connectorId ? view : null;
}
