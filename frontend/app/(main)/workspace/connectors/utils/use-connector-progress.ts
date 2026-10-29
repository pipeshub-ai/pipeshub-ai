'use client';

import { useEffect, useState } from 'react';
import { ConnectorsApi } from '../api';
import type { ProcessedError } from '@/lib/api/api-error';
import { nextPollMs, type ConnectorProgress } from './connector-progress';

/** The caller may not open this connector, or it is gone: asking again will not change that. */
function isRefused(error: unknown): boolean {
  const status = (error as Partial<ProcessedError> | null)?.statusCode;
  return status === 403 || status === 404;
}

/**
 * Poll a connector's progress at the pace its state calls for (`nextPollMs`).
 * `refreshKey` restarts the loop at once, e.g. when the instance's status changes
 * after the user clicks Sync. Errors keep the last answer and retry at the slow pace,
 * except a refusal (403/404), which stops the loop. `quiet` shows no error toast.
 */
export function useConnectorProgress(
  connectorId: string | undefined,
  detail: 'summary' | 'full' = 'summary',
  refreshKey?: unknown,
  quiet = false,
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
        const fresh = await ConnectorsApi.getConnectorProgress(
          connectorId,
          detail,
          quiet ? { suppressErrorToast: true } : undefined,
        );
        if (cancelled) return;
        setView(fresh);
        next = nextPollMs(fresh);
      } catch (error) {
        // Progress is a view; a failed read must not break the card around it.
        if (isRefused(error)) next = null;
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
  }, [connectorId, detail, refreshKey, quiet]);

  // Never another connector's answer while this one's first read is pending or failed.
  return view && view.connectorId === connectorId ? view : null;
}
