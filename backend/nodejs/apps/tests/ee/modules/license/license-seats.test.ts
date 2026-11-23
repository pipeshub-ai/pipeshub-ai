import 'reflect-metadata';
import { expect } from 'chai';
import { generateKeyPairSync, sign, type KeyObject } from 'crypto';
import sinon from 'sinon';
import { PUBLIC_URL_ENV } from '../../../../src/ee/modules/license/license-binding';
import {
  installSeats,
  licenceSeatGuard,
  licenseEditionBoot,
  setLicenseStore,
} from '../../../../src/ee/modules/license/license-boot';
import { setSeatGuard } from '../../../../src/libs/edition/seat-guard';
import { licenseSigningInput } from '../../../../src/ee/modules/license/license-codec';
import { useLicenseKeysForTesting } from '../../../../src/ee/modules/license/license-keys';
import {
  checkSeats,
  SEAT_LIMIT_KEY,
  SEAT_REFUSAL_AT_SIGN_IN,
  SEAT_WARNING_DAYS,
  seatRoom,
  SERVICE_ACCOUNTS_HOLD_SEATS,
} from '../../../../src/ee/modules/license/license-seats';
import {
  LICENSE_FILES_KEY,
  type LicenseStore,
} from '../../../../src/ee/modules/license/license.service';
import {
  LICENSE_FORMAT,
  type LicensePayload,
} from '../../../../src/ee/modules/license/license.types';
import { ForbiddenError } from '../../../../src/libs/errors/http.errors';
import { setIdentityPort } from '../../../../src/libs/repositories/identity';
import { setGroupRepository } from '../../../../src/libs/repositories/group';
import { getOrgRepository } from '../../../../src/libs/repositories/org';
import { setActivityRepository } from '../../../../src/libs/repositories/activity';
import {
  getUserRepository,
  setUserRepository,
  type UserRepository,
} from '../../../../src/libs/repositories/user';
import { MongoUserRepository } from '../../../../src/libs/repositories/user/mongo';
import { JitProvisioningService } from '../../../../src/modules/auth/services/jit-provisioning.service';
import { UserController } from '../../../../src/modules/user_management/controller/users.controller';
import { Users } from '../../../../src/modules/user_management/schema/users.schema';

const DAY_MS = 24 * 60 * 60 * 1000;
const MINUTE_MS = 60 * 1000;
const NOW = new Date('2026-11-20T12:00:00.000Z');
const ADDRESS = 'https://pipeshub.license.test';
const ORG = '507f1f77bcf86cd799439012';
const at = (offsetMs: number, from: Date = NOW): Date =>
  new Date(from.getTime() + offsetMs);

/** A configuration store in memory that keeps values as JSON text, like the real one. */
class MemoryStore implements LicenseStore {
  data = new Map<string, string>();
  async read(key: string): Promise<unknown> {
    const value = this.data.get(key);
    return value === undefined ? null : JSON.parse(value);
  }
  async write(key: string, value: unknown): Promise<void> {
    this.data.set(key, JSON.stringify(value));
  }
  overSince(): unknown {
    const value = this.data.get(SEAT_LIMIT_KEY);
    return value === undefined ? undefined : JSON.parse(value).overSince;
  }
}

