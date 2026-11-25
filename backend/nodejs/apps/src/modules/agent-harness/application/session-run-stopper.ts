import { principalRefFor } from '../domain/run-spec';
import { erasureCancelCommandId } from '../domain/session-erasure';
import type { ExecutionStore } from '../ports/execution-store';
import type { ExecutionService } from './execution-service';
import { cancelOpenRuns } from './run-stopper';

/** The conversation module's token: what deleting a chat calls to stop its run at once (R30-LAT). */
export const AGENT_HARNESS_SESSION_RUN_STOPPER =
  'AgentHarnessSessionRunStopper';

export interface SessionRunStopperPort {
  /** Cancels the session's open run (its children through it); how many runs it cancelled. */
  stop(orgId: string, sessionId: string, byUserId: string): Promise<number>;
}

/**
 * Deleting a chat stops what runs in it when the delete commits, as the person's Stop does (SLO #7), instead of when
 * the erasure job's first step comes round on the sweeper's tick (QA-LAT-9 measured 52-62 s). The command id is the
 * erasure step's own, so whichever comes second finds the cancel already done; that step stays the durable path
 * for a stop this one never made (a crash between the commit and here).
 */
export class SessionRunStopper implements SessionRunStopperPort {
  constructor(
    private readonly deps: {
      runs: Pick<ExecutionStore, 'getActive'>;
      executions: Pick<ExecutionService, 'control'>;
    },
  ) {}

  stop(orgId: string, sessionId: string, byUserId: string): Promise<number> {
    return cancelOpenRuns(
      this.deps.executions,
      async () => {
        const run = await this.deps.runs.getActive(orgId, sessionId);
        return run === null ? [] : [run];
      },
      {
        commandId: (run) => erasureCancelCommandId(sessionId, run.runId),
        by: principalRefFor(byUserId),
      },
    );
  }
}
