/**
 * QA13-6, QA13-9 (JRN-23, WRK-31): `fc.commands` over the run's operations
 * against the in-memory store, each answer and the state after it checked
 * against `RunModel` (support/run-machine.ts): one active root run per
 * session, fences rising, terminal states absorbing, deadlines on every run,
 * stale fences refused, a wake hint per return to the queue, interactions
 * resolved once, and a valid journal history after every command. The same
 * commands run against Mongo in `execution-store.model.mongo.itest.ts`.
 */
import fc from 'fast-check';
import { createContractHarness, contractClock } from './support/execution-store-contract';
import { inMemoryBackend } from './support/in-memory-backend';
import { FIRST_RUN, Op, opArbitrary, RunModel, RunWorld, step } from './support/run-machine';
import { propertyRuns } from '../../support/harness/property-profile';

class OpCommand implements fc.AsyncCommand<RunModel, RunWorld> {
  constructor(readonly op: Op) {}

  check(): boolean {
    return true;
  }

  async run(model: RunModel, world: RunWorld): Promise<void> {
    await step(model, world, this.op);
  }

  toString(): string {
    return JSON.stringify(this.op);
  }
}

export const runCommands = fc.commands([opArbitrary.map((op) => new OpCommand(op))], {
  maxCommands: 60,
  size: 'large',
});

describe('QA13-6 the run state machine against the in-memory store (JRN-23)', function () {
  this.timeout(600_000);

  it('every command answers and leaves the store as the model says', async () => {
    await fc.assert(
      fc.asyncProperty(runCommands, async (commands) => {
        const clock = contractClock();
        const world = new RunWorld(createContractHarness(inMemoryBackend(clock), clock));
        const model = new RunModel();
        await step(model, world, FIRST_RUN);
        await fc.asyncModelRun(() => ({ model, real: world }), commands);
      }),
      propertyRuns(150),
    );
  });
});
