import { principalRefFor } from '../domain/run-spec';
import type { ActorRuns } from '../ports/execution-store';
import type { ScheduleEraser } from '../ports/schedules';
import type { ExecutionService } from './execution-service';
import { cancelOpenRuns } from './run-stopper';

/** The user module's token: what a person's deactivation calls on the harness (SEC-AZ-08). */
export const PRINCIPAL_DEACTIVATION = 'AgentHarnessPrincipalDeactivation';

/** Why a deactivated person's schedules are paused; the firer pauses on the same reason. */
export const ACTOR_DISABLED = 'actor_disabled';

export interface DeactivationCounts {
  cancelledRuns: number;
  pausedSchedules: number;
}

export interface PrincipalDeactivationPort {
  deactivate(
    orgId: string,
    userId: string,
    byUserId: string,
  ): Promise<DeactivationCounts>;
}

export interface PrincipalDeactivationDeps {
  runs: ActorRuns;
  executions: Pick<ExecutionService, 'control'>;
  schedules: Pick<ScheduleEraser, 'pauseUser'>;
}

/**
 * SEC-AZ-08: what the harness does once a person is deactivated. Called after
 * the account is marked disabled, so admission, schedule firings and token
 * minting already refuse them: this stops what was already admitted. Every
 * step is idempotent (the cancel's command id is the run's own), so calling it
 * again after a failure finishes the work. Nothing here is undone on
 * reactivation: cancelled runs stay cancelled and paused schedules wait for
 * their owner to resume them.
 */
export class PrincipalDeactivation implements PrincipalDeactivationPort {
  constructor(private readonly deps: PrincipalDeactivationDeps) {}

  async deactivate(
    orgId: string,
    userId: string,
    byUserId: string,
  ): Promise<DeactivationCounts> {
    const pausedSchedules = await this.deps.schedules.pauseUser(
      orgId,
      userId,
      ACTOR_DISABLED,
    );
    const cancelledRuns = await this.cancelRuns(orgId, userId, byUserId);
    return { cancelledRuns, pausedSchedules };
  }

  private async cancelRuns(
    orgId: string,
    userId: string,
    byUserId: string,
  ): Promise<number> {
    return cancelOpenRuns(
      this.deps.executions,
      () => this.deps.runs.activeRunsOfActor(orgId, userId),
      {
        commandId: (run) => `user-disable:${run.orgId}:${run.runId}`,
        by: principalRefFor(byUserId),
      },
    );
  }
}
