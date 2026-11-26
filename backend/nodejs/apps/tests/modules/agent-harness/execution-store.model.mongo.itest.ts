/**
 * QA13-7 (JRN-23 nightly differential): the run machine's commands on a
 * MongoDB replica set and on the in-memory store, step by step. Every
 * command must answer the same on both, and leave both with the same runs
 * (status, fence, attempts, open interactions), active runs, wake hints and
 * journal; each also agrees with `RunModel`. Run like the other
 * `*.mongo.itest.ts` (see `execution-store.mongo.itest.ts`); the nightly
 * workflow sets `HARNESS_PROPERTY_PROFILE=nightly`.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import fc from 'fast-check';
import { contractClock, createContractHarness } from './support/execution-store-contract';
import { connectHarnessMongo, disconnectHarnessMongo, mongoBackend, resetHarnessMongo } from './support/harness-mongo';
import { inMemoryBackend } from './support/in-memory-backend';
import {
  expectAnswer,
  FIRST_RUN,
  Op,
  opArbitrary,
  RunModel,
  RunWorld,
  step,
  WorldSnapshot,
} from './support/run-machine';
import { propertyRuns } from '../../support/harness/property-profile';

/** A snapshot without the ids each store minted: runs by admission order. */
const comparable = (snapshot: WorldSnapshot) => ({
  runs: snapshot.runs.map(({ status, fence, attempts, open }) => ({ status, fence, attempts, open })),
  active: snapshot.active.map((runId) =>
    runId === null ? null : snapshot.runs.findIndex((run) => run.runId === runId),
  ),
  wakes: snapshot.wakes,
  journal: snapshot.journal,
});

async function differential(ops: Op[]): Promise<void> {
  await resetHarnessMongo();
  const memoryClock = contractClock();
  const mongoClock = contractClock();
  const memory = new RunWorld(createContractHarness(inMemoryBackend(memoryClock), memoryClock));
  const mongo = new RunWorld(createContractHarness(mongoBackend({ clock: mongoClock }), mongoClock));
  const model = new RunModel();
  for (const op of [FIRST_RUN, ...ops]) {
    const label = JSON.stringify(op);
    const answered = await mongo.apply(op);
    expectAnswer(await step(model, memory, op), answered, `${label} on Mongo`);
    expect(comparable(await mongo.snapshot()), label).to.deep.equal(comparable(await memory.snapshot()));
  }
}

describe('QA13-7 the run state machine on Mongo, against the in-memory store (JRN-23)', function () {
  this.timeout(1_800_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);

  it('every command answers alike and leaves both stores alike', async () => {
    await fc.assert(
      fc.asyncProperty(fc.array(opArbitrary, { maxLength: 40, size: 'large' }), differential),
      propertyRuns(25, 300),
    );
  });
});
