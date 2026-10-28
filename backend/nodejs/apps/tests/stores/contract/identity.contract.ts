/**
 * Contract of the identity repositories (user, group, credential, activity,
 * org auth config, reset link, picture, identity port). Store-neutral: it talks
 * only to the repository interfaces; the store under test supplies instances and
 * a way to make an organisation row. Each scenario uses its own organisation(s)
 * so tests do not depend on each other.
 */
import { expect } from 'chai';
import { UniqueConstraintError } from '../../../src/libs/repositories/errors';
import type { ScopedUnitOfWork } from './unit-of-work-scope';
import type { UserRepository } from '../../../src/libs/repositories/user/types';
import type { GroupRepository } from '../../../src/libs/repositories/group/types';
import type { CredentialRepository } from '../../../src/libs/repositories/credential/types';
import type { ActivityRepository } from '../../../src/libs/repositories/activity/types';
import type { OrgAuthConfigRepository } from '../../../src/libs/repositories/org-auth-config/types';
import type { ResetLinkRepository } from '../../../src/libs/repositories/reset-link/types';
import { hashResetLink } from '../../../src/libs/repositories/reset-link/types';
import type { PictureRepository } from '../../../src/libs/repositories/picture/types';
import type { IdentityPort } from '../../../src/libs/repositories/identity/types';

export interface IdentityFixture {
  users: UserRepository;
  groups: GroupRepository;
  credentials: CredentialRepository;
  activities: ActivityRepository;
  orgAuth: OrgAuthConfigRepository;
  resetLinks: ResetLinkRepository;
  pictures: PictureRepository;
  identity: IdentityPort;
  uow: ScopedUnitOfWork;
  /** True when a failed run rolls back (replica set). */
  transactional: boolean;
  /** Makes an organisation row and returns its id. */
  newOrg(): Promise<string>;
  /** A well-formed id nothing refers to. */
  newId(): string;
  /** Removes every org auth config row (the "first live config" lookups are store-wide). */
  clearOrgAuth(): Promise<void>;
}

const MAX = 5;

