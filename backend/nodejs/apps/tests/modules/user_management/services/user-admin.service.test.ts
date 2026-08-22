import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import {
  normalizeUserRole,
  resolveOptionalUserRole,
  toDisplayUserRole,
  isUserOrgAdmin,
  findOrgAdminUserIds,
  assertCanDemoteAdmin,
  saveUserEnsuringOrgRetainsAdmin,
} from '../../../../src/modules/user_management/services/user-admin.service';
import { Users } from '../../../../src/modules/user_management/schema/users.schema';
import { Org } from '../../../../src/modules/user_management/schema/org.schema';
import { UserGroups } from '../../../../src/modules/user_management/schema/userGroup.schema';

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
});
