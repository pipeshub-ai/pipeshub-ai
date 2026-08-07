import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import {
  normalizeUserRole,
  toDisplayUserRole,
  isUserOrgAdmin,
} from '../../../../src/modules/user_management/services/user-admin.service';
import { Users } from '../../../../src/modules/user_management/schema/users.schema';
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

    it('falls back to admin group membership when role is unset', async () => {
      stubUsersFindOne(null);
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([{ type: 'admin' }, { type: 'everyone' }]),
      } as any);

      const result = await isUserOrgAdmin(userId, orgId);

      expect(result).to.equal(true);
    });

    it('returns false when role is unset and user is not in admin group', async () => {
      stubUsersFindOne(undefined);
      sinon.stub(UserGroups, 'find').returns({
        select: sinon.stub().resolves([{ type: 'everyone' }, { type: 'standard' }]),
      } as any);

      const result = await isUserOrgAdmin(userId, orgId);

      expect(result).to.equal(false);
    });

    it('queries Users with the expected filter', async () => {
      const findOneStub = stubUsersFindOne('admin');

      await isUserOrgAdmin(userId, orgId);

      expect(findOneStub.calledOnce).to.equal(true);
      expect(findOneStub.firstCall.args[0]).to.deep.equal({
        _id: userId,
        orgId,
        isDeleted: false,
      });
    });
  });
});
