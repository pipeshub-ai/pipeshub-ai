'use client';

import { useCallback, useEffect, useState } from 'react';
import { apiClient } from '@/lib/api';
import { useAuthStore } from '@/lib/store/auth-store';
import {
  connectNotificationSocket,
  getNotificationSocket,
} from '@/lib/socket/notification-socket';
import type { ProgressSnapshot } from './types';

interface UseIndexingProgressResult {
  snapshot: ProgressSnapshot | null;
  dismissed: boolean;
  dismiss: () => void;
}

/**
 * Admin-only org-wide indexing progress. Fetches the current snapshot once on
 * mount (which also triggers the server-side on-demand seed if Redis is cold),
 * then keeps it live via the `progressUpdate` socket event. A 204/403 simply
 * yields no snapshot (nothing to show / not an admin). Dismiss is client-side
 * only — the bar is shared across admins, so we never delete server state.
 */
export function useIndexingProgress(): UseIndexingProgressResult {
  const [snapshot, setSnapshot] = useState<ProgressSnapshot | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const accessToken = useAuthStore((s) => s.accessToken);

  // Initial fetch.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await apiClient.get<ProgressSnapshot>('/api/v1/progress');
        if (cancelled) return;
        // 204 => no active/complete progress; axios gives empty data.
        setSnapshot(res.status === 204 || !res.data ? null : res.data);
      } catch {
        // 403 (non-admin) or transient error — show nothing.
        if (!cancelled) setSnapshot(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Live updates.
  useEffect(() => {
    const socket = getNotificationSocket() || connectNotificationSocket(accessToken);
    if (!socket) return undefined;

    const onProgress = (data: (ProgressSnapshot & { retired?: boolean }) | null) => {
      // Retire signal: the org finished and its grace window elapsed. Clear the
      // widget so it doesn't freeze on a stale snapshot until a manual refresh.
      if (!data || (data as { retired?: boolean }).retired) {
        setSnapshot(null);
        return;
      }
      // A fresh update reopens a bar the admin previously dismissed only when new
      // work arrives (server stops emitting once retired), which is intended.
      setDismissed(false);
      setSnapshot(data);
    };
    socket.on('progressUpdate', onProgress);
    return () => {
      socket.off('progressUpdate', onProgress);
    };
  }, [accessToken]);

  const dismiss = useCallback(() => setDismissed(true), []);

  return { snapshot, dismissed, dismiss };
}
