import { forEnumeratedOrg } from '../../../libs/context/orgContext';
import { HarnessConflictError } from '../domain/errors';
import {
  childOf,
  HarnessRun,
  TERMINAL_RUN_STATUSES,
} from '../domain/execution';
import type { OperatorStop } from '../domain/operator-controls';
import { RETIRABLE_RUN_STATUSES } from '../domain/operator-suspension';
import type { ExecutionService } from './execution-service';

const STOPPER_JOB = 'agent-harness.run-stopper';

/** A run cancelled by a parent's cancel, or by another pass, moves between the read and the control. */
const MAX_PASSES = 3;

export interface StopCommand {
  /** Idempotent per run: a repeat of the same id controls nothing twice. */
  commandId: (run: HarnessRun) => string;
  /** Principal ref journaled as who cancelled. */
  by: string;
  stop?: OperatorStop;
}

/** `suspend` (ADR-OPS-01 D6): until when a suspended run is held. */
export interface SuspendCommand extends StopCommand {
  holdUntil: number;
}

/**
 * Cancels every run `open` answers that has not ended, by control command
 * (the person's Stop, a deactivation and the operator controls share it).
 * Roots go first, so their children are cancelled through them.
 */
export function cancelOpenRuns(
  executions: Pick<ExecutionService, 'control'>,
  open: () => Promise<HarnessRun[]>,
  command: StopCommand,
): Promise<number> {
  return controlOpenRuns(
    executions,
    open,
    (run) =>
      !TERMINAL_RUN_STATUSES.has(run.status) && run.status !== 'cancelling',
    (run, commandId) => ({
      action: 'cancel',
      ...(command.stop !== undefined ? { stop: command.stop } : {}),
      ...base(run, commandId, command.by),
    }),
    command.commandId,
  );
}

/** Suspends every queued or running run `open` answers for a retired engine version (ADR-OPS-01 D6). */
export function suspendOpenRuns(
  executions: Pick<ExecutionService, 'control'>,
  open: () => Promise<HarnessRun[]>,
  command: SuspendCommand,
): Promise<number> {
  return controlOpenRuns(
    executions,
    open,
    (run) => RETIRABLE_RUN_STATUSES.has(run.status),
    (run, commandId) => ({
      action: 'suspend',
      holdUntil: command.holdUntil,
      ...(command.stop !== undefined ? { stop: command.stop } : {}),
      ...base(run, commandId, command.by),
    }),
    command.commandId,
  );
}

const base = (
  run: HarnessRun,
  commandId: string,
  by: string,
): Omit<ControlCommand, 'action'> => ({
  orgId: run.orgId,
  runId: run.runId,
  expectedRevision: run.revision,
  by,
  commandId,
  dispatchCommandId: `${commandId}:dispatch`,
});

type ControlCommand = Parameters<ExecutionService['control']>[0];

/**
 * One control command for each run `applies` keeps. A conflict (the run
 * moved) reads again, at most `MAX_PASSES` times; then it throws so the
 * caller's retry finishes the work.
 */
async function controlOpenRuns(
  executions: Pick<ExecutionService, 'control'>,
  open: () => Promise<HarnessRun[]>,
  applies: (run: HarnessRun) => boolean,
  build: (run: HarnessRun, commandId: string) => ControlCommand,
  commandIdOf: (run: HarnessRun) => string,
): Promise<number> {
  let done = 0;
  let conflict: HarnessConflictError | null = null;
  for (let pass = 0; pass < MAX_PASSES; pass += 1) {
    conflict = null;
    const runs = (await open()).filter(applies).sort(rootsFirst);
    if (runs.length === 0) return done;
    for (const run of runs) {
      try {
        // The run's org, as the read found it: an instance-wide control's runs span orgs.
        await forEnumeratedOrg(run.orgId, STOPPER_JOB, () =>
          executions.control(build(run, commandIdOf(run))),
        );
        done += 1;
      } catch (error) {
        if (!(error instanceof HarnessConflictError)) throw error;
        conflict = error;
      }
    }
    if (conflict === null) return done;
  }
  throw conflict as HarnessConflictError;
}

const rootsFirst = (left: HarnessRun, right: HarnessRun): number =>
  Number(childOf(left) !== null) - Number(childOf(right) !== null);
