import type { Tx } from '../../../src/libs/repositories/uow';
/**
 * CLEANUP-G2 by behaviour, on a MongoDB replica
 * set (and Redis when REDIS_IT_URL is set), with the folder broker behind the
 * production HTTP client: a deleted user's private conversations are erased;
 * one shared with others moves to the org's earliest active admin, with the
 * move in the audit chain; the account, re-invited, starts empty. The job
 * store's own contract runs here too.
 * Run as execution-store.mongo.itest.ts says.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import mongoose, { Model, Types } from 'mongoose';
import { randomUUID } from 'node:crypto';
import { connectHarnessMongo, disconnectHarnessMongo, registerHarnessModels, resetHarnessMongo } from './support/harness-mongo';
import { redis, Seeded, world } from './support/session-erasure-world';
import { FolderSandboxBroker } from './support/folder-sandbox-broker';
import { composedWorker, drain, expectErasedByCensus, Rig, seeded } from './support/erasure-census-rig';
import { MemoryKv, platformCipher, KEYS } from './support/model-accounts';
import { aSchedule } from './support/schedules';
import { describePrincipalErasureJobsContract } from './support/principal-erasure-jobs-contract';
import { MongoPrincipalErasureJobs } from '../../../src/libs/repositories/agent-harness/principal-erasure/mongo';
import { AgentPrincipalErasureModel } from '../../../src/libs/repositories/agent-harness/principal-erasure/schema/principal-erasure.schema';
import { MongoScheduleStore } from '../../../src/libs/repositories/agent-harness/schedules/mongo';
import { AgentScheduleCounterModel, AgentScheduleFiringModel, AgentScheduleModel } from '../../../src/libs/repositories/agent-harness/schedules/schema/schedules.schema';
import { ModelAccountVault } from '../../../src/modules/agent-harness/adapters/configuration/model-account-vault';
import { inMongoDurableTransaction } from '../../../src/libs/repositories/agent-harness/durable-transaction/mongo';
import { auditLinksIdle } from '../../../src/libs/repositories/agent-harness/audit/mongo';
import { PrincipalErasureRunner } from '../../../src/modules/agent-harness/application/principal-erasure';
import { SessionParticipantJournal } from '../../../src/modules/agent-harness/application/session-participant-journal';
import { ChatSession } from '../../../src/modules/enterprise_search/schema/chat.session.schema';
import { ChatSessionMessage } from '../../../src/modules/enterprise_search/schema/chat.session.message.schema';
import { PrincipalDeletionService } from '../../../src/modules/user_management/services/principal-deletion.service';
import { Users } from '../../../src/modules/user_management/schema/users.schema';
import {
  ControlPlaneUserEraser,
  createBindingService,
  GrantAdmin,
  InstallationAdmin,
  MongoBindingStore,
  MongoControlPlaneTransaction,
  MongoGrantStore,
  MongoInstallationStore,
} from '../../../src/modules/control-plane';
import { ChainConfigAuditLog } from '../../../src/modules/agent-harness/adapters/chain-config-audit-log';
import { MongoAuditChainStore } from '../../../src/libs/repositories/agent-harness/audit/mongo';
import { DURABLE_TRANSACTION_OPTIONS } from '../../../src/libs/repositories/agent-harness/durable-transaction/mongo';
import { InMemoryChangeOutbox } from '../control-plane/support/in-memory';
import { BINDING_MIGRATION } from '../../../src/libs/repositories/control-plane/migrations/mongo';
import { anInstallation, instId, storeChange } from '../control-plane/support/cp-objects';

registerHarnessModels([
  AgentPrincipalErasureModel,
  AgentScheduleModel,
  AgentScheduleFiringModel,
  AgentScheduleCounterModel,
] as unknown as Array<Model<never>>);

/** Collections of the platform, not the harness: the org's and its people's records, and the entity event telling the platform. */

