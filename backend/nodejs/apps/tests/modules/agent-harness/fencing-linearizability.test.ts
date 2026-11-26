import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import {
  contractClock,
  createContractHarness,
} from './support/execution-store-contract';
import {
  assertLinearizableFencing,
  LinearizabilityTarget,
} from './support/fencing-linearizability';

const inMemoryTarget = (): LinearizabilityTarget => {
  const clock = contractClock();
  const store = new InMemoryExecutionStore(clock);
  const payloads = new InMemoryPayloadStore();
  const harness = createContractHarness(
    {
      executionStore: store,
      admissionStore: store,
      journal: store.journal,
      payloads,
      deletePayload: async () => undefined,
      dispatches: async () => [],
    },
    clock,
  );
  return { harness, setNow: clock.set, now: clock.now };
};

describe('J-10 fenced commits are linearizable (in-memory)', function () {
  this.timeout(120_000);

  it('holds for sequential histories of two workers', async () => {
    await assertLinearizableFencing(inMemoryTarget(), {
      concurrent: false,
      numRuns: 300,
    });
  });

  it('holds when the two workers act concurrently', async () => {
    await assertLinearizableFencing(inMemoryTarget(), {
      concurrent: true,
      numRuns: 300,
    });
  });
});
