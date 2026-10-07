/**
 * The Node API's MongoDB code against a real MongoDB, as a replica set and as a
 * single server.
 *
 * Kubernetes (Helm) installs run MongoDB as a replica set and set
 * REPLICA_SET_AVAILABLE=true, which switches many writes onto multi-document
 * transactions. Docker Compose installs, and every other automated test, use a
 * single server with REPLICA_SET_AVAILABLE=false, so without this suite the
 * transaction branch never meets a real database. Unit tests mock mongoose.
 *
 *   MONGO_IT_URI=mongodb://localhost:27017/?replicaSet=rs0 REPLICA_SET_AVAILABLE=true
 *   MONGO_IT_URI=mongodb://localhost:27017                 REPLICA_SET_AVAILABLE=false
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { randomUUID } from 'crypto';
import mongoose from 'mongoose';
import { MongoService } from '../../src/libs/services/mongo.service';
import { Org } from '../../src/modules/user_management/schema/org.schema';
import { Users, type User } from '../../src/modules/user_management/schema/users.schema';
import {
  MAX_ORG_ADMINS,
  saveUserEnsuringAdminCap,
  saveUserEnsuringOrgRetainsAdmin,
} from '../../src/modules/user_management/services/user-admin.service';
import { BadRequestError } from '../../src/libs/errors/http.errors';

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`${name} must be set for the MongoDB tests`);
  }
  return value;
}

describe('MongoDB code paths against a real MongoDB', function () {
  this.timeout(60000);

  const uri = requireEnv('MONGO_IT_URI');
  const rsFlag = requireEnv('REPLICA_SET_AVAILABLE');
  if (rsFlag !== 'true' && rsFlag !== 'false') {
    throw new Error(`REPLICA_SET_AVAILABLE must be true or false, got '${rsFlag}'`);
  }
  const rsAvailable = rsFlag === 'true';
  const dbName = `pipeshub_it_${randomUUID().slice(0, 8)}`;
  let mongo: MongoService;

  async function newOrg(): Promise<string> {
    const org = await Org.create({
      domain: `${randomUUID().slice(0, 8)}.example.com`,
      contactEmail: `owner-${randomUUID().slice(0, 8)}@example.com`,
      accountType: 'business',
      registeredName: 'Integration Test Org',
    });
    return String(org._id);
  }

  async function newUser(orgId: string, role: 'admin' | 'member'): Promise<User> {
    return Users.create({
      orgId,
      email: `user-${randomUUID().slice(0, 8)}@example.com`,
      fullName: 'Test User',
      role,
    });
  }

  async function adminCount(orgId: string): Promise<number> {
    return Users.countDocuments({ orgId, role: 'admin', isDeleted: { $ne: true } });
  }

  /** Runs every save at once and returns how many succeeded and how many were refused. */
  async function raceSaves(saves: Array<() => Promise<void>>): Promise<{ ok: number; refused: number }> {
    const results = await Promise.allSettled(saves.map((save) => save()));
    for (const result of results) {
      if (result.status === 'rejected' && !(result.reason instanceof BadRequestError)) {
        throw result.reason;
      }
    }
    return {
      ok: results.filter((r) => r.status === 'fulfilled').length,
      refused: results.filter((r) => r.status === 'rejected').length,
    };
  }

  before(async () => {
    mongo = new MongoService({ uri, db: dbName });
    await mongo.initialize();
  });

  after(async () => {
    if (mongo?.isConnected()) {
      await mongo.getConnection().dropDatabase();
      await mongo.destroy();
    }
  });

  it('connects through the service and creates the collections transactions rely on', async () => {
    expect(await mongo.healthCheck()).to.equal(true);
    const names = (await mongo.getConnection().db!.listCollections().toArray()).map((c) => c.name);
    expect(names).to.include.members(['users', 'org', 'chatSessions', 'projects', 'documents']);
  });

  it('matches REPLICA_SET_AVAILABLE to what the server really is', async () => {
    const hello = await mongo.getConnection().db!.admin().command({ hello: 1 });
    expect(Boolean(hello.setName), `server replica set: ${hello.setName ?? 'none'}`).to.equal(
      rsAvailable,
    );
  });

  if (rsAvailable) {
    it('rolls back every write in a transaction that fails part-way', async () => {
      const orgId = await newOrg();
      const session = await mongoose.startSession();
      try {
        await session.withTransaction(async () => {
          await Users.create(
            [{ orgId, email: `rollback-${randomUUID().slice(0, 8)}@example.com`, role: 'member' }],
            { session },
          );
          await Org.updateOne({ _id: orgId }, { $set: { shortName: 'changed' } }, { session });
          throw new Error('fail after two writes');
        });
        expect.fail('the transaction should have failed');
      } catch (error) {
        expect((error as Error).message).to.equal('fail after two writes');
      } finally {
        await session.endSession();
      }
      expect(await Users.countDocuments({ orgId })).to.equal(0);
      expect((await Org.findById(orgId).lean())?.shortName).to.equal(undefined);
    });
  }

  it('refuses to demote the only admin and leaves them an admin', async () => {
    const orgId = await newOrg();
    const admin = await newUser(orgId, 'admin');
    await newUser(orgId, 'member');

    admin.role = 'member';
    let caught: unknown;
    try {
      await saveUserEnsuringOrgRetainsAdmin(admin, rsAvailable);
    } catch (error) {
      caught = error;
    }
    expect(caught).to.be.instanceOf(BadRequestError);
    expect(await adminCount(orgId)).to.equal(1);
    expect((await Users.findById(admin._id).lean())?.role).to.equal('admin');
  });

  it('demotes an admin when another admin remains', async () => {
    const orgId = await newOrg();
    const first = await newUser(orgId, 'admin');
    await newUser(orgId, 'admin');

    first.role = 'member';
    await saveUserEnsuringOrgRetainsAdmin(first, rsAvailable);
    expect((await Users.findById(first._id).lean())?.role).to.equal('member');
    expect(await adminCount(orgId)).to.equal(1);
  });

  it('never leaves an org without an admin when its last two admins are demoted at once', async () => {
    const orgId = await newOrg();
    const a = await newUser(orgId, 'admin');
    const b = await newUser(orgId, 'admin');
    a.role = 'member';
    b.role = 'member';

    const { ok, refused } = await raceSaves([
      () => saveUserEnsuringOrgRetainsAdmin(a, rsAvailable),
      () => saveUserEnsuringOrgRetainsAdmin(b, rsAvailable),
    ]);

    expect(await adminCount(orgId)).to.be.at.least(1);
    if (rsAvailable) {
      // The transaction serializes the two checks: exactly one demotion wins.
      expect({ ok, refused }).to.deep.equal({ ok: 1, refused: 1 });
      expect(await adminCount(orgId)).to.equal(1);
    }
  });

  it(`refuses to promote a member past ${MAX_ORG_ADMINS} admins`, async () => {
    const orgId = await newOrg();
    for (let i = 0; i < MAX_ORG_ADMINS; i += 1) {
      await newUser(orgId, 'admin');
    }
    const member = await newUser(orgId, 'member');

    member.role = 'admin';
    let caught: unknown;
    try {
      await saveUserEnsuringAdminCap(member, rsAvailable);
    } catch (error) {
      caught = error;
    }
    expect(caught).to.be.instanceOf(BadRequestError);
    expect(await adminCount(orgId)).to.equal(MAX_ORG_ADMINS);
    expect((await Users.findById(member._id).lean())?.role).to.equal('member');
  });

  it(`never exceeds ${MAX_ORG_ADMINS} admins when two members are promoted at once`, async () => {
    const orgId = await newOrg();
    for (let i = 0; i < MAX_ORG_ADMINS - 1; i += 1) {
      await newUser(orgId, 'admin');
    }
    const x = await newUser(orgId, 'member');
    const y = await newUser(orgId, 'member');
    x.role = 'admin';
    y.role = 'admin';

    const { ok, refused } = await raceSaves([
      () => saveUserEnsuringAdminCap(x, rsAvailable),
      () => saveUserEnsuringAdminCap(y, rsAvailable),
    ]);

    expect(await adminCount(orgId)).to.be.at.most(MAX_ORG_ADMINS);
    if (rsAvailable) {
      expect({ ok, refused }).to.deep.equal({ ok: 1, refused: 1 });
      expect(await adminCount(orgId)).to.equal(MAX_ORG_ADMINS);
    }
  });
});
