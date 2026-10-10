'use client';

import { useEffect } from 'react';
import {
  connectNotificationSocket,
  disconnectNotificationSocket,
} from '@/lib/socket/notification-socket';
import { logoutAndRedirect, useAuthStore } from '@/config';
import { useNotificationStore } from './store';
import { useFeatureFlagsStore, selectCollaborativeChatsEnabled } from '@/lib/store/feature-flags-store';
import { NotificationsApi, type NotificationListItem } from './api';

/** Refetch stats (and list when the panel is open) from the server. */
async function syncNotificationsFromServer(): Promise<void> {
  try {
    const { isPanelOpen, listFilter, setStats, setInitialPage } =
      useNotificationStore.getState();
    const stats = await NotificationsApi.getStats();
    setStats(stats);
    if (isPanelOpen) {
      const page = await NotificationsApi.list(
        listFilter === 'unread'
          ? { status: 'unread' }
          : listFilter === 'archived'
            ? { status: 'archived' }
            : {},
      );
      setInitialPage(page);
    }
  } catch {
    // non-fatal: user still gets live events
  }
}

let mutedLoad: Promise<void> | null = null;

/**
 * The sidebar's muted marker reads this; it must not wait for the inbox panel to open. A background
 * fetch: a failure only leaves the marker off (no toast), and the startup effect and the socket's
 * first connect share one request.
 */
function loadMutedSessions(): void {
  if (mutedLoad || !selectCollaborativeChatsEnabled(useFeatureFlagsStore.getState())) return;
  mutedLoad = NotificationsApi.getPreferences({ quiet: true })
    .then((prefs) => useNotificationStore.getState().setMutedSessionIds(prefs.mutedSessions))
    .catch(() => undefined)
    .finally(() => {
      mutedLoad = null;
    });
}

/** Subscribes to real-time notifications when authenticated. Mount once under the main app shell. */
export function useNotificationSocket(): void {
  const accessToken = useAuthStore((s) => s.accessToken);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const isHydrated = useAuthStore((s) => s.isHydrated);
  const setInitialPage = useNotificationStore((s) => s.setInitialPage);
  const setStats = useNotificationStore((s) => s.setStats);
  const addNotification = useNotificationStore((s) => s.addNotification);
  const upsertNotification = useNotificationStore((s) => s.upsertNotification);

  const collabEnabled = useFeatureFlagsStore(selectCollaborativeChatsEnabled);
  useEffect(() => {
    if (collabEnabled && isHydrated && isAuthenticated && accessToken) loadMutedSessions();
  }, [collabEnabled, isHydrated, isAuthenticated, accessToken]);

  useEffect(() => {
    if (!isHydrated || !isAuthenticated || !accessToken) {
      disconnectNotificationSocket();
      return;
    }

    const sock = connectNotificationSocket(accessToken);
    if (!sock) return;

    const onConnect = async () => {
      loadMutedSessions();
      try {
        const stats = await NotificationsApi.getStats();
        setStats(stats);
        if (useNotificationStore.getState().isPanelOpen) {
          const page = await NotificationsApi.list();
          setInitialPage(page);
        }
      } catch {
        // non-fatal: user still gets live events
      }
    };

    const onNew = (payload: NotificationListItem) => {
      if (payload._id) {
        addNotification(payload);
      }
    };

    const onUpdated = (payload: NotificationListItem) => {
      if (payload._id) {
        upsertNotification(payload);
      }
    };

    const onForceLogout = () => {
      disconnectNotificationSocket();
      logoutAndRedirect();
    };

    sock.on('connect', onConnect);
    sock.on('newNotification', onNew);
    sock.on('notificationUpdated', onUpdated);
    sock.on('force_logout', onForceLogout);

    if (sock.connected) {
      void onConnect();
    }

    return () => {
      sock.off('connect', onConnect);
      sock.off('newNotification', onNew);
      sock.off('notificationUpdated', onUpdated);
      sock.off('force_logout', onForceLogout);
      disconnectNotificationSocket();
    };
  }, [accessToken, isAuthenticated, isHydrated, addNotification, upsertNotification]);

  // When the user returns to this tab, refresh counts (and panel list) after actions in another tab.
  useEffect(() => {
    if (!isHydrated || !isAuthenticated || !accessToken) return;

    const onVisible = () => {
      if (document.visibilityState === 'visible') {
        void syncNotificationsFromServer();
      }
    };

    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [accessToken, isAuthenticated, isHydrated]);
}

/** Thin wrapper so layout can mount the hook once. */
export function NotificationProvider(): null {
  useNotificationSocket();
  return null;
}
