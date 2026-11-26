import { performance } from 'perf_hooks';
import { HarnessRun } from '../domain/execution';

/**
 * Harness log lines (ground rule 9): an event name as the message, a reason
 * code where one applies, and whichever correlation fields apply. Values are
 * ids, sizes, counts, digests and reason codes; never content, tokens or keys.
 */
export interface HarnessLogger {
  debug(message: string, meta?: Record<string, unknown>): void;
  info(message: string, meta?: Record<string, unknown>): void;
  warn(message: string, meta?: Record<string, unknown>): void;
  error(message: string, meta?: Record<string, unknown>): void;
}

export const silentHarnessLogger: HarnessLogger = {
  debug: () => undefined,
  info: () => undefined,
  warn: () => undefined,
  error: () => undefined,
};

export interface HarnessLogFields {
  orgId?: string;
  sessionId?: string;
  runId?: string;
  attemptId?: string | null;
  fence?: number;
  seq?: number | null;
  commandId?: string;
  invocationId?: string;
  requestId?: string;
}

type LogValue = string | number | boolean | null | undefined | string[];

/** The meta object for one line: the event, then only the fields that are set. */
export function harnessLogMeta(
  event: string,
  fields: HarnessLogFields & Record<string, LogValue>,
): Record<string, unknown> {
  const meta: Record<string, unknown> = { event };
  for (const [key, value] of Object.entries(fields)) {
    if (value !== undefined && value !== null) meta[key] = value;
  }
  return meta;
}

/** Correlation fields of a run as the store holds it. */
export const runLogFields = (
  run: Pick<HarnessRun, 'orgId' | 'runId' | 'attemptId' | 'fence'> &
    Partial<Pick<HarnessRun, 'sessionId'>>,
): HarnessLogFields => ({
  orgId: run.orgId,
  sessionId: run.sessionId,
  runId: run.runId,
  attemptId: run.attemptId,
  fence: run.fence,
});

/** Milliseconds since a `performance.now()` reading, for the timing fields of a line. */
export const elapsedMs = (since: number): number =>
  Math.round(performance.now() - since);
