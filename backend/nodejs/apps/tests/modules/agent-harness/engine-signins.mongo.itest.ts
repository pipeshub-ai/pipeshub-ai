/**
 * SIGNIN on MongoDB (ES-M1..ES-M4): a person's record and the org's switch in
 * `agentEngineSignIns`, never on the user document. Run as
 * execution-store.mongo.itest.ts says.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { Types } from 'mongoose';
import { EngineSignInModel } from '../../../src/libs/repositories/agent-harness/engine-signin/schema/engine-signin.schema';
import { MongoEngineSignIns } from '../../../src/libs/repositories/agent-harness/engine-signin/mongo';
import { Users } from '../../../src/modules/user_management/schema/users.schema';
import { connectHarnessMongo, disconnectHarnessMongo } from './support/harness-mongo';
import { describeEngineSignInStoreContract } from './support/engine-signin-store-contract';

async function user(orgId: string, kind = 'human'): Promise<string> {
  const id = new Types.ObjectId();
  await Users.collection.insertOne({
    _id: id,
    slug: `user-${id.toHexString()}`,
    orgId: new Types.ObjectId(orgId),
    email: `${id.toHexString()}@example.com`,
    kind,
    isDeleted: false,
  });
  return id.toHexString();
}

describe('engine sign-ins on MongoDB (SIGNIN)', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);

  describeEngineSignInStoreContract('MongoEngineSignIns', async () => {
    const orgId = new Types.ObjectId().toHexString();
    const other = new Types.ObjectId().toHexString();
    return {
      store: new MongoEngineSignIns(),
      orgId,
      users: [await user(orgId), await user(orgId)],
      serviceAccount: await user(orgId, 'service'),
      stranger: { orgId: other, userId: await user(other) },
    };
  });

  it('ES-M1: the record is never on the user document, and a deleted user is not found', async () => {
    const store = new MongoEngineSignIns();
    const orgId = new Types.ObjectId().toHexString();
    const me = await user(orgId);
    await store.write(orgId, me, { enabled: true, email: 'ada@example.com', plan: 'team' });
    const userDoc = await Users.collection.findOne({ _id: new Types.ObjectId(me) });
    expect(JSON.stringify(userDoc)).not.to.include('ada@example.com');
    expect(await EngineSignInModel.countDocuments({ orgId })).to.equal(1);
    await Users.collection.updateOne({ _id: new Types.ObjectId(me) }, { $set: { isDeleted: true } });
    expect(await store.read(orgId, me)).to.equal(null);
  });
});