export function describeIdentityContract(fx: IdentityFixture): void {
  const { users, groups, credentials, activities, orgAuth, resetLinks, pictures, identity } = fx;
  let n = 0;
  const uniq = (p: string): string => `${p}${process.pid}x${Date.now()}x${n++}`;
  const email = (p = 'u'): string => `${uniq(p)}@example.com`;
  const isStr = (v: unknown): void => {
    expect(typeof v).to.equal('string');
  };
  const person = async (orgId: string, extra: Record<string, unknown> = {}) =>
    users.create(orgId, { email: email(), fullName: uniq('Name'), ...extra });
  /** The id of a live account in the organisation: a credential belongs to an account. */
  const acct = async (orgId: string): Promise<string> => (await person(orgId))._id;
  const svcEmail = (orgId: string): string =>
    `svc-${uniq('s')}-${orgId}@service.pipeshub.internal`.toLowerCase();

  describe('login: credentials', () => {
    it('find returns the live credential with string ids, null for others', async () => {
      const org = await fx.newOrg();
      const uid = await acct(org);
      expect(await credentials.find(uid, org)).to.equal(null);
      await credentials.savePassword(uid, org, 'hash-value-1', '1.1.1.1');
      const c = await credentials.find(uid, org);
      isStr(c!._id);
      isStr(c!.orgId);
      expect(c!.userId).to.equal(uid);
      expect(c!.hashedPassword).to.equal('hash-value-1');
      expect(c!.wrongCredentialCount).to.equal(0);
      expect(await credentials.find(uid, await fx.newOrg())).to.equal(null);
    });

    it('reserveAttempt counts, then returns null at the maximum', async () => {
      const org = await fx.newOrg();
      const uid = await acct(org);
      await credentials.savePassword(uid, org, 'hash-value', '1.1.1.1');
      for (let i = 1; i <= MAX; i++) {
        const a = await credentials.reserveAttempt(uid, org, MAX);
        expect(a!.wrongCredentialCount).to.equal(i);
      }
      expect(await credentials.reserveAttempt(uid, org, MAX)).to.equal(null);
    });

    it('reserveAttempt returns null while blocked; clearBlock lifts it and zeroes the count', async () => {
      const org = await fx.newOrg();
      const uid = await acct(org);
      await credentials.savePassword(uid, org, 'hash-value', '1.1.1.1');
      await credentials.reserveAttempt(uid, org, MAX);
      await credentials.setBlocked(uid, org, new Date(Date.now() + 60000));
      const blocked = await credentials.find(uid, org);
      expect(blocked!.isBlocked).to.equal(true);
      expect(blocked!.blockExpiresAt).to.be.instanceOf(Date);
      expect(await credentials.reserveAttempt(uid, org, MAX)).to.equal(null);
      await credentials.clearBlock(uid, org);
      const c = await credentials.find(uid, org);
      expect(c!.isBlocked).to.equal(false);
      expect(c!.wrongCredentialCount).to.equal(0);
      expect(c!.blockExpiresAt).to.equal(null);
    });

    it('releaseAttempt resets only while the count still matches and the credential is not blocked', async () => {
      const org = await fx.newOrg();
      const uid = await acct(org);
      await credentials.savePassword(uid, org, 'hash-value', '1.1.1.1');
      await credentials.reserveAttempt(uid, org, MAX);
      await credentials.reserveAttempt(uid, org, MAX);
      await credentials.releaseAttempt(uid, org, 1); // stale: count is 2
      expect((await credentials.find(uid, org))!.wrongCredentialCount).to.equal(2);
      await credentials.setBlocked(uid, org, new Date(Date.now() + 60000));
      await credentials.releaseAttempt(uid, org, 2); // blocked
      expect((await credentials.find(uid, org))!.wrongCredentialCount).to.equal(2);
      await credentials.clearBlock(uid, org);
      await credentials.reserveAttempt(uid, org, MAX);
      await credentials.releaseAttempt(uid, org, 0); // stale: count is 1
      expect((await credentials.find(uid, org))!.wrongCredentialCount).to.equal(1);
      await credentials.releaseAttempt(uid, org, 1);
      expect((await credentials.find(uid, org))!.wrongCredentialCount).to.equal(0);
    });

    it('incrementWrongCount counts even for a blocked credential', async () => {
      const org = await fx.newOrg();
      const uid = await acct(org);
      await credentials.savePassword(uid, org, 'hash-value', '1.1.1.1');
      const c = await credentials.incrementWrongCount(uid, org);
      expect(c!.wrongCredentialCount).to.equal(1);
      expect(await credentials.incrementWrongCount(fx.newId(), org)).to.equal(null);
    });

    it('claimOtp has a single winner among racing claims', async () => {
      const org = await fx.newOrg();
      const uid = await acct(org);
      await credentials.saveOtp(uid, org, { hashedOTP: 'otp-hash-1', otpValidity: Date.now() + 1000, ipAddress: '1.1.1.1' });
      const results = await Promise.all(
        [1, 2, 3, 4, 5].map(() => credentials.claimOtp(uid, org, 'otp-hash-1')),
      );
      expect(results.filter((r) => r !== null)).to.have.length(1);
      const c = await credentials.find(uid, org);
      expect(c!.hashedOTP).to.equal(undefined);
      expect(c!.otpValidity).to.equal(undefined);
    });

    it('claimOtp with a different hash claims nothing', async () => {
      const org = await fx.newOrg();
      const uid = await acct(org);
      await credentials.saveOtp(uid, org, { hashedOTP: 'otp-hash-a', otpValidity: 1, ipAddress: 'ip' });
      expect(await credentials.claimOtp(uid, org, 'otp-hash-b')).to.equal(null);
    });

    it('saveOtp creates then updates the same credential', async () => {
      const org = await fx.newOrg();
      const uid = await acct(org);
      await credentials.saveOtp(uid, org, { hashedOTP: 'otp-hash-a', otpValidity: 10, ipAddress: 'ip1' });
      const first = await credentials.find(uid, org);
      await credentials.saveOtp(uid, org, { hashedOTP: 'otp-hash-b', otpValidity: 20, ipAddress: 'ip2' });
      const second = await credentials.find(uid, org);
      expect(second!._id).to.equal(first!._id);
      expect(second!.hashedOTP).to.equal('otp-hash-b');
      expect(second!.otpValidity).to.equal(20);
      expect(second!.ipAddress).to.equal('ip1'); // an update leaves the stored address alone
    });

    it('savePassword creates then updates; the address is written only when given', async () => {
      const org = await fx.newOrg();
      const uid = await acct(org);
      await credentials.savePassword(uid, org, 'hash-value-1', '9.9.9.9');
      await credentials.savePassword(uid, org, 'hash-value-2');
      const c = await credentials.find(uid, org);
      expect(c!.hashedPassword).to.equal('hash-value-2');
      expect(c!.ipAddress).to.equal('9.9.9.9');
      await credentials.savePassword(uid, org, 'hash-value-3', '8.8.8.8');
      expect((await credentials.find(uid, org))!.ipAddress).to.equal('8.8.8.8');
    });

    it('savePassword for a new person without an address fails validation', async () => {
      let err: unknown;
      const org = await fx.newOrg();
      try {
        await credentials.savePassword(await acct(org), org, 'hash-value');
      } catch (e) {
        err = e;
      }
      expect(err).to.be.instanceOf(Error);
    });

    it('createWithPassword stores a credential', async () => {
      const org = await fx.newOrg();
      const uid = await acct(org);
      await credentials.createWithPassword({ userId: uid, orgId: org, hashedPassword: 'hash-value', ipAddress: '1.1.1.1' });
      expect((await credentials.find(uid, org))!.hashedPassword).to.equal('hash-value');
    });

    it('touchOnRefresh creates the credential when missing and keeps it when present', async () => {
      const org = await fx.newOrg();
      const uid = await acct(org);
      const created = await credentials.touchOnRefresh(uid, org, '3.3.3.3');
      expect(created!.ipAddress).to.equal('3.3.3.3');
      const again = await credentials.touchOnRefresh(uid, org, '4.4.4.4');
      expect(again!._id).to.equal(created!._id);
      expect(again!.ipAddress).to.equal('4.4.4.4');
    });

    it('unblock is true for a blocked person and false otherwise', async () => {
      const org = await fx.newOrg();
      const uid = await acct(org);
      await credentials.savePassword(uid, org, 'hash-value', 'ip');
      expect(await credentials.unblock(uid, org)).to.equal(false);
      await credentials.reserveAttempt(uid, org, MAX);
      await credentials.setBlocked(uid, org, new Date(Date.now() + 60000));
      expect(await credentials.unblock(uid, org)).to.equal(true);
      const c = await credentials.find(uid, org);
      expect(c!.isBlocked).to.equal(false);
      expect(c!.wrongCredentialCount).to.equal(0);
      expect(await credentials.unblock(uid, org)).to.equal(false);
    });

    it('blockedUserIds lists locked people, optionally among some', async () => {
      const org = await fx.newOrg();
      const [a, b, c] = [await acct(org), await acct(org), await acct(org)];
      for (const id of [a, b, c]) await credentials.savePassword(id, org, 'hash-value', 'ip');
      for (const id of [a, b]) {
        await credentials.reserveAttempt(id, org, MAX);
        await credentials.setBlocked(id, org, new Date(Date.now() + 60000));
      }
      expect((await credentials.blockedUserIds(org)).sort()).to.deep.equal([a, b].sort());
      expect(await credentials.blockedUserIds(org, [b, c])).to.deep.equal([b]);
      expect(await credentials.blockedUserIds(org, [])).to.deep.equal([]);
      expect(await credentials.blockedUserIds(await fx.newOrg())).to.deep.equal([]);
    });

    it('clearPassword drops the password of a removed account; deleteForUser drops the credential', async () => {
      const org = await fx.newOrg();
      const u = await person(org);
      await credentials.savePassword(u._id, org, 'hash-value', 'ip');
      await users.markDeleted(org, u._id);
      await credentials.clearPassword(u._id, u.email);
      expect((await credentials.find(u._id, org))?.hashedPassword).to.equal(undefined);
      await credentials.deleteForUser(u._id, u.email);
      expect(await credentials.find(u._id, org)).to.equal(null);
    });
  });

  describe('login: activity', () => {
    it('latestSessionEnding returns the newest invalidating activity only', async () => {
      const org = await fx.newOrg();
      const uid = fx.newId();
      const base = { userId: uid, orgId: org, ipAddress: 'ip' };
      expect(await activities.latestSessionEnding(uid, org)).to.equal(null);
      await activities.record({ ...base, activityType: 'LOGOUT' });
      await new Promise((r) => setTimeout(r, 5));
      await activities.record({ ...base, activityType: 'ROLE CHANGED' });
      await new Promise((r) => setTimeout(r, 5));
      await activities.record({ ...base, activityType: 'LOGIN' });
      const latest = await activities.latestSessionEnding(uid, org);
      expect(latest!.activityType).to.equal('ROLE CHANGED');
      isStr(latest!._id);
      isStr(latest!.userId);
      expect(latest!.createdAt).to.be.instanceOf(Date);
    });

    it('latestSessionEnding is scoped to the person and organisation', async () => {
      const org = await fx.newOrg();
      const uid = fx.newId();
      await activities.record({ userId: uid, orgId: org, ipAddress: 'ip', activityType: 'LOGOUT' });
      expect(await activities.latestSessionEnding(uid, await fx.newOrg())).to.equal(null);
      expect(await activities.latestSessionEnding(fx.newId(), org)).to.equal(null);
    });

    it('latestPasswordChange ignores other types', async () => {
      const org = await fx.newOrg();
      const uid = fx.newId();
      const base = { userId: uid, orgId: org, ipAddress: 'ip' };
      await activities.record({ ...base, activityType: 'LOGOUT' });
      expect(await activities.latestPasswordChange(uid, org)).to.equal(null);
      await activities.record({ ...base, activityType: 'PASSWORD CHANGED' });
      expect((await activities.latestPasswordChange(uid, org))!.activityType).to.equal('PASSWORD CHANGED');
    });
  });

  describe('activity logging', () => {
    it('record and recordMany write rows the reads can see', async () => {
      const org = await fx.newOrg();
      const [a, b] = [fx.newId(), fx.newId()];
      await activities.record({ email: email(), activityType: 'LOGIN', ipAddress: 'ip', loginMode: 'PASSWORD' });
      await activities.recordMany([
        { orgId: org, userId: a, email: email(), activityType: 'ROLE CHANGED', ipAddress: 'ip' },
        { orgId: org, userId: b, email: email(), activityType: 'ROLE CHANGED', ipAddress: 'ip' },
      ]);
      expect((await activities.latestSessionEnding(a, org))!.activityType).to.equal('ROLE CHANGED');
      expect((await activities.latestSessionEnding(b, org))!.activityType).to.equal('ROLE CHANGED');
    });

    it('a row without an ipAddress is rejected, and so is an empty one (required)', async () => {
      const org = await fx.newOrg();
      let err: unknown;
      try {
        await activities.record({ orgId: org, userId: fx.newId(), activityType: 'LOGIN', ipAddress: undefined });
      } catch (e) {
        err = e;
      }
      expect(err).to.be.instanceOf(Error);
      let err2: unknown;
      try {
        await activities.recordMany([{ orgId: org, userId: fx.newId(), activityType: 'LOGIN', ipAddress: '' }]);
      } catch (e) {
        err2 = e;
      }
      expect(err2).to.be.instanceOf(Error);
    });

    it('an unknown activity type is rejected', async () => {
      let err: unknown;
      try {
        await activities.record({ activityType: 'NOT A TYPE', ipAddress: 'ip' });
      } catch (e) {
        err = e;
      }
      expect(err).to.be.instanceOf(Error);
    });
  });

  describe('pre-org lookups by email', () => {
    it('listLiveByEmail and findActiveByEmail match the address, skip removed accounts and honour the organisation', async () => {
      const o1 = await fx.newOrg();
      const o2 = await fx.newOrg();
      const addr = email('Mixed').toLowerCase();
      const a = await users.create(o1, { email: addr, fullName: 'A' });
      expect((await users.listLiveByEmail(addr, ['_id', 'orgId'])).map((r) => r._id)).to.deep.equal([a._id]);
      expect(await users.listLiveByEmail(addr, ['_id'], o2)).to.deep.equal([]);
      expect(await users.listLiveByEmail(addr, ['_id'], o1)).to.have.length(1);
      expect(await users.listLiveByEmail(email(), ['_id'])).to.deep.equal([]);
      expect(await users.findActiveByEmail(addr)).to.deep.equal({ _id: a._id, orgId: o1, email: addr });
      expect((await users.listLiveMembershipsByEmail(addr))).to.deep.equal([{ _id: a._id, orgId: o1 }]);
      await users.markDeleted(o1, a._id);
      expect(await users.listLiveByEmail(addr, ['_id'])).to.deep.equal([]);
      expect(await users.listLiveMembershipsByEmail(addr)).to.deep.equal([]);
      expect(await users.findActiveByEmail(addr)).to.equal(null);
    });

    it('listSignInEligible keeps people who signed in and are neither disabled nor service accounts; userIdsWithPassword names those with a password', async () => {
      const org = await fx.newOrg();
      const ok = await person(org, { hasLoggedIn: true });
      await person(org, { hasLoggedIn: false });
      await person(org, { hasLoggedIn: true, isDisabled: true });
      const eligible = await users.listSignInEligible(org);
      expect(eligible.map((r) => r._id)).to.deep.equal([ok._id]);
      expect(await credentials.userIdsWithPassword(org, [ok._id])).to.deep.equal([]);
      await credentials.savePassword(ok._id, org, 'hash-value', '1.1.1.1');
      expect(await credentials.userIdsWithPassword(org, [ok._id])).to.deep.equal([ok._id]);
      expect(await credentials.userIdsWithPassword(await fx.newOrg(), [ok._id])).to.deep.equal([]);
    });

    it('listLiveFieldsByIds returns the named fields of live accounts only', async () => {
      const org = await fx.newOrg();
      const a = await person(org);
      const gone = await person(org);
      await users.markDeleted(org, gone._id);
      const rows = await users.listLiveFieldsByIds([a._id, gone._id], ['_id', 'email']);
      expect(rows.map((r) => r._id)).to.deep.equal([a._id]);
    });

    it('listSessionEndingSince returns session-ending activity newest first, from the given time on', async () => {
      const org = await fx.newOrg();
      const uid = fx.newId();
      const base = { userId: uid, orgId: org, ipAddress: 'ip' };
      await activities.record({ ...base, activityType: 'LOGOUT' });
      await new Promise((r) => setTimeout(r, 5));
      await activities.record({ ...base, activityType: 'LOGIN' });
      await activities.record({ ...base, activityType: 'ROLE CHANGED' });
      const all = await activities.listSessionEndingSince([uid], new Date(0));
      expect(all.map((r) => r.activityType)).to.deep.equal(['ROLE CHANGED', 'LOGOUT']);
      expect(await activities.listSessionEndingSince([uid], new Date(Date.now() + 60_000))).to.deep.equal([]);
    });

    it('oldestOrgId is the oldest organisation, orgCount counts them', async () => {
      const before = await identity.orgCount(false);
      const live = await identity.orgCount(true);
      const o = await fx.newOrg();
      expect(await identity.orgCount(false)).to.equal(before + 1);
      expect(await identity.orgCount(true)).to.equal(live + 1);
      expect(await identity.oldestOrgId()).to.be.a('string');
      expect(await identity.oldestOrgId()).to.not.equal(o);
    });

    it('recordPreOrgActivity takes an address, a known type and an address of origin', async () => {
      await identity.recordPreOrgActivity({ email: email(), activityType: 'LOGIN ATTEMPT', loginMode: 'OTP', ipAddress: 'ip' });
      let err: unknown;
      try {
        await identity.recordPreOrgActivity({ email: email(), activityType: 'NOT A TYPE', ipAddress: 'ip' });
      } catch (e) {
        err = e;
      }
      expect(err).to.be.instanceOf(Error);
    });

    it('getByEmail finds an account in any state; existsLiveEmail only live ones', async () => {
      const org = await fx.newOrg();
      const a = await person(org);
      expect((await users.getByEmail(a.email))!._id).to.equal(a._id);
      expect(await users.existsLiveEmail(a.email)).to.equal(true);
      await users.markDeleted(org, a._id);
      expect((await users.getByEmail(a.email))!.isDeleted).to.equal(true);
      expect(await users.existsLiveEmail(a.email)).to.equal(false);
      expect(await users.getByEmail(email())).to.equal(null);
    });

    it('emailTakenByOther is per organisation and live only; emailTakenElsewhere is store-wide', async () => {
      const [o1, o2] = [await fx.newOrg(), await fx.newOrg()];
      const a = await person(o1);
      const b = await person(o1);
      expect(await users.emailTakenByOther(o1, a.email, b._id)).to.equal(true);
      expect(await users.emailTakenByOther(o1, a.email, a._id)).to.equal(false);
      expect(await users.emailTakenByOther(o2, a.email, b._id)).to.equal(false);
      expect(await users.emailTakenElsewhere(a.email, b._id)).to.equal(true);
      expect(await users.emailTakenElsewhere(a.email, a._id)).to.equal(false);
      await users.markDeleted(o1, a._id);
      expect(await users.emailTakenByOther(o1, a.email, b._id)).to.equal(false);
      expect(await users.emailTakenElsewhere(a.email, b._id)).to.equal(true);
    });

    it('hasRemovedAccount is true only for a removed account of that organisation', async () => {
      const [o1, o2] = [await fx.newOrg(), await fx.newOrg()];
      const a = await person(o1);
      expect(await users.hasRemovedAccount(o1, a.email)).to.equal(false);
      await users.markDeleted(o1, a._id);
      expect(await users.hasRemovedAccount(o1, a.email)).to.equal(true);
      expect(await users.hasRemovedAccount(o2, a.email)).to.equal(false);
    });
  });

  describe('invites', () => {
    it('listByEmails includes removed accounts and stays in the organisation', async () => {
      const [o1, o2] = [await fx.newOrg(), await fx.newOrg()];
      const live = await person(o1);
      const gone = await person(o1);
      const other = await person(o2);
      await users.markDeleted(o1, gone._id);
      const rows = await users.listByEmails(o1, [live.email, gone.email, other.email]);
      expect(rows.map((r) => r._id).sort()).to.deep.equal([live._id, gone._id].sort());
      expect(rows.find((r) => r._id === gone._id)!.isDeleted).to.equal(true);
    });

    it('createMany creates invitees with a slug and the given role', async () => {
      const org = await fx.newOrg();
      const rows = await users.createMany(org, [
        { email: email(), isDeleted: false, hasLoggedIn: false, role: 'member' },
        { email: email(), isDeleted: false, hasLoggedIn: false, role: 'admin' },
      ]);
      expect(rows).to.have.length(2);
      for (const r of rows) {
        isStr(r._id);
        expect(r.orgId).to.equal(org);
        isStr(r.slug);
      }
      expect(rows.map((r) => r.role)).to.deep.equal(['member', 'admin']);
    });

    it('restoreByEmails restores only removed accounts of that organisation', async () => {
      const [o1, o2] = [await fx.newOrg(), await fx.newOrg()];
      const gone = await person(o1);
      const live = await person(o1);
      const otherGone = await person(o2);
      await users.markDeleted(o1, gone._id);
      await users.markDeleted(o2, otherGone._id);
      const restored = await users.restoreByEmails(o1, [gone.email, otherGone.email]);
      expect(restored.map((r) => r._id)).to.deep.equal([gone._id]);
      expect(restored[0]!.isDeleted).to.equal(false);
      expect((await users.getLive(o1, gone._id))!._id).to.equal(gone._id);
      expect(await users.getLive(o2, otherGone._id)).to.equal(null);
      expect((await users.getLive(o1, live._id))!._id).to.equal(live._id);
    });

    it('promoteToAdmin promotes people; a batch that reaches a service account is refused by the schema', async () => {
      const org = await fx.newOrg();
      const p = await person(org);
      const s = await users.create(org, { email: svcEmail(org), fullName: 'svc', kind: 'service' });
      await users.promoteToAdmin(org, [p._id]);
      expect((await users.getLive(org, p._id))!.role).to.equal('admin');
      // Pins today's behaviour: the `kind: {$ne: 'service'}` narrowing does not help,
      // because the schema hook re-queries the ids with kind 'service' and throws.
      let err: unknown;
      try {
        await users.promoteToAdmin(org, [(await person(org))._id, s._id]);
      } catch (e) {
        err = e;
      }
      expect((err as Error).message).to.equal('A service account cannot be an administrator');
      expect((await users.getLive(org, s._id))!.role).to.equal('member');
    });

    it('group membership: addToEveryone, addMemberToGroups, removeFromEveryone, removeFromAll', async () => {
      const org = await fx.newOrg();
      const every = await groups.create(org, { name: uniq('every'), type: 'everyone' });
      const g1 = await groups.create(org, { name: uniq('g'), type: 'custom' });
      const g2 = await groups.create(org, { name: uniq('g'), type: 'custom' });
      const p = await person(org);
      await groups.addToEveryone(org, p._id);
      await groups.addToEveryone(org, p._id); // idempotent
      await groups.addMemberToGroups(org, [g1._id, g2._id], p._id);
      expect((await groups.get(org, every._id))!.users).to.deep.equal([p._id]);
      expect((await groups.groupsOfUser(org, p._id)).map((g) => g._id).sort()).to.deep.equal(
        [every._id, g1._id, g2._id].sort(),
      );
      await groups.removeFromEveryone(org, p._id);
      expect((await groups.get(org, every._id))!.users).to.deep.equal([]);
      await groups.removeFromAll(org, p._id);
      expect(await groups.groupsOfUser(org, p._id)).to.deep.equal([]);
    });

    it('addMemberToGroups reaches deleted groups too; addMembers does not', async () => {
      const org = await fx.newOrg();
      const g = await groups.create(org, { name: uniq('g'), type: 'custom' });
      await groups.softDelete(org, g._id);
      const p = await person(org);
      expect(await groups.addMembers(org, [g._id], [p._id])).to.equal(0);
      await groups.addMemberToGroups(org, [g._id], p._id);
      expect((await groups.get(org, g._id))!.users).to.deep.equal([p._id]);
    });
  });

  describe('password reset links', () => {
    const linkOrg = fx.newId();
    const claim = (linkHash: string) => resetLinks.claim({ linkHash, userId: fx.newId(), orgId: linkOrg, expiresAt: new Date(Date.now() + 60000) });

    it('hashResetLink is deterministic and does not echo the token', () => {
      expect(hashResetLink('t')).to.equal(hashResetLink('t'));
      expect(hashResetLink('t')).to.not.equal(hashResetLink('u'));
      expect(hashResetLink('t')).to.match(/^[0-9a-f]{64}$/);
    });

    it('claim twice: true then false', async () => {
      const h = hashResetLink(uniq('tok'));
      expect(await claim(h)).to.equal(true);
      expect(await claim(h)).to.equal(false);
    });

    it('five racing claims: exactly one wins', async () => {
      const h = hashResetLink(uniq('tok'));
      const r = await Promise.all([1, 2, 3, 4, 5].map(() => claim(h)));
      expect(r.filter(Boolean)).to.have.length(1);
    });

    it('release hands the link back for another claim', async () => {
      const h = hashResetLink(uniq('tok'));
      expect(await claim(h)).to.equal(true);
      await resetLinks.release(h);
      expect(await claim(h)).to.equal(true);
    });
  });

  describe('groups', () => {
    it('a live duplicate name is a UniqueConstraintError on name; after soft delete the name is free', async () => {
      const org = await fx.newOrg();
      const name = uniq('dup');
      const g = await groups.create(org, { name, type: 'custom' });
      isStr(g._id);
      isStr(g.slug);
      expect(g.users).to.deep.equal([]);
      let err: unknown;
      try {
        await groups.create(org, { name, type: 'custom' });
      } catch (e) {
        err = e;
      }
      expect(err).to.be.instanceOf(UniqueConstraintError);
      expect(String((err as UniqueConstraintError).metadata?.key)).to.include('name');
      // Another organisation may use the name.
      await groups.create(await fx.newOrg(), { name, type: 'custom' });
      await groups.softDelete(org, g._id);
      await groups.create(org, { name, type: 'custom' });
    });

    it('rename changes the name, refuses a taken name, and returns null for a deleted group', async () => {
      const org = await fx.newOrg();
      const a = await groups.create(org, { name: uniq('a'), type: 'custom' });
      const b = await groups.create(org, { name: uniq('b'), type: 'custom' });
      const renamed = await groups.rename(org, a._id, uniq('new'));
      expect(renamed!.name).to.not.equal(a.name);
      let err: unknown;
      try {
        await groups.rename(org, b._id, renamed!.name);
      } catch (e) {
        err = e;
      }
      expect(err).to.be.instanceOf(UniqueConstraintError);
      await groups.softDelete(org, b._id);
      expect(await groups.rename(org, b._id, uniq('x'))).to.equal(null);
      expect(await groups.rename(await fx.newOrg(), a._id, uniq('x'))).to.equal(null);
    });

    it('nameTaken: live groups only, with an optional exception', async () => {
      const org = await fx.newOrg();
      const g = await groups.create(org, { name: uniq('n'), type: 'custom' });
      expect(await groups.nameTaken(org, g.name)).to.equal(true);
      expect(await groups.nameTaken(org, g.name, g._id)).to.equal(false);
      expect(await groups.nameTaken(await fx.newOrg(), g.name)).to.equal(false);
      await groups.softDelete(org, g._id);
      expect(await groups.nameTaken(org, g.name)).to.equal(false);
    });

    it('softDelete marks the group deleted with the actor; get still sees it, getLive does not', async () => {
      const org = await fx.newOrg();
      const actor = fx.newId();
      const g = await groups.create(org, { name: uniq('s'), type: 'custom' });
      const d = await groups.softDelete(org, g._id, actor);
      expect(d!.isDeleted).to.equal(true);
      expect(d!.deletedBy).to.equal(actor);
      expect((await groups.get(org, g._id))!.isDeleted).to.equal(true);
      expect(await groups.getLive(org, g._id)).to.equal(null);
      expect(await groups.softDelete(org, g._id)).to.equal(null);
    });

    it('addMembers and removeMembers return how many groups changed', async () => {
      const org = await fx.newOrg();
      const g1 = await groups.create(org, { name: uniq('g'), type: 'custom' });
      const g2 = await groups.create(org, { name: uniq('g'), type: 'custom' });
      const [u1, u2] = [fx.newId(), fx.newId()];
      expect(await groups.addMembers(org, [g1._id, g2._id], [u1, u2])).to.equal(2);
      // timestamps bump updatedAt (millisecond resolution), so wait a tick: a repeat add then counts both groups as modified
      await new Promise((r) => setTimeout(r, 3));
      expect(await groups.addMembers(org, [g1._id, g2._id], [u1, u2])).to.equal(2);
      expect((await groups.getLive(org, g1._id))!.users.sort()).to.deep.equal([u1, u2].sort());
      expect(await groups.removeMembers(org, [g1._id], [u1])).to.equal(1);
      expect((await groups.getLive(org, g1._id))!.users).to.deep.equal([u2]);
      await new Promise((r) => setTimeout(r, 3));
      expect(await groups.removeMembers(org, [g1._id], [u1])).to.equal(1);
      expect(await groups.addMembers(await fx.newOrg(), [g1._id], [u1])).to.equal(0);
    });

    it('groupsOfUser returns id, name and type of live groups', async () => {
      const org = await fx.newOrg();
      const g = await groups.create(org, { name: uniq('g'), type: 'standard' });
      const gone = await groups.create(org, { name: uniq('g'), type: 'custom' });
      const u = fx.newId();
      await groups.addMemberToGroups(org, [g._id, gone._id], u);
      await groups.softDelete(org, gone._id);
      const r = await groups.groupsOfUser(org, u);
      expect(r).to.deep.equal([{ _id: g._id, name: g.name, type: 'standard' }]);
    });

    it('summariesForUsers returns live groups containing any of the people, with member ids as strings', async () => {
      const org = await fx.newOrg();
      const g = await groups.create(org, { name: uniq('g'), type: 'custom' });
      const other = await groups.create(org, { name: uniq('g'), type: 'custom' });
      const [u1, u2, u3] = [fx.newId(), fx.newId(), fx.newId()];
      await groups.addMembers(org, [g._id], [u1, u2]);
      await groups.addMembers(org, [other._id], [u3]);
      const r = await groups.summariesForUsers(org, [u1]);
      expect(r).to.have.length(1);
      expect(r[0]!._id).to.equal(g._id);
      expect(r[0]!.users.sort()).to.deep.equal([u1, u2].sort());
      expect(await groups.summariesForUsers(await fx.newOrg(), [u1])).to.deep.equal([]);
    });

    it('stats groups live groups by name with member totals', async () => {
      const org = await fx.newOrg();
      const g = await groups.create(org, { name: uniq('g'), type: 'custom' });
      const gone = await groups.create(org, { name: uniq('g'), type: 'custom' });
      await groups.addMembers(org, [g._id], [fx.newId(), fx.newId()]);
      await groups.softDelete(org, gone._id);
      const s = await groups.stats(org);
      expect(s).to.deep.equal([{ _id: g.name, count: 1, totalUsers: 2, avgUsers: 2 }]);
    });

    it('list filters by search and creation dates and pages', async () => {
      const org = await fx.newOrg();
      const tag = uniq('Lst');
      for (let i = 0; i < 3; i++) await groups.create(org, { name: `${tag}-${i}`, type: 'custom' });
      await groups.create(org, { name: uniq('other'), type: 'custom' });
      const all = await groups.list(org, { skip: 0, limit: 10 });
      expect(all.total).to.equal(4);
      const found = await groups.list(org, { skip: 0, limit: 10, search: tag.toLowerCase() });
      expect(found.total).to.equal(3);
      const page = await groups.list(org, { skip: 2, limit: 2 });
      expect(page.rows).to.have.length(2);
      expect(page.total).to.equal(4);
      isStr(page.rows[0]!._id);
      const future = new Date(Date.now() + 86400000).toISOString();
      expect((await groups.list(org, { skip: 0, limit: 10, createdAfter: future })).total).to.equal(0);
      const past = new Date(Date.now() - 86400000).toISOString();
      expect((await groups.list(org, { skip: 0, limit: 10, createdBefore: past })).total).to.equal(0);
      const today = new Date().toISOString().slice(0, 10);
      expect((await groups.list(org, { skip: 0, limit: 10, createdBefore: today })).total).to.equal(4);
    });
  });

  describe('users', () => {
    it('create assigns a slug and string ids; a duplicate address is a UniqueConstraintError on email', async () => {
      const org = await fx.newOrg();
      const addr = email('Dup');
      const u = await users.create(org, { email: addr, fullName: ' Ann ' });
      isStr(u._id);
      expect(u.orgId).to.equal(org);
      isStr(u.slug);
      expect(u.email).to.equal(addr.toLowerCase());
      expect(u.fullName).to.equal('Ann');
      expect(u.role).to.equal('member');
      expect(u.isDeleted).to.equal(false);
      expect(u.createdAt).to.be.instanceOf(Date);
      let err: unknown;
      try {
        await users.create(org, { email: addr });
      } catch (e) {
        err = e;
      }
      expect(err).to.be.instanceOf(UniqueConstraintError);
      expect(String((err as UniqueConstraintError).metadata?.key)).to.include('email');
    });

    it('getLive, getAny, getLiveById, getAccountState, getAuthSnapshot, getRoleSnapshot', async () => {
      const org = await fx.newOrg();
      const u = await person(org, { role: 'admin' });
      expect((await users.getLive(org, u._id))!._id).to.equal(u._id);
      expect(await users.getLive(await fx.newOrg(), u._id)).to.equal(null);
      expect((await users.getLiveById(u._id))!.orgId).to.equal(org);
      expect(await users.getAccountState(u._id)).to.include({ kind: 'human', isDisabled: false });
      const snap = await users.getAuthSnapshot(org, u._id);
      expect(snap).to.include({ email: u.email, role: 'admin' });
      expect(await users.getAuthSnapshot(await fx.newOrg(), u._id)).to.equal(null);
      expect(await users.getRoleSnapshot(org, u._id)).to.include({ role: 'admin' });
      await users.markDeleted(org, u._id);
      expect(await users.getLive(org, u._id)).to.equal(null);
      expect(await users.getLiveById(u._id)).to.equal(null);
      expect(await users.getAccountState(u._id)).to.equal(null);
      expect(await users.getAuthSnapshot(org, u._id)).to.equal(null);
      expect(await users.getRoleSnapshot(org, u._id)).to.equal(null);
      expect((await users.getAny(org, u._id))!.isDeleted).to.equal(true);
      expect(await users.getAny(await fx.newOrg(), u._id)).to.equal(null);
    });

    it('getForEdit returns the live account with defaults applied; getEmail only the address', async () => {
      const [org, other] = [await fx.newOrg(), await fx.newOrg()];
      const u = await person(org);
      const e = await users.getForEdit(org, u._id);
      expect(e).to.include({ _id: u._id, role: 'member', hasLoggedIn: false });
      expect(await users.getEmail(org, u._id)).to.deep.equal({ _id: u._id, email: u.email });
      expect(await users.getForEdit(other, u._id)).to.equal(null);
      expect(await users.getEmail(other, u._id)).to.equal(null);
      await users.markDeleted(org, u._id);
      expect(await users.getForEdit(org, u._id)).to.equal(null);
      expect(await users.getEmail(org, u._id)).to.equal(null);
    });

    it('discardForUser removes the credential written for an unfinished account', async () => {
      const org = await fx.newOrg();
      const u = await person(org);
      await credentials.createWithPassword({ userId: u._id, orgId: org, hashedPassword: 'hash-value', ipAddress: 'ip' });
      await users.markDeleted(org, u._id);
      await credentials.discardForUser(u._id, u.email);
      expect(await credentials.find(u._id, org)).to.equal(null);
      await credentials.discardForUser(u._id, u.email); // nothing left: no error
    });

    it('updateProfile writes fields; null for a removed or other-organisation account', async () => {
      const [org, other] = [await fx.newOrg(), await fx.newOrg()];
      const u = await person(org);
      const up = await users.updateProfile(org, u._id, { fullName: 'New Name', designation: 'Dev' });
      expect(up).to.include({ fullName: 'New Name', designation: 'Dev' });
      expect((await users.getLive(org, u._id))!.designation).to.equal('Dev');
      expect(await users.updateProfile(other, u._id, { fullName: 'x' })).to.equal(null);
      await users.markDeleted(org, u._id);
      expect(await users.updateProfile(org, u._id, { fullName: 'x' })).to.equal(null);
    });

    it('markDeleted keeps the row, optionally signs the person out and records who deleted', async () => {
      const org = await fx.newOrg();
      const actor = fx.newId();
      const u = await person(org, { hasLoggedIn: true });
      const d = await users.markDeleted(org, u._id, { signedOut: true, deletedBy: actor });
      expect(d).to.include({ isDeleted: true, hasLoggedIn: false, deletedBy: actor });
      const v = await person(org, { hasLoggedIn: true });
      const d2 = await users.markDeleted(org, v._id);
      expect(d2).to.include({ isDeleted: true, hasLoggedIn: true });
      expect(await users.markDeleted(await fx.newOrg(), v._id)).to.equal(null);
    });

    it('countActiveAdmins and listAdminIds exclude removed accounts and service accounts', async () => {
      const org = await fx.newOrg();
      const a1 = await person(org, { role: 'admin' });
      const a2 = await person(org, { role: 'admin' });
      await person(org);
      await users.create(org, { email: svcEmail(org), kind: 'service', fullName: 's' });
      expect(await users.countActiveAdmins(org)).to.equal(2);
      expect((await users.listAdminIds(org)).sort()).to.deep.equal([a1._id, a2._id].sort());
      await users.markDeleted(org, a2._id);
      expect(await users.countActiveAdmins(org)).to.equal(1);
      expect(await users.listAdminIds(org)).to.deep.equal([a1._id]);
      expect(await users.countActiveAdmins(await fx.newOrg())).to.equal(0);
    });

    it('setRole changes the role of a not-removed account', async () => {
      const org = await fx.newOrg();
      const u = await person(org);
      await users.setRole(org, u._id, 'admin');
      expect((await users.getLive(org, u._id))!.role).to.equal('admin');
      await users.setRole(await fx.newOrg(), u._id, 'member');
      expect((await users.getLive(org, u._id))!.role).to.equal('admin');
    });

    it('lockOrgAdminChanges runs inside a unit of work and countActiveAdmins reads within it', async () => {
      const org = await fx.newOrg();
      await person(org, { role: 'admin' });
      const seen = await fx.uow.run(async (tx) => {
        await users.lockOrgAdminChanges(org, tx);
        return users.countActiveAdmins(org, tx);
      }, { org });
      expect(seen).to.equal(1);
    });

    it('updateProfile inside a failing unit of work rolls back only when transactional', async () => {
      const org = await fx.newOrg();
      const u = await person(org);
      let err: unknown;
      try {
        await fx.uow.run(async (tx) => {
          await users.updateProfile(org, u._id, { designation: 'tx' }, tx);
          throw new Error('abort');
        }, { org });
      } catch (e) {
        err = e;
      }
      expect((err as Error).message).to.equal('abort');
      const d = (await users.getLive(org, u._id))!.designation;
      expect(d).to.equal(fx.transactional ? undefined : 'tx');
    });

    it('listDirectory: search, service accounts, hasLoggedIn, paging and sort by name', async () => {
      const org = await fx.newOrg();
      const tag = uniq('Dir');
      const mk = (name: string, extra: Record<string, unknown> = {}) =>
        users.create(org, { email: email(), fullName: `${tag} ${name}`, ...extra });
      const c = await mk('C');
      const a = await mk('A', { hasLoggedIn: true });
      const b = await mk('B');
      const svc = await users.create(org, { email: svcEmail(org), kind: 'service', fullName: `${tag} S` });
      await mk('Gone').then((g) => users.markDeleted(org, g._id));
      const base = { skip: 0, limit: 10, includeServiceAccounts: false };
      const all = await users.listDirectory(org, base);
      expect(all.rows.map((r) => r._id)).to.deep.equal([a._id, b._id, c._id]);
      expect(all.total).to.equal(3);
      const withSvc = await users.listDirectory(org, { ...base, includeServiceAccounts: true });
      expect(withSvc.rows.map((r) => r._id)).to.deep.equal([a._id, b._id, c._id, svc._id]);
      expect((await users.listDirectory(org, { ...base, search: 'zzz-no-match' })).total).to.equal(0);
      expect((await users.listDirectory(org, { ...base, search: tag.toLowerCase() })).total).to.equal(3);
      expect((await users.listDirectory(org, { ...base, search: c.email.toUpperCase() })).rows.map((r) => r._id)).to.deep.equal([c._id]);
      expect((await users.listDirectory(org, { ...base, hasLoggedIn: true })).rows.map((r) => r._id)).to.deep.equal([a._id]);
      expect((await users.listDirectory(org, { ...base, hasLoggedIn: false })).total).to.equal(2);
      const page = await users.listDirectory(org, { ...base, skip: 1, limit: 1 });
      expect(page.rows.map((r) => r._id)).to.deep.equal([b._id]);
      expect(page.total).to.equal(3);
      expect(await users.listDirectory(await fx.newOrg(), base)).to.deep.equal({ rows: [], total: 0 });
    });

    it('listDirectory: people with the same name keep one order (by id) and pages neither repeat nor skip one', async () => {
      const org = await fx.newOrg();
      const created = [] as string[];
      for (let i = 0; i < 5; i++) created.push((await users.create(org, { email: email(), fullName: 'Same Name' }))._id);
      const base = { limit: 2, includeServiceAccounts: false };
      const paged: string[] = [];
      for (let skip = 0; skip < 5; skip += 2) paged.push(...(await users.listDirectory(org, { ...base, skip })).rows.map((r) => r._id));
      expect(paged).to.deep.equal([...created].sort());
    });

    it('listDirectory: blocked true/false combine with credentials, search and hasLoggedIn', async () => {
      const org = await fx.newOrg();
      const mk = (name: string, extra: Record<string, unknown> = {}) =>
        users.create(org, { email: email(), fullName: name, ...extra });
      const blockedIn = await mk('Bi', { hasLoggedIn: true });
      const blockedOut = await mk('Bo', { hasLoggedIn: false });
      const free = await mk('Fr', { hasLoggedIn: true });
      for (const u of [blockedIn, blockedOut]) {
        await credentials.savePassword(u._id, org, 'hash-value', 'ip');
        await credentials.reserveAttempt(u._id, org, MAX);
        await credentials.setBlocked(u._id, org, new Date(Date.now() + 60000));
      }
      const q = { skip: 0, limit: 10, includeServiceAccounts: false };
      const ids = async (x: object) => (await users.listDirectory(org, { ...q, ...x })).rows.map((r) => r._id).sort();
      expect(await ids({ blocked: true })).to.deep.equal([blockedIn._id, blockedOut._id].sort());
      expect(await ids({ blocked: false })).to.deep.equal([free._id]);
      // blocked + hasLoggedIn means "either condition"
      expect(await ids({ blocked: true, hasLoggedIn: true })).to.deep.equal([blockedIn._id, blockedOut._id, free._id].sort());
      expect(await ids({ blocked: false, hasLoggedIn: false })).to.deep.equal([]);
      expect(await ids({ blocked: true, search: 'Bo' })).to.deep.equal([blockedOut._id]);
    });

    it('listDirectory: groupIds restricts to members; an empty match returns nothing', async () => {
      const org = await fx.newOrg();
      const a = await person(org);
      await person(org);
      const g = await groups.create(org, { name: uniq('g'), type: 'custom' });
      const empty = await groups.create(org, { name: uniq('g'), type: 'custom' });
      await groups.addMembers(org, [g._id], [a._id]);
      const q = { skip: 0, limit: 10, includeServiceAccounts: false };
      expect((await users.listDirectory(org, { ...q, groupIds: [g._id] })).rows.map((r) => r._id)).to.deep.equal([a._id]);
      expect((await users.listDirectory(org, { ...q, groupIds: [empty._id] })).total).to.equal(0);
      expect((await users.listDirectory(org, { ...q, groupIds: [] })).total).to.equal(2);
    });

    it('listLiveByIds, listAllLive, listSummariesByIds stay in the organisation', async () => {
      const [org, other] = [await fx.newOrg(), await fx.newOrg()];
      const a = await person(org);
      const b = await person(org);
      const gone = await person(org);
      await users.markDeleted(org, gone._id);
      const o = await person(other);
      expect((await users.listLiveByIds(org, [a._id, gone._id, o._id])).map((r) => r._id)).to.deep.equal([a._id]);
      expect((await users.listAllLive(org)).map((r) => r._id).sort()).to.deep.equal([a._id, b._id].sort());
      const sums = await users.listSummariesByIds(org, [a._id, gone._id, o._id]);
      expect(sums.map((r) => r._id).sort()).to.deep.equal([a._id, gone._id].sort());
      isStr(sums[0]!._id);
      expect(await users.listSummariesByIds(other, [a._id])).to.deep.equal([]);
    });

    it('listWithGroups lists live people with the names and types of their live groups', async () => {
      const org = await fx.newOrg();
      const a = await person(org);
      const b = await person(org);
      const g = await groups.create(org, { name: uniq('g'), type: 'custom' });
      const gone = await groups.create(org, { name: uniq('g'), type: 'custom' });
      await groups.addMemberToGroups(org, [g._id, gone._id], a._id);
      await groups.softDelete(org, gone._id);
      const rows = await users.listWithGroups(org);
      const byId = new Map(rows.map((r) => [r._id, r]));
      expect(byId.size).to.equal(2);
      expect(byId.get(a._id)!.groups).to.deep.equal([{ name: g.name, type: 'custom' }]);
      expect(byId.get(b._id)!.groups).to.deep.equal([]);
      isStr(byId.get(a._id)!.orgId);
      expect(byId.get(a._id)).to.not.have.property('email');
    });

    it('listMembersPage pages members, searches by name or address, skips removed accounts', async () => {
      const org = await fx.newOrg();
      const tag = uniq('Mem');
      const a = await users.create(org, { email: email(), fullName: `${tag} one` });
      const b = await users.create(org, { email: email(), fullName: `${tag} two` });
      const gone = await users.create(org, { email: email(), fullName: `${tag} gone` });
      await users.markDeleted(org, gone._id);
      const ids = [a._id, b._id, gone._id];
      const all = await users.listMembersPage(org, ids, { skip: 0, limit: 10 });
      expect(all.total).to.equal(2);
      expect(all.rows.map((r) => r._id).sort()).to.deep.equal([a._id, b._id].sort());
      expect((await users.listMembersPage(org, ids, { skip: 0, limit: 10, search: `${tag} ONE` })).rows.map((r) => r._id)).to.deep.equal([a._id]);
      expect((await users.listMembersPage(org, ids, { skip: 1, limit: 1 })).rows).to.have.length(1);
      expect((await users.listMembersPage(org, [], { skip: 0, limit: 10 })).total).to.equal(0);
    });

    it('listMembersPage lists only accounts of the organisation, even when a group holds an id from another', async () => {
      const org = await fx.newOrg();
      const other = await fx.newOrg();
      const mine = await users.create(org, { email: email(), fullName: 'Mem mine' });
      const theirs = await users.create(other, { email: email(), fullName: 'Mem theirs' });
      const page = await users.listMembersPage(org, [mine._id, theirs._id], { skip: 0, limit: 10 });
      expect(page.rows.map((r) => r._id)).to.deep.equal([mine._id]);
      expect(page.total).to.equal(1);
    });

    it('service accounts stay out of listWithGroups and listMembersPage unless asked for, and serviceAccountIdsAmong names them', async () => {
      const org = await fx.newOrg();
      const other = await fx.newOrg();
      const tag = uniq('Svc');
      const p = await users.create(org, { email: email(), fullName: `${tag} person` });
      const s = await users.create(org, { email: svcEmail(org), kind: 'service', fullName: `${tag} bot` });
      const ids = [p._id, s._id];

      const plain = await users.listWithGroups(org);
      expect(plain.map((r) => r._id)).to.deep.equal([p._id]);
      const admin = await users.listWithGroups(org, { includeServiceAccounts: true });
      expect(admin.map((r) => r._id).sort()).to.deep.equal([p._id, s._id].sort());

      const page = await users.listMembersPage(org, ids, { skip: 0, limit: 10 });
      expect(page.rows.map((r) => r._id)).to.deep.equal([p._id]);
      expect(page.total).to.equal(1); // the count agrees with the page
      const adminPage = await users.listMembersPage(org, ids, { skip: 0, limit: 10, includeServiceAccounts: true });
      expect(adminPage.total).to.equal(2);
      expect((await users.listMembersPage(org, ids, { skip: 0, limit: 10, search: `${tag} bot` })).total).to.equal(0);

      expect(await users.serviceAccountIdsAmong(org, ids)).to.deep.equal([s._id]);
      expect(await users.serviceAccountIdsAmong(org, [p._id])).to.deep.equal([]);
      expect(await users.serviceAccountIdsAmong(org, [])).to.deep.equal([]);
      expect(await users.serviceAccountIdsAmong(other, ids)).to.deep.equal([]);
    });

    it('sample accounts: listRemovedIdsByEmail, deleteRemoved, deleteById, setDisabledByEmailDomain', async () => {
      const org = await fx.newOrg();
      const domain = `${uniq('demo')}.example`;
      const mk = (n: string) => users.create(org, { email: `${n}${uniq('')}@${domain}`, fullName: n });
      const caller = await mk('caller');
      const x = await mk('x');
      const stranger = await person(org);
      const n = await users.setDisabledByEmailDomain(org, domain, caller._id, true);
      expect(n).to.equal(1);
      expect((await users.getLive(org, x._id))!.isDisabled).to.equal(true);
      expect((await users.getLive(org, caller._id))!.isDisabled).to.equal(false);
      expect((await users.getLive(org, stranger._id))!.isDisabled).to.equal(false);
      expect(await users.setDisabledByEmailDomain(org, domain, undefined, false)).to.equal(2);
      expect(await users.setDisabledByEmailDomain(await fx.newOrg(), domain, undefined, true)).to.equal(0);

      await users.markDeleted(org, x._id);
      expect(await users.listRemovedIdsByEmail(org, x.email)).to.deep.equal([x._id]);
      expect(await users.listRemovedIdsByEmail(await fx.newOrg(), x.email)).to.deep.equal([]);
      expect(await users.deleteRemoved(org, caller._id)).to.equal(false); // live
      expect(await users.deleteRemoved(org, x._id)).to.equal(true);
      expect(await users.getAny(org, x._id)).to.equal(null);
      await users.deleteById(caller._id);
      expect(await users.getAny(org, caller._id)).to.equal(null);
    });

    it('changeEmail moves the account; correctEmail rewrites the address', async () => {
      const org = await fx.newOrg();
      const u = await person(org);
      const next = email();
      expect((await users.changeEmail(u._id, next))!.email).to.equal(next);
      expect(await users.changeEmail(fx.newId(), email())).to.equal(null);
      const again = email();
      await users.correctEmail(u._id, again);
      expect((await users.getLive(org, u._id))!.email).to.equal(again);
    });
  });

  describe('service accounts', () => {
    const mkSvc = async (org: string) =>
      users.create(org, { email: svcEmail(org), fullName: 'svc', kind: 'service', description: 'old' });

    it('list and get see live service accounts of the organisation only', async () => {
      const [org, other] = [await fx.newOrg(), await fx.newOrg()];
      const s1 = await mkSvc(org);
      await new Promise((r) => setTimeout(r, 5));
      const s2 = await mkSvc(org);
      await person(org);
      await mkSvc(other);
      expect((await users.listServiceAccounts(org)).map((r) => r._id)).to.deep.equal([s2._id, s1._id]); // newest first
      expect((await users.getServiceAccount(org, s1._id))!._id).to.equal(s1._id);
      expect(await users.getServiceAccount(other, s1._id)).to.equal(null);
      const human = await person(org);
      expect(await users.getServiceAccount(org, human._id)).to.equal(null);
    });

    it('restore: only a removed service account, disabled and marked until finished', async () => {
      const org = await fx.newOrg();
      const s = await mkSvc(org);
      const input = { fullName: 'again', description: undefined, restoreOpId: uniq('op'), role: 'member' as const };
      expect(await users.restoreServiceAccount(org, s._id, input)).to.equal(null); // still live
      await users.markDeleted(org, s._id);
      expect(await users.restoreServiceAccount(await fx.newOrg(), s._id, input)).to.equal(null);
      const r = await users.restoreServiceAccount(org, s._id, input);
      expect(r).to.include({ isDeleted: false, isDisabled: true, fullName: 'again', restoreOpId: input.restoreOpId });
      expect(r!.description).to.equal(undefined); // old text cleared
      expect(r!.deletedBy).to.equal(undefined);
      // while restoring, enabling is refused
      expect(await users.enableServiceAccount(org, s._id)).to.equal(false);
      // a different attempt cannot finish it
      expect(await users.finishServiceAccountRestore(s._id, 'someone-else')).to.equal(null);
      const fin = await users.finishServiceAccountRestore(s._id, input.restoreOpId);
      expect(fin).to.include({ isDisabled: false });
      expect(fin!.restoreOpId).to.equal(undefined);
      expect(await users.finishServiceAccountRestore(s._id, input.restoreOpId)).to.equal(null);
    });

    it('restore keeps a new description when one is given', async () => {
      const org = await fx.newOrg();
      const s = await mkSvc(org);
      await users.markDeleted(org, s._id);
      const r = await users.restoreServiceAccount(org, s._id, { fullName: 'n', description: 'fresh', restoreOpId: uniq('op'), role: 'member' });
      expect(r!.description).to.equal('fresh');
    });

    it('abort puts a restore back only for the attempt that started it', async () => {
      const org = await fx.newOrg();
      const s = await mkSvc(org);
      await users.markDeleted(org, s._id);
      const op = uniq('op');
      await users.restoreServiceAccount(org, s._id, { fullName: 'n', restoreOpId: op, role: 'member' });
      await users.abortServiceAccountRestore(s._id, 'other-attempt');
      expect((await users.getAny(org, s._id))!.isDeleted).to.equal(false);
      await users.abortServiceAccountRestore(s._id, op);
      const after = await users.getAny(org, s._id);
      expect(after!.isDeleted).to.equal(true);
      expect(after!.restoreOpId).to.equal(undefined);
    });

    it('enable and update change a live service account', async () => {
      const org = await fx.newOrg();
      const s = await mkSvc(org);
      await users.updateServiceAccount(org, s._id, { isDisabled: true, fullName: 'renamed' });
      expect(await users.getServiceAccount(org, s._id)).to.include({ isDisabled: true, fullName: 'renamed' });
      expect(await users.enableServiceAccount(org, s._id)).to.equal(true);
      expect((await users.getServiceAccount(org, s._id))!.isDisabled).to.equal(false);
      expect(await users.enableServiceAccount(await fx.newOrg(), s._id)).to.equal(false);
      const human = await person(org);
      expect(await users.enableServiceAccount(org, human._id)).to.equal(false);
    });
  });

  describe('org auth config', () => {
    it('createDefault stores one password step; get/getLive/allowsPassword read it', async () => {
      const org = await fx.newOrg();
      expect(await orgAuth.get(org)).to.equal(null);
      expect(await orgAuth.allowsPassword(org)).to.equal(false);
      await orgAuth.createDefault(org, 'example.com');
      const c = await orgAuth.getLive(org);
      isStr(c!._id);
      expect(c!.orgId).to.equal(org);
      expect(c!.domain).to.equal('example.com');
      expect(c!.authSteps).to.deep.equal([{ order: 1, allowedMethods: [{ type: 'password' }] }]);
      expect((await orgAuth.get(org))!._id).to.equal(c!._id);
      expect(await orgAuth.allowsPassword(org)).to.equal(true);
      expect(await orgAuth.removedSignInMethods(org)).to.deep.equal([]);
      expect(await orgAuth.getLive(await fx.newOrg())).to.equal(null);
    });

    it('createDefault inside a unit of work', async () => {
      const org = await fx.newOrg();
      await fx.uow.run((tx) => orgAuth.createDefault(org, undefined, tx), { org });
      expect((await orgAuth.getLive(org))!.orgId).to.equal(org);
    });

    it('getFirstLive and anyLive are store-wide', async () => {
      await fx.clearOrgAuth();
      expect(await orgAuth.anyLive()).to.equal(false);
      expect(await orgAuth.getFirstLive()).to.equal(null);
      const org = await fx.newOrg();
      await orgAuth.createDefault(org, undefined);
      expect(await orgAuth.anyLive()).to.equal(true);
      expect((await orgAuth.getFirstLive())!.orgId).to.equal(org);
    });

    it('replaceSteps changes the steps; allowsPassword follows; bad step counts fail validation', async () => {
      const org = await fx.newOrg();
      await orgAuth.createDefault(org, undefined);
      const r = await orgAuth.replaceSteps(org, [{ order: 1, allowedMethods: [{ type: 'google' }] }]);
      expect(r!.authSteps).to.deep.equal([{ order: 1, allowedMethods: [{ type: 'google' }] }]);
      expect(await orgAuth.allowsPassword(org)).to.equal(false);
      for (const steps of [
        [],
        [1, 2, 3, 4].map((order) => ({ order, allowedMethods: [{ type: 'otp' }] })),
        [{ order: 1, allowedMethods: [] }, { order: 1, allowedMethods: [] }],
      ]) {
        let err: unknown;
        try {
          await orgAuth.replaceSteps(org, steps);
        } catch (e) {
          err = e;
        }
        expect(err, JSON.stringify(steps)).to.be.instanceOf(Error);
      }
      expect(await orgAuth.replaceSteps(await fx.newOrg(), [{ order: 1, allowedMethods: [] }])).to.equal(null);
    });
  });

  describe('pictures', () => {
    it('set upserts, get reads, remove keeps the row with pic null, listSet skips null', async () => {
      const org = await fx.newOrg();
      const [u1, u2] = [fx.newId(), fx.newId()];
      expect(await pictures.get(org, u1)).to.equal(null);
      expect(await pictures.remove(org, u1)).to.equal(null);
      await pictures.set(org, u1, 'AAA', 'image/jpeg');
      await pictures.set(org, u1, 'BBB', 'image/jpeg'); // replace
      await pictures.set(org, u2, 'CCC', 'image/jpeg');
      const p = await pictures.get(org, u1);
      isStr(p!._id);
      expect(p).to.include({ userId: u1, orgId: org, pic: 'BBB', mimeType: 'image/jpeg' });
      expect((await pictures.listSet(org, [u1, u2])).map((x) => x.userId).sort()).to.deep.equal([u1, u2].sort());
      const removed = await pictures.remove(org, u1);
      expect(removed).to.include({ pic: null, mimeType: null });
      expect((await pictures.get(org, u1))!.pic).to.equal(null);
      expect((await pictures.listSet(org, [u1, u2])).map((x) => x.userId)).to.deep.equal([u2]);
    });
  });

  describe('isolation: another organisation sees nothing', () => {
    it('every org-scoped read is empty for a different organisation', async () => {
      const [org, other] = [await fx.newOrg(), await fx.newOrg()];
      const u = await person(org, { role: 'admin' });
      const g = await groups.create(org, { name: uniq('g'), type: 'custom' });
      await groups.addMembers(org, [g._id], [u._id]);
      await credentials.savePassword(u._id, org, 'hash-value', 'ip');
      await pictures.set(org, u._id, 'P', 'image/jpeg');
      await orgAuth.createDefault(org, undefined);
      expect(await users.getLive(other, u._id)).to.equal(null);
      expect(await users.getAny(other, u._id)).to.equal(null);
      expect(await users.listLiveByIds(other, [u._id])).to.deep.equal([]);
      expect(await users.listAllLive(other)).to.deep.equal([]);
      expect(await users.listWithGroups(other)).to.deep.equal([]);
      expect(await users.listAdminIds(other)).to.deep.equal([]);
      expect(await groups.get(other, g._id)).to.equal(null);
      expect(await groups.getLive(other, g._id)).to.equal(null);
      expect(await groups.groupsOfUser(other, u._id)).to.deep.equal([]);
      expect((await groups.list(other, { skip: 0, limit: 10 })).total).to.equal(0);
      expect(await credentials.find(u._id, other)).to.equal(null);
      expect(await pictures.get(other, u._id)).to.equal(null);
      expect(await pictures.listSet(other, [u._id])).to.deep.equal([]);
      expect(await orgAuth.getLive(other)).to.equal(null);
    });
  });
}
