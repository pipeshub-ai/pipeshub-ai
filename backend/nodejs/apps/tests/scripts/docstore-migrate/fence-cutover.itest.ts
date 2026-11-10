/** The cutover's wait for fenced writes, against a real mongod: a tagged long-running operation blocks the move to readonly. */
import 'reflect-metadata';
import { expect } from 'chai';
import { FENCED_WRITE_COMMENT } from '../../../src/libs/repositories/fence';
import {
  FenceOpsTimeoutError,
  casMongoFence,
  fenceTuning,
  readMongoFence,
} from '../../../src/scripts/docstore-migrate/fence';
import { haveMongod, startMongo, TestMongo } from './mongo-harness';

const suite = haveMongod ? describe : describe.skip;

suite('fence cutover waits for fenced writes (real MongoDB)', function () {
  this.timeout(120_000);
  let mongo: TestMongo;
  const rt = () => ({ db: () => mongo.client.db('cutover') }) as never;
  const original = { ...fenceTuning };

  before(async () => {
    mongo = await startMongo(process.env.PG_TEST_WORK ?? '/tmp', false);
    await mongo.client.db('cutover').collection('t').insertOne({ a: 1 });
  });
  after(async () => {
    await mongo?.stop();
  });
  beforeEach(async () => {
    // draining since long enough that only the operations matter
    await mongo.client
      .db('cutover')
      .collection<{ _id: string }>('docstore_fence')
      .replaceOne(
        { _id: 'fence' },
        {
          _id: 'fence',
          mode: 'draining',
          epoch: 1,
          at: new Date(Date.now() - 60_000),
        } as never,
        { upsert: true },
      );
    fenceTuning.pollMs = 200;
    fenceTuning.clientOptions = () => ({
      waitQueueTimeoutMS: 100,
      connectTimeoutMS: 100,
      serverSelectionTimeoutMS: 100,
      retryWrites: true,
    });
    fenceTuning.log = () => undefined;
  });
  afterEach(() => Object.assign(fenceTuning, original));

  /** A find that sleeps `ms` on the server, tagged the way a fenced write is (or not). */
  const slowOp = (ms: number, comment: unknown) =>
    mongo.client
      .db('cutover')
      .collection('t')
      .find({ $where: `sleep(${ms}) || true` }, { comment })
      .toArray();
  const untilListed = async (): Promise<void> => {
    for (
      let i = 0;
      i < 50 && (await fenceTuning.fencedOpsRunning(rt())) === 0;
      i++
    )
      await new Promise((r) => setTimeout(r, 100));
  };

  it('a tagged operation blocks the move to readonly until it finishes', async () => {
    const op = slowOp(4000, { tag: FENCED_WRITE_COMMENT }).then(() =>
      Date.now(),
    );
    await untilListed();
    expect(await fenceTuning.fencedOpsRunning(rt())).to.equal(1);
    const moved = casMongoFence(rt(), ['draining'], 'readonly').then(() =>
      Date.now(),
    );
    await new Promise((r) => setTimeout(r, 1500));
    expect(
      (await readMongoFence(rt())).mode,
      'still draining while the operation runs',
    ).to.equal('draining');
    const [opEnd, movedAt] = await Promise.all([op, moved]);
    expect(movedAt).to.be.at.least(opEnd);
    expect((await readMongoFence(rt())).mode).to.equal('readonly');
  });

  it('an operation that is not tagged does not block it', async () => {
    const op = slowOp(3000, { tag: 'something else' });
    await new Promise((r) => setTimeout(r, 300));
    const t0 = Date.now();
    await casMongoFence(rt(), ['draining'], 'readonly');
    expect(Date.now() - t0).to.be.lessThan(2000);
    expect((await readMongoFence(rt())).mode).to.equal('readonly');
    await op;
  });

  it('the operator timeout aborts the move and leaves the store draining', async () => {
    fenceTuning.opsTimeoutMs = 1200;
    const op = slowOp(5000, { tag: FENCED_WRITE_COMMENT });
    await untilListed();
    await casMongoFence(rt(), ['draining'], 'readonly').then(
      () => expect.fail('moved while a fenced write was running'),
      (e: Error) => expect(e).to.be.instanceOf(FenceOpsTimeoutError),
    );
    expect((await readMongoFence(rt())).mode).to.equal('draining');
    await op;
    fenceTuning.opsTimeoutMs = original.opsTimeoutMs;
    expect(await casMongoFence(rt(), ['draining'], 'readonly')).to.equal(true);
  });
});
