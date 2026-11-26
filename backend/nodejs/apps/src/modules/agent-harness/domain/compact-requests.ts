import type { RunCompact, RunInputSnapshot } from './execution';

/** The longest focus `/compact <focus>` keeps; the composer cuts there too. */
export const COMPACT_FOCUS_MAX_CHARS = 500;

/**
 * The person's `/compact [focus]` (SLASH2 C2), waiting on the session until
 * its next run is admitted. That run carries it in its input and compacts
 * before its first turn; the request is gone from the session once a run took
 * it. A later request replaces an earlier one (the last focus wins).
 */
export interface CompactRequest extends RunCompact {
  requestedAt: number;
  /** Principal ref of who asked (the session's owner). */
  requestedBy: string;
}

export interface CompactRequests {
  /**
   * Saves the session's request; false when no harness run was ever admitted
   * in the session, so there is no context to compact.
   */
  requestCompact(
    orgId: string,
    sessionId: string,
    request: CompactRequest,
  ): Promise<boolean>;
}

/** A stored request as the next run's input carries it: the focus only. */
export function compactInput(stored: unknown): RunCompact | undefined {
  if (typeof stored !== 'object' || stored === null) return undefined;
  const focus = (stored as { focus?: unknown }).focus;
  return typeof focus === 'string' && focus.length > 0
    ? { focus: focus.slice(0, COMPACT_FOCUS_MAX_CHARS) }
    : {};
}

/** `input` with the request a run of harness major 2 takes; any other run leaves it waiting. */
export function withCompact(
  input: RunInputSnapshot,
  harnessMajor: number,
  stored: unknown,
): RunInputSnapshot {
  const compact = harnessMajor === 2 ? compactInput(stored) : undefined;
  return compact === undefined ? input : { ...input, compact };
}
