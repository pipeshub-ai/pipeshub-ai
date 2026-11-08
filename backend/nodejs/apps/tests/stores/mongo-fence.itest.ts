/**
 * The Mongo store fence against a real MongoDB, as a replica set and as a single server. The server needs
 * `--setParameter enableTestCommands=1` (the stall tests use the `failCommand` fail point).
 *
 *   MONGO_IT_URI=mongodb://localhost:27017/?replicaSet=rs0 REPLICA_SET_AVAILABLE=true
 *   MONGO_IT_URI=mongodb://localhost:27017                 REPLICA_SET_AVAILABLE=false
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { randomUUID } from 'crypto';
import mongoose, { Schema } from 'mongoose';
import { MongoService } from '../../src/libs/services/mongo.service';
import { MongoUnitOfWork, sessionOf } from '../../src/libs/repositories/mongo';
import {
  DocStoreFencedError,
  DocStoreReadOnlyError,
} from '../../src/libs/repositories/errors';
import { ServiceUnavailableError } from '../../src/libs/errors/http.errors';
import {
  FENCE_COLLECTION,
  setFenceTrustMsForTests,
} from '../../src/libs/repositories/fence';

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} must be set for the MongoDB tests`);
  return value;
}
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

describe('Mongo store fence against a real MongoDB', function () {
  this.timeout(60000);
  const uri = requireEnv('MONGO_IT_URI');
  const rsAvailable = requireEnv('REPLICA_SET_AVAILABLE') === 'true';
  const dbName = `pipeshub_fence_${randomUUID().slice(0, 8)}`;
  let mongo: MongoService;
  let Thing: mongoose.Model<{ k: string; v: number }>;
  const fence = () => mongoose.connection.collection(FENCE_COLLECTION);
  const things = () => mongoose.connection.collection('things');
  const setMode = (mode: 'active' | 'draining' | 'readonly' | 'retired') =>
    fence().updateOne({ _id: 'fence' as never }, { $set: { mode, epoch: 1 } }, { upsert: true });
  const failPoint = (data: object | null) =>
    mongoose.connection.db!.admin().command({
      configureFailPoint: 'failCommand',
      mode: data ? { times: 1 } : 'off',
      ...(data ? { data } : {}),
    });

  before(async () => {
    process.env.DOC_STORE_FENCE = 'on'; // armed at the start of a migration
    setFenceTrustMsForTests(0); // the tests change the mode between writes: no trusted `active` answer
    mongo = new MongoService({ uri, db: dbName });
    await mongo.initialize();
    Thing = mongoose.model(
      'FenceThing',
      new Schema({ k: String, v: { type: Number, default: 0 } }, { collection: 'things' }),
    );
    await Thing.createCollection();
  });
  beforeEach(async () => {
    await failPoint(null);
    await fence().deleteMany({}); // no document means active
    await things().deleteMany({});
    await things().insertOne({ k: 'a', v: 0 } as never);
  });
  after(async () => {
    await failPoint(null).catch(() => undefined);
    if (mongo?.isConnected()) {
      await mongo.getConnection().dropDatabase();
      await mongo.destroy();
    }
  });

  const value = async () => ((await things().findOne({ k: 'a' })) as { v: number }).v;

  it('active, or no fence document: Mongoose and raw writes go through', async () => {
    await Thing.updateOne({ k: 'a' }, { $inc: { v: 1 } });
    await new Thing({ k: 'b' }).save();
    await things().insertOne({ k: 'c' } as never);
    expect(await things().countDocuments({})).to.equal(3);
    await setMode('active');
    await Thing.updateOne({ k: 'a' }, { $inc: { v: 1 } });
    expect(await value()).to.equal(2);
  });

  it('read-only: every kind of write is refused with DocStoreReadOnlyError and changes nothing; reads still work', async () => {
    await setMode('readonly');
    const writes: Array<() => Promise<unknown>> = [
      () => Thing.create({ k: 'x' }),
      () => new Thing({ k: 'x' }).save(),
      () => Thing.insertMany([{ k: 'x' }]),
      () => Thing.updateOne({ k: 'a' }, { $inc: { v: 1 } }),
      () => Thing.updateMany({}, { $inc: { v: 1 } }),
      () => Thing.findOneAndUpdate({ k: 'a' }, { $inc: { v: 1 } }),
      () => Thing.replaceOne({ k: 'a' }, { k: 'a', v: 9 }),
      () => Thing.deleteOne({ k: 'a' }),
      () => Thing.deleteMany({}),
      () => Thing.bulkWrite([{ updateOne: { filter: { k: 'a' }, update: { $inc: { v: 1 } } } }]),
      () => things().insertOne({ k: 'raw' } as never),
    ];
    for (const w of writes) expect(await w().catch((e) => e)).to.be.instanceOf(DocStoreReadOnlyError);
    expect(await things().countDocuments({})).to.equal(1);
    expect(await value()).to.equal(0);
    expect((await Thing.findOne({ k: 'a' }).lean())?.v).to.equal(0);
    await setMode('active');
    await Thing.updateOne({ k: 'a' }, { $inc: { v: 1 } });
    expect(await value()).to.equal(1);
  });

  it('retired: writes are refused with DocStoreFencedError', async () => {
    await setMode('retired');
    expect(await Thing.updateOne({ k: 'a' }, { $inc: { v: 1 } }).catch((e) => e)).to.be.instanceOf(DocStoreFencedError);
    expect(await value()).to.equal(0);
  });

  it('the fence collection itself can be written while the store is read-only (so it can be lifted)', async () => {
    await setMode('readonly');
    await setMode('active');
    await Thing.updateOne({ k: 'a' }, { $inc: { v: 1 } });
    expect(await value()).to.equal(1);
  });

  it('a fence read that stalls past its 2 s deadline refuses the write, which never lands', async () => {
    await failPoint({ failCommands: ['find'], blockConnection: true, blockTimeMS: 3000 });
    const t0 = Date.now();
    const e = await Thing.updateOne({ k: 'a' }, { $inc: { v: 1 } }).catch((x) => x);
    expect(e).to.be.instanceOf(ServiceUnavailableError);
    expect(Date.now() - t0).to.be.within(1800, 3500);
    await failPoint(null);
    await sleep(1500);
    expect(await value()).to.equal(0);
  });

  it('a write that passed the fence and then stalls on the server fails at 5 s and does not land after the freeze', async () => {
    await setMode('draining'); // only a store that is being moved puts a deadline on a write
    await failPoint({ failCommands: ['update'], blockConnection: true, blockTimeMS: 9000 });
    const t0 = Date.now();
    const pending = Thing.updateOne({ k: 'a' }, { $inc: { v: 1 } }).catch((x) => x);
    await sleep(500);
    await setMode('readonly'); // the freeze, while the write is stalled
    const e = await pending;
    expect(e).to.be.instanceOf(Error);
    expect(Date.now() - t0).to.be.within(4500, 7000);
    await sleep(5000); // past the 9 s block: the write must not appear
    expect(await value()).to.equal(0);
  });

  it('writers racing a freeze: nothing lands once the fence is read-only', async () => {
    let stop = false;
    const writers = Array.from({ length: 8 }, async () => {
      while (!stop) await Thing.updateOne({ k: 'a' }, { $inc: { v: 1 } }).catch(() => undefined);
    });
    await sleep(500);
    await setMode('readonly');
    await sleep(1000);
    const atFreeze = await value();
    await sleep(1500);
    stop = true;
    await Promise.all(writers);
    expect(atFreeze).to.be.greaterThan(0);
    expect(await value()).to.equal(atFreeze);
  });

  if (rsAvailable) {
    it('a transaction that wrote before the freeze cannot commit after it (the commit is fenced too)', async () => {
      const uow = new MongoUnitOfWork(true);
      const e = await uow
        .run(async (tx) => {
          await things().updateOne({ k: 'a' }, { $inc: { v: 1 } }, { session: sessionOf(tx) });
          await setMode('readonly'); // freeze while the transaction is open
        })
        .catch((x) => x);
      expect(e).to.be.instanceOf(DocStoreReadOnlyError);
      expect(await value()).to.equal(0);
    });

    it('a commit that stalls past the freeze is not retried into the frozen store (0 commits after the freeze)', async () => {
      await failPoint({ failCommands: ['commitTransaction'], blockConnection: true, blockTimeMS: 15000 });
      const t0 = Date.now();
      const flip = setTimeout(() => void setMode('readonly'), 1000); // the freeze, while the commit is stalled
      const e = await new MongoUnitOfWork(true)
        .run(async (tx) => {
          await things().updateOne({ k: 'a' }, { $inc: { v: 1 } }, { session: sessionOf(tx) });
        })
        .catch((x) => x);
      clearTimeout(flip);
      expect(e).to.be.instanceOf(Error);
      expect(Date.now() - t0).to.be.lessThan(9000);
      await sleep(16000); // past the block and any retry
      expect(await value()).to.equal(0);
    });

    it('a read-only unit of work is not stopped by the freeze', async () => {
      await setMode('readonly');
      const out = await new MongoUnitOfWork(true).run(
        async (tx) => ((await things().findOne({ k: 'a' }, { session: sessionOf(tx) })) as { k: string }).k,
        { readOnly: true },
      );
      expect(out).to.equal('a');
    });

    it('a write blocked behind another transaction on the same document fails at its 5 s deadline instead of landing later', async () => {
      await setMode('draining'); // only a store that is being moved puts a deadline on a write
      const blocker = await mongoose.startSession();
      blocker.startTransaction();
      await things().updateOne({ k: 'a' }, { $set: { v: 100 } }, { session: blocker });
      const t0 = Date.now();
      const e = await Thing.updateOne({ k: 'a' }, { $set: { v: 7 } }).catch((x) => x);
      expect(e).to.be.instanceOf(Error);
      expect(Date.now() - t0).to.be.within(4500, 7000);
      await blocker.abortTransaction();
      await blocker.endSession();
      await sleep(500);
      expect(await value()).to.equal(0);
    });
  }
});
