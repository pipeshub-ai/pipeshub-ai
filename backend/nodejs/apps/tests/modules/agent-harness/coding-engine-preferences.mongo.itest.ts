/**
 * ENGPICK on MongoDB: the coding-engine preferences contract against the user
 * and org documents. Run as execution-store.mongo.itest.ts says.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { Types } from 'mongoose';
import { MongoCodingEnginePreferences } from '../../../src/libs/repositories/agent-harness/coding-engine-preferences/mongo';
import { HarnessNotFoundError } from '../../../src/modules/agent-harness/domain/errors';
import { Org } from '../../../src/modules/user_management/schema/org.schema';
import { Users } from '../../../src/modules/user_management/schema/users.schema';
import { ChatSession } from '../../../src/modules/enterprise_search/schema/chat.session.schema';
import { describeCodingEnginePreferencesContract } from './support/coding-engine-preferences-contract';
import { connectHarnessMongo, disconnectHarnessMongo } from './support/harness-mongo';

async function org(): Promise<string> {
  const id = new Types.ObjectId();
  await Org.collection.insertOne({
    _id: id,
    slug: `org-${id.toHexString()}`,
    domain: 'example.com',
    contactEmail: 'admin@example.com',
    accountType: 'business',
    registeredName: 'Example',
    isDeleted: false,
  });
  return id.toHexString();
}

async function user(orgId: string): Promise<string> {
  const id = new Types.ObjectId();
  await Users.collection.insertOne({
    _id: id,
    slug: `user-${id.toHexString()}`,
    orgId: new Types.ObjectId(orgId),
    email: `${id.toHexString()}@example.com`,
    isDeleted: false,
  });
  return id.toHexString();
}

async function session(orgId: string, userId: string): Promise<string> {
  const id = new Types.ObjectId();
  await ChatSession.collection.insertOne({
    _id: id,
    orgId: new Types.ObjectId(orgId),
    userId: new Types.ObjectId(userId),
    isDeleted: false,
  });
  return id.toHexString();
}

describe('coding-engine preferences on MongoDB (ENGPICK)', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);

  describeCodingEnginePreferencesContract('user and org documents', async () => {
    const orgId = await org();
    const other = await org();
    const users: [string, string] = [await user(orgId), await user(orgId)];
    return {
      store: new MongoCodingEnginePreferences(),
      orgId,
      users,
      stranger: { orgId: other, userId: await user(other) },
      session: () => session(orgId, users[0]),
    };
  });

  it('CP-M1: a user of another org, or a deleted user, is never written', async () => {
    const store = new MongoCodingEnginePreferences();
    const orgId = await org();
    const outsider = await user(await org());
    const refused = await store.writeUser(orgId, outsider, 'codex').catch((e: unknown) => e);
    expect(refused).to.be.instanceOf(HarnessNotFoundError);
    const gone = await user(orgId);
    await Users.collection.updateOne({ _id: new Types.ObjectId(gone) }, { $set: { isDeleted: true } });
    expect(await store.writeUser(orgId, gone, 'codex').catch((e: unknown) => e)).to.be.instanceOf(HarnessNotFoundError);
    expect((await store.read(orgId, gone)).user).to.equal(null);
  });

  it('CP-M2 (WP114-FU-ERASE): an already deleted person and conversation are erased all the same', async () => {
    const store = new MongoCodingEnginePreferences();
    const orgId = await org();
    const me = await user(orgId);
    const chat = await session(orgId, me);
    await store.writeUser(orgId, me, 'codex');
    await store.writeSession(orgId, chat, 'codex');
    await Users.collection.updateOne({ _id: new Types.ObjectId(me) }, { $set: { isDeleted: true } });
    await ChatSession.collection.updateOne({ _id: new Types.ObjectId(chat) }, { $set: { isDeleted: true } });
    expect(await store.eraseUser(orgId, me)).to.equal(1);
    expect(await store.eraseSession(orgId, chat)).to.equal(1);
    const left = await Users.collection.findOne({ _id: new Types.ObjectId(me) });
    expect(left).not.to.have.property('codingEngine');
    const chatLeft = await ChatSession.collection.findOne({ _id: new Types.ObjectId(chat) });
    expect(chatLeft).not.to.have.property('codingEngine');
  });
});