describe('licence seat limit', () => {
  let privateKey: KeyObject;
  let restoreKeys: () => void;
  let previousAddress: string | undefined;
  let store: MemoryStore;

  const install = async (
    seats: LicensePayload['seats'],
    type: LicensePayload['type'] = 'pro',
    around: Date = NOW,
  ): Promise<void> => {
    const license: LicensePayload = {
      version: 1,
      licenseId: `lic_seats_${String(seats)}`,
      licensee: 'Example Org',
      type,
      seats,
      startDate: at(-DAY_MS, around).toISOString(),
      endDate: at(90 * DAY_MS, around).toISOString(),
      issuedAt: at(-DAY_MS, around).toISOString(),
      url: ADDRESS,
    };
    await store.write(LICENSE_FILES_KEY, {
      licenses: [
        {
          format: LICENSE_FORMAT,
          keyId: 'test-key',
          license,
          signature: sign(
            null,
            licenseSigningInput(license),
            privateKey,
          ).toString('base64url'),
        },
      ],
    });
  };

  const people = (n: number) => async () => n;

  before(() => {
    const pair = generateKeyPairSync('ed25519');
    privateKey = pair.privateKey;
    restoreKeys = useLicenseKeysForTesting([
      pair.publicKey.export({ format: 'pem', type: 'spki' }),
    ]);
    previousAddress = process.env[PUBLIC_URL_ENV];
    process.env[PUBLIC_URL_ENV] = ADDRESS;
  });

  after(() => {
    restoreKeys();
    if (previousAddress === undefined) delete process.env[PUBLIC_URL_ENV];
    else process.env[PUBLIC_URL_ENV] = previousAddress;
  });

  beforeEach(() => {
    store = new MemoryStore();
  });

  describe('the seat state, with a clock passed in', () => {
    it('under the limit: no warning, adding allowed', async () => {
      await install(10);
      const limit = await checkSeats(store, people(9), NOW);
      expect(limit).to.deep.include({
        seats: 10,
        used: 9,
        over: 0,
        overSince: null,
        refusing: false,
      });
      expect(seatRoom(limit)).to.equal(Infinity);
      expect(store.overSince()).to.equal(undefined);
    });

    it('exactly at the limit (the 10-seat development key): no warning', async () => {
      await install(10, 'development');
      const limit = await checkSeats(store, people(10), NOW);
      expect(limit).to.deep.include({
        over: 0,
        overSince: null,
        refusing: false,
      });
    });

    it('one over: stores when, warns for 14 days, adding still allowed', async () => {
      await install(10);
      const limit = await checkSeats(store, people(11), NOW);
      expect(limit).to.deep.include({
        over: 1,
        overSince: NOW.toISOString(),
        refuseFrom: at(SEAT_WARNING_DAYS * DAY_MS).toISOString(),
        daysLeft: 14,
        refusing: false,
      });
      expect(seatRoom(limit)).to.equal(Infinity);
      expect(store.overSince()).to.equal(NOW.toISOString());
    });

    it('day 13, 23 h 59 m: still allowed, and the first time over is kept', async () => {
      await install(10);
      await checkSeats(store, people(11), NOW);
      const limit = await checkSeats(
        store,
        people(12),
        at(14 * DAY_MS - MINUTE_MS),
      );
      expect(limit).to.deep.include({
        overSince: NOW.toISOString(),
        daysLeft: 1,
        refusing: false,
      });
      expect(seatRoom(limit)).to.equal(Infinity);
    });

    it('day 14: new users are refused', async () => {
      await install(10);
      await checkSeats(store, people(11), NOW);
      const limit = await checkSeats(store, people(11), at(14 * DAY_MS));
      expect(limit).to.deep.include({ daysLeft: 0, refusing: true });
      expect(seatRoom(limit)).to.equal(0);
    });

    it('back within: the stored time is cleared and adding is allowed', async () => {
      await install(10);
      await checkSeats(store, people(11), NOW);
      const limit = await checkSeats(store, people(10), at(20 * DAY_MS));
      expect(limit).to.deep.include({
        over: 0,
        overSince: null,
        refusing: false,
      });
      expect(store.overSince()).to.equal(null);
    });

    it('over again: a new 14 days from the new time', async () => {
      await install(10);
      await checkSeats(store, people(11), NOW);
      await checkSeats(store, people(10), at(20 * DAY_MS));
      const again = at(21 * DAY_MS);
      const limit = await checkSeats(store, people(11), again);
      expect(limit).to.deep.include({
        overSince: again.toISOString(),
        daysLeft: 14,
        refusing: false,
      });
      expect(
        (await checkSeats(store, people(11), at(35 * DAY_MS - MINUTE_MS)))
          ?.refusing,
      ).to.equal(false);
      expect(
        (await checkSeats(store, people(11), at(35 * DAY_MS)))?.refusing,
      ).to.equal(true);
    });

    it('unlimited seats never limit, and people are not counted', async () => {
      await install('unlimited');
      const count = sinon.stub().resolves(100_000);
      expect(await checkSeats(store, count, NOW)).to.equal(null);
      expect(count.called).to.equal(false);
    });

    it('no licence: no limit, people are not counted, and a stale time is cleared', async () => {
      await store.write(SEAT_LIMIT_KEY, {
        overSince: at(-30 * DAY_MS).toISOString(),
      });
      const count = sinon.stub().resolves(500);
      const limit = await checkSeats(store, count, NOW);
      expect(limit).to.equal(null);
      expect(seatRoom(limit)).to.equal(Infinity);
      expect(count.called).to.equal(false);
      expect(store.overSince()).to.equal(null);
    });
  });

  describe('who counts', () => {
    afterEach(() => sinon.restore());

    it('service accounts hold no seat; removed accounts do not count, invited ones do', async () => {
      expect(SERVICE_ACCOUNTS_HOLD_SEATS).to.equal(false);
      const countDocuments = sinon
        .stub(Users, 'countDocuments')
        .resolves(3 as never);
      expect(await new MongoUserRepository().countLive(ORG, false)).to.equal(3);
      expect(countDocuments.firstCall.args[0]).to.deep.equal({
        orgId: ORG,
        isDeleted: { $ne: true },
        kind: { $ne: 'service' },
      });
      await new MongoUserRepository().countLive(ORG, true);
      expect(countDocuments.secondCall.args[0]).to.deep.equal({
        orgId: ORG,
        isDeleted: { $ne: true },
      });
    });
  });

  describe('the paths that add people, once new users are refused', () => {
    type Row = {
      _id: string;
      orgId: string;
      email: string;
      isDeleted: boolean;
      hasLoggedIn: boolean;
      role: string;
      kind: string;
    };
    let rows: Row[];
    let created: string[];
    let restored: string[];
    let nextId: number;

    const row = (email: string, extra: Partial<Row> = {}): Row => ({
      _id: `u${String(nextId++)}`,
      orgId: ORG,
      email,
      isDeleted: false,
      hasLoggedIn: true,
      role: 'member',
      kind: 'human',
      ...extra,
    });
    const live = () => rows.filter((u) => !u.isDeleted && u.kind !== 'service');

    /** A licence of `seats` around the real clock, over since `overDaysAgo` days (null: no time stored). */
    const seatsFull = async (
      overDaysAgo: number | null,
      seats = 2,
    ): Promise<void> => {
      await install(seats, 'pro', new Date());
      if (overDaysAgo !== null) {
        await store.write(SEAT_LIMIT_KEY, {
          overSince: at(-overDaysAgo * DAY_MS, new Date()).toISOString(),
        });
      }
    };

    const controller = () =>
      new UserController(
        { scopedJwtSecret: 'test-secret' } as never,
        { sendMail: sinon.stub().resolves({ statusCode: 200 }) } as never,
        {
          passwordMethodEnabled: sinon.stub().resolves({
            statusCode: 200,
            data: { isPasswordAuthEnabled: false },
          }),
        } as never,
        {
          info: sinon.stub(),
          warn: sinon.stub(),
          error: sinon.stub(),
          debug: sinon.stub(),
        } as never,
        {
          start: sinon.stub().resolves(),
          stop: sinon.stub().resolves(),
          publishEvent: sinon.stub().resolves(),
        } as never,
        {
          start: sinon.stub().resolves(),
          publishEvent: sinon.stub().resolves(),
        } as never,
      );
    const invite = (emails: string[]) =>
      (
        controller() as unknown as {
          processInvites: (
            emails: string[],
            groupIds: undefined,
            orgId: string,
            inviterName: string,
            org: null,
          ) => Promise<{
            invited: number;
            restored: number;
            reinvited: number;
            refused: string[];
            refusedReason?: string;
          }>;
        }
      ).processInvites(emails, undefined, ORG, 'Admin', null);

    beforeEach(() => {
      nextId = 1;
      created = [];
      restored = [];
      rows = [
        row('admin@example.com', { role: 'admin' }),
        row('member@example.com'),
        row('invited@example.com', { hasLoggedIn: false }),
        row('gone@example.com', { isDeleted: true }),
        row('bot@service.pipeshub.internal', { kind: 'service' }),
      ];
      setLicenseStore(store);
      // As app.ts does at start-up with the edition boot's guard.
      setSeatGuard(licenseEditionBoot.seatGuard);
      setUserRepository({
        countLive: async (_org: string, withServiceAccounts: boolean) =>
          rows.filter(
            (u) =>
              !u.isDeleted && (withServiceAccounts || u.kind !== 'service'),
          ).length,
        existsLiveEmail: async (email: string) =>
          rows.some((u) => u.email === email && !u.isDeleted),
        hasRemovedAccount: async (_org: string, email: string) =>
          rows.some((u) => u.email === email && u.isDeleted),
        listByEmails: async (_org: string, emails: string[]) =>
          rows.filter((u) => emails.includes(u.email)).map((u) => ({ ...u })),
        create: async (_org: string, fields: { email: string }) => {
          const u = row(fields.email);
          rows.push(u);
          created.push(u.email);
          return { ...u };
        },
        createMany: async (_org: string, list: Array<{ email: string }>) =>
          list.map((fields) => {
            const u = row(fields.email, { hasLoggedIn: false });
            rows.push(u);
            created.push(u.email);
            return { ...u };
          }),
        restoreByEmails: async (_org: string, emails: string[]) =>
          rows
            .filter((u) => emails.includes(u.email) && u.isDeleted)
            .map((u) => {
              u.isDeleted = false;
              restored.push(u.email);
              return { ...u };
            }),
      } as unknown as UserRepository);
      setIdentityPort({
        oldestOrgId: async () => ORG,
        blockedUserIds: async () => [],
      } as never);
      setGroupRepository({ addToEveryone: async () => undefined } as never);
      setActivityRepository({ recordMany: async () => undefined } as never);
    });

    afterEach(() => {
      sinon.restore();
      setLicenseStore(null);
      setSeatGuard();
      setUserRepository();
      setIdentityPort();
      setGroupRepository();
      setActivityRepository();
    });

    it('a bulk add crossing the limit during the warning adds everyone and starts the 14 days', async () => {
      // Three people on three seats: exactly at the limit.
      await seatsFull(null, 3);
      expect(store.overSince()).to.equal(undefined);
      const result = await invite(['new1@example.com', 'new2@example.com']);
      expect(result).to.deep.include({ invited: 2, refused: [] });
      expect(live()).to.have.length(5);
      const limit = await installSeats(store);
      expect(limit).to.deep.include({
        seats: 3,
        used: 5,
        over: 2,
        refusing: false,
      });
      expect(store.overSince()).to.be.a('string');
    });

    it('a bulk add once refused: re-invites go out, new and removed addresses are refused whole', async () => {
      await seatsFull(15);
      const result = await invite([
        'invited@example.com',
        'new1@example.com',
        'gone@example.com',
      ]);
      expect(result).to.deep.include({
        invited: 0,
        restored: 0,
        reinvited: 1,
        refused: ['new1@example.com', 'gone@example.com'],
        refusedReason:
          'Your licence covers 2 users. Remove users or add seats to invite more.',
      });
      expect(created).to.deep.equal([]);
      expect(restored).to.deep.equal([]);
    });

    it('a restore is refused once over for 14 days, and allowed on day 13', async () => {
      await seatsFull(14);
      expect((await invite(['gone@example.com'])).refused).to.deep.equal([
        'gone@example.com',
      ]);
      expect(restored).to.deep.equal([]);

      await seatsFull(13);
      expect((await invite(['gone@example.com'])).restored).to.equal(1);
      expect(restored).to.deep.equal(['gone@example.com']);
    });

    it('addManyUsers answers 403 with the plain message when every address is refused', async () => {
      await seatsFull(15);
      const next = sinon.stub();
      sinon.stub(getOrgRepository(), 'getLive').resolves(null);
      await controller().addManyUsers(
        {
          user: { userId: 'u1', orgId: ORG, fullName: 'Admin' },
          body: { emails: ['new1@example.com'] },
        } as never,
        { status: sinon.stub().returnsThis(), json: sinon.stub() } as never,
        next,
      );
      const error = next.firstCall.args[0] as Error;
      expect(error).to.be.instanceOf(ForbiddenError);
      expect(error.message).to.equal(
        'Your licence covers 2 users. Remove users or add seats to invite more.',
      );
    });

    it('creating one user is refused once over for 14 days, and nothing is written', async () => {
      await seatsFull(15);
      const next = sinon.stub();
      await controller().createUser(
        {
          user: { userId: 'u1', orgId: ORG },
          body: { email: 'new1@example.com', fullName: 'New One' },
        } as never,
        { status: sinon.stub().returnsThis(), json: sinon.stub() } as never,
        next,
      );
      expect(next.firstCall.args[0]).to.be.instanceOf(ForbiddenError);
      expect(created).to.deep.equal([]);
    });

    describe('recreating a removed sample account', () => {
      const SAMPLE = 'alice@acme-demo.example';
      let credentialsDeleted: string[];
      const removedIds = (): string[] =>
        rows.filter((u) => u.email === SAMPLE && u.isDeleted).map((u) => u._id);

      beforeEach(() => {
        credentialsDeleted = [];
        rows.push(row(SAMPLE, { isDeleted: true }));
        const users = getUserRepository();
        Object.assign(users, {
          listRemovedIdsByEmail: async (_org: string, email: string) =>
            rows.filter((u) => u.email === email && u.isDeleted).map((u) => u._id),
          deleteRemoved: async (_org: string, id: string) => {
            const before = rows.length;
            rows = rows.filter((u) => !(u._id === id && u.isDeleted));
            return rows.length < before;
          },
        });
        setIdentityPort({
          oldestOrgId: async () => ORG,
          blockedUserIds: async () => [],
          deleteForUser: async (id: string) => {
            credentialsDeleted.push(id);
          },
        } as never);
      });

      const createSample = async (): Promise<unknown> => {
        const next = sinon.stub();
        await controller().createUser(
          {
            user: { userId: 'u1', orgId: ORG },
            body: { email: SAMPLE, fullName: 'Alice' },
          } as never,
          { status: sinon.stub().returnsThis(), json: sinon.stub() } as never,
          next,
        );
        return next.firstCall?.args[0];
      };

      it('once refused, keeps the removed row and its credentials', async () => {
        await seatsFull(15);
        const before = await getUserRepository().listRemovedIdsByEmail(
          ORG,
          SAMPLE,
        );
        expect(before).to.have.length(1);
        expect(await createSample()).to.be.instanceOf(ForbiddenError);
        expect(
          await getUserRepository().listRemovedIdsByEmail(ORG, SAMPLE),
        ).to.deep.equal(before);
        expect(credentialsDeleted).to.deep.equal([]);
        expect(created).to.deep.equal([]);
      });

      it('when the seat check throws, keeps the removed row and its credentials', async () => {
        await seatsFull(null);
        sinon.stub(store, 'read').rejects(new Error('store down'));
        const before = removedIds();
        expect(await createSample()).to.be.instanceOf(Error);
        expect(
          await getUserRepository().listRemovedIdsByEmail(ORG, SAMPLE),
        ).to.deep.equal(before);
        expect(credentialsDeleted).to.deep.equal([]);
        expect(created).to.deep.equal([]);
      });
    });

    it('JIT sign-in is refused once over for 14 days, with words for the person signing in', async () => {
      await seatsFull(15);
      const jit = new JitProvisioningService(
        { error: sinon.stub() } as never,
        {
          start: sinon.stub().resolves(),
          stop: sinon.stub().resolves(),
          publishEvent: sinon.stub().resolves(),
        } as never,
      );
      let error: unknown;
      try {
        await jit.provisionUser(
          'new1@example.com',
          { fullName: 'New One' },
          ORG,
          'google',
        );
      } catch (caught) {
        error = caught;
      }
      expect(error).to.be.instanceOf(ForbiddenError);
      expect((error as Error).message).to.equal(SEAT_REFUSAL_AT_SIGN_IN);
      expect(created).to.deep.equal([]);
    });

    it('JIT sign-in during the warning creates the account', async () => {
      await seatsFull(3);
      const jit = new JitProvisioningService(
        { error: sinon.stub() } as never,
        {
          start: sinon.stub().resolves(),
          stop: sinon.stub().resolves(),
          publishEvent: sinon.stub().resolves(),
        } as never,
      );
      await jit.provisionUser(
        'new1@example.com',
        { fullName: 'New One' },
        ORG,
        'google',
      );
      expect(created).to.deep.equal(['new1@example.com']);
    });

    it('the licence edition boot supplies the licence guard', () => {
      expect(licenseEditionBoot.seatGuard).to.equal(licenceSeatGuard);
    });

    it('service accounts are not counted toward the seats', async () => {
      await seatsFull(null);
      // admin, member and invited: 3 people; the bot and the removed account do not count.
      expect((await installSeats(store))?.used).to.equal(3);
    });
  });
});
