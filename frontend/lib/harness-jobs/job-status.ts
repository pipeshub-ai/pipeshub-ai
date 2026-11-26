import type { RunStatus, SuspendReason } from '@/lib/contracts/harness/generated/core/enums';
import type { WorkerStatus } from './view-models';
import type { Words } from './words';

/**
 * The six status words (design copy register, "Sandbox jobs"), one meaning each, everywhere:
 *   Up next          not started: waiting for a free sandbox or for work it needs
 *   Working          going, including waiting on something that is not a person
 *   Needs you        parked on a person: an approval, a question, a sign-in
 *   Done             finished
 *   Couldn't finish  ended on something it could not get past
 *   Stopped          a person or an operator stopped it
 * Waiting looks alive (a spinner, never a tick), and silence is never a yes: an ask that expired is not Done.
 */
export type JobStatus = 'upNext' | 'working' | 'needsYou' | 'done' | 'couldntFinish' | 'stopped';

export const JOB_STATUSES: ReadonlyArray<JobStatus> = ['upNext', 'working', 'needsYou', 'done', 'couldntFinish', 'stopped'];

export function jobStatusWord(status: JobStatus, t: Words): string {
  return t(`ui.status.${status}`);
}

/** The suspend reasons that wait for a PERSON; the rest (a timer, a child, a capability) are still Working. */
const PERSON_REASONS: ReadonlySet<SuspendReason> = new Set<SuspendReason>(['approval', 'input']);

/**
 * A durable run's status (contract `RunStatus`, or the child view's subset), its suspend reason, and whether an ask
 * is open, as one word. An open ask wins over "running": the run is waiting on you even while its row says running.
 */
export function jobStatusOf(
  status: RunStatus | 'running' | 'completed' | 'failed' | 'cancelled' | string | null | undefined,
  options: { suspendReason?: SuspendReason | string | null; openAsk?: boolean } = {},
): JobStatus {
  switch (status) {
    case 'queued':
      return 'upNext';
    case 'completed':
      return 'done';
    case 'failed':
    case 'quarantined':
      return 'couldntFinish';
    case 'cancelled':
      return 'stopped';
    case 'suspended':
      return options.openAsk || PERSON_REASONS.has(options.suspendReason as SuspendReason) ? 'needsYou' : 'working';
    default:
      return options.openAsk ? 'needsYou' : 'working';
  }
}

/** The prototype's finer worker states, as the six words. */
export function jobStatusOfWorker(status: WorkerStatus): JobStatus {
  switch (status) {
    case 'queued':
      return 'upNext';
    case 'waiting':
      return 'needsYou';
    case 'done':
      return 'done';
    case 'failed':
      return 'couldntFinish';
    case 'stopped':
      return 'stopped';
    default:
      return 'working';
  }
}

/** Still going in any form: the job's clock runs and Stop is offered. */
export const isLive = (s: JobStatus): boolean => s === 'upNext' || s === 'working' || s === 'needsYou';
