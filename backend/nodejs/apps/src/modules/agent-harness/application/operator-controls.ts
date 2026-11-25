import {
  admissionStop,
  coveringControls,
  deploymentInstanceControl,
  DEPLOYMENT_SETTER,
  effectOf,
  egressStopped,
  stopOf,
  stoppedEngines,
  type OperatorControl,
} from '../domain/operator-controls';
import { principalRefFor } from '../domain/run-spec';
import type {
  ExecutionStore,
  OpenRun,
  OpenRunFilter,
  OperatorRuns,
} from '../ports/execution-store';
import type { OperatorStops } from '../ports/operator-stops';
import { forEnumeratedOrg } from '../../../libs/context/orgContext';
import type { ExecutionService } from './execution-service';
import {
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from './harness-log';
import { cancelOpenRuns } from './run-stopper';

export interface OperatorStopsDeps {
  runs: OperatorRuns & Pick<ExecutionStore, 'get'>;
  executions: Pick<ExecutionService, 'control'>;
  /** `AGENT_HARNESS_KILL_SWITCH`: the deployment's instance stop, which no admin clears. */
  deploymentKillSwitch: boolean;
  logger?: HarnessLogger;
  now?: () => number;
}

/** Most runs one control cancels per pass; a bigger backlog finishes on the next sweeper tick. */
export const ENFORCE_BATCH = 200;

/** Operator work bound to the org of the runs it controls. */
export const OPERATOR_JOB = 'agent-harness.operator-controls';

/** The open runs a control may cover, as one store query. */
export function filterOf(control: OperatorControl): OpenRunFilter {
  const org = control.orgId === null ? {} : { orgId: control.orgId };
  switch (control.scope.kind) {
    case 'session':
      return { ...org, sessionId: control.scope.sessionId };
    case 'agent':
      return { ...org, agentKey: control.scope.agentKey };
    case 'org':
      return org;
    case 'engine_version':
      return { ...org, engine: control.scope.engine };
    case 'instance':
      return {};
    case 'egress':
      return { ...org, engineOnly: true };
  }
}

/**
 * The operator stops (ADR-OPS-01) and their enforcement on open runs, at
 * once and on every sweeper tick. This class holds the deployment's env
 * switch and no other control; a subclass adds stored ones
 * (`storedControls`). It applies `stop`; an effect it does not apply leaves
 * the runs a control covers as they are (`applyEffect`).
 */
export class OperatorStopService implements OperatorStops {
  protected readonly logger: HarnessLogger;
  protected readonly now: () => number;

  constructor(protected readonly deps: OperatorStopsDeps) {
    this.now = deps.now ?? Date.now;
    this.logger = deps.logger ?? silentHarnessLogger;
  }

  /** Controls kept beyond the env switch; none here. */
  protected storedControls(): Promise<OperatorControl[]> {
    return Promise.resolve([]);
  }

  async controls(): Promise<OperatorControl[]> {
    const stored = await this.storedControls();
    return this.deps.deploymentKillSwitch &&
      !stored.some((control) => control.scope.kind === 'instance')
      ? [...stored, deploymentInstanceControl()]
      : stored;
  }

  async admissionStop(turn: {
    orgId: string;
    sessionId: string;
    agentKey?: string | null;
  }): Promise<OperatorControl | null> {
    return admissionStop(await this.controls(), turn);
  }

  async stoppedEngines(orgId: string): Promise<ReadonlySet<string>> {
    return stoppedEngines(await this.controls(), orgId);
  }

  async egressStopped(orgId: string): Promise<boolean> {
    return egressStopped(await this.controls(), orgId);
  }

  /**
   * Applies one control to the open runs it covers: a stop cancels them.
   * Idempotent: a run once cancelled is past the filter, so every pass
   * controls each run once.
   */
  async enforce(control: OperatorControl): Promise<number> {
    const controlled =
      effectOf(control) === 'stop'
        ? await cancelOpenRuns(
            this.deps.executions,
            async () =>
              (await this.openRunsCovered(control)).map(({ run }) => run),
            {
              commandId: (run) =>
                `operator-stop:${control.id}:${run.orgId}:${run.runId}`,
              by: this.setterOf(control),
              stop: stopOf(control),
            },
          )
        : await this.applyEffect(control);
    if (controlled > 0 && effectOf(control) === 'stop') {
      this.logger.warn(
        'operator.runs_stopped',
        harnessLogMeta('operator.runs_stopped', {
          controlId: control.id,
          scope: control.scope.kind,
          runs: controlled,
        }),
      );
    }
    return controlled;
  }

  /** The sweeper's pass (D4): every control in force, so a run admitted in the race with a stop is controlled too. */
  async enforceAll(): Promise<number> {
    let controlled = 0;
    for (const control of await this.controls()) {
      try {
        controlled += await this.enforce(control);
      } catch (error) {
        this.logger.warn(
          'operator.enforce_failed',
          harnessLogMeta('operator.enforce_failed', {
            controlId: control.id,
            errorType: error instanceof Error ? error.name : typeof error,
          }),
        );
      }
    }
    return controlled + (await this.afterEnforce());
  }

  /** A control whose effect is not `stop`: this class applies none, so its runs are left as they are. */
  protected applyEffect(_control: OperatorControl): Promise<number> {
    return Promise.resolve(0);
  }

  /** Work the sweeper's pass does after every control is enforced; none here. */
  protected afterEnforce(): Promise<number> {
    return Promise.resolve(0);
  }

  /**
   * The open runs `control` covers. An org's control reads that org's runs
   * as that org; one with no org reads across orgs.
   */
  protected async openRunsCovered(
    control: OperatorControl,
  ): Promise<OpenRun[]> {
    const filter = filterOf(control);
    const open =
      filter.orgId === undefined
        ? await this.deps.runs.openRuns(filter, ENFORCE_BATCH)
        : await forEnumeratedOrg(filter.orgId, OPERATOR_JOB, () =>
            this.deps.runs.openRuns(filter, ENFORCE_BATCH),
          );
    // The store's filter is the scope's; this keeps the match the one domain function's.
    return open.filter(
      ({ run, engine }) =>
        coveringControls({ ...run, engine }, [control]).length > 0,
    );
  }

  /** Who a control's commands are journalled as: its admin, or the deployment for the env switch. */
  protected setterOf(control: OperatorControl): string {
    return control.setBy === DEPLOYMENT_SETTER
      ? principalRefFor('deployment')
      : control.setBy;
  }
}
