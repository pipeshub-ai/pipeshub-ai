/** Rows land this far apart; past the cap the rest arrive together. */
export const ROW_REVEAL_STEP_MS = 35;
export const ROW_REVEAL_MAX_STEPS = 14;
/** Matches `.kb-row-in` in globals.css. */
export const ROW_REVEAL_DURATION_MS = 280;

export function rowRevealDelay(index: number): string {
  return `${Math.min(index, ROW_REVEAL_MAX_STEPS) * ROW_REVEAL_STEP_MS}ms`;
}

/** When the last of `count` rows has finished landing. */
export function rowRevealEndMs(count: number): number {
  return Math.min(Math.max(count - 1, 0), ROW_REVEAL_MAX_STEPS) * ROW_REVEAL_STEP_MS + ROW_REVEAL_DURATION_MS;
}
