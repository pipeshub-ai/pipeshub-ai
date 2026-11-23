/** The latest message must end this far (px) below the visible area before the button shows. */
export const JUMP_TO_NEWEST_THRESHOLD_PX = 120;

/**
 * Whether the newest message has been scrolled out of reach. Measured against the
 * end of the last message, not the scroll height: the list keeps blank spacer space
 * under it, so scroll distance alone would show the button on a short last message.
 */
export function shouldShowJumpToNewest(
  lastMessageBottom: number | null,
  viewportBottom: number,
  thresholdPx: number = JUMP_TO_NEWEST_THRESHOLD_PX,
): boolean {
  if (lastMessageBottom === null) return false;
  return lastMessageBottom - viewportBottom > thresholdPx;
}

/**
 * How far to scroll so the newest message's end, which just grew, stays above the composer.
 * Only when its end was in view before the growth: someone reading the top of a long answer
 * is left where they are.
 */
export function tailClearance(wasInView: boolean, tailBottom: number, visibleBottom: number): number {
  return wasInView ? Math.max(0, Math.ceil(tailBottom - visibleBottom)) : 0;
}
