import type { ControlPlaneLiveness } from '../../../../modules/agent-harness/ports/control-plane-liveness';
import { pickHarnessStore } from '../stores';
import { MongoControlPlaneLiveness } from './mongo';
import { PgControlPlaneLiveness } from './pg';

export const createControlPlaneLiveness = (): ControlPlaneLiveness =>
  pickHarnessStore<ControlPlaneLiveness>(
    'agent-harness/control-plane-liveness',
    {
      mongo: () => new MongoControlPlaneLiveness(),
      postgres: () => new PgControlPlaneLiveness(),
    },
  );
