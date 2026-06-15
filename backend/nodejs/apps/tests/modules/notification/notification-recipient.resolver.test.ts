/// <reference types="mocha" />
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { UserGroups } from '../../../src/modules/user_management/schema/userGroup.schema';
import {
  resolveRoleRecipientUserIds,
  resolveNotificationRecipientUserIds,
} from '../../../src/modules/notification/utils/notification-recipient.resolver';

/** Build a stub chain for UserGroups.find(...).select(...).lean() */
function stubUserGroupsFind(groups: { users?: unknown[] }[]): sinon.SinonStub {
  const lean = sinon.stub().resolves(groups);
  const select = sinon.stub().returns({ lean });
  return sinon.stub(UserGroups, 'find').returns({ select } as any);
}

describe('notification/notification-recipient.resolver', () => {
  const orgId = new mongoose.Types.ObjectId();

  afterEach(() => {
    sinon.restore();
  });

  // ---------------------------------------------------------------------------
  // resolveRoleRecipientUserIds
  // ---------------------------------------------------------------------------
  describe('resolveRoleRecipientUserIds', () => {
    it('returns empty array for empty roles list', async () => {
      const findStub = stubUserGroupsFind([]);
      const result = await resolveRoleRecipientUserIds(orgId, []);
      expect(result).to.deep.equal([]);
      expect(findStub.called).to.be.false;
    });

    it('skips unsupported role names and does not query DB', async () => {
      const findStub = stubUserGroupsFind([]);
      const result = await resolveRoleRecipientUserIds(orgId, ['superuser', 'unknown']);
      expect(result).to.deep.equal([]);
      expect(findStub.called).to.be.false;
    });

    it('filters out non-string and blank entries', async () => {
      const findStub = stubUserGroupsFind([]);
      const result = await resolveRoleRecipientUserIds(orgId, [
        '',
        '   ',
        123 as unknown as string,
      ]);
      expect(result).to.deep.equal([]);
      expect(findStub.called).to.be.false;
    });

    it('resolves users for a supported role (admin)', async () => {
      const user1 = new mongoose.Types.ObjectId();
      const user2 = new mongoose.Types.ObjectId();
      stubUserGroupsFind([{ users: [user1, user2] }]);

      const result = await resolveRoleRecipientUserIds(orgId, ['admin']);
      expect(result.map((id) => id.toString())).to.have.members([
        user1.toString(),
        user2.toString(),
      ]);
    });

    it('resolves users for all four supported role types', async () => {
      const user = new mongoose.Types.ObjectId();
      const findStub = stubUserGroupsFind([{ users: [user] }]);

      for (const role of ['admin', 'standard', 'everyone', 'custom']) {
        sinon.restore();
        stubUserGroupsFind([{ users: [user] }]);
        const result = await resolveRoleRecipientUserIds(orgId, [role]);
        expect(result).to.have.length(1);
        expect(result[0].toString()).to.equal(user.toString());
      }
      void findStub; // suppress unused-variable warning
    });

    it('deduplicates users appearing in multiple groups for the same role', async () => {
      const shared = new mongoose.Types.ObjectId();
      const other = new mongoose.Types.ObjectId();
      stubUserGroupsFind([{ users: [shared, other] }, { users: [shared] }]);

      const result = await resolveRoleRecipientUserIds(orgId, ['admin']);
      expect(result.map((id) => id.toString())).to.have.members([
        shared.toString(),
        other.toString(),
      ]);
    });

    it('deduplicates users across different roles', async () => {
      const shared = new mongoose.Types.ObjectId();
      const lean1 = sinon.stub().resolves([{ users: [shared] }]);
      const lean2 = sinon.stub().resolves([{ users: [shared] }]);
      let callCount = 0;
      const select = sinon.stub().callsFake(() => {
        return { lean: callCount++ === 0 ? lean1 : lean2 };
      });
      sinon.stub(UserGroups, 'find').returns({ select } as any);

      const result = await resolveRoleRecipientUserIds(orgId, ['admin', 'standard']);
      expect(result).to.have.length(1);
      expect(result[0].toString()).to.equal(shared.toString());
    });

    it('normalizes role casing and trims whitespace', async () => {
      const user = new mongoose.Types.ObjectId();
      const findStub = stubUserGroupsFind([{ users: [user] }]);

      const result = await resolveRoleRecipientUserIds(orgId, ['  Admin  ', 'ADMIN']);
      expect(result).to.have.length(1);
      expect(findStub.calledOnce).to.be.true;
    });

    it('filters invalid ObjectIds out of group.users', async () => {
      const valid = new mongoose.Types.ObjectId();
      stubUserGroupsFind([{ users: [valid, 'not-an-oid', null as unknown as string] }]);

      const result = await resolveRoleRecipientUserIds(orgId, ['admin']);
      expect(result).to.have.length(1);
      expect(result[0].toString()).to.equal(valid.toString());
    });

    it('returns empty array when a group has no users field', async () => {
      stubUserGroupsFind([{}]);
      const result = await resolveRoleRecipientUserIds(orgId, ['admin']);
      expect(result).to.deep.equal([]);
    });
  });

  // ---------------------------------------------------------------------------
  // resolveNotificationRecipientUserIds
  // ---------------------------------------------------------------------------
  describe('resolveNotificationRecipientUserIds', () => {
    it('resolves direct recipientUserIds when no roles are given', async () => {
      const findStub = stubUserGroupsFind([]);
      const user1 = new mongoose.Types.ObjectId().toString();
      const user2 = new mongoose.Types.ObjectId().toString();

      const result = await resolveNotificationRecipientUserIds(orgId, [user1, user2], []);
      expect(result.map((id) => id.toString())).to.have.members([user1, user2]);
      expect(findStub.called).to.be.false;
    });

    it('resolves role recipients when no direct ids are given', async () => {
      const user = new mongoose.Types.ObjectId();
      stubUserGroupsFind([{ users: [user] }]);

      const result = await resolveNotificationRecipientUserIds(orgId, [], ['admin']);
      expect(result.map((id) => id.toString())).to.include(user.toString());
    });

    it('merges and deduplicates direct ids and role-resolved ids', async () => {
      const shared = new mongoose.Types.ObjectId();
      const roleOnly = new mongoose.Types.ObjectId();
      stubUserGroupsFind([{ users: [shared, roleOnly] }]);

      const result = await resolveNotificationRecipientUserIds(
        orgId,
        [shared.toString()],
        ['admin'],
      );
      const strings = result.map((id) => id.toString());
      expect(strings).to.include(shared.toString());
      expect(strings).to.include(roleOnly.toString());
      expect(strings.filter((s) => s === shared.toString())).to.have.length(1);
    });

    it('filters invalid ObjectIds from direct recipientUserIds', async () => {
      stubUserGroupsFind([]);
      const valid = new mongoose.Types.ObjectId().toString();

      const result = await resolveNotificationRecipientUserIds(
        orgId,
        [valid, 'bad-id', ''],
        [],
      );
      expect(result).to.have.length(1);
      expect(result[0].toString()).to.equal(valid);
    });

    it('handles non-array recipientUserIds (single string)', async () => {
      stubUserGroupsFind([]);
      const user = new mongoose.Types.ObjectId().toString();

      const result = await resolveNotificationRecipientUserIds(orgId, user, []);
      expect(result).to.have.length(1);
      expect(result[0].toString()).to.equal(user);
    });

    it('handles non-array recipientRoles (ignores non-array value)', async () => {
      const findStub = stubUserGroupsFind([]);

      const result = await resolveNotificationRecipientUserIds(
        orgId,
        [],
        'admin' as unknown as string[],
      );
      expect(result).to.deep.equal([]);
      expect(findStub.called).to.be.false;
    });

    it('returns empty array when both recipientUserIds and roles are empty', async () => {
      const findStub = stubUserGroupsFind([]);
      const result = await resolveNotificationRecipientUserIds(orgId, [], []);
      expect(result).to.deep.equal([]);
      expect(findStub.called).to.be.false;
    });

    it('handles null/undefined recipientUserIds gracefully', async () => {
      stubUserGroupsFind([]);
      const result = await resolveNotificationRecipientUserIds(orgId, null, []);
      expect(result).to.deep.equal([]);
    });
  });
});
