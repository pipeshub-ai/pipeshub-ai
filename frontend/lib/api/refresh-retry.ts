/**
 * Retry policy for the token-refresh call.
 *
 * Only what is known to be temporary is retried: a network error, a 5xx, a 408
 * and a 429. Every other answer goes back to the caller unchanged, so the
 * existing sign-out logic still runs (a 401 or 403, and the 400 and 404 the
 * server answers when an account is locked or has no credentials, all end the
 * session as they always did).
 *
 * Pure policy: no store, i18n or network imports, so pages that only decode a
 * token (sign-up, SSO) do not load the translations through it. The
 * "Reconnecting…" notice lives in `session-notice.ts` and registers itself here.
 */

/** First wait between attempts. */
export const REFRESH_BASE_DELAY_MS = 1_000;
/** Longest single wait between attempts. */
export const REFRESH_MAX_DELAY_MS = 15_000;
/** Longest one attempt may take; slower counts as a network failure. */
export const REFRESH_ATTEMPT_TIMEOUT_MS = 10_000;
/**
 * Longest wait for the body of a yes. Past it the cycle settles as no answer and releases the refresh lock; the body is
 * still read, and a replacement refresh token in it is kept.
 */
export const REFRESH_BODY_TIMEOUT_MS = 30_000;
/** Total time one refresh cycle keeps trying, attempt time included, before it stops and shows the reconnecting notice. */
export const REFRESH_MAX_TOTAL_WAIT_MS = 45_000;
/** Pause between refresh cycles the scheduler starts on its own while the notice is showing. */
export const REFRESH_COOLDOWN_MS = 60_000;
/** Cycles the scheduler starts on its own before it waits for activity (visibility, online, a request). */
export const REFRESH_MAX_COOLDOWN_CYCLES = 5;
/** Longest `Retry-After` honoured. One bad header must not lock refresh out for hours. */
export const REFRESH_RETRY_AFTER_CAP_MS = 60_000;

/** The allowlist of temporary answers: 5xx, request timeout and rate limiting. Everything else is final. */
export function isRetryableRefreshStatus(status: number): boolean {
  return status >= 500 || status === 408 || status === 429;
}

/** `Retry-After` as milliseconds: delta-seconds or an HTTP date. `null` when absent or unreadable. */
export function parseRetryAfterMs(value: string | null | undefined, now: number = Date.now()): number | null {
  if (!value) return null;
  const trimmed = value.trim();
  if (/^\d+$/.test(trimmed)) return Number(trimmed) * 1_000;
  const at = Date.parse(trimmed);
  return Number.isNaN(at) ? null : Math.max(0, at - now);
}

/** Capped exponential backoff for retry number `attempt` (0-based), with 50-100 % jitter. */
export function backoffDelayMs(attempt: number, random: () => number = Math.random): number {
  const ceiling = Math.min(REFRESH_BASE_DELAY_MS * 2 ** Math.min(attempt, 30), REFRESH_MAX_DELAY_MS);
  return Math.round(ceiling * (0.5 + random() * 0.5));
}

/** The wait before the next attempt: the backoff, or the server's `Retry-After` (capped) when that is longer. */
export function nextRetryDelayMs(attempt: number, retryAfterMs: number | null | undefined): number {
  return Math.max(backoffDelayMs(attempt), Math.min(retryAfterMs ?? 0, REFRESH_RETRY_AFTER_CAP_MS));
}

// ── "Reconnecting…" notice ───────────────────────────────────────────────

type ReconnectingListener = (reconnecting: boolean) => void;
let reconnectingListener: ReconnectingListener | null = null;

/** The notice (`session-notice.ts`) registers here; without one (a page that never shows it) the calls do nothing. */
export function registerReconnectingNotice(listener: ReconnectingListener): void {
  reconnectingListener = listener;
}

let reconnectingNow = false;

/** Show (or clear) the one non-destructive notice that the session cannot be checked right now. */
export function setSessionReconnecting(reconnecting: boolean): void {
  reconnectingNow = reconnecting;
  reconnectingListener?.(reconnecting);
}

/** True while the session could not be checked and the notice says so. */
export function isSessionReconnecting(): boolean {
  return reconnectingNow;
}

/**
 * After a cycle gives up, a scheduler starts another one every
 * `REFRESH_COOLDOWN_MS`, at most `REFRESH_MAX_COOLDOWN_CYCLES` times. Then it
 * waits for activity instead of looping.
 */
export function createCooldownRetrier(run: () => void) {
  let timer: ReturnType<typeof setTimeout> | null = null;
  let cycles = 0;
  return {
    arm(): void {
      if (timer !== null || cycles >= REFRESH_MAX_COOLDOWN_CYCLES) return;
      cycles += 1;
      timer = setTimeout(() => {
        timer = null;
        run();
      }, REFRESH_COOLDOWN_MS);
    },
    cancel(): void {
      if (timer !== null) clearTimeout(timer);
      timer = null;
    },
    /** A refresh worked or activity resumed: forget past cycles. */
    reset(): void {
      this.cancel();
      cycles = 0;
    },
  };
}
