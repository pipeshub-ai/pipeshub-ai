/**
 * J-10 on a real replica set: the two workers' operations of a round run
 * concurrently against MongoDB transactions (run instructions in
 * execution-store.mongo.itest.ts).
 */
import 'reflect-metadata';
import {
  contractClock,
  createContractHarness,
} from './support/execution-store-contract';
import { assertLinearizableFencing } from './support/fencing-linearizability';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  mongoBackend,
} from './support/harness-mongo';

describe('J-10 fenced commits are linearizable (MongoDB replica set)', function () {
  this.timeout(600_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);

  it('holds when the two workers act concurrently', async () => {
    const clock = contractClock();
    const harness = createContractHarness(mongoBackend({ clock }), clock);
    const stats = await assertLinearizableFencing(
      { harness, setNow: clock.set, now: clock.now },
      {
        concurrent: true,
        numRuns: Number(process.env.J10_MONGO_RUNS ?? 60),
      },
    );
    // eslint-disable-next-line no-console
    console.log('      J-10 history stats', stats);
  });
});