interface OrgWorld {
  orgId: string;
  adminId: string;
  memberId: string;
  sessions: Seeded[];
  vault: ModelAccountVault;
}

async function seedOrg(rig: Rig, orgId: string): Promise<OrgWorld> {
  const adminId = new Types.ObjectId().toHexString();
  const memberId = new Types.ObjectId().toHexString();
  await mongoose.connection.collection('org').insertOne({ _id: new Types.ObjectId(orgId), slug: orgId, registeredName: `org ${orgId}`, isDeleted: false } as never);
  await mongoose.connection.collection('users').insertMany([
    { _id: new Types.ObjectId(adminId), orgId: new Types.ObjectId(orgId), slug: adminId, email: `${adminId}@example.com`, role: 'admin', isDeleted: false, createdAt: new Date(1) },
    { _id: new Types.ObjectId(memberId), orgId: new Types.ObjectId(orgId), slug: memberId, email: `${memberId}@example.com`, role: 'member', isDeleted: false, createdAt: new Date(2) },
  ] as never[]);
  const tenant = { orgId, actorId: memberId };
  const sessions = [await seeded(rig, false, tenant), await seeded(rig, false, tenant)];
  const vault = new ModelAccountVault(new MemoryKv(), platformCipher);
  const write = { kind: 'user_api_key' as const, provider: 'openAI', label: null, material: { provider: 'openAI', apiKey: KEYS.openAI }, hint: KEYS.openAI };
  await vault.put(orgId, { kind: 'user', userId: memberId }, 'openAI', write, memberId);
  await new MongoScheduleStore().create(aSchedule({ id: `sch-${randomUUID()}`, orgId, actorId: memberId }), { perActor: 50, perOrg: 500 });
  // The org's control-plane settings, its data key, and a spend counter the worker keeps.
  await mongoose.connection.collection('cpGrants').insertOne({ _id: `${orgId}:agent:a1`, orgId, subject: 'agent:a1', entries: [], revision: 1 } as never);
  await mongoose.connection.collection('cpOrgHeads').insertOne({ _id: orgId, revision: 1 } as never);
  await mongoose.connection.collection('agentHarnessDataKeys').insertOne({ _id: randomUUID(), orgId, version: 1, kekId: 'test', wrapped: 'x', createdAt: 1, seals: 0 } as never);
  if (redis !== null) {
    await redis.getClient().hset(`${redis.keyNamespace ? `${redis.keyNamespace}:` : ''}harness:spend:{${orgId}:${randomUUID()}}`, 'usd', '1');
  }
  return { orgId, adminId, memberId, sessions, vault };
}

describe('PrincipalErasureJobStore on a MongoDB replica set', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  describePrincipalErasureJobsContract<Tx>('mongo replica set', async () => {
    await resetHarnessMongo();
    return {
      jobs: new MongoPrincipalErasureJobs(),
      transact: (_orgId, work) => inMongoDurableTransaction(work),
    };
  });
});

