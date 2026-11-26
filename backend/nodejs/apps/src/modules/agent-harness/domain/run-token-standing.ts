/**
 * What a run-scoped token (a gateway token, or the attempt token the budget routes also take) may still do on its
 * run, decided from Node's record of the run and the token's own binding, never from a request body (SEC-C-11,
 * ADR-GW-02). One rule for every `run-token` route.
 */
import { HarnessConflictError } from './errors';
import { HarnessRun, WORKER_OWNED_RUN_STATUSES } from './execution';

/**
 * - `act`: start something that spends or grants (a model grant, a budget reservation): the token's attempt holds
 *   the lease of the running run.
 * - `report`: say what happened or read the run's state (a gateway decision, a budget read): the token's attempt
 *   holds the lease, while the run runs or is being cancelled. Nothing journals after a run's terminal event
 *   (JRN-22), so an attempt that let the run go reports nothing more.
 * - `settle`: settle a reservation the attempt made while it held the lease: any attempt of this run, so spend that
 *   happened is never lost when a later claim took the run over.
 */
export type RunTokenUse = 'act' | 'report' | 'settle';

export interface RunTokenHolder {
  attemptId: string;
  fence: number;
}

/** Why a run-scoped token was refused; the reason is logged, the caller reads 409 `RUN_NOT_LEASED`. */
export type RunTokenRefusal = 'attempt_superseded' | 'run_not_leased';

/** Null when the token's attempt may do `use` on the run now. */
export function runTokenRefusal(
  run: HarnessRun,
  holder: RunTokenHolder,
  use: RunTokenUse,
  now: number,
): RunTokenRefusal | null {
  if (use === 'settle') return null;
  // Every claim raises the fence, so a token from an earlier fence is a superseded attempt's.
  if (holder.fence !== run.fence) return 'attempt_superseded';
  const holds =
    (use === 'act'
      ? run.status === 'running'
      : WORKER_OWNED_RUN_STATUSES.has(run.status)) &&
    run.attemptId === holder.attemptId &&
    run.leaseExpiresAt !== null &&
    run.leaseExpiresAt > now;
  return holds ? null : 'run_not_leased';
}

const REFUSED: Record<RunTokenRefusal, string> = {
  attempt_superseded:
    'A later attempt took the run over: this token is no longer its own',
  run_not_leased: 'The attempt no longer holds the run',
};

/** One answer for every refusal (409 `RUN_NOT_LEASED`): the gateway stops reporting for this attempt, whatever the cause. */
export const runTokenRefused = (
  refusal: RunTokenRefusal,
): HarnessConflictError =>
  new HarnessConflictError('RUN_NOT_LEASED', REFUSED[refusal]);
