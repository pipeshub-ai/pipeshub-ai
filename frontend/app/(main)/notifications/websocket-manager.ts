'use client';

import { useEffect } from 'react';
import {
  connectNotificationSocket,
  disconnectNotificationSocket,
} from '@/lib/socket/notification-socket';
import { useAuthStore } from '@/lib/store/auth-store';
import { useNotificationStore } from './store';
import { NotificationsApi } from './api';
import type { NotificationListItem } from './api';

export function normalizeNotificationPayload(raw: Record<string, unknown>): NotificationListItem {
  const id = (raw._id ?? raw.id) as string | undefined;
  const assigned = raw.assignedTo as { toString?: () => string } | string | undefined;
  const assignedStr =
    typeof assigned === 'object' && assigned && 'toString' in assigned
      ? assigned.toString?.()
      : String(assigned ?? '');
  return {
    _id: id ? String(id) : '',
    title: String(raw.title ?? ''),
    type: String(raw.type ?? ''),
    link: String(raw.link ?? '/connectors'),
    status: String(raw.status ?? 'Unread'),
    orgId: raw.orgId ? String(raw.orgId) : undefined,
    assignedTo: assignedStr,
    appName: raw.appName ? String(raw.appName) : undefined,
    appId: raw.appId ? String(raw.appId) : undefined,
    payload: (raw.payload as Record<string, unknown>) ?? undefined,
    createdAt: raw.createdAt ? String(raw.createdAt) : undefined,
    updatedAt: raw.updatedAt ? String(raw.updatedAt) : undefined,
  };
}

/** Subscribes to real-time notifications when authenticated. Mount once under the main app shell. */
export function useNotificationSocket(): void {
  const accessToken = useAuthStore((s) => s.accessToken);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const isHydrated = useAuthStore((s) => s.isHydrated);
  const setAll = useNotificationStore((s) => s.setAll);
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
        const list = await NotificationsApi.getAll();
        setAll(list);
      } catch {
        // non-fatal: user still gets live events
      }
    };

    const onNew = (payload: Record<string, unknown>) => {
      const item = normalizeNotificationPayload(payload);
      if (item._id) {
        addNotification(item);
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
  }, [accessToken, isAuthenticated, isHydrated, setAll, addNotification]);
}

/** Thin wrapper so layout can mount the hook once. */
export function NotificationProvider(): null {
  useNotificationSocket();
  return null;
}
