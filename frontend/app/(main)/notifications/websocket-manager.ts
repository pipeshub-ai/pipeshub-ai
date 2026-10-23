'use client';

import { useEffect } from 'react';
import {
  connectNotificationSocket,
  disconnectNotificationSocket,
  ensureNotificationSocketConnected,
} from '@/lib/socket/notification-socket';
import { logoutAndRedirect, useAuthStore, notificationExtension } from '@/config';
import { useNotificationStore } from './store';
import { NotificationsApi, type NotificationListItem } from './api';
import { isCatchUpPrimed, noteSeen, resetCatchUp, takeMissed } from './catch-up';

/**
 * On every (re)connect: the counts, the open panel's list, and the unread notices pushed while the socket was down,
 * which get what their push would have given them (`onMissedNotifications`). The first connect after a load or
 * sign-in hands the unread page to `onUnreadAtStart` instead: what came while no tab was open.
 */
async function catchUpOnConnect(): Promise<void> {
  const { isPanelOpen, setInitialPage, setStats, mergeMissed } = useNotificationStore.getState();
  const unread = await NotificationsApi.list({ status: 'unread' });
  const first = !isCatchUpPrimed();
  const missed = takeMissed(unread.notifications);
  if (missed.length > 0) mergeMissed(missed);
  const [stats, page] = await Promise.all([
    NotificationsApi.getStats(),
    isPanelOpen ? NotificationsApi.list() : Promise.resolve(null),
  ]);
  setStats(stats);
  if (page) setInitialPage(page);
  if (first) notificationExtension.onUnreadAtStart(unread.notifications);
  else if (missed.length > 0) notificationExtension.onMissedNotifications(missed);
}

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
  const addNotification = useNotificationStore((s) => s.addNotification);

  notificationExtension.useSocketEffects({ isHydrated, isAuthenticated, accessToken });

  useEffect(() => {
    if (!isHydrated || !isAuthenticated || !accessToken) {
      disconnectNotificationSocket();
      if (!isAuthenticated) resetCatchUp();
      return;
    }

    const sock = connectNotificationSocket(accessToken);
    if (!sock) return;

    const onConnect = async () => {
      notificationExtension.onSocketConnect();
      try {
        await catchUpOnConnect();
      } catch {
        // non-fatal: user still gets live events
      }
    };

    const onNew = (payload: NotificationListItem) => {
      if (payload._id) {
        noteSeen(payload);
        addNotification(payload);
      }
    };

    const onForceLogout = () => {
      disconnectNotificationSocket();
      logoutAndRedirect();
    };

    sock.on('connect', onConnect);
    sock.on('newNotification', onNew);
    sock.on('force_logout', onForceLogout);

    if (sock.connected) {
      void onConnect();
    }

    return () => {
      sock.off('connect', onConnect);
      sock.off('newNotification', onNew);
      sock.off('force_logout', onForceLogout);
      disconnectNotificationSocket();
    };
  }, [accessToken, isAuthenticated, isHydrated, addNotification]);

  // When the user returns to this tab, refresh counts (and panel list) after actions in another tab, and bring back
  // a socket that gave up while the tab slept or the network was gone; its reconnect catches up on what was missed.
  useEffect(() => {
    if (!isHydrated || !isAuthenticated || !accessToken) return;

    const onVisible = () => {
      if (document.visibilityState === 'visible') {
        ensureNotificationSocketConnected();
        void syncNotificationsFromServer();
      }
    };
    const onOnline = () => ensureNotificationSocketConnected();

    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('online', onOnline);
    return () => {
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('online', onOnline);
    };
  }, [accessToken, isAuthenticated, isHydrated]);
}

/** Thin wrapper so layout can mount the hook once. */
export function NotificationProvider(): null {
  useNotificationSocket();
  return null;
}