describe('CLEANUP-G2: user deletion on a MongoDB replica set', function () {
  this.timeout(240_000);
  let rig: Rig;

  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(async () => {
    await resetHarnessMongo();
    for (const name of ['conversations', 'agentconversations', 'citations', 'org', 'users', 'cpGrants', 'cpOrgHeads', 'notifications', 'outbox_events', 'agentHarnessDataKeys']) {
      await mongoose.connection.collection(name).deleteMany({});
    }
    const broker = new FolderSandboxBroker();
    rig = { w: world(), broker, endpoint: await broker.start() };
  });
  afterEach(async () => {
    await rig.broker.stop();
  });

  const runner = () =>
    new PrincipalErasureRunner<Tx>({
      jobs: new MongoPrincipalErasureJobs(),
      orgSteps: null,
      recorder: new SessionParticipantJournal(rig.w.journal, rig.w.journals),
      unsent: null,
      transaction: inMongoDurableTransaction,
    });

  it("CLEANUP-G2: a deleted user's private conversation is erased; the shared one moves to the earliest admin with the move audited; re-invited, the account starts empty", async () => {
    const o = await seedOrg(rig, new Types.ObjectId().toHexString());
    const [privateOne, sharedOne] = o.sessions as [Seeded, Seeded];
    await ChatSession.updateOne({ _id: privateOne.sessionId }, { $set: { isShared: false, sharedWith: [] } });
    const messagesBefore = await ChatSessionMessage.countDocuments({ sessionId: new Types.ObjectId(sharedOne.sessionId) });

    const handed = await new PrincipalDeletionService().handOverUser(o.orgId, o.memberId, o.adminId);
    expect(handed).to.deep.equal({ erased: 1, transferred: 1 });
    // The account's own stores, as deleteUser's hooks erase them.
    await o.vault.eraseUser(o.orgId, o.memberId);
    await new MongoScheduleStore().eraseUser(o.orgId, o.memberId);

    expect(await drain(rig, composedWorker(rig), privateOne)).to.equal('completed');
    await runner().tick(Date.now());
    await expectErasedByCensus(rig, privateOne);
    expect(await AgentPrincipalErasureModel.countDocuments({})).to.equal(0);

    const moved = await ChatSession.findById(sharedOne.sessionId).lean();
    expect(String(moved?.userId)).to.equal(o.adminId);
    expect(moved?.isDeleted).to.not.equal(true);
    expect(await ChatSessionMessage.countDocuments({ sessionId: new Types.ObjectId(sharedOne.sessionId) })).to.equal(messagesBefore);
    await auditLinksIdle();
    const audited = await mongoose.connection.collection('agentAuditRecords').find({ orgId: o.orgId, sessionId: sharedOne.sessionId, kind: 'session.owner_changed' }).toArray();
    expect(audited).to.have.length(1);
    expect(JSON.stringify(audited[0])).to.include(`p_${o.adminId}`).and.to.include(`p_${o.memberId}`);

    // Re-invited: the account sees no conversation, schedule or model account of before.
    await Users.updateOne({ _id: o.memberId }, { $set: { isDeleted: false } });
    expect(await ChatSession.countDocuments({ orgId: new Types.ObjectId(o.orgId), userId: new Types.ObjectId(o.memberId), isDeleted: { $ne: true } })).to.equal(0);
    expect(await AgentScheduleModel.countDocuments({ orgId: o.orgId, actorId: o.memberId })).to.equal(0);
    expect(await o.vault.list(o.orgId, { kind: 'user', userId: o.memberId })).to.deep.equal([]);
  });
});

