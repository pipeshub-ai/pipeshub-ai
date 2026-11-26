'use client';

import { useEffect } from 'react';
import { useAuthStore } from '@/config';
import { selectAgentHarnessOn, useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { getNotificationSocket } from '@/lib/socket/notification-socket';
import type { InboxCounts } from '@/lib/contracts/harness/generated/ext/pipeshub/api/inbox-counts';
import { EMPTY_COUNTS, refreshCount, reloadInbox, takeCounts, useInboxStore } from './inbox-store';

const isCounts = (value: unknown): value is InboxCounts => {
  const v = value as Partial<InboxCounts> | null;
  return (
    !!v &&
    typeof v.unread === 'number' &&
    typeof v.waiting === 'number' &&
    typeof v.badge === 'number' &&
    typeof v.paused === 'boolean' &&
    (v.pausedUntil === null || typeof v.pausedUntil === 'number')
  );
};

/**
 * Keeps the Inbox's count live: read once signed in, then from the `inboxCount` push on the notification socket the
 * classic `NotificationProvider` opens (mounted before this, so its socket is there). A new notice also re-reads an
 * open list. Coming back to the tab reads again, for what happened in another tab. Mounted once, in the signed-in
 * layout; with the harness off for the org there is nothing to count and nothing is read.
 */
export function useInboxLive(): void {
  const accessToken = useAuthStore((s) => s.accessToken);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const harness = useFeatureFlagsStore(selectAgentHarnessOn);

  useEffect(() => {
    if (!isAuthenticated || !accessToken || !harness) {
      useInboxStore.setState({ counts: EMPTY_COUNTS, countsKnown: false });
      return;
    }
    void refreshCount();
    const sock = getNotificationSocket();
    const onCount = (payload: unknown) => {
      if (!isCounts(payload)) return;
      // The server could not count what only this session's own credential counts (FIXMS1-FU-1): read it here.
      if (payload.recount === true) void refreshCount();
      else takeCounts(payload);
    };
    const onNotice = () => {
      if (useInboxStore.getState().open) void reloadInbox();
      else void refreshCount();
    };
    const onConnect = () => void refreshCount();
    sock?.on('inboxCount', onCount);
    sock?.on('newNotification', onNotice);
    sock?.on('connect', onConnect);
    const onVisible = () => {
      if (document.visibilityState === 'visible') void refreshCount();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      sock?.off('inboxCount', onCount);
      sock?.off('newNotification', onNotice);
      sock?.off('connect', onConnect);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [accessToken, isAuthenticated, harness]);
}
