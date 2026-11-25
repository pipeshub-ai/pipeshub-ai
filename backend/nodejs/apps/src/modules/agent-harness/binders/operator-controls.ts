import { envFlag } from '../../../libs/utils/env-flags';
import type { ExecutionService } from '../application/execution-service';
import { OperatorStopService } from '../application/operator-controls';
import type { ExecutionStore, OperatorRuns } from '../ports/execution-store';
import type { OperatorStops } from '../ports/operator-stops';
import type { HarnessBinder } from './context';
import {
  AGENT_HARNESS_EXECUTION_SERVICE,
  AGENT_HARNESS_EXECUTION_STORE,
  AGENT_HARNESS_OPERATOR_CONTROLS,
} from './tokens';

/**
 * ADR-OPS-01: one source holds the operator stops; admission, child runs,
 * engine eligibility and the sweeper read it. Built in: the deployment's env
 * switch alone. A binding made before this binder (an edition's) is kept.
 */
export const bindOperatorControls: HarnessBinder = ({ container, logger }) => {
  if (container.isBound(AGENT_HARNESS_OPERATOR_CONTROLS)) return;
  container
    .bind<OperatorStops>(AGENT_HARNESS_OPERATOR_CONTROLS)
    .toDynamicValue(
      (context) =>
        new OperatorStopService({
          runs: context.container.get<ExecutionStore & OperatorRuns>(
            AGENT_HARNESS_EXECUTION_STORE,
          ),
          executions: context.container.get<ExecutionService>(
            AGENT_HARNESS_EXECUTION_SERVICE,
          ),
          deploymentKillSwitch: envFlag('AGENT_HARNESS_KILL_SWITCH'),
          ...(logger !== undefined ? { logger } : {}),
        }),
    )
    .inSingletonScope();
};
