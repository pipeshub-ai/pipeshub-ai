/**
 * After a token refresh that got no answer (5xx, 429, network), the session may still be good: the
 * person stays signed in, one "Reconnecting…" notice shows, and the refresh is tried again in the
 * background with capped exponential backoff and jitter until the server answers. A refusal still
 * signs out (`refreshOrSignOut`), and any answer ends the notice.
 */
// The i18next instance the app configures (lib/i18n); imported bare so the refresh modules do not load React bindings.
import i18n from 'i18next';
import { useToastStore } from '@/lib/store/toast-store';

/** First wait before trying again. */
export const RECONNECT_BASE_DELAY_MS = 2_000;
/** Longest wait between two tries. */
export const RECONNECT_MAX_DELAY_MS = 60_000;

/** The wait before try number `attempt` (0-based): doubling, capped, 50-100 % of it so tabs do not ask at once. */
export function reconnectDelayMs(attempt: number, random: () => number = Math.random): number {
  const ceiling = Math.min(RECONNECT_BASE_DELAY_MS * 2 ** attempt, RECONNECT_MAX_DELAY_MS);
  return Math.round(ceiling * (0.5 + random() * 0.5));
}

let attempts = 0;
let timer: ReturnType<typeof setTimeout> | null = null;
let noticeId: string | null = null;

/** True while a refresh got no answer and the next try is pending. */
export function isReconnecting(): boolean {
  return noticeId !== null;
}

/** The refresh got no answer: show the notice and run `retry` after the next backoff delay (one pending try at most). */
export function reconnectLater(retry: () => void): void {
  const store = useToastStore.getState();
  if (noticeId === null || !store.toasts.some((t) => t.id === noticeId && !t.isExiting)) {
    noticeId = store.addToast({
      variant: 'loading',
      title: i18n.t('errorToast.reconnecting'),
      description: i18n.t('errorToast.reconnectingDescription'),
      duration: null,
    });
  }
  if (timer !== null) return;
  timer = setTimeout(() => {
    timer = null;
    retry();
  }, reconnectDelayMs(attempts));
  attempts += 1;
}

/** The server answered (a new token or a refusal), or the session ended: no more tries, no notice. */
export function stopReconnecting(): void {
  if (timer !== null) clearTimeout(timer);
  timer = null;
  attempts = 0;
  if (noticeId !== null) useToastStore.getState().removeToast(noticeId);
  noticeId = null;
}