describe('CLEANUP-G2c: a deleted user\'s control-plane data on a MongoDB replica set', function () {
  this.timeout(240_000);
  let rig: Rig;

  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(async () => {
    await resetHarnessMongo();
    for (const name of ['conversations', 'agentconversations', 'citations', 'org', 'users', 'cpGrants', 'cpOrgHeads', 'cpInstallations', 'cpInstallationHistory', 'cpBindings', 'notifications', 'outbox_events', 'agentHarnessDataKeys']) {
      await mongoose.connection.collection(name).deleteMany({});
    }
    const broker = new FolderSandboxBroker();
    rig = { w: world(), broker, endpoint: await broker.start() };
  });
  afterEach(async () => {
    await rig.broker.stop();
  });

  it("the user job removes their installations, revokes their connections and empties their grant through audited writes; another user's and the org's stay; re-invited, they own none", async () => {
    const o = await seedOrg(rig, new Types.ObjectId().toHexString());
    // The binding store reads through the indexes its migration builds at start.
    await BINDING_MIGRATION.up(mongoose.connection.db!);
    const transaction = new MongoControlPlaneTransaction({
      audit: new ChainConfigAuditLog(new MongoAuditChainStore()),
      outbox: new InMemoryChangeOutbox(),
      transaction: DURABLE_TRANSACTION_OPTIONS,
    });
    const installationStore = new MongoInstallationStore();
    const grantStore = new MongoGrantStore();
    const kv = new MemoryKv();
    const bindings = createBindingService({ transaction, kv, cipher: () => platformCipher, bindings: new MongoBindingStore() });
    const installations = new InstallationAdmin({
      catalog: {} as never,
      installations: installationStore,
      grants: grantStore,
      transaction,
      validator: () => null,
      newId: () => 'unused',
      now: () => Date.now(),
    });
    const grants = new GrantAdmin({ grants: grantStore, installations: installationStore, transaction });
    const member = `user:${o.memberId}` as const;
    const admin = `user:${o.adminId}` as const;
    const install = async (n: number, owner: 'org' | `user:${string}`) => {
      await transaction.run({ orgId: o.orgId, actor: 'system' }, async (tx) => {
        await installationStore.create(tx, anInstallation({ id: instId(n), orgId: o.orgId, owner, visibility: owner === 'org' ? 'org' : 'owner' }));
        return { result: null, change: storeChange('installation', instId(n)) };
      });
    };
    await install(1, member);
    await install(2, admin);
    await install(3, 'org');
    const connect = (installation: string, owner: `user:${string}`) =>
      bindings.importRecord(
        { orgId: o.orgId, actor: owner },
        { installation, owner, mode: 'api_key', kind: 'toolset', provider: null, material: { kind: 'record', value: { apiToken: `token-${owner}` } }, source: null },
      );
    await connect(instId(1), member);
    await connect(instId(3), member);
    await connect(instId(3), admin);
    const entry = { installation: instId(3), tools: null, required: false };
    await grants.put({ orgId: o.orgId, actor: 'system' }, member, 0, [entry]);
    await grants.put({ orgId: o.orgId, actor: 'system' }, admin, 0, [entry]);

    await new PrincipalDeletionService().handOverUser(o.orgId, o.memberId, o.adminId);
    await new PrincipalErasureRunner<Tx>({
      jobs: new MongoPrincipalErasureJobs(),
      orgSteps: null,
      recorder: new SessionParticipantJournal(rig.w.journal, rig.w.journals),
      unsent: null,
      controlPlane: new ControlPlaneUserEraser({ installations, bindings, grants }),
      transaction: inMongoDurableTransaction,
    }).tick(Date.now());
    expect(await AgentPrincipalErasureModel.countDocuments({})).to.equal(0);

    expect((await installationStore.get(o.orgId, instId(1)))?.status).to.equal('removed');
    expect((await installationStore.get(o.orgId, instId(2)))?.status).to.not.equal('removed');
    expect((await installationStore.get(o.orgId, instId(3)))?.status).to.not.equal('removed');
    const mine = await mongoose.connection.collection('cpBindings').find({ orgId: o.orgId, owner: member }).toArray();
    // Revoked rows stay as history; the secrets they named are tombstoned in the vault (below).
    expect(mine.map((b) => b['status'])).to.deep.equal(['revoked', 'revoked']);
    expect((await bindings.list(o.orgId, admin)).map((b) => b.status)).to.deep.equal(['active']);
    expect(await kv.dump()).to.not.include(`token-${member}`);
    expect((await grants.get(o.orgId, member)).entries).to.deep.equal([]);
    expect((await grants.get(o.orgId, admin)).entries).to.have.length(1);
    // The removal is an audited revision of the installation, kept in its history.
    const history = await installationStore.history(o.orgId, instId(1), 10);
    expect(JSON.stringify(history)).to.include('"status":"removed"').and.to.include('"updatedBy":"system"');

    // Re-invited: nothing of before is theirs.
    await Users.updateOne({ _id: o.memberId }, { $set: { isDeleted: false } });
    const live = (await installations.list(o.orgId, { owner: member, limit: 10 }, () => true)).items;
    expect(live).to.deep.equal([]);
    expect((await bindings.list(o.orgId, member)).filter((b) => b.status !== 'revoked')).to.deep.equal([]);
  });
});

