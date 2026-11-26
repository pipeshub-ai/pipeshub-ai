/**
 * WP-100 (ADR-RUN-01 D1): which revision of the durable stream's frames a client reads. Version 1 is the legacy
 * stream the D-5 and LAT2 goldens pin, byte for byte; version 2 adds what a client that words a run itself reads:
 * the root `RUN_ERROR`'s typed `runOutcome` and the run's spend as the ledger moves (`run_cost`). A client asks with the
 * `X-PipesHub-Frames` request header; a client that does not ask, and every surface Node streams for (Slack, MCP,
 * a shared viewer), keeps version 1.
 */
export const FRAMES_HEADER = 'x-pipeshub-frames';

export const LEGACY_FRAMES = 1;
export const OUTCOME_FRAMES = 2;

export type FrameVersion = typeof LEGACY_FRAMES | typeof OUTCOME_FRAMES;

/** The version a header value names: a newer one than this Node knows reads as the newest it serves. */
export function frameVersion(value: unknown): FrameVersion {
  const sent: unknown = Array.isArray(value) ? value[0] : value;
  if (typeof sent !== 'string' || !/^\s*\d{1,3}\s*$/.test(sent))
    return LEGACY_FRAMES;
  return Number.parseInt(sent, 10) >= OUTCOME_FRAMES
    ? OUTCOME_FRAMES
    : LEGACY_FRAMES;
}

/** The version a request asks for, as a field to spread; none for the legacy frames. */
export function framesOf(req: { headers?: Record<string, unknown> }): {
  frames?: FrameVersion;
} {
  const version = frameVersion(req.headers?.[FRAMES_HEADER]);
  return version === LEGACY_FRAMES ? {} : { frames: version };
}

/** The frames carry the run's typed outcome on its failure and push its spend. */
export const servesOutcomeFrames = (
  version: FrameVersion | undefined,
): boolean => (version ?? LEGACY_FRAMES) >= OUTCOME_FRAMES;
