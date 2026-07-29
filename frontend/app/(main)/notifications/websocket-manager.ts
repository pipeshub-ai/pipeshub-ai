'use client';

import { useEffect } from 'react';
import {
  connectNotificationSocket,
  disconnectNotificationSocket,
} from '@/lib/socket/notification-socket';
import { useAuthStore } from '@/lib/store/auth-store';
import { useNotificationStore } from './store';
import { NotificationsApi, type NotificationListItem } from './api';
import { showDesktopNotification } from './browser-notifications';

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

/** Subscribes to real-time notifications when authenticated. Mount once under the main app shell. */
export function useNotificationSocket(): void {
  const accessToken = useAuthStore((s) => s.accessToken);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const isHydrated = useAuthStore((s) => s.isHydrated);
  const setInitialPage = useNotificationStore((s) => s.setInitialPage);
  const setStats = useNotificationStore((s) => s.setStats);
  const addNotification = useNotificationStore((s) => s.addNotification);

  useEffect(() => {
    if (!isHydrated || !isAuthenticated || !accessToken) {
      disconnectNotificationSocket();
      return;
    }

    const sock = connectNotificationSocket(accessToken);
    if (!sock) return;

    const onConnect = async () => {
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
        showDesktopNotification(payload);
      }
    };

    sock.on('connect', onConnect);
    sock.on('newNotification', onNew);

    if (sock.connected) {
      void onConnect();
    }

    return () => {
      sock.off('connect', onConnect);
      sock.off('newNotification', onNew);
      disconnectNotificationSocket();
    };
  }, [accessToken, isAuthenticated, isHydrated, addNotification]);

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
