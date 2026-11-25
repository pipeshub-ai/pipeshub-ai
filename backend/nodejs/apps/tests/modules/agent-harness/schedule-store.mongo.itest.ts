/**
 * P8a on a MongoDB replica set: the schedule store contract (ST-1..ST-8) and
 * exactly-once firing under two concurrent schedulers (ST-9, in the shared races). Run as
 * execution-store.mongo.itest.ts says.
 */
import 'reflect-metadata';
import { Model } from 'mongoose';
import { MongoScheduleStore } from '../../../src/libs/repositories/agent-harness/schedules/mongo';
import { fixedMongoTime } from '../../../src/libs/repositories/agent-harness/time/mongo';
import {
  AgentScheduleCounterModel,
  AgentScheduleFiringModel,
  AgentScheduleModel,
} from '../../../src/libs/repositories/agent-harness/schedules/schema/schedules.schema';
import { RunInputSeal } from '../../../src/modules/agent-harness/application/run-input-seal';
import { testCipher } from './support/data-protection';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  registerHarnessModels,
  resetHarnessMongo,
} from './support/harness-mongo';
import { describeScheduleStoreContract, describeScheduleStoreRaces } from './support/schedule-store-contract';
import { describeScheduleFiringMachine } from './support/schedule-firing-machine';
import { TestClock } from './support/schedules';

registerHarnessModels([
  AgentScheduleModel,
  AgentScheduleFiringModel,
  AgentScheduleCounterModel,
] as unknown as Array<Model<never>>);

describe('P8a schedules on a MongoDB replica set', function () {
  this.timeout(120_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(resetHarnessMongo);

  describeScheduleStoreContract('mongo', async () => {
    const clock = new TestClock();
    return {
      // Sealed as production stores a template (GAPS-1): every contract case reads it back through the seal.
      store: new MongoScheduleStore(fixedMongoTime(() => clock.now()), new RunInputSeal(testCipher())),
      clock,
    };
  });

  describeScheduleFiringMachine(
    'mongo',
    async (clock) => {
      await resetHarnessMongo();
      return new MongoScheduleStore(fixedMongoTime(() => clock.now()));
    },
    { ci: 15, nightly: 300 },
  );

  describeScheduleStoreRaces('mongo', async (clock) => {
    await resetHarnessMongo();
    // Sealed as production stores a template (GAPS-1).
    return () => new MongoScheduleStore(fixedMongoTime(() => clock.now()), new RunInputSeal(testCipher()));
  });
});
