'use client';

import { useEffect } from 'react';
import {
  connectNotificationSocket,
  disconnectNotificationSocket,
} from '@/lib/socket/notification-socket';
import { useAuthStore } from '@/lib/store/auth-store';
import { useNotificationStore } from './store';
import { NotificationsApi, type NotificationListItem } from './api';

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
  }, [accessToken, isAuthenticated, isHydrated, setInitialPage, setStats, addNotification]);
}

/** Thin wrapper so layout can mount the hook once. */
export function NotificationProvider(): null {
  useNotificationSocket();
  return null;
}
