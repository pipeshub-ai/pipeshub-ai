'use client';

import type { NotificationListItem } from './api';
import { useBrowserNotificationsStore } from './browser-notifications-store';
import { notificationHref } from './notification-link';

const NOTIFICATION_ICON = '/icon.svg';

export function isBrowserNotificationSupported(): boolean {
  return typeof window !== 'undefined' && 'Notification' in window;
}

export function getBrowserNotificationPermission(): NotificationPermission | 'unsupported' {
  if (!isBrowserNotificationSupported()) return 'unsupported';
  return Notification.permission;
}

export async function requestBrowserNotificationPermission(): Promise<NotificationPermission> {
  if (!isBrowserNotificationSupported()) return 'denied';
  return Notification.requestPermission();
}

/** True when desktop notifications may be shown for a live socket event. */
export function shouldShowDesktopNotification(): boolean {
  if (typeof document === 'undefined') return false;
  if (document.visibilityState !== 'hidden') return false;
  if (!isBrowserNotificationSupported()) return false;
  if (getBrowserNotificationPermission() !== 'granted') return false;
  return useBrowserNotificationsStore.getState().desktopEnabled;
}

function navigateFromNotification(href: string): void {
  if (/^https?:\/\//i.test(href)) {
    window.open(href, '_blank', 'noopener,noreferrer');
    return;
  }
  window.location.href = href;
}

/** Shows a native OS notification for an incoming item (tab must be hidden). */
export function showDesktopNotification(item: NotificationListItem): void {
  if (!shouldShowDesktopNotification()) return;
  if (item.isDeleted) return;
  if (item.status === 'archived') return;

  const title = item.title?.trim() || item.type?.trim() || 'PipesHub';
  const body = item.message?.trim() ?? '';
  if (!title && !body) return;

  try {
    const notification = new Notification(title, {
      body: body || undefined,
      tag: item._id,
      icon: NOTIFICATION_ICON,
    });

    notification.onclick = () => {
      window.focus();
      const href = notificationHref(item.redirectLink);
      if (href) navigateFromNotification(href);
      notification.close();
    };
  } catch {
    // Non-fatal: inbox still receives the event.
  }
}
