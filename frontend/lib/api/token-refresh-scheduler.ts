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
 * 2. `storage` event — when another tab writes a new access token to
 *    `localStorage`, this tab adopts it (no extra refresh call) and
 *    reschedules.
 * 3. `visibilitychange` — when the tab returns to foreground (e.g. after
 *    laptop sleep, where setTimeout firing is unreliable) the scheduler
 *    re-checks expiry and refreshes immediately if needed.
 */

import {
  useAuthStore,
  ACCESS_TOKEN_STORAGE_KEY,
  REFRESH_TOKEN_STORAGE_KEY,
  logoutAndRedirect,
} from '@/lib/store/auth-store';
import {
  refreshAccessToken,
  isTokenExpired,
  getTokenExpirySeconds,
  DEFAULT_EXPIRY_BUFFER_SECONDS,
} from './token-refresh';

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

async function refreshNow(): Promise<void> {
  // Clear before awaiting so a second timer cannot stack on top of this one.
  // The post-success setTokens will re-arm the timer via the zustand subscription.
  timerId = null;
  scheduledForToken = null;

  const ok = await refreshAccessToken();
  if (!ok) {
    // Hard stop: clear any state that may have been re-armed concurrently
    // by an inbound `storage` event, then log the user out.
    clearTimer();
    logoutAndRedirect();
  }
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

  if (!token) return;

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
}

function handleStorageEvent(event: StorageEvent): void {
  if (event.key !== ACCESS_TOKEN_STORAGE_KEY) return;

  const current = useAuthStore.getState().accessToken;
  // Same value as we already hold → skip; re-applying it would write
  // localStorage again and fan out a redundant `storage` event in N tabs.
  if (event.newValue === current) return;

  // Another tab logged out → mirror that here.
  if (event.newValue === null) {
    useAuthStore.getState().logout();
    return;
  }

  // Another tab refreshed the access token → adopt it locally so we don't
  // re-issue a refresh of our own. The refresh-token key may also flip in
  // the same write batch; pick up whichever one we have.
  const refreshToken =
    window.localStorage.getItem(REFRESH_TOKEN_STORAGE_KEY) ??
    useAuthStore.getState().refreshToken;
  if (refreshToken) {
    useAuthStore.getState().setTokens(event.newValue, refreshToken);
  } else {
    useAuthStore.getState().setAccessToken(event.newValue);
  }
  // The zustand subscription will then call `scheduleFromToken`.
}

function handleVisibilityChange(): void {
  if (typeof document === 'undefined' || document.visibilityState !== 'visible') {
    return;
  }
  const token = useAuthStore.getState().accessToken;
  if (!token) return;
  if (isTokenExpired(token)) {
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

  useAuthStore.subscribe((state) => {
    scheduleFromToken(state.accessToken);
  });

  window.addEventListener('storage', handleStorageEvent);
  document.addEventListener('visibilitychange', handleVisibilityChange);

  // Arm for whatever token the store has right now (post-hydration).
  scheduleFromToken(useAuthStore.getState().accessToken);
}
