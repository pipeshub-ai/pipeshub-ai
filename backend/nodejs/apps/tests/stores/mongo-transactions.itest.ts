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
import { MongoUnitOfWork } from '../../src/libs/repositories/mongo';
import { UniqueConstraintError } from '../../src/libs/repositories/errors';
import { Org } from '../../src/modules/user_management/schema/org.schema';
import { Users, type User } from '../../src/modules/user_management/schema/users.schema';
import {
  MAX_ORG_ADMINS,
  createUserEnsuringAdminCap,
  saveUserEnsuringAdminCap,
  saveUserEnsuringOrgRetainsAdmin,
} from '../../src/modules/user_management/services/user-admin.service';
import sinon from 'sinon';
import { Logger } from '../../src/libs/services/logger.service';
import { BadRequestError, ServiceUnavailableError } from '../../src/libs/errors/http.errors';
import { setUserRepository, type UserRepository } from '../../src/libs/repositories/user';
import { MongoUserRepository, adminLeaseTiming } from '../../src/libs/repositories/user/mongo';
import { MongoOrgRepository } from '../../src/libs/repositories/org/mongo';
import { adminInviter, withoutSideStores } from './admin-invite-race';
import { deleteRace, gate, waitUntil, type DeleteOutcome, type DeleteRace } from './admin-delete-race';
import { createRace, type CreateOutcome, type CreateRace } from './admin-create-race';
import { USER_PROMOTED_DURING_DELETE } from '../../src/modules/user_management/controller/users.controller';

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
    let caught: unknown;
    try {
      await saveUserEnsuringOrgRetainsAdmin(String(admin.orgId), String(admin._id), { role: 'member' }, rsAvailable);
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
    await saveUserEnsuringOrgRetainsAdmin(String(first.orgId), String(first._id), { role: 'member' }, rsAvailable);
    expect((await Users.findById(first._id).lean())?.role).to.equal('member');
    expect(await adminCount(orgId)).to.equal(1);
  });

  it('never leaves an org without an admin when its last two admins are demoted at once', async () => {
    const orgId = await newOrg();
    const a = await newUser(orgId, 'admin');
    const b = await newUser(orgId, 'admin');

    const { ok, refused } = await raceSaves([
      () => saveUserEnsuringOrgRetainsAdmin(String(a.orgId), String(a._id), { role: 'member' }, rsAvailable),
      () => saveUserEnsuringOrgRetainsAdmin(String(b.orgId), String(b._id), { role: 'member' }, rsAvailable),
    ]);

    expect(await adminCount(orgId)).to.be.at.least(1);
    // The transaction (replica set) or the admin-role lease (standalone) serialises the two checks: exactly one wins.
    expect({ ok, refused }).to.deep.equal({ ok: 1, refused: 1 });
    expect(await adminCount(orgId)).to.equal(1);
  });

  it(`refuses to promote a member past ${MAX_ORG_ADMINS} admins`, async () => {
    const orgId = await newOrg();
    for (let i = 0; i < MAX_ORG_ADMINS; i += 1) {
      await newUser(orgId, 'admin');
    }
    const member = await newUser(orgId, 'member');
    let caught: unknown;
    try {
      await saveUserEnsuringAdminCap(String(member.orgId), String(member._id), { role: 'admin' }, rsAvailable);
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

    const { ok, refused } = await raceSaves([
      () => saveUserEnsuringAdminCap(String(x.orgId), String(x._id), { role: 'admin' }, rsAvailable),
      () => saveUserEnsuringAdminCap(String(y.orgId), String(y._id), { role: 'admin' }, rsAvailable),
    ]);

    expect(await adminCount(orgId)).to.be.at.most(MAX_ORG_ADMINS);
    expect({ ok, refused }).to.deep.equal({ ok: 1, refused: 1 });
    expect(await adminCount(orgId)).to.equal(MAX_ORG_ADMINS);
  });

  const newAdminFields = () => ({
    email: `new-admin-${randomUUID().slice(0, 8)}@example.com`,
    fullName: 'New Admin',
    role: 'admin' as const,
  });

  it(`refuses to create an admin past ${MAX_ORG_ADMINS} admins, and leaves no account`, async () => {
    const orgId = await newOrg();
    for (let i = 0; i < MAX_ORG_ADMINS; i += 1) {
      await newUser(orgId, 'admin');
    }
    const fields = newAdminFields();
    let caught: unknown;
    try {
      await createUserEnsuringAdminCap(orgId, fields, rsAvailable);
    } catch (error) {
      caught = error;
    }
    expect(caught).to.be.instanceOf(BadRequestError);
    expect(await adminCount(orgId)).to.equal(MAX_ORG_ADMINS);
    expect(await Users.countDocuments({ email: fields.email })).to.equal(0);
  });

  it(`never exceeds ${MAX_ORG_ADMINS} admins when two admins are created at once, and a refused create leaves no account`, async () => {
    const orgId = await newOrg();
    for (let i = 0; i < MAX_ORG_ADMINS - 1; i += 1) {
      await newUser(orgId, 'admin');
    }
    const a = newAdminFields();
    const b = newAdminFields();

    const { ok, refused } = await raceSaves([
      async () => { await createUserEnsuringAdminCap(orgId, a, rsAvailable); },
      async () => { await createUserEnsuringAdminCap(orgId, b, rsAvailable); },
    ]);

    expect(await adminCount(orgId)).to.equal(MAX_ORG_ADMINS - 1 + ok);
    expect(await Users.countDocuments({ email: { $in: [a.email, b.email] } })).to.equal(ok);
    expect(ok).to.be.at.most(1);
    expect({ ok, refused }).to.deep.equal({ ok: 1, refused: 1 });
  });

  it(`never exceeds ${MAX_ORG_ADMINS} admins when an admin create races a promotion`, async () => {
    const orgId = await newOrg();
    for (let i = 0; i < MAX_ORG_ADMINS - 1; i += 1) {
      await newUser(orgId, 'admin');
    }
    const member = await newUser(orgId, 'member');

    const { ok, refused } = await raceSaves([
      async () => { await createUserEnsuringAdminCap(orgId, newAdminFields(), rsAvailable); },
      async () => { await saveUserEnsuringAdminCap(orgId, String(member._id), { role: 'admin' }, rsAvailable); },
    ]);

    expect(await adminCount(orgId)).to.equal(MAX_ORG_ADMINS - 1 + ok);
    expect(ok).to.be.at.most(1);
    expect({ ok, refused }).to.deep.equal({ ok: 1, refused: 1 });
  });

  describe('bulk admin invites racing for the last seats', () => {
    let restoreSideStores: () => void;
    before(() => { restoreSideStores = withoutSideStores(); });
    after(() => restoreSideStores());

    /** One invite batch: a removed member, a pending member and a new address — three new admins. */
    async function batch(orgId: string) {
      const tag = randomUUID().slice(0, 8);
      const removed = await Users.create({ orgId, email: `removed-${tag}@example.com`, fullName: 'Removed', role: 'member', isDeleted: true });
      const pending = await Users.create({ orgId, email: `pending-${tag}@example.com`, fullName: 'Pending', role: 'member', hasLoggedIn: false });
      return { removed, pending, fresh: `fresh-${tag}@example.com` };
    }

    it(`never passes ${MAX_ORG_ADMINS} admins, and a refused batch leaves no trace`, async () => {
      const orgId = await newOrg();
      await newUser(orgId, 'admin');
      await newUser(orgId, 'admin');
      const a = await batch(orgId);
      const b = await batch(orgId);
      const invite = adminInviter(rsAvailable);

      const done = await Promise.allSettled([a, b].map((x) => invite(orgId, [x.removed.email, x.pending.email, x.fresh])));
      for (const d of done) if (d.status === 'rejected') expect(d.reason).to.be.instanceOf(BadRequestError);
      const ok = done.filter((d) => d.status === 'fulfilled').length;

      expect(await adminCount(orgId)).to.equal(2 + 3 * ok);
      for (const [x, d] of [[a, done[0]], [b, done[1]]] as const) {
        const removed = await Users.findById(x.removed._id).lean();
        const pending = await Users.findById(x.pending._id).lean();
        const fresh = await Users.find({ email: x.fresh }).lean();
        if (d.status === 'fulfilled') {
          expect([removed?.isDeleted, removed?.role, pending?.role, fresh.map((u) => u.role)]).to.deep.equal([false, 'admin', 'admin', ['admin']]);
        } else {
          // Removed again and a member, as it was; the pending account is still a member; no row for the new address.
          expect([removed?.isDeleted, removed?.role, pending?.role, fresh.length]).to.deep.equal([true, 'member', 'member', 0]);
        }
      }
      expect(ok).to.equal(1);
    });

    // A removed admin row is what a delete racing a promotion left behind. A restore grants nothing; only an admin
    // invite promotes, within the cap and audited.
    const removedAdmin = (orgId: string) =>
      Users.create({ orgId, email: `gone-admin-${randomUUID().slice(0, 8)}@example.com`, fullName: 'Removed Admin', role: 'admin', isDeleted: true });

    it(`a member invite brings a removed admin back as a member, with the org at ${MAX_ORG_ADMINS} admins`, async () => {
      const orgId = await newOrg();
      for (let i = 0; i < MAX_ORG_ADMINS; i += 1) await newUser(orgId, 'admin');
      const gone = await removedAdmin(orgId);

      await adminInviter(rsAvailable, 'member')(orgId, [gone.email]);

      const back = await Users.findById(gone._id).lean();
      expect([back?.isDeleted, back?.role]).to.deep.equal([false, 'member']);
      expect(await adminCount(orgId)).to.equal(MAX_ORG_ADMINS);
    });

    it(`an admin invite of a removed admin still counts it: refused at ${MAX_ORG_ADMINS} admins, the account stays removed`, async () => {
      const orgId = await newOrg();
      for (let i = 0; i < MAX_ORG_ADMINS; i += 1) await newUser(orgId, 'admin');
      const gone = await removedAdmin(orgId);

      const refused = await adminInviter(rsAvailable)(orgId, [gone.email]).then(() => null, (error: unknown) => error);

      expect(refused).to.be.instanceOf(BadRequestError);
      expect((await Users.findById(gone._id).lean())?.isDeleted).to.equal(true);
      expect(await adminCount(orgId)).to.equal(MAX_ORG_ADMINS);
    });

    it('an admin invite of a removed admin under the cap restores it, then promotes it with a role-change line', async () => {
      const orgId = await newOrg();
      await newUser(orgId, 'admin');
      const gone = await removedAdmin(orgId);
      const info = sinon.spy(Logger.prototype, 'info');
      try {
        await adminInviter(rsAvailable)(orgId, [gone.email]);
        const events = info.getCalls().map((c) => c.args[0]).filter((e) => e === 'user.restore' || e === 'user.role.change');
        expect(events).to.deep.equal(['user.restore', 'user.role.change']);
      } finally {
        info.restore();
      }
      expect((await Users.findById(gone._id).lean())?.role).to.equal('admin');
      expect(await adminCount(orgId)).to.equal(2);
    });

    it('a restore of an address whose account is already live leaves its role alone', async () => {
      const orgId = await newOrg();
      const live = await newUser(orgId, 'admin');
      await new MongoUserRepository().restoreByEmails(orgId, [live.email]);
      expect((await Users.findById(live._id).lean())?.role).to.equal('admin');
    });
  });

  // A profile write reads the account as live, then saves it. A removal that lands in between must not be overwritten:
  // on a standalone server a promotion would otherwise leave a removed admin, which a member invite later restores.
  describe('a profile write racing the removal of its account', () => {
    const users = new MongoUserRepository();
    afterEach(() => sinon.restore());

    /** Parks `updateProfile`'s live read of `id` once it has loaded the document; `release` lets the save go on. */
    const parkProfileRead = (id: string) => {
      const atRead = gate();
      const released = gate();
      const real = Users.findOne.bind(Users) as (...args: unknown[]) => Promise<unknown>;
      let armed = true;
      sinon.stub(Users, 'findOne').callsFake(((filter: Record<string, unknown>, ...rest: unknown[]) => {
        const isProfileRead = armed && String(filter?._id) === id && filter.isDeleted === false && 'orgId' in filter;
        if (!isProfileRead) return real(filter, ...rest);
        armed = false;
        return real(filter, ...rest).then(async (doc) => {
          atRead.open();
          await released.opened;
          return doc;
        });
      }) as never);
      return { atRead: atRead.opened, release: released.open };
    };
    /** Waits for the park; a write that settles first means the gate matched no read, so fail at once. */
    const reachedRead = (atRead: Promise<void>, write: Promise<unknown>) =>
      Promise.race([
        atRead,
        write.then(
          () => expect.fail('the write finished without reaching the parked read'),
          (error: unknown) => { throw error; },
        ),
      ]);
    /** Runs `run` on a new member and removes the member between its read and its write. */
    const removedDuring = async (run: (orgId: string, id: string) => Promise<unknown>) => {
      const orgId = await newOrg();
      const u = await newUser(orgId, 'member');
      const id = String(u._id);
      const { atRead, release } = parkProfileRead(id);
      const write = run(orgId, id);
      await reachedRead(atRead, write);
      await users.markDeleted(orgId, id, { signedOut: true });
      release();
      return { result: await write, row: await Users.findById(id).lean() };
    };

    it('writes nothing onto an account removed after the read, and answers null', async () => {
      const { result, row } = await removedDuring((orgId, id) =>
        users.updateProfile(orgId, id, { role: 'admin', fullName: 'Late Name' }));
      expect(result).to.equal(null);
      expect(row).to.include({ role: 'member', fullName: 'Test User', isDeleted: true });
    });

    it('answers null for a removed account even when the write changes nothing', async () => {
      const { result, row } = await removedDuring((orgId, id) =>
        users.updateProfile(orgId, id, { fullName: 'Test User' }));
      expect(result).to.equal(null);
      expect(row).to.include({ isDeleted: true });
    });

    it('a save that fails for another reason still throws: a taken address is a UniqueConstraintError', async () => {
      const orgId = await newOrg();
      const [u, other] = [await newUser(orgId, 'member'), await newUser(orgId, 'member')];
      const err = await users.updateProfile(orgId, String(u._id), { email: other.email }).then(() => null, (e: unknown) => e);
      expect(err).to.be.instanceOf(UniqueConstraintError);
    });

    if (rsAvailable) {
      it('inside a transaction whose first write is the save: nothing written, null, and the unit commits', async () => {
        const { result, row } = await removedDuring((orgId, id) =>
          new MongoUnitOfWork(true).run((tx) => users.updateProfile(orgId, id, { role: 'admin' }, tx)));
        expect(result).to.equal(null);
        expect(row).to.include({ role: 'member', isDeleted: true });
      });
    }

    it('a promotion whose account is removed after its read answers 404 and leaves a removed member', async () => {
      const orgId = await newOrg();
      const a = String((await newUser(orgId, 'admin'))._id);
      const u = String((await newUser(orgId, 'member'))._id);
      const { atRead, release } = parkProfileRead(u);
      const promotion = saveUserEnsuringAdminCap(orgId, u, { role: 'admin' }, rsAvailable);
      await reachedRead(atRead, promotion);
      // The delete's own last write, after its member check passed.
      await users.markDeleted(orgId, u, { signedOut: true, deletedBy: a });
      release();
      const outcome = await promotion.then(() => null, (error: Error) => error);
      expect(outcome?.message).to.equal('User not found');
      expect(await Users.findById(u).lean()).to.include({ role: 'member', isDeleted: true });
      expect(await adminCount(orgId)).to.equal(1);
    });
  });

  // Without a replica set, admin-role writes take turns on the org's lease. Each race below parks the first writer
  // between its write and its re-count, starts the second, and lets the first go on once the second has either
  // finished (no lease: the race happens) or been turned away by the lease (it waits). No sleeps.
  if (!rsAvailable) describe('admin-role writes take turns on a standalone MongoDB (no admin left behind)', () => {
    const real = new MongoUserRepository();
    let restoreSideStores: () => void;
    let leaseRefused: () => void;
    let refusedOnce: Promise<void>;
    before(() => { restoreSideStores = withoutSideStores(); });
    after(() => restoreSideStores());
    beforeEach(() => {
      refusedOnce = new Promise<void>((resolve) => { leaseRefused = resolve; });
      const take = Org.findOneAndUpdate.bind(Org);
      sinon.stub(Org, 'findOneAndUpdate').callsFake(((...args: Parameters<typeof Org.findOneAndUpdate>) =>
        Promise.resolve(take(...args)).then((taken) => {
          if (!taken) leaseRefused();
          return taken;
        })) as never);
    });
    afterEach(() => {
      sinon.restore();
      setUserRepository();
    });

    const gate = () => {
      let open!: () => void;
      const opened = new Promise<void>((resolve) => { open = resolve; });
      return { opened, open };
    };
    /** The real repository with some methods replaced. */
    const install = (over: Record<string, (...args: any[]) => Promise<any>>) =>
      setUserRepository(new Proxy(real, { get: (t, k: string) => (k in over ? over[k] : (t as any)[k]?.bind(t)) }) as never);
    const settled = (p: Promise<unknown>) => p.then(() => undefined, () => undefined);

    it('R1: two promotions over the limit and the demotion of every original admin still leave an admin', async () => {
      const orgId = await newOrg();
      const originals = [];
      for (let i = 0; i < MAX_ORG_ADMINS - 1; i += 1) originals.push(String((await newUser(orgId, 'admin'))._id));
      const [x, y] = [String((await newUser(orgId, 'member'))._id), String((await newUser(orgId, 'member'))._id)];
      const bothChecked = gate();
      const bothParked = gate();
      const reverts = gate();
      let writes = 0;
      let parked = 0;
      install({
        // Both promotions pass the pre-check before either writes (unless the lease turns the second away).
        updateProfile: async (o: string, id: string, f: any, tx?: any) => {
          if ([x, y].includes(id)) {
            if (++writes === 2) bothChecked.open();
            await Promise.race([bothChecked.opened, refusedOnce]);
          }
          return real.updateProfile(o, id, f, tx);
        },
        // An over-limit revert waits until the original admins are demoted.
        setRole: async (o: string, id: string, role: any) => {
          if ([x, y].includes(id) && role === 'member') {
            if (++parked === 2) bothParked.open();
            await reverts.opened;
          }
          return real.setRole(o, id, role);
        },
      });
      const promotions = [x, y].map((id) => saveUserEnsuringAdminCap(orgId, id, { role: 'admin' }, false));
      await Promise.race([bothParked.opened, Promise.all(promotions.map(settled))]);
      for (const id of originals) await settled(saveUserEnsuringOrgRetainsAdmin(orgId, id, { role: 'member' }, false));
      reverts.open();
      await Promise.all(promotions.map(settled));
      expect(await adminCount(orgId)).to.be.greaterThan(0);
    });

    it('R2: an admin create taken back after its re-count fails, while the only other admin is demoted, leaves an admin', async () => {
      const orgId = await newOrg();
      const a = String((await newUser(orgId, 'admin'))._id);
      const atRecount = gate();
      const recount = gate();
      let counts = 0;
      install({
        countActiveAdmins: async (o: string, tx?: any) => {
          // 1: the create's pre-check; 2: its re-count, parked, then failing as a store error would.
          if (++counts === 2) {
            atRecount.open();
            await recount.opened;
            throw new Error('store down');
          }
          return real.countActiveAdmins(o, tx);
        },
      });
      const create = createUserEnsuringAdminCap(orgId, { email: `b-${randomUUID().slice(0, 8)}@example.com`, fullName: 'B', role: 'admin' }, false);
      await atRecount.opened;
      const demotion = saveUserEnsuringOrgRetainsAdmin(orgId, a, { role: 'member' }, false);
      await Promise.race([settled(demotion), refusedOnce]);
      recount.open();
      const [created, demoted] = await Promise.allSettled([create, demotion]);
      expect(created.status).to.equal('rejected');
      expect(await adminCount(orgId)).to.equal(1);
      // The demotion waited for the create's undo, then found A the last admin.
      expect(demoted.status === 'rejected' && demoted.reason).to.be.instanceOf(BadRequestError);
    });

    it('R3: a bulk admin invite taken back after its re-count fails, while the only other admin is demoted, leaves an admin', async () => {
      const orgId = await newOrg();
      const a = String((await newUser(orgId, 'admin'))._id);
      const removed = await Users.create({ orgId, email: `removed-${randomUUID().slice(0, 8)}@example.com`, fullName: 'Removed', role: 'member', isDeleted: true });
      const atRecount = gate();
      const recount = gate();
      let counts = 0;
      install({
        countActiveAdmins: async (o: string, tx?: any) => {
          // 1: the invite's early refusal check; 2: the helper's pre-check; 3: the re-count after the grant.
          if (++counts === 3) {
            atRecount.open();
            await recount.opened;
            throw new Error('store down');
          }
          return real.countActiveAdmins(o, tx);
        },
      });
      const invite = adminInviter(false)(orgId, [removed.email]);
      await atRecount.opened;
      const demotion = saveUserEnsuringOrgRetainsAdmin(orgId, a, { role: 'member' }, false);
      await Promise.race([settled(demotion), refusedOnce]);
      recount.open();
      await Promise.allSettled([invite, demotion]);
      expect(await adminCount(orgId)).to.equal(1);
      const back = await Users.findById(removed._id).lean();
      expect([back?.isDeleted, back?.role]).to.deep.equal([true, 'member']);
    });

    // The lease expiring mid-change is forced by moving its `until` into the past while the change is parked: no clock.
    const expireLease = (orgId: string) => Org.updateOne({ _id: orgId }, { $set: { 'adminRoleLease.until': new Date(0) } });

    it('R4: a create whose lease expired before its re-count failed keeps its admin, so the demotion that counted it leaves one', async () => {
      const orgId = await newOrg();
      const a = String((await newUser(orgId, 'admin'))._id);
      const atRecount = gate();
      const recount = gate();
      let counts = 0;
      install({
        countActiveAdmins: async (o: string, tx?: any) => {
          // 1: the create's pre-check; 2: its re-count, parked, then failing as a store error would.
          if (++counts === 2) {
            atRecount.open();
            await recount.opened;
            throw new Error('store down');
          }
          return real.countActiveAdmins(o, tx);
        },
      });
      const create = createUserEnsuringAdminCap(orgId, { email: `b-${randomUUID().slice(0, 8)}@example.com`, fullName: 'B', role: 'admin' }, false);
      await atRecount.opened;
      await expireLease(orgId);
      // The lease is free, so the demotion runs at once and passes because B counts.
      await saveUserEnsuringOrgRetainsAdmin(orgId, a, { role: 'member' }, false);
      recount.open();
      const [created] = await Promise.allSettled([create]);
      expect(await adminCount(orgId)).to.equal(1);
      expect(created.status === 'fulfilled' && created.value.role).to.equal('admin');
    });

    it('a demotion whose lease expired before its write is not made, so a later take-back cannot leave no admin', async () => {
      const orgId = await newOrg();
      const a = String((await newUser(orgId, 'admin'))._id);
      const atCheck = gate();
      const check = gate();
      const atRecount = gate();
      const recount = gate();
      let counts = 0;
      install({
        countActiveAdmins: async (o: string, tx?: any) => {
          const n = ++counts;
          // 1: the demotion's check, parked until the create has written B; 3: the create's re-count, parked, then failing.
          if (n === 1) {
            atCheck.open();
            await check.opened;
          }
          if (n === 3) {
            atRecount.open();
            await recount.opened;
            throw new Error('store down');
          }
          return real.countActiveAdmins(o, tx);
        },
      });
      const demotion = saveUserEnsuringOrgRetainsAdmin(orgId, a, { role: 'member' }, false);
      await atCheck.opened;
      await expireLease(orgId);
      const create = createUserEnsuringAdminCap(orgId, { email: `b-${randomUUID().slice(0, 8)}@example.com`, fullName: 'B', role: 'admin' }, false);
      await atRecount.opened;
      check.open();
      const [demoted] = await Promise.allSettled([demotion]);
      recount.open();
      const [created] = await Promise.allSettled([create]);
      expect(await adminCount(orgId)).to.equal(1);
      expect((await Users.findById(a).lean())?.role).to.equal('admin');
      expect(demoted.status === 'rejected' && demoted.reason).to.be.instanceOf(ServiceUnavailableError);
      expect(created.status).to.equal('rejected');
    });

    it('a lease left by a holder that died is taken over once it has expired, without waiting', async () => {
      const orgId = await newOrg();
      await Org.updateOne({ _id: orgId }, { $set: { adminRoleLease: { holder: 'crashed', until: new Date(Date.now() - 60_000) } } });
      const saved = { ...adminLeaseTiming };
      adminLeaseTiming.waitMs = 0;
      try {
        expect(await real.withAdminLease(orgId, async () => 'ran')).to.equal('ran');
      } finally {
        Object.assign(adminLeaseTiming, saved);
      }
      expect((await Org.findById(orgId).lean())?.adminRoleLease).to.equal(undefined);
    });

    it('a lease another holder still holds turns the change away with 503 once the wait runs out', async () => {
      const orgId = await newOrg();
      await Org.updateOne({ _id: orgId }, { $set: { adminRoleLease: { holder: 'other', until: new Date(Date.now() + 60_000) } } });
      const saved = { ...adminLeaseTiming };
      adminLeaseTiming.waitMs = 50;
      let error: unknown;
      try {
        await real.withAdminLease(orgId, async () => 'ran');
      } catch (e) {
        error = e;
      } finally {
        Object.assign(adminLeaseTiming, saved);
      }
      expect(error).to.be.instanceOf(ServiceUnavailableError);
      expect((await Org.findById(orgId).lean())?.adminRoleLease?.holder).to.equal('other');
    });

    it('a holder whose lease expired under it cannot give back the newer holder\'s lease', async () => {
      const orgId = await newOrg();
      const result = await real.withAdminLease(orgId, async () => {
        // Our lease expired mid-change and another change took it.
        await Org.updateOne({ _id: orgId }, { $set: { adminRoleLease: { holder: 'newer', until: new Date(Date.now() + 60_000) } } });
        return 'ran';
      });
      expect(result).to.equal('ran');
      expect((await Org.findById(orgId).lean())?.adminRoleLease?.holder).to.equal('newer');
    });

    it('whole-organisation reads leave the lease out', async () => {
      const orgId = await newOrg();
      await Org.updateOne({ _id: orgId }, { $set: { adminRoleLease: { holder: 'other', until: new Date(Date.now() + 60_000) } } });
      const orgs = new MongoOrgRepository();
      expect(await orgs.getLive(orgId)).to.include({ _id: orgId }).and.not.have.property('adminRoleLease');
      expect(await orgs.update(orgId, { registeredName: 'Renamed' })).to.not.have.property('adminRoleLease');
    });
  });

  describe('deleting a member who is made an admin meanwhile', () => {
    let race: DeleteRace;
    before(() => { race = deleteRace(rsAvailable); });
    after(() => race.restore());
    afterEach(() => setUserRepository());

    const expectKeptAsAdmin = async (orgId: string, u: string, a: string, outcome: DeleteOutcome) => {
      expect(outcome.status).to.equal('refused');
      const error = (outcome as { error: Error }).error;
      expect(error).to.be.instanceOf(BadRequestError);
      expect(error.message).to.equal(USER_PROMOTED_DURING_DELETE);
      expect(await Users.findById(u).lean()).to.include({ role: 'admin', isDeleted: false });
      expect(await Users.findById(a).lean()).to.include({ role: 'member' });
      expect(await adminCount(orgId)).to.equal(1);
    };

    it('a delete that checked a member, then saw them promoted and the other admin demoted, keeps them: the org keeps its admin', async () => {
      const orgId = await newOrg();
      const a = String((await newUser(orgId, 'admin'))._id);
      const u = String((await newUser(orgId, 'member'))._id);
      const del = race.start(orgId, a, u);
      await del.parked;
      await saveUserEnsuringAdminCap(orgId, u, { role: 'admin' }, rsAvailable);
      // Passes: the person being deleted counts as an admin now.
      await saveUserEnsuringOrgRetainsAdmin(orgId, a, { role: 'member' }, rsAvailable);
      del.release();
      await expectKeptAsAdmin(orgId, u, a, await del.done);
    });

    it('a delete that finishes first leaves nobody to promote, and the demotion is refused', async () => {
      const orgId = await newOrg();
      const a = String((await newUser(orgId, 'admin'))._id);
      const u = String((await newUser(orgId, 'member'))._id);
      const del = race.start(orgId, a, u);
      await del.parked;
      del.release();
      expect((await del.done).status).to.equal('deleted');
      const promote = await saveUserEnsuringAdminCap(orgId, u, { role: 'admin' }, rsAvailable).catch((e: Error) => e);
      expect((promote as Error).message).to.equal('User not found');
      const demote = await saveUserEnsuringOrgRetainsAdmin(orgId, a, { role: 'member' }, rsAvailable).catch((e: Error) => e);
      expect(demote).to.be.instanceOf(BadRequestError);
      expect(await adminCount(orgId)).to.equal(1);
    });

    if (rsAvailable) {
      it('a removal sent while the promotion is still uncommitted waits for it, then keeps the new admin', async () => {
        const orgId = await newOrg();
        const a = String((await newUser(orgId, 'admin'))._id);
        const u = String((await newUser(orgId, 'member'))._id);
        const real = new MongoUserRepository();
        const written = gate();
        const commit = gate();
        setUserRepository(new Proxy(real, {
          get: (t, k: keyof UserRepository) => (k === 'updateProfile'
            ? async (...args: Parameters<UserRepository['updateProfile']>) => {
              const saved = await real.updateProfile(...args);
              written.open();
              await commit.opened; // still inside the transaction
              return saved;
            }
            : (t[k] as (...x: unknown[]) => unknown).bind(t)),
        }) as UserRepository);
        const del = race.start(orgId, a, u);
        await del.parked;
        const promotion = saveUserEnsuringAdminCap(orgId, u, { role: 'admin' }, true);
        let settled = false;
        try {
          await written.opened;
          del.release();
          void del.done.then(() => { settled = true; });
          // The removal reached the server and is held there by the open transaction.
          const admin = mongo.getConnection().getClient().db('admin');
          await waitUntil('the removal is in flight', async () => (await admin.aggregate([
            { $currentOp: { allUsers: true } },
            { $match: { 'command.findAndModify': 'users', 'command.query._id': new mongoose.Types.ObjectId(u) } },
          ]).toArray()).length > 0);
          expect(settled, 'the removal finished before the promotion committed').to.equal(false);
        } finally {
          commit.open();
        }
        await promotion;
        setUserRepository();
        await saveUserEnsuringOrgRetainsAdmin(orgId, a, { role: 'member' }, true);
        await expectKeptAsAdmin(orgId, u, a, await del.done);
      });
    }
  });
  describe('a failed create taking back an account the org relies on', () => {
    let race: CreateRace;
    before(() => { race = createRace(rsAvailable); });
    after(() => race.restore());
    afterEach(() => setUserRepository());

    const email = () => `b-${randomUUID().slice(0, 8)}@example.com`;
    const idOf = async (address: string) => String((await Users.findOne({ email: address }).lean())?._id);
    const expectKeptAsAdmin = async (orgId: string, address: string, outcome: CreateOutcome) => {
      expect(outcome.status).to.equal('failed');
      expect((outcome as { error: Error }).error.message).to.equal('everyone-group write failed');
      expect(await Users.findOne({ email: address }).lean()).to.include({ role: 'admin', isDeleted: false });
      expect(await adminCount(orgId)).to.equal(1);
    };

    it('an admin create the only other admin was demoted on is kept: the org keeps its admin', async () => {
      const orgId = await newOrg();
      const a = String((await newUser(orgId, 'admin'))._id);
      const address = email();
      const create = race.start(orgId, a, address, 'admin');
      await create.parked;
      // Passes: the new admin counts.
      await saveUserEnsuringOrgRetainsAdmin(orgId, a, { role: 'member' }, rsAvailable);
      create.fail();
      await expectKeptAsAdmin(orgId, address, await create.done);
      expect(await Users.findById(a).lean()).to.include({ role: 'member' });
    });

    it('a member create promoted meanwhile, with the other admin demoted, is kept: the org keeps its admin', async () => {
      const orgId = await newOrg();
      const a = String((await newUser(orgId, 'admin'))._id);
      const address = email();
      const create = race.start(orgId, a, address, 'member');
      await create.parked;
      await saveUserEnsuringAdminCap(orgId, await idOf(address), { role: 'admin' }, rsAvailable);
      await saveUserEnsuringOrgRetainsAdmin(orgId, a, { role: 'member' }, rsAvailable);
      create.fail();
      await expectKeptAsAdmin(orgId, address, await create.done);
    });

    it('with nothing in the window, the account is removed and the address is free again', async () => {
      const orgId = await newOrg();
      const a = String((await newUser(orgId, 'admin'))._id);
      const address = email();
      const create = race.start(orgId, a, address, 'admin');
      await create.parked;
      create.fail();
      expect((await create.done).status).to.equal('failed');
      expect(await Users.findOne({ email: address }).lean()).to.equal(null);
      const demote = await saveUserEnsuringOrgRetainsAdmin(orgId, a, { role: 'member' }, rsAvailable).catch((e: Error) => e);
      expect(demote).to.be.instanceOf(BadRequestError);
      expect(await adminCount(orgId)).to.equal(1);
    });

    it('an admin create someone already demoted is removed, though its own demotion is refused', async () => {
      const orgId = await newOrg();
      const a = String((await newUser(orgId, 'admin'))._id);
      const address = email();
      const create = race.start(orgId, a, address, 'admin');
      await create.parked;
      await saveUserEnsuringOrgRetainsAdmin(orgId, await idOf(address), { role: 'member' }, rsAvailable);
      create.fail();
      expect((await create.done).status).to.equal('failed');
      expect(await Users.findOne({ email: address }).lean()).to.equal(null);
      expect(await Users.findById(a).lean()).to.include({ role: 'admin' });
    });

    if (rsAvailable) {
      it('a removal sent while a promotion of the account is uncommitted waits for it, then keeps the new admin', async () => {
        const orgId = await newOrg();
        const a = String((await newUser(orgId, 'admin'))._id);
        const address = email();
        const create = race.start(orgId, a, address, 'member');
        await create.parked;
        const real = new MongoUserRepository();
        const written = gate();
        const commit = gate();
        // Installed after the create took its repository: only the promotion is held.
        setUserRepository(new Proxy(real, {
          get: (t, k: keyof UserRepository) => (k === 'updateProfile'
            ? async (...args: Parameters<UserRepository['updateProfile']>) => {
              const saved = await real.updateProfile(...args);
              written.open();
              await commit.opened; // still inside the transaction
              return saved;
            }
            : (t[k] as (...x: unknown[]) => unknown).bind(t)),
        }) as UserRepository);
        const b = await idOf(address);
        const promotion = saveUserEnsuringAdminCap(orgId, b, { role: 'admin' }, true);
        let settled = false;
        try {
          await written.opened;
          create.fail();
          void create.done.then(() => { settled = true; });
          // The conditional removal reached the server and is held there by the open transaction.
          const admin = mongo.getConnection().getClient().db('admin');
          await waitUntil('the removal is in flight', async () => (await admin.aggregate([
            { $currentOp: { allUsers: true } },
            { $match: {
              ns: `${dbName}.users`,
              'command.findAndModify': 'users',
              'command.query._id': new mongoose.Types.ObjectId(b),
              'command.query.role': { $exists: true },
            } },
          ]).toArray()).length > 0);
          expect(settled, 'the removal finished before the promotion committed').to.equal(false);
        } finally {
          commit.open();
        }
        await promotion;
        setUserRepository();
        await saveUserEnsuringOrgRetainsAdmin(orgId, a, { role: 'member' }, true);
        await expectKeptAsAdmin(orgId, address, await create.done);
      });
    }
  });
});
