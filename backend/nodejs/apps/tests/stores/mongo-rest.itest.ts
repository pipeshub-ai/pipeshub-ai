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
    // organisation live without its administrator.
    const appliesLate = (real: (...a: unknown[]) => Promise<unknown>) =>
      async function (this: unknown, ...args: unknown[]) {
        setTimeout(() => void real.apply(this, args).catch(() => undefined), 300);
        throw new Error('reply lost');
      };
    const settle = () => new Promise((r) => setTimeout(r, 600));

    it('an organisation insert that applies after the cleanup never shows a live organisation', async () => {
      // The bytes already sent: the server inserts the document as it was when save was called.
      sinon.stub(Org.prototype, 'save').callsFake(async function (this: InstanceType<typeof Org>) {
        const sent = this.toObject();
        return appliesLate(() => Org.collection.insertOne(sent))();
      });
      const ids = orgs.allocateIds();
      let message = '';
      await create(ids).catch((e: Error) => (message = e.message));
      await settle();
      expect(message).to.equal('reply lost');
      expect(await orgs.getLive(ids.orgId)).to.equal(null);
      expect({ ...(await leftOf(ids)), orgs: 0 }).to.deep.equal(none);
    });

    it('the final write that applies after the cleanup never shows a live organisation', async () => {
      // Every write of the create succeeds except the last one, which applies late.
      const realUpdate = Org.updateOne.bind(Org);
      sinon.stub(Org, 'updateOne').callsFake(appliesLate(realUpdate as never) as never);
      const ids = orgs.allocateIds();
      let message = '';
      await create(ids).catch((e: Error) => (message = e.message));
      await settle();
      expect(message).to.equal('reply lost');
      expect(await orgs.getLive(ids.orgId)).to.equal(null);
      expect(await leftOf(ids)).to.deep.equal(none);
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
