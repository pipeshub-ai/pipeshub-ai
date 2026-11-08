/** The pre-flight _id check goes by the BSON type, against a real mongod: a 24-hex string is not an ObjectId. */
import 'reflect-metadata';
import { expect } from 'chai';
import { ObjectId } from 'mongodb';
import { nonObjectIdSample } from '../../../src/scripts/docstore-migrate/preflight';
import { haveMongod, startMongo, TestMongo } from './mongo-harness';

const suite = haveMongod ? describe : describe.skip;

suite('pre-flight _id check (real MongoDB)', function () {
  this.timeout(60_000);
  let mongo: TestMongo;
  before(async () => {
    mongo = await startMongo(process.env.PG_TEST_WORK ?? '/tmp', false);
  });
  after(async () => {
    await mongo?.stop();
  });

  it('flags a 24-hex string _id, a number and a UUID string, and not an ObjectId', async () => {
    const db = mongo.client.db('ids');
    const c = db.collection<{ _id: unknown }>('things');
    await c.insertMany([
      { _id: new ObjectId() },
      { _id: 'a'.repeat(24) },
      { _id: 42 },
      { _id: '0b8d3f1e-4c1a-4a52-9d52-6a4f2b3f6c11' },
    ]);
    const found = await nonObjectIdSample(db, 'things');
    expect(found.map((f) => f.type).sort()).to.deep.equal([
      'number',
      'string',
      'string',
    ]);
    expect(
      found.some((f) => f.type === 'string' && f.value === 'a'.repeat(24)),
    ).to.equal(true);
    await c.deleteMany({ _id: { $type: 'string' } as never });
    await c.deleteMany({ _id: 42 });
    expect(await nonObjectIdSample(db, 'things')).to.deep.equal([]);
  });
});
