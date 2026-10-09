import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import {
  normalizeUserRole,
  resolveOptionalUserRole,
  toDisplayUserRole,
  getActiveUserOrgRole,
  isUserOrgAdmin,
  findOrgAdminUserIds,
  assertCanDemoteAdmin,
  assertCanPromoteAdmin,
  saveUserEnsuringOrgRetainsAdmin,
  saveUserEnsuringAdminCap,
  MAX_ORG_ADMINS,
  MAX_ORG_ADMINS_MESSAGE,
  ADMIN_LIMIT_ENFORCEMENT_DATE,
  selectAdminToRetain,
  getOrgAdminLimitStatus,
  getOrgAdminLimit,
  setOrgAdminLimit,
  transferOrgAdmin,
} from '../../../../src/modules/user_management/services/user-admin.service';
import { Users } from '../../../../src/modules/user_management/schema/users.schema';
import { Org } from '../../../../src/modules/user_management/schema/org.schema';
import { UserGroups } from '../../../../src/modules/user_management/schema/userGroup.schema';
import { UserCredentials } from '../../../../src/modules/auth/schema/userCredentials.schema';

function stubUsersFindOne(role: 'admin' | 'member' | null | undefined) {
  const doc =
    role === undefined
      ? null
      : role === null
        ? {}
        : { role };
  return sinon.stub(Users, 'findOne').returns({
    select: sinon.stub().returns({
      lean: sinon.stub().resolves(doc),
    }),
  } as any);
}

