/** The write deadline against a real MongoDB: none while the store is active, 5 s once it is being moved. */
import 'reflect-metadata';
import { expect } from 'chai';
import { Collection, MongoClient } from 'mongodb';
import { haveMongod, startMongo, TestMongo } from '../../scripts/docstore-migrate/mongo-harness';

const suite = haveMongod ? describe : describe.skip;

suite('Mongo store fence deadline (real MongoDB)', function () {
  this.timeout(120_000);
  let mongo: TestMongo;
  let client: MongoClient;
  let Fenced: typeof Collection;
  before(async () => {
    process.env.DOC_STORE_FENCE = 'on';
    mongo = await startMongo(process.env.PG_TEST_WORK ?? '/tmp', false);
    client = await new MongoClient(mongo.uri).connect();
    // a subclass, so the process's own Collection is left alone; the module is loaded afresh so that it wraps once more
    delete require.cache[require.resolve('../../../src/libs/repositories/fence')];
    Fenced = class extends Collection {};
    const fresh = require('../../../src/libs/repositories/fence');
    fresh.setFenceTrustMsForTests(0);
    fresh.installMongoFence(Fenced);
  });
  after(async () => {
    process.env.DOC_STORE_FENCE = 'off';
    await client?.close();
    await mongo?.stop();
  });

  const slowUpdate = async (): Promise<number> => {
    const db = client.db('fence_deadline');
    await db.collection('t').drop().catch(() => undefined);
    await db.collection('t').insertMany(Array.from({ length: 30 }, (_, i) => ({ i })));
    const fenced = new Fenced(db, 't', {} as never);
    // server-side JS makes the statement last about 9 s, longer than the 5 s deadline
    const r = await fenced.updateMany({ $where: 'sleep(300) || true' }, { $set: { done: true } });
    return r.modifiedCount;
  };
  const setMode = (mode: string) => client.db('fence_deadline').collection<any>('docstore_fence').replaceOne({ _id: 'fence' }, { _id: 'fence', mode, epoch: 1 }, { upsert: true });

  it('a long updateMany under an active fence is not cut short', async () => {
    await setMode('active');
    expect(await slowUpdate()).to.equal(30);
  });

  it('the same statement is cut at the deadline once the store is draining', async () => {
    await setMode('draining');
    let err: any;
    await slowUpdate().catch((e) => (err = e));
    expect(err, 'a deadline error').to.not.equal(undefined);
    expect(String(err.name) + String(err.message)).to.match(/time|Timeout|expired/i);
  });
});
