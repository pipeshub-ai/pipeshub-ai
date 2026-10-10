export const POLL_ACTIVE_MS = 4_000;
export const POLL_IDLE_MS = 15_000;
export const POLL_MAX_MS = 60_000;
/** No activity for this long counts as idle; the delay ramps up to `POLL_IDLE_MS` over the next 4 minutes. */
export const ACTIVE_WINDOW_MS = 60_000;
export const IDLE_RAMP_MS = 240_000;

export interface PollDelayInput {
  visible: boolean;
  /** Milliseconds since the last message or run in the conversation. */
  idleMs: number;
  /** Consecutive failed polls. */
  failures: number;
  /**
   * False for a visible window the user is not working in. Several windows share one per-user server
   * budget, so only the focused one polls fast; the rest use the idle cadence. Default true.
   */
  focused?: boolean;
}

/** Milliseconds until the next poll, or `null` while the tab is hidden (the caller waits for it to show). */
export function nextPollDelayMs({ visible, idleMs, failures, focused = true }: PollDelayInput): number | null {
  if (!visible) return null;
  const idle = Math.max(0, idleMs);
  const ramp = Math.min(Math.max(idle - ACTIVE_WINDOW_MS, 0) / IDLE_RAMP_MS, 1);
  const ramped = POLL_ACTIVE_MS + (POLL_IDLE_MS - POLL_ACTIVE_MS) * ramp;
  const base = focused ? ramped : Math.max(ramped, POLL_IDLE_MS);
  if (failures <= 0) return Math.round(base);
  return Math.min(Math.round(base * 2 ** failures), POLL_MAX_MS);
}

/** Spreads `ms` by +-`ratio` so several clients do not poll in lockstep. `random` is injectable for tests. */
export function withJitter(ms: number, random: () => number = Math.random, ratio = 0.2): number {
  return Math.max(0, Math.round(ms * (1 + (random() * 2 - 1) * ratio)));
}

/** A 429's `retryAfter` (seconds) is a floor under the computed delay. */
export function applyRetryAfter(delayMs: number, retryAfterSeconds: number | undefined): number {
  if (retryAfterSeconds === undefined || retryAfterSeconds <= 0) return delayMs;
  return Math.max(delayMs, retryAfterSeconds * 1000);
}
