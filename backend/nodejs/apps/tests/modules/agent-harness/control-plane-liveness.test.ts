/** The ControlPlaneLiveness contract on `InMemoryControlPlaneLiveness` (one process: every replica is the same object). */
import { InMemoryControlPlaneLiveness } from '../../../src/modules/agent-harness/adapters/in-memory-control-plane-liveness';
import { describeControlPlaneLivenessContract } from './support/control-plane-liveness-contract';

describeControlPlaneLivenessContract('in-memory', async (clock) => {
  const liveness = new InMemoryControlPlaneLiveness(clock);
  return { replica: () => liveness };
});
