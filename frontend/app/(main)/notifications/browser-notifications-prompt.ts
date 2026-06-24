'use client';

import { useEffect, useRef } from 'react';
import type { TFunction } from 'i18next';
import { useTranslation } from 'react-i18next';
import { toast } from '@/lib/store/toast-store';
import {
  getBrowserNotificationPermission,
  isBrowserNotificationSupported,
  requestBrowserNotificationPermission,
} from './browser-notifications';
import {
  shouldShowDesktopNotificationPrompt,
  useBrowserNotificationsStore,
} from './browser-notifications-store';

const PROFILE_NOTIFICATIONS_PATH = '/workspace/profile/#notifications';
const FOLLOW_UP_DISMISS_MS = 10000;

function scheduleDismiss(toastId: string, ms: number): void {
  window.setTimeout(() => {
    toast.dismiss(toastId);
  }, ms);
}

function showSettingsHintToast(toastId: string, t: TFunction): void {
  toast.update(toastId, {
    variant: 'info',
    title: t('notifications.desktop.promptAckTitle'),
    description: t('notifications.desktop.settingsHint'),
    actions: undefined,
    action: {
      label: t('notifications.desktop.openSettings'),
      icon: 'settings',
      variant: 'secondary',
      onClick: () => {
        window.location.href = PROFILE_NOTIFICATIONS_PATH;
      },
    },
    showCloseButton: true,
    duration: FOLLOW_UP_DISMISS_MS,
  });
  scheduleDismiss(toastId, FOLLOW_UP_DISMISS_MS);
}

function showDesktopNotificationPromptToast(t: TFunction): void {
  const toastId = toast.info(t('notifications.desktop.promptTitle'), {
    description: t('notifications.desktop.promptDescription'),
    icon: 'notifications',
    showCloseButton: true,
    duration: null,
    actions: [
      {
        label: t('notifications.desktop.allow'),
        variant: 'primary',
        onClick: () => {
          void (async () => {
            const permission = await requestBrowserNotificationPermission();
            if (permission === 'granted') {
              useBrowserNotificationsStore.getState().setDesktopEnabled(true);
              toast.update(toastId, {
                variant: 'success',
                title: t('notifications.desktop.enabledTitle'),
                description: undefined,
                actions: undefined,
                action: undefined,
                showCloseButton: true,
                duration: 3000,
              });
              scheduleDismiss(toastId, 3000);
              return;
            }

            toast.update(toastId, {
              variant: 'info',
              title: t('notifications.desktop.blockedTitle'),
              description: t('notifications.desktop.blockedDescription'),
              actions: undefined,
              action: undefined,
              showCloseButton: true,
              duration: FOLLOW_UP_DISMISS_MS,
            });
            scheduleDismiss(toastId, FOLLOW_UP_DISMISS_MS);
          })();
        },
      },
      {
        label: t('notifications.desktop.dismiss'),
        variant: 'secondary',
        onClick: () => {
          useBrowserNotificationsStore.getState().dismissPrompt();
          showSettingsHintToast(toastId, t);
        },
      },
      {
        label: t('notifications.desktop.askLater'),
        variant: 'secondary',
        onClick: () => {
          useBrowserNotificationsStore.getState().snoozePrompt();
          showSettingsHintToast(toastId, t);
        },
      },
    ],
  });
}

/** Shows the desktop-notification opt-in toast when the inbox opens with unread items. */
export function useDesktopNotificationPrompt(
  isPanelOpen: boolean,
  unreadCount: number,
): void {
  const { t } = useTranslation();
  const promptedThisOpenRef = useRef(false);

  useEffect(() => {
    if (!isPanelOpen) {
      promptedThisOpenRef.current = false;
      return;
    }
    if (unreadCount === 0) return;
    if (promptedThisOpenRef.current) return;
    if (!isBrowserNotificationSupported()) return;
    if (getBrowserNotificationPermission() !== 'default') return;
    if (!shouldShowDesktopNotificationPrompt()) return;

    promptedThisOpenRef.current = true;
    showDesktopNotificationPromptToast(t);
  }, [isPanelOpen, unreadCount, t]);
}
