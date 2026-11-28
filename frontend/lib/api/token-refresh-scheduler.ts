/**
 * Proactive token-refresh scheduler.
 *
 * Drives the primary refresh path: a `setTimeout` that fires
 * `DEFAULT_EXPIRY_BUFFER_SECONDS` (90 s) before the access token's `exp`
 * claim. The axios request/response interceptors in `axios-instance.ts`
 * remain as a safety net, sharing the same in-memory lock from
 * `token-refresh.ts` so duplicate network calls cannot occur.
 *
 * Lifecycle wired up in `initTokenRefreshScheduler()`:
 *
 * 1. Zustand subscription on `useAuthStore` — every time `accessToken`
 *    changes (login / setTokens / logout, including refreshes initiated
 *    here) the timer is recomputed from the new token.
 * 2. `storage` event on the signed-in mark — another tab signed out (this
 *    one follows), signed in as someone else (this one reloads), or signed
 *    in while this one was signed out (this one restores from the session).
 * 3. `visibilitychange` — when the tab returns to foreground (e.g. after
 *    laptop sleep, where setTimeout firing is unreliable) the scheduler
 *    re-checks expiry and refreshes immediately if needed, or at once while
 *    the session could not be checked (the Reconnecting… notice shows).
 */

import { restoreSession } from '@/lib/store/auth-store';
import { useAuthStore } from '@/config';
import { accountOfToken, SESSION_HINT_KEY } from '@/lib/auth/session-credential';
import {
  refreshOrSignOut,
  isTokenExpired,
  getTokenExpirySeconds,
  DEFAULT_EXPIRY_BUFFER_SECONDS,
} from './token-refresh';
import { createCooldownRetrier, isSessionReconnecting, setSessionReconnecting } from '@/lib/api/refresh-retry';
// Registers the Reconnecting… notice the refresh retries show.
import '@/lib/api/session-notice';
/**
 * `setTimeout` accepts a 32-bit signed delay; values larger than this wrap
 * to a near-zero delay and would fire immediately. We clamp and chain.
 */
const MAX_TIMEOUT_MS = 2_000_000_000;

const BUFFER_MS = DEFAULT_EXPIRY_BUFFER_SECONDS * 1_000;

let initialized = false;
let timerId: ReturnType<typeof setTimeout> | null = null;
let scheduledForToken: string | null = null;

function clearTimer(): void {
  if (timerId !== null) {
    clearTimeout(timerId);
    timerId = null;
  }
  scheduledForToken = null;
}

// After a refresh cycle gives up (server temporarily unable to check the
// session), try again later a bounded number of times. Not a sign-out.
const cooldownRetry = createCooldownRetrier(() => {
  void refreshNow();
});

async function refreshNow(): Promise<void> {
  // Cancel before awaiting: a pending timer (this refresh came from the tab returning) would fire a
  // second refresh. The post-success setTokens will re-arm the timer via the zustand subscription.
  clearTimer();

  // A refusal logs the user out, unless the session already ended meanwhile; then the timer may
  // already belong to the next person, so it is left alone.
  const outcome = await refreshOrSignOut();
  if (outcome === 'unreachable') {
    // No answer for the whole retry cycle. Stay signed in; the "Reconnecting…" notice is
    // already showing. Try again later, a bounded number of times; the next request's
    // pre-send check, the tab becoming visible or the browser going online also try again.
    cooldownRetry.arm();
    return;
  }
  cooldownRetry.reset();
  if (outcome === 'signed-out') {
    // Hard stop: clear any state that may have been re-armed concurrently by an inbound `storage` event.
    clearTimer();
  }
}

/**
 * Every path that installs a token goes through the store (this timer, a request or a stream that
 * refreshed, a new sign-in). A token that does not need refreshing yet means the session was
 * checked: the Reconnecting… notice goes and the next outage gets the full retry schedule.
 */
function noteTokenInstalled(token: string | null): void {
  if (!token || isTokenExpired(token)) return;
  cooldownRetry.reset();
  setSessionReconnecting(false);
}

/**
 * Compute the next refresh delay for `token` and arm a timer. Pass `null`
 * (e.g. on logout) to cancel any pending timer.
 */
