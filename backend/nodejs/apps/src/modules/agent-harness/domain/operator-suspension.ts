/**
 * Which runs an operator's retire (ADR-OPS-01 D6) may suspend and resume: the
 * store holds every adapter to these, so a retire never pauses a run waiting
 * for a person or its children, and its resume never wakes another wait.
 */
import { HarnessConflictError } from './errors';
import type { HarnessRun, RunStatus } from './execution';
import { RETIRED } from './operator-controls';

/** A run an attempt may hold or is about to take: the ones a retire takes back. */
export const RETIRABLE_RUN_STATUSES: ReadonlySet<RunStatus> = new Set([
  'queued',
  'running',
]);

export function assertRetirable(run: Pick<HarnessRun, 'status'>): void {
  if (!RETIRABLE_RUN_STATUSES.has(run.status)) {
    throw new HarnessConflictError(
      'INVALID_TRANSITION',
      `A ${run.status} run is not suspended for a retired engine version`,
    );
  }
}

export function assertRetired(
  run: Pick<HarnessRun, 'status' | 'suspendedFor'>,
): void {
  if (run.status !== 'paused' || run.suspendedFor !== RETIRED) {
    throw new HarnessConflictError(
      'INVALID_TRANSITION',
      'Only a run suspended for a retired engine version resumes this way',
    );
  }
}
