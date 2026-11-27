/**
 * Org, project, connectors config, Slack thread, delegation and org metrics
 * repositories against a real MongoDB, as a replica set and as a single server.
 *
 *   MONGO_IT_URI=mongodb://localhost:27017/?replicaSet=rs0 REPLICA_SET_AVAILABLE=true
 *   MONGO_IT_URI=mongodb://localhost:27017                 REPLICA_SET_AVAILABLE=false
 */
import 'reflect-metadata';
import { randomUUID } from 'crypto';
import { expect } from 'chai';
import mongoose from 'mongoose';
import sinon from 'sinon';
import { MongoService } from '../../src/libs/services/mongo.service';
import { MongoUnitOfWork } from '../../src/libs/repositories/mongo';
import { MongoOrgRepository } from '../../src/libs/repositories/org/mongo';
import { MongoProjectRepository } from '../../src/libs/repositories/project/mongo';
import { MongoConnectorsConfigRepository } from '../../src/libs/repositories/connectors-config/mongo';
import { MongoSlackThreadRepository } from '../../src/libs/repositories/slack-thread/mongo';
import { MongoDelegationRepository } from '../../src/libs/repositories/delegation/mongo';
import { MongoOrgMetricsJobRepository } from '../../src/libs/repositories/org-metrics-job/mongo';
import { MongoUserRepository } from '../../src/libs/repositories/user/mongo';
import { MongoGroupRepository } from '../../src/libs/repositories/group/mongo';
import { MongoIdentityPort } from '../../src/libs/repositories/identity/mongo';
import { MongoOrgAuthConfigRepository } from '../../src/libs/repositories/org-auth-config/mongo';
import { Users } from '../../src/modules/user_management/schema/users.schema';
import { UserGroups, ACTIVE_GROUP_NAME_INDEX } from '../../src/modules/user_management/schema/userGroup.schema';
import { Org } from '../../src/modules/user_management/schema/org.schema';
import { OrgLogos } from '../../src/modules/user_management/schema/orgLogo.schema';
import { UserCredentials } from '../../src/modules/auth/schema/userCredentials.schema';
import { OrgAuthConfig } from '../../src/modules/auth/schema/orgAuthConfiguration.schema';
import { Project } from '../../src/modules/projects/schema/project.schema';
import { ConnectorsConfig } from '../../src/modules/configuration_manager/schema/connectors.schema';
import { SlackThread } from '../../src/libs/repositories/slack-thread/schema/slack-thread.schema';
import { ServiceAccountDelegation } from '../../src/modules/oauth_provider/schema/service-account-delegation.schema';
import { DelegationEvent } from '../../src/modules/oauth_provider/schema/delegation-event.schema';
import { Counter } from '../../src/libs/repositories/slug/mongo';
import { describeRestContract } from './contract/rest.contract';
import { Logger } from '../../src/libs/services/logger.service';

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} must be set for the MongoDB tests`);
  return value;
}

describe('Org, project and remaining repositories against a real MongoDB', function () {
  this.timeout(120000);
  const uri = requireEnv('MONGO_IT_URI');
  const rsFlag = requireEnv('REPLICA_SET_AVAILABLE');
  if (rsFlag !== 'true' && rsFlag !== 'false') {
    throw new Error(`REPLICA_SET_AVAILABLE must be true or false, got '${rsFlag}'`);
  }
  const rsAvailable = rsFlag === 'true';
  const dbName = `pipeshub_it_${randomUUID().slice(0, 8)}`;
  let mongo: MongoService;

  before(async () => {
    mongo = new MongoService({ uri, db: dbName });
    await mongo.initialize();
    for (const m of [Users, UserGroups, Org, OrgLogos, UserCredentials, OrgAuthConfig, Project, ConnectorsConfig, SlackThread, ServiceAccountDelegation, DelegationEvent, Counter]) {
      await m.createCollection().catch(() => undefined);
      await m.init();
    }
    await UserGroups.collection.createIndex(ACTIVE_GROUP_NAME_INDEX.keys, ACTIVE_GROUP_NAME_INDEX.options);
  });

  after(async () => {
    if (mongo?.isConnected()) {
      await mongo.getConnection().dropDatabase();
      await mongo.destroy();
    }
  });

  describeRestContract({
    orgs: new MongoOrgRepository(),
    projects: new MongoProjectRepository(),
    connectors: new MongoConnectorsConfigRepository(),
    slack: new MongoSlackThreadRepository(),
    delegation: new MongoDelegationRepository(),
    metrics: new MongoOrgMetricsJobRepository(),
    users: new MongoUserRepository(),
    groups: new MongoGroupRepository(),
    credentials: new MongoIdentityPort(),
    orgAuth: new MongoOrgAuthConfigRepository(),
    uow: new MongoUnitOfWork(rsAvailable),
    transactional: rsAvailable,
    newId: () => new mongoose.Types.ObjectId().toString(),
    asNewOrg: (_ids, fn) => fn(),
    failCredentialWrites: async () => {
      const stub = sinon.stub(UserCredentials.prototype, 'save').rejects(new Error('credential write refused'));
      return async () => stub.restore();
    },
  });

  describe('organisation create on a failed write', () => {
    const orgs = new MongoOrgRepository();
    const uow = new MongoUnitOfWork(rsAvailable);
    const create = (ids: { orgId: string; adminUserId: string }) =>
      uow.run((tx) =>
        orgs.createWithAdmin(
          {
            ids,
            org: { registeredName: 'Lost', domain: 'lost.example.com', contactEmail: `${randomUUID()}@lost.example.com`, accountType: 'business' },
            admin: { fullName: 'Lost', email: `${randomUUID()}@lost.example.com` },
            passwordHash: 'hash-value',
            ipAddress: '10.0.0.3',
          },
          tx,
        ),
      );
    const leftOf = async (ids: { orgId: string; adminUserId: string }) => ({
      orgs: await Org.countDocuments({ _id: ids.orgId }),
      users: await Users.countDocuments({ _id: ids.adminUserId }),
      groups: await UserGroups.countDocuments({ orgId: ids.orgId }),
      authConfigs: await OrgAuthConfig.countDocuments({ orgId: ids.orgId }),
      credentials: await UserCredentials.countDocuments({ userId: ids.adminUserId }),
    });
    const none = { orgs: 0, users: 0, groups: 0, authConfigs: 0, credentials: 0 };
    afterEach(() => sinon.restore());

    it('removes an organisation whose insert was applied but whose reply was lost', async () => {
      const save = Org.prototype.save;
      sinon.stub(Org.prototype, 'save').callsFake(async function (this: InstanceType<typeof Org>, ...args: unknown[]) {
        await (save as (...a: unknown[]) => Promise<unknown>).apply(this, args);
        throw new Error('reply lost');
      });
      const ids = orgs.allocateIds();
      let message = '';
      await create(ids).catch((e: Error) => (message = e.message));
      expect(message).to.equal('reply lost');
      expect(await leftOf(ids)).to.deep.equal(none);
    });

    // The driver can reject a write the server still applies later, after the cleanup ran: that write must not make the
    // organisation live without its administrator. The write is held until `release()`, which a test calls once the create
    // (and its cleanup) has settled, so the order never depends on timing; `release()` is the write's own result, and a
    // database error in it fails the test.
    const lateWrite = () => {
      let held: (() => Promise<unknown>) | undefined;
      return {
        fake: (real: () => Promise<unknown>): Promise<never> => {
          held = real;
          return Promise.reject(new Error('reply lost'));
        },
        release: async (): Promise<unknown> => {
          if (!held) throw new Error('no write was held');
          try {
            return await held();
          } catch (e) {
            // In a transaction that was aborted the server never applies it; anything else is a real failure.
            if (rsAvailable && (e as Error).name === 'MongoExpiredSessionError') return { matchedCount: 0 };
            throw e;
          }
        },
      };
    };
    /** Org.updateOne whose first call (the activation) goes through `first`; later calls are the real ones. */
    const firstOrgUpdate = (first: (real: () => Promise<unknown>) => Promise<unknown>) => {
      const real = Org.updateOne.bind(Org) as (...a: unknown[]) => Promise<unknown>;
      let calls = 0;
      return sinon.stub(Org, 'updateOne').callsFake(((...args: unknown[]) =>
        (calls += 1) === 1 ? first(() => real(...args)) : real(...args)) as never);
    };

    it('an organisation insert that applies after the cleanup never shows a live organisation', async () => {
      const late = lateWrite();
      // The bytes already sent: the server inserts the document as it was when save was called.
      sinon.stub(Org.prototype, 'save').callsFake(async function (this: InstanceType<typeof Org>) {
        // The stub skips the save hook that numbers the slug; a unique one keeps the late insert from failing on the index.
        const sent = { ...this.toObject(), slug: `org-late-${randomUUID()}` };
        return late.fake(() => Org.collection.insertOne(sent));
      });
      const ids = orgs.allocateIds();
      let message = '';
      await create(ids).catch((e: Error) => (message = e.message));
      await late.release();
      expect(message).to.equal('reply lost');
      expect(await orgs.getLive(ids.orgId)).to.equal(null);
      expect({ ...(await leftOf(ids)), orgs: 0 }).to.deep.equal(none);
    });

    it('a half-made organisation left by a late insert is no organisation: setup still sees none and a retry succeeds', async () => {
      const before = { count: await orgs.count(), summary: await orgs.liveSummary() };
      const late = lateWrite();
      sinon.stub(Org.prototype, 'save').callsFake(async function (this: InstanceType<typeof Org>) {
        // The stub skips the save hook that numbers the slug; a unique one keeps the late insert from failing on the index.
        const sent = { ...this.toObject(), slug: `org-late-${randomUUID()}` };
        return late.fake(() => Org.collection.insertOne(sent));
      });
      const ids = orgs.allocateIds();
      await create(ids).catch(() => undefined);
      await late.release();
      sinon.restore();
      expect(await Org.countDocuments({ _id: ids.orgId }), 'the late insert left its hidden row').to.equal(1);
      expect(await orgs.count(), 'setup and signup count no organisation for it').to.equal(before.count);
      expect(await orgs.liveSummary()).to.deep.equal(before.summary);
      expect(await new MongoIdentityPort().orgCount(false)).to.equal(before.count);
      const retry = orgs.allocateIds();
      const created = await create(retry);
      expect(created.org.isDeleted).to.equal(false);
      expect((await orgs.getLive(retry.orgId))?.slug).to.match(/^org-\d+$/);
    });

    it('the final write that applies after the cleanup never shows a live organisation', async () => {
      // Every write of the create succeeds except the activation, which applies late.
      const late = lateWrite();
      firstOrgUpdate(late.fake);
      const ids = orgs.allocateIds();
      let message = '';
      await create(ids).catch((e: Error) => (message = e.message));
      const applied = (await late.release()) as { matchedCount: number };
      expect(message).to.equal('reply lost');
      expect(applied.matchedCount, 'the late activation found no organisation to make live').to.equal(0);
      expect(await orgs.getLive(ids.orgId)).to.equal(null);
      expect(await leftOf(ids)).to.deep.equal(none);
    });

    // The activation applied, its reply was lost, and the cleanup cannot delete the organisation row.
    const orgDeleteFails = () => sinon.stub(Org.prototype, 'deleteOne').rejects(new Error('connection lost'));

    it('an activation that applied but whose reply was lost never leaves a live organisation without its administrator', async () => {
      firstOrgUpdate(async (real) => {
        await real();
        throw new Error('reply lost');
      });
      orgDeleteFails();
      const before = await orgs.count();
      const ids = orgs.allocateIds();
      await create(ids).catch(() => undefined);
      expect(await orgs.getLive(ids.orgId)).to.equal(null);
      if (!rsAvailable) {
        expect(await leftOf(ids)).to.deep.equal({ ...none, orgs: 1 });
        expect(await orgs.count(), 'the row left behind is a failed create, not an organisation').to.equal(before);
      }
    });

    it('an activation still on its way after the cleanup hid the organisation again changes nothing', async () => {
      const late = lateWrite();
      firstOrgUpdate(late.fake);
      orgDeleteFails();
      const ids = orgs.allocateIds();
      await create(ids).catch(() => undefined);
      const applied = (await late.release()) as { matchedCount: number };
      expect(await orgs.getLive(ids.orgId)).to.equal(null);
      if (!rsAvailable) expect(applied.matchedCount, 'the cleanup moved the row on, so the late activation matches nothing').to.equal(0);
    });

    it('if the organisation cannot be hidden again, the cleanup removes nothing and says so', async () => {
      let calls = 0;
      const real = Org.updateOne.bind(Org) as (...a: unknown[]) => Promise<unknown>;
      sinon.stub(Org, 'updateOne').callsFake(((...args: unknown[]) => {
        calls += 1;
        if (calls === 1) return real(...args).then(() => Promise.reject(new Error('reply lost')));
        return Promise.reject(new Error('connection lost'));
      }) as never);
      const warn = sinon.stub(Logger.getInstance(), 'warn');
      const ids = orgs.allocateIds();
      let message = '';
      await create(ids).catch((e: Error) => (message = e.message));
      expect(message).to.equal('reply lost');
      if (!rsAvailable) {
        // Live and whole: better than live without its administrator.
        expect(await leftOf(ids)).to.deep.equal({ orgs: 1, users: 1, groups: 2, authConfigs: 1, credentials: 1 });
        expect(warn.getCalls().map((c) => c.args[0])).to.include('Organisation create failed and the organisation could not be hidden again; its rows are left as they are');
      }
    });

    it('logs each row it could not remove, by collection and id, and still throws the original error', async () => {
      sinon.stub(UserCredentials.prototype, 'save').rejects(new Error('credential write refused'));
      sinon.stub(Users.prototype, 'deleteOne').rejects(new Error('connection lost'));
      const warn = sinon.stub(Logger.getInstance(), 'warn');
      const ids = orgs.allocateIds();
      let message = '';
      await create(ids).catch((e: Error) => (message = e.message));
      expect(message).to.equal('credential write refused');
      const calls = warn.getCalls().filter((c) => c.args[0].includes('could not be removed'));
      if (rsAvailable) {
        // The transaction undoes the writes; nothing is deleted by hand.
        expect(calls).to.have.length(0);
        expect(await leftOf(ids)).to.deep.equal(none);
      } else {
        expect(calls.map((c) => c.args[1])).to.deep.equal([{ collection: 'users', id: ids.adminUserId, error: 'connection lost' }]);
        expect(await leftOf(ids)).to.deep.equal({ ...none, users: 1 });
      }
    });
  });

  it('lists a legacy project-wide pin first for the owner only', async () => {
    const repo = new MongoProjectRepository();
    const org = new mongoose.Types.ObjectId().toHexString();
    const owner = new mongoose.Types.ObjectId().toHexString();
    const member = new mongoose.Types.ObjectId().toHexString();
    const base = { tools: [] as string[] };
    const old = await repo.create(org, owner, { ...base, name: 'Old' });
    await repo.create(org, owner, { ...base, name: 'New' });
    await repo.upsertMembers(old._id, [{ principalId: member, principalType: 'user', role: 'viewer' }], owner);
    await Project.collection.updateOne({ _id: new mongoose.Types.ObjectId(old._id) }, { $set: { isPinned: true, lastActivityAt: 1 } });
    const q = { scope: 'all' as const, includeArchived: false, teamIds: [], skip: 0, limit: 10 };

    expect((await repo.list(org, owner, q)).rows.map((r) => r.name)).to.deep.equal(['Old', 'New']);
    expect((await repo.list(org, member, q)).rows.map((r) => r.name)).to.deep.equal(['Old']);
    // The owner unpinning clears it; a viewer's unpin leaves it.
    await repo.setPinned(old._id, member, false, false);
    expect((await repo.getLive(old._id))?.isPinned).to.equal(true);
    await repo.setPinned(old._id, owner, false, true);
    expect((await repo.list(org, owner, q)).rows.map((r) => r.name)).to.deep.equal(['New', 'Old']);
  });
});
