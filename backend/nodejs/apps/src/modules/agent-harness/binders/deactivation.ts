import { createScheduleStore } from '../../../libs/repositories/agent-harness/schedules';
import type { ExecutionService } from '../application/execution-service';
import {
  PRINCIPAL_DEACTIVATION,
  PrincipalDeactivation,
  type PrincipalDeactivationPort,
} from '../application/principal-deactivation';
import type { ActorRuns, ExecutionStore } from '../ports/execution-store';
import type { ScheduleEraser } from '../ports/schedules';
import type { HarnessBinder } from './context';
import {
  AGENT_HARNESS_EXECUTION_SERVICE,
  AGENT_HARNESS_EXECUTION_STORE,
} from './tokens';

/**
 * SEC-AZ-08: what a person's deactivation stops in the harness (their runs,
 * their schedules). The user module borrows it (app.ts `shareHarnessToUsers`).
 */
export const bindPrincipalDeactivation: HarnessBinder = ({
  container,
  dependencies,
}) => {
  if (container.isBound(PRINCIPAL_DEACTIVATION)) return;
  const configured = dependencies.schedules?.store as
    (Partial<ScheduleEraser> & object) | undefined;
  const schedules: Pick<ScheduleEraser, 'pauseUser'> =
    configured !== undefined && typeof configured.pauseUser === 'function'
      ? (configured as Pick<ScheduleEraser, 'pauseUser'>)
      : createScheduleStore();
  container
    .bind<PrincipalDeactivationPort>(PRINCIPAL_DEACTIVATION)
    .toDynamicValue((context) => {
      const store = context.container.get<ExecutionStore & Partial<ActorRuns>>(
        AGENT_HARNESS_EXECUTION_STORE,
      );
      if (typeof store.activeRunsOfActor !== 'function') {
        throw new Error('The execution store cannot list a person’s runs');
      }
      return new PrincipalDeactivation({
        runs: store as ActorRuns,
        executions: context.container.get<ExecutionService>(
          AGENT_HARNESS_EXECUTION_SERVICE,
        ),
        schedules,
      });
    })
    .inSingletonScope();
};