describe('user-admin.service', () => {
  const userId = new mongoose.Types.ObjectId().toString();
  const orgId = new mongoose.Types.ObjectId().toString();

  afterEach(() => {
    sinon.restore();
    setOrgAdminLimit(MAX_ORG_ADMINS);
  });

  describe('normalizeUserRole', () => {
    it('returns admin for Admin / ADMIN / admin with whitespace', () => {
      expect(normalizeUserRole('admin')).to.equal('admin');
      expect(normalizeUserRole('Admin')).to.equal('admin');
      expect(normalizeUserRole('  ADMIN  ')).to.equal('admin');
    });

    it('returns member for Member / MEMBER / member', () => {
      expect(normalizeUserRole('member')).to.equal('member');
      expect(normalizeUserRole('Member')).to.equal('member');
      expect(normalizeUserRole('MEMBER')).to.equal('member');
    });

    it('returns null for empty or unsupported values', () => {
      expect(normalizeUserRole(null)).to.equal(null);
      expect(normalizeUserRole(undefined)).to.equal(null);
      expect(normalizeUserRole('')).to.equal(null);
      expect(normalizeUserRole('guest')).to.equal(null);
      expect(normalizeUserRole('owner')).to.equal(null);
    });
  });

  describe('resolveOptionalUserRole', () => {
    it('defaults to member when role is absent or blank', () => {
      expect(resolveOptionalUserRole(undefined)).to.equal('member');
      expect(resolveOptionalUserRole(null)).to.equal('member');
      expect(resolveOptionalUserRole('')).to.equal('member');
      expect(resolveOptionalUserRole('   ')).to.equal('member');
    });

    it('returns normalized admin / member when valid', () => {
      expect(resolveOptionalUserRole('Admin')).to.equal('admin');
      expect(resolveOptionalUserRole('member')).to.equal('member');
    });

    it('throws BadRequestError for invalid supplied roles', () => {
      try {
        resolveOptionalUserRole('admn');
        expect.fail('expected BadRequestError');
      } catch (error: any) {
        expect(error.message).to.equal('Invalid role. Must be admin or member');
      }
    });
  });

  describe('toDisplayUserRole', () => {
    it('maps admin variants to Admin', () => {
      expect(toDisplayUserRole('admin')).to.equal('Admin');
      expect(toDisplayUserRole('Admin')).to.equal('Admin');
    });

    it('maps everything else to Member', () => {
      expect(toDisplayUserRole('member')).to.equal('Member');
      expect(toDisplayUserRole(undefined)).to.equal('Member');
      expect(toDisplayUserRole(null)).to.equal('Member');
      expect(toDisplayUserRole('unknown')).to.equal('Member');
    });
  });

  describe('isUserOrgAdmin', () => {
    it('returns true when user.role is admin', async () => {
      stubUsersFindOne('admin');
      const groupsStub = sinon.stub(UserGroups, 'find');

      const result = await isUserOrgAdmin(userId, orgId);

      expect(result).to.equal(true);
      expect(groupsStub.called).to.equal(false);
    });

    it('returns false when user.role is member without querying groups', async () => {
      stubUsersFindOne('member');
      const groupsStub = sinon.stub(UserGroups, 'find');

      const result = await isUserOrgAdmin(userId, orgId);

      expect(result).to.equal(false);
      expect(groupsStub.called).to.equal(false);
    });

    it('returns false when role is unset', async () => {
      stubUsersFindOne(null);
      const groupsStub = sinon.stub(UserGroups, 'find');

      const result = await isUserOrgAdmin(userId, orgId);

      expect(result).to.equal(false);
      expect(groupsStub.called).to.equal(false);
    });

    it('returns false for missing/deleted users without querying groups', async () => {
      stubUsersFindOne(undefined);
      const groupsStub = sinon.stub(UserGroups, 'find');

      const result = await isUserOrgAdmin(userId, orgId);

      expect(result).to.equal(false);
      expect(groupsStub.called).to.equal(false);
    });

    it('queries Users with the expected filter', async () => {
      const findOneStub = stubUsersFindOne('admin');

      await isUserOrgAdmin(userId, orgId);

      expect(findOneStub.calledOnce).to.equal(true);
      expect(findOneStub.firstCall.args[0]).to.deep.equal({
        _id: userId,
        orgId,
        isDeleted: { $ne: true },
      });
    });

    it('returns false for invalid ObjectIds without querying', async () => {
      const findOneStub = sinon.stub(Users, 'findOne');

      const result = await isUserOrgAdmin('not-an-id', orgId);

      expect(result).to.equal(false);
      expect(findOneStub.called).to.equal(false);
    });
  });

  describe('getActiveUserOrgRole', () => {
    it('returns admin for an active admin', async () => {
      stubUsersFindOne('admin');
      expect(await getActiveUserOrgRole(userId, orgId)).to.equal('admin');
    });

    it('returns member for an active member', async () => {
      stubUsersFindOne('member');
      expect(await getActiveUserOrgRole(userId, orgId)).to.equal('member');
    });

    it('returns member when the stored role is unset', async () => {
      stubUsersFindOne(null);
      expect(await getActiveUserOrgRole(userId, orgId)).to.equal('member');
    });

    it('returns null for a missing or deleted user', async () => {
      stubUsersFindOne(undefined);
      expect(await getActiveUserOrgRole(userId, orgId)).to.equal(null);
    });

    it('returns null for invalid ObjectIds without querying', async () => {
      const findOneStub = sinon.stub(Users, 'findOne');

      expect(await getActiveUserOrgRole(userId, 'not-an-id')).to.equal(null);
      expect(findOneStub.called).to.equal(false);
    });

    it('only considers active users of the org', async () => {
      const findOneStub = stubUsersFindOne('admin');

      await getActiveUserOrgRole(userId, orgId);

      expect(findOneStub.firstCall.args[0]).to.deep.equal({
        _id: userId,
        orgId,
        isDeleted: { $ne: true },
      });
    });
  });

  describe('findOrgAdminUserIds', () => {
    it('returns admin user ids with the shared active-admin filter', async () => {
      const adminId = new mongoose.Types.ObjectId();
      const lean = sinon.stub().resolves([{ _id: adminId }]);
      const select = sinon.stub().returns({ lean });
      const findStub = sinon.stub(Users, 'find').returns({ select } as any);

      const result = await findOrgAdminUserIds(orgId);

      expect(result).to.deep.equal([adminId.toString()]);
      expect(findStub.firstCall.args[0]).to.deep.equal({
        orgId,
        role: 'admin',
        isDeleted: { $ne: true },
        // Service accounts are excluded so they cannot be counted as one of
        // an organisation's administrators: were one counted, the last person
        // who can actually sign in could be demoted.
        kind: { $ne: 'service' },
      });
    });

    it('does not include legacy admin-group members', async () => {
      const roleAdmin = new mongoose.Types.ObjectId();
      const groupsFind = sinon.stub(UserGroups, 'find');

      sinon.stub(Users, 'find').returns({
        select: sinon.stub().returns({
          lean: sinon.stub().resolves([{ _id: roleAdmin }]),
        }),
      } as any);

      const result = await findOrgAdminUserIds(orgId);

      expect(result).to.deep.equal([roleAdmin.toString()]);
      expect(groupsFind.called).to.equal(false);
    });

    it('skips malformed users and invalid ids', async () => {
      const validId = new mongoose.Types.ObjectId();

      sinon.stub(Users, 'find').returns({
        select: sinon.stub().returns({
          lean: sinon.stub().resolves([
            null,
            {},
            { _id: 'not-an-object-id' },
            { _id: validId },
          ]),
        }),
      } as any);

      const result = await findOrgAdminUserIds(orgId);

      expect(result).to.deep.equal([validId.toString()]);
    });
  });

  describe('assertCanDemoteAdmin', () => {
    it('allows demotion when more than one admin exists', async () => {
      const countStub = sinon.stub(Users, 'countDocuments').resolves(2);

      await assertCanDemoteAdmin(orgId);

      expect(countStub.calledOnce).to.equal(true);
    });

    it('rejects when only one admin remains', async () => {
      sinon.stub(Users, 'countDocuments').resolves(1);

      try {
        await assertCanDemoteAdmin(orgId);
        expect.fail('expected BadRequestError');
      } catch (error: any) {
        expect(error.message).to.equal(
          'Cannot demote the last admin. Promote another user to admin first.',
        );
      }
    });

    it('passes the session through to count when provided', async () => {
      const session = { id: 'test-session' } as any;
      const sessionStub = sinon.stub().callsFake(() => Promise.resolve(2));
      sinon.stub(Users, 'countDocuments').returns({
        session: sessionStub,
      } as any);

      await assertCanDemoteAdmin(orgId, session);

      expect(sessionStub.calledOnce).to.equal(true);
      expect(sessionStub.firstCall.args[0]).to.equal(session);
    });
  });

  describe('assertCanPromoteAdmin', () => {
    it('caps an organization at a single admin', () => {
      expect(MAX_ORG_ADMINS).to.equal(1);
      expect(MAX_ORG_ADMINS_MESSAGE).to.equal(
        'An organization can have at most 1 admin.',
      );
    });

    it('allows promotion when the org has no admins', async () => {
      const countStub = sinon.stub(Users, 'countDocuments').resolves(0);

      await assertCanPromoteAdmin(orgId);

      expect(countStub.calledOnce).to.equal(true);
    });

    it('rejects a second admin when the org already has one', async () => {
      sinon.stub(Users, 'countDocuments').resolves(1);

      try {
        await assertCanPromoteAdmin(orgId);
        expect.fail('expected BadRequestError');
      } catch (error: any) {
        expect(error.message).to.equal(MAX_ORG_ADMINS_MESSAGE);
      }
    });

    it('rejects adding two admins at once to an org with none', async () => {
      sinon.stub(Users, 'countDocuments').resolves(0);

      try {
        await assertCanPromoteAdmin(orgId, 2);
        expect.fail('expected BadRequestError');
      } catch (error: any) {
        expect(error.message).to.equal(MAX_ORG_ADMINS_MESSAGE);
      }
    });

    it('rejects an org that is already over the cap', async () => {
      sinon.stub(Users, 'countDocuments').resolves(5);

      try {
        await assertCanPromoteAdmin(orgId);
        expect.fail('expected BadRequestError');
      } catch (error: any) {
        expect(error.message).to.equal(MAX_ORG_ADMINS_MESSAGE);
      }
    });

    it('applies the cap inside the provided session', async () => {
      const session = { id: 'test-session' } as any;
      const sessionStub = sinon.stub().callsFake(() => Promise.resolve(1));
      sinon.stub(Users, 'countDocuments').returns({
        session: sessionStub,
      } as any);

      try {
        await assertCanPromoteAdmin(orgId, 1, session);
        expect.fail('expected BadRequestError');
      } catch (error: any) {
        expect(error.message).to.equal(MAX_ORG_ADMINS_MESSAGE);
      }
      expect(sessionStub.firstCall.args[0]).to.equal(session);
    });

    it('skips the count when additionalAdmins is 0', async () => {
      const countStub = sinon.stub(Users, 'countDocuments');

      await assertCanPromoteAdmin(orgId, 0);

      expect(countStub.called).to.equal(false);
    });
  });

  describe('saveUserEnsuringOrgRetainsAdmin', () => {
    it('checks then saves when replica set is unavailable and admins remain', async () => {
      const save = sinon.stub().resolves();
      const user = {
        _id: userId,
        orgId,
        role: 'member',
        save,
      };
      const countStub = sinon.stub(Users, 'countDocuments');
      countStub.onFirstCall().resolves(2); // pre-check
      countStub.onSecondCall().resolves(1); // post-save verify

      await saveUserEnsuringOrgRetainsAdmin(user as any, false);

      expect(save.calledOnce).to.equal(true);
      expect(save.firstCall.args[0]).to.equal(undefined);
      expect(countStub.callCount).to.equal(2);
    });

    it('restores admin when non-RS save leaves the org with zero admins', async () => {
      const save = sinon.stub().resolves();
      const user = {
        _id: userId,
        orgId,
        role: 'member',
        save,
      };
      const countStub = sinon.stub(Users, 'countDocuments');
      countStub.onFirstCall().resolves(2);
      countStub.onSecondCall().resolves(0);
      const updateStub = sinon.stub(Users, 'updateOne').resolves({} as any);

      try {
        await saveUserEnsuringOrgRetainsAdmin(user as any, false);
        expect.fail('expected BadRequestError');
      } catch (error: any) {
        expect(error.message).to.equal(
          'Cannot demote the last admin. Promote another user to admin first.',
        );
      }

      expect(save.calledOnce).to.equal(true);
      expect(updateStub.calledOnce).to.equal(true);
      expect(updateStub.firstCall.args[1]).to.deep.equal({
        $set: { role: 'admin' },
      });
      expect(user.role).to.equal('admin');
    });

    it('touches Org then checks then saves inside a transaction when RS is available', async () => {
      const save = sinon.stub().resolves();
      const user = {
        _id: userId,
        orgId,
        save,
      };
      const withTransaction = sinon.stub().callsFake(async (fn: () => Promise<void>) => {
        await fn();
      });
      const endSession = sinon.stub().resolves();
      sinon.stub(mongoose, 'startSession').resolves({
        withTransaction,
        endSession,
      } as any);
      sinon.stub(Users, 'countDocuments').returns({
        session: sinon.stub().callsFake(() => Promise.resolve(2)),
      } as any);
      const orgUpdate = sinon.stub(Org, 'updateOne').resolves({} as any);

      await saveUserEnsuringOrgRetainsAdmin(user as any, true);

      expect(withTransaction.calledOnce).to.equal(true);
      expect(orgUpdate.calledOnce).to.equal(true);
      expect(orgUpdate.firstCall.args[0]).to.deep.include({ _id: orgId });
      expect(orgUpdate.firstCall.args[1]).to.have.nested.property(
        '$set.adminRoleGuardAt',
      );
      expect(save.calledOnce).to.equal(true);
      expect(save.firstCall.args[0]).to.have.property('session');
      expect(endSession.calledOnce).to.equal(true);
    });

    it('does not save when pre-check finds this would demote the last admin', async () => {
      const save = sinon.stub().resolves();
      const user = {
        _id: userId,
        orgId,
        save,
      };
      const withTransaction = sinon.stub().callsFake(async (fn: () => Promise<void>) => {
        await fn();
      });
      sinon.stub(mongoose, 'startSession').resolves({
        withTransaction,
        endSession: sinon.stub().resolves(),
      } as any);
      sinon.stub(Org, 'updateOne').resolves({} as any);
      sinon.stub(Users, 'countDocuments').returns({
        session: sinon.stub().callsFake(() => Promise.resolve(1)),
      } as any);

      try {
        await saveUserEnsuringOrgRetainsAdmin(user as any, true);
        expect.fail('expected BadRequestError');
      } catch (error: any) {
        expect(error.message).to.equal(
          'Cannot demote the last admin. Promote another user to admin first.',
        );
      }

      expect(save.called).to.equal(false);
    });
  });

  describe('saveUserEnsuringAdminCap', () => {
    it('checks then saves when replica set is unavailable and the cap is not exceeded', async () => {
      const save = sinon.stub().resolves();
      const user = {
        _id: userId,
        orgId,
        role: 'admin',
        save,
      };
      const countStub = sinon.stub(Users, 'countDocuments');
      countStub.onFirstCall().resolves(0);
      countStub.onSecondCall().resolves(1);

      await saveUserEnsuringAdminCap(user as any, false);

      expect(save.calledOnce).to.equal(true);
      expect(countStub.callCount).to.equal(2);
    });

    it('does not save on non-RS when the org already has an admin', async () => {
      const save = sinon.stub().resolves();
      const user = { _id: userId, orgId, role: 'admin', save };
      sinon.stub(Users, 'countDocuments').resolves(1);

      try {
        await saveUserEnsuringAdminCap(user as any, false);
        expect.fail('expected BadRequestError');
      } catch (error: any) {
        expect(error.message).to.equal(MAX_ORG_ADMINS_MESSAGE);
      }

      expect(save.called).to.equal(false);
    });

    it('saves inside a transaction when RS is available and the org has no admin', async () => {
      const save = sinon.stub().resolves();
      const user = { _id: userId, orgId, role: 'admin', save };
      const withTransaction = sinon.stub().callsFake(async (fn: () => Promise<void>) => {
        await fn();
      });
      const endSession = sinon.stub().resolves();
      sinon.stub(mongoose, 'startSession').resolves({
        withTransaction,
        endSession,
      } as any);
      sinon.stub(Org, 'updateOne').resolves({} as any);
      sinon.stub(Users, 'countDocuments').returns({
        session: sinon.stub().callsFake(() => Promise.resolve(0)),
      } as any);

      await saveUserEnsuringAdminCap(user as any, true);

      expect(save.calledOnce).to.equal(true);
      expect(save.firstCall.args[0]).to.have.property('session');
      expect(endSession.calledOnce).to.equal(true);
    });

    it('restores member when non-RS save leaves the org over the cap', async () => {
      const save = sinon.stub().resolves();
      const user = {
        _id: userId,
        orgId,
        role: 'admin',
        save,
      };
      const countStub = sinon.stub(Users, 'countDocuments');
      countStub.onFirstCall().resolves(0);
      countStub.onSecondCall().resolves(2);
      const updateStub = sinon.stub(Users, 'updateOne').resolves({} as any);

      try {
        await saveUserEnsuringAdminCap(user as any, false);
        expect.fail('expected BadRequestError');
      } catch (error: any) {
        expect(error.message).to.equal(MAX_ORG_ADMINS_MESSAGE);
      }

      expect(save.calledOnce).to.equal(true);
      expect(updateStub.calledOnce).to.equal(true);
      expect(updateStub.firstCall.args[1]).to.deep.equal({
        $set: { role: 'member' },
      });
      expect(user.role).to.equal('member');
    });

    it('does not save when pre-check finds the org already has an admin', async () => {
      const save = sinon.stub().resolves();
      const user = {
        _id: userId,
        orgId,
        save,
      };
      const withTransaction = sinon.stub().callsFake(async (fn: () => Promise<void>) => {
        await fn();
      });
      sinon.stub(mongoose, 'startSession').resolves({
        withTransaction,
        endSession: sinon.stub().resolves(),
      } as any);
      sinon.stub(Org, 'updateOne').resolves({} as any);
      sinon.stub(Users, 'countDocuments').returns({
        session: sinon.stub().callsFake(() => Promise.resolve(1)),
      } as any);

      try {
        await saveUserEnsuringAdminCap(user as any, true);
        expect.fail('expected BadRequestError');
      } catch (error: any) {
        expect(error.message).to.equal(MAX_ORG_ADMINS_MESSAGE);
      }

      expect(save.called).to.equal(false);
    });
  });
  describe('selectAdminToRetain', () => {
    const a = { _id: 'a', email: 'a@x.test', createdAt: new Date('2025-02-01') };
    const b = { _id: 'b', email: 'b@x.test', createdAt: new Date('2025-01-01') };
    const c = { _id: 'c', email: 'c@x.test', createdAt: new Date('2025-01-01') };

    it('returns null when there are no admins', () => {
      expect(selectAdminToRetain([], 'a@x.test')).to.equal(null);
    });

    it('prefers the org contact, ignoring case and whitespace', () => {
      expect(selectAdminToRetain([b, a], '  A@X.test ')).to.equal(a);
    });

    it('falls back to the earliest admin, breaking ties by id', () => {
      expect(selectAdminToRetain([a, c, b], 'nobody@x.test')).to.equal(b);
      expect(selectAdminToRetain([a, c, b], null)).to.equal(b);
    });

    it('puts admins without a createdAt last', () => {
      const undated = { _id: '0', email: 'u@x.test', createdAt: null };
      expect(selectAdminToRetain([undated, a])).to.equal(a);
    });
  });

  describe('getOrgAdminLimitStatus', () => {
    function stubAdmins(admins: any[]) {
      sinon.stub(Users, 'find').returns({
        select: sinon.stub().returns({ lean: sinon.stub().resolves(admins) }),
      } as any);
    }

    it('is not over the limit with a single admin and does not look up the contact', async () => {
      stubAdmins([{ _id: 'a', email: 'a@x.test' }]);
      const orgFind = sinon.stub(Org, 'findOne');

      const status = await getOrgAdminLimitStatus(orgId);

      expect(status).to.deep.equal({
        adminCount: 1,
        maxAdmins: 1,
        overLimit: false,
        enforcementDate: ADMIN_LIMIT_ENFORCEMENT_DATE.toISOString(),
        retainedAdminEmail: null,
      });
      expect(orgFind.called).to.equal(false);
    });

    it('reports the admin who will be kept when the org is over the limit', async () => {
      stubAdmins([
        { _id: 'a', email: 'a@x.test', createdAt: new Date('2025-01-01') },
        { _id: 'b', email: 'owner@x.test', createdAt: new Date('2025-06-01') },
      ]);
      sinon.stub(Org, 'findOne').returns({
        select: sinon.stub().returns({
          lean: sinon.stub().resolves({ contactEmail: 'owner@x.test' }),
        }),
      } as any);

      const status = await getOrgAdminLimitStatus(orgId);

      expect(status.adminCount).to.equal(2);
      expect(status.overLimit).to.equal(true);
      expect(status.retainedAdminEmail).to.equal('owner@x.test');
    });
  });

  describe('edition admin limit', () => {
    it('defaults to the Community Edition limit', () => {
      expect(getOrgAdminLimit()).to.equal(MAX_ORG_ADMINS);
    });

    it('lets an edition with no limit promote past MAX_ORG_ADMINS', async () => {
      setOrgAdminLimit(null);
      const countStub = sinon.stub(Users, 'countDocuments').resolves(7);

      await assertCanPromoteAdmin(orgId, 3);

      expect(countStub.called).to.equal(false);
    });

    it('saves a promotion with no admin count or transaction when there is no limit', async () => {
      setOrgAdminLimit(null);
      const save = sinon.stub().resolves();
      const countStub = sinon.stub(Users, 'countDocuments').resolves(7);
      const sessionStub = sinon.stub(mongoose, 'startSession');

      await saveUserEnsuringAdminCap({ _id: userId, orgId, role: 'admin', save } as any, true);

      expect(save.calledOnce).to.equal(true);
      expect(countStub.called).to.equal(false);
      expect(sessionStub.called).to.equal(false);
    });

    it('never reports an org over the limit when there is no limit', async () => {
      setOrgAdminLimit(null);
      sinon.stub(Users, 'find').returns({
        select: sinon.stub().returns({
          lean: sinon.stub().resolves([{ _id: 'a' }, { _id: 'b' }, { _id: 'c' }]),
        }),
      } as any);
      const orgFind = sinon.stub(Org, 'findOne');

      const status = await getOrgAdminLimitStatus(orgId);

      expect(status.overLimit).to.equal(false);
      expect(status.maxAdmins).to.equal(null);
      expect(orgFind.called).to.equal(false);
    });
  });

  describe('transferOrgAdmin', () => {
    const newAdminId = new mongoose.Types.ObjectId().toString();

    function stubRoleWrites(results: Array<unknown>) {
      const stub = sinon.stub(Users, 'findOneAndUpdate');
      results.forEach((result, i) => {
        stub.onCall(i).returns({ lean: sinon.stub().resolves(result) } as any);
      });
      return stub;
    }

    beforeEach(() => {
      sinon.stub(UserCredentials, 'exists').resolves(null);
    });

    it('promotes the member before demoting the admin when there is no replica set', async () => {
      const writes = stubRoleWrites([
        { _id: newAdminId, email: 'new@x.test' },
        { _id: userId, email: 'old@x.test' },
      ]);

      const result = await transferOrgAdmin(orgId, userId, newAdminId, false);

      expect(writes.firstCall.args[0]).to.include({ _id: newAdminId, hasLoggedIn: true });
      expect(writes.firstCall.args[1]).to.deep.equal({ $set: { role: 'admin' } });
      expect(writes.secondCall.args[0]).to.include({ _id: userId, role: 'admin' });
      expect(writes.secondCall.args[1]).to.deep.equal({ $set: { role: 'member' } });
      expect(result).to.deep.equal({
        previousAdmin: { userId, email: 'old@x.test' },
        newAdmin: { userId: newAdminId, email: 'new@x.test' },
      });
    });

    it('puts the member back when the caller turns out not to be an admin (no replica set)', async () => {
      stubRoleWrites([{ _id: newAdminId, email: 'new@x.test' }, null]);
      const restore = sinon.stub(Users, 'updateOne').resolves({} as any);

      try {
        await transferOrgAdmin(orgId, userId, newAdminId, false);
        expect.fail('expected ForbiddenError');
      } catch (error: any) {
        expect(error.name).to.equal('ForbiddenError');
      }
      expect(restore.calledOnce).to.equal(true);
      expect(restore.firstCall.args[0]).to.include({ _id: newAdminId });
      expect(restore.firstCall.args[1]).to.deep.equal({ $set: { role: 'member' } });
    });

    it('swaps both roles inside one transaction guarded on the org', async () => {
      const writes = stubRoleWrites([
        { _id: newAdminId, email: 'new@x.test' },
        { _id: userId, email: 'old@x.test' },
      ]);
      const withTransaction = sinon.stub().callsFake(async (fn: () => Promise<void>) => fn());
      const endSession = sinon.stub().resolves();
      const session = { withTransaction, endSession };
      sinon.stub(mongoose, 'startSession').resolves(session as any);
      const orgUpdate = sinon.stub(Org, 'updateOne').resolves({} as any);

      const result = await transferOrgAdmin(orgId, userId, newAdminId, true);

      expect(withTransaction.calledOnce).to.equal(true);
      expect(orgUpdate.calledOnce).to.equal(true);
      expect(writes.firstCall.args[2]).to.include({ session });
      expect(writes.secondCall.args[2]).to.include({ session });
      expect(endSession.calledOnce).to.equal(true);
      expect(result.newAdmin.userId).to.equal(newAdminId);
    });

    it('aborts the transaction when the target is not an eligible member', async () => {
      const writes = stubRoleWrites([null]);
      const withTransaction = sinon.stub().callsFake(async (fn: () => Promise<void>) => fn());
      const endSession = sinon.stub().resolves();
      sinon.stub(mongoose, 'startSession').resolves({ withTransaction, endSession } as any);
      sinon.stub(Org, 'updateOne').resolves({} as any);

      try {
        await transferOrgAdmin(orgId, userId, newAdminId, true);
        expect.fail('expected BadRequestError');
      } catch (error: any) {
        expect(error.name).to.equal('BadRequestError');
      }
      expect(writes.calledOnce).to.equal(true);
      expect(endSession.calledOnce).to.equal(true);
    });

    it('works whatever the admin limit, since the admin count does not change', async () => {
      setOrgAdminLimit(null);
      stubRoleWrites([{ _id: newAdminId }, { _id: userId }]);

      const result = await transferOrgAdmin(orgId, userId, newAdminId, false);

      expect(result.previousAdmin.userId).to.equal(userId);
    });
  });
});