export function scheduleFromToken(token: string | null): void {
  // No-op if the same token is already scheduled — prevents spurious
  // resets when the auth-store fires a no-change update.
  if (token === scheduledForToken && timerId !== null) {
    return;
  }

  clearTimer();

  if (!token) {
    // Signed out: nothing left to reconnect, and the notice must not outlive the session.
    cooldownRetry.reset();
    setSessionReconnecting(false);
    return;
  }

  const exp = getTokenExpirySeconds(token);
  if (exp === null) {
    // Token has no `exp` claim; the request/response interceptors in
    // axios-instance will still react to 401s. Nothing to schedule.
    return;
  }

  const delayMs = exp * 1_000 - Date.now() - BUFFER_MS;

  if (delayMs <= 0) {
    void refreshNow();
    return;
  }

  scheduledForToken = token;
  // Clamp to setTimeout's safe range; if the real delay is longer, the
  // timer fires once at MAX_TIMEOUT_MS and re-enters scheduleFromToken,
  // which recomputes from the still-current token.
  const armedDelay = Math.min(delayMs, MAX_TIMEOUT_MS);
  timerId = setTimeout(() => {
    timerId = null;
    if (armedDelay < delayMs) {
      // Long-lived token path: re-arm with the remaining time.
      scheduleFromToken(useAuthStore.getState().accessToken);
    } else {
      void refreshNow();
    }
  }, armedDelay);
}

/** Public alias matching the plan's contract. Cancels any pending timer. */
export function cancel(): void {
  clearTimer();
  cooldownRetry.reset();
}

function handleStorageEvent(event: StorageEvent): void {
  if (event.key !== SESSION_HINT_KEY && event.key !== null) return;
  if (event.oldValue === event.newValue) return;
  const current = useAuthStore.getState().accessToken;

  // Another tab signed out (or cleared the site's storage) → mirror that here.
  if (event.newValue === null) {
    if (current) useAuthStore.getState().logout();
    return;
  }

  // Another tab signed in while this one was signed out → sign in here too.
  if (!current) {
    void restoreSession();
    return;
  }

  // Another tab signed in as someone else without this one signing out. Every
  // cache, store and the open chat here belong to the previous account, and a
  // page load is the one reset that leaves none of it on screen. Web only in
  // practice: the desktop app has a single window, so no other tab writes.
  const before = accountOfToken(current);
  if (before && before !== event.newValue) window.location.assign('/chat/');
}

function handleOnline(): void {
  const token = useAuthStore.getState().accessToken;
  if (!token) return;
  // Back on the network: a refresh that was waiting on it can go now.
  cooldownRetry.reset();
  if (isTokenExpired(token)) void refreshNow();
}

function handleVisibilityChange(): void {
  if (typeof document === 'undefined' || document.visibilityState !== 'visible') {
    return;
  }
  const token = useAuthStore.getState().accessToken;
  if (!token) return;
  // Back in front while the session could not be checked: try now rather than wait out the cooldown.
  if (isTokenExpired(token) || isSessionReconnecting()) {
    // Back in front of the person: a refresh that was waiting out the outage can go now.
    cooldownRetry.reset();
    void refreshNow();
  } else {
    // Re-arm in case the timer was throttled or never fired during sleep.
    scheduleFromToken(token);
  }
}

/**
 * Wire up the scheduler. Idempotent — safe to call from any client-side
 * mount point (e.g. `<AuthHydrator />`).
 */
export function initTokenRefreshScheduler(): void {
  if (initialized) return;
  if (typeof window === 'undefined') return;
  initialized = true;

  useAuthStore.subscribe((state, previous) => {
    if (state.accessToken !== previous.accessToken) noteTokenInstalled(state.accessToken);
    scheduleFromToken(state.accessToken);
  });

  window.addEventListener('storage', handleStorageEvent);
  document.addEventListener('visibilitychange', handleVisibilityChange);
  window.addEventListener('online', handleOnline);

  // Arm for whatever token the store has right now (post-hydration).
  scheduleFromToken(useAuthStore.getState().accessToken);
}
