/**
 * Identity repositories against a real MongoDB, as a replica set and as a single server.
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
import { MongoUserRepository } from '../../src/libs/repositories/user/mongo';
import { MongoGroupRepository } from '../../src/libs/repositories/group/mongo';
import { MongoActivityRepository } from '../../src/libs/repositories/activity/mongo';
import { MongoOrgAuthConfigRepository } from '../../src/libs/repositories/org-auth-config/mongo';
import { MongoResetLinkRepository } from '../../src/libs/repositories/reset-link/mongo';
import { MongoPictureRepository } from '../../src/libs/repositories/picture/mongo';
import { MongoIdentityPort } from '../../src/libs/repositories/identity/mongo';
import { Users } from '../../src/modules/user_management/schema/users.schema';
import { UserGroups, ACTIVE_GROUP_NAME_INDEX } from '../../src/modules/user_management/schema/userGroup.schema';
import { Org } from '../../src/modules/user_management/schema/org.schema';
import { UserCredentials } from '../../src/modules/auth/schema/userCredentials.schema';
import { UserActivities } from '../../src/modules/auth/schema/userActivities.schema';
import { OrgAuthConfig } from '../../src/modules/auth/schema/orgAuthConfiguration.schema';
import { UsedPasswordResetLink } from '../../src/modules/auth/schema/usedPasswordResetLink.schema';
import { UserDisplayPicture } from '../../src/modules/user_management/schema/userDp.schema';
import { describeIdentityContract } from './contract/identity.contract';

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} must be set for the MongoDB tests`);
  return value;
}

describe('Identity repositories against a real MongoDB', function () {
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
    // Collections must exist before a transaction touches them; indexes before the tests lean on them.
    for (const m of [Users, UserGroups, Org, UserCredentials, UserActivities, OrgAuthConfig, UsedPasswordResetLink, UserDisplayPicture]) {
      await m.createCollection().catch(() => undefined);
      await m.init();
    }
    // Built by a migration in production, so the suite builds it the same way.
    await UserGroups.collection.createIndex(ACTIVE_GROUP_NAME_INDEX.keys, ACTIVE_GROUP_NAME_INDEX.options);
  });

  after(async () => {
    if (mongo?.isConnected()) {
      await mongo.getConnection().dropDatabase();
      await mongo.destroy();
    }
  });

  describeIdentityContract({
    users: new MongoUserRepository(),
    groups: new MongoGroupRepository(),
    credentials: new MongoIdentityPort(),
    activities: new MongoActivityRepository(),
    orgAuth: new MongoOrgAuthConfigRepository(),
    resetLinks: new MongoResetLinkRepository(),
    pictures: new MongoPictureRepository(),
    identity: new MongoIdentityPort(),
    uow: new MongoUnitOfWork(rsAvailable),
    transactional: rsAvailable,
    newOrg: async () => {
      const org = await Org.create({
        registeredName: 'Contract Org',
        domain: `${randomUUID().slice(0, 8)}.example.com`,
        contactEmail: `owner-${randomUUID().slice(0, 8)}@example.com`,
        accountType: 'business',
        onBoardingStatus: 'configured',
      });
      return String(org._id);
    },
    newId: () => new mongoose.Types.ObjectId().toString(),
    clearOrgAuth: async () => {
      await OrgAuthConfig.deleteMany({});
    },
  });

  it('oldestOrgId reads one organisation, not all of them', async () => {
    await Org.create({
      registeredName: 'Oldest Org',
      domain: `${randomUUID().slice(0, 8)}.example.com`,
      contactEmail: `owner-${randomUUID().slice(0, 8)}@example.com`,
      accountType: 'business',
      onBoardingStatus: 'configured',
    });
    const find = sinon.spy(Org, 'find');
    const findOne = sinon.spy(Org, 'findOne');
    try {
      expect(await new MongoIdentityPort().oldestOrgId()).to.be.a('string');
      expect(find.called).to.equal(false);
      expect(findOne.calledOnce).to.equal(true);
    } finally {
      find.restore();
      findOne.restore();
    }
  });
});
