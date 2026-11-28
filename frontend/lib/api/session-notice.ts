/**
 * The words and the one persistent notice for "the server can't check your
 * session right now" (see refresh-retry.ts). Kept apart from the retry policy so
 * that only the API client, the streaming client and the refresh scheduler load
 * the translations, not every page that decodes a token.
 */

import { i18n } from '@/lib/i18n';
import { useToastStore } from '@/lib/store/toast-store';
import { registerReconnectingNotice } from './refresh-retry';

/** What a request sees while the session cannot be checked. Not an auth error: nobody is signed out. */
export function sessionUnavailableMessage(): string {
  return i18n.t('errorToast.sessionUnavailable');
}

let reconnectingToastId: string | null = null;

function setReconnecting(reconnecting: boolean): void {
  const store = useToastStore.getState();
  const stillShown =
    reconnectingToastId !== null && store.toasts.some((t) => t.id === reconnectingToastId && !t.isExiting);
  if (reconnecting) {
    if (stillShown) return;
    reconnectingToastId = store.addToast({
      variant: 'loading',
      title: i18n.t('errorToast.reconnecting'),
      description: i18n.t('errorToast.reconnectingDescription'),
      duration: null,
    });
  } else {
    if (reconnectingToastId !== null) store.removeToast(reconnectingToastId);
    reconnectingToastId = null;
  }
}

registerReconnectingNotice(setReconnecting);
