import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { Users } from '../../../../src/modules/user_management/schema/users.schema';
import {
  assertServiceAccountRole,
  SERVICE_ACCOUNT_ADMIN_ROLE_MESSAGE,
} from '../../../../src/modules/user_management/constants/service-account.constants';

/**
 * Pinning the role at creation is not enough on its own: a service account is
 * an ordinary user record, so the role-update endpoint and the invite
 * processor can both reach it, and isUserOrgAdmin reads the role field without
 * caring what kind of principal owns it. These cover the rule where the role
 * is written, rather than only where service accounts are created.
 */
describe('a service account can never be an administrator', () => {
  const orgId = new mongoose.Types.ObjectId();

  afterEach(() => sinon.restore());

  describe('assertServiceAccountRole', () => {
    it('refuses admin for a service account', () => {
      expect(() => assertServiceAccountRole('service', 'admin')).to.throw(
        SERVICE_ACCOUNT_ADMIN_ROLE_MESSAGE,
      );
    });

    it('allows every other combination', () => {
      expect(() => assertServiceAccountRole('service', 'member')).to.not.throw();
      expect(() => assertServiceAccountRole('human', 'admin')).to.not.throw();
      expect(() => assertServiceAccountRole(undefined, 'admin')).to.not.throw();
    });
  });

  describe('the save hook', () => {
    it('refuses to store a service account with the admin role', async () => {
      const account = new Users({
        orgId,
        email: 'svc-x@service.pipeshub.internal',
        kind: 'service',
        role: 'admin',
        // Set so the hook does not first go to the counter collection for a
        // generated one; the rule under test runs after that.
        slug: 'user-1',
      });

      try {
        await account.save();
        expect.fail('expected a service account admin to be refused');
      } catch (error) {
        expect((error as Error).message).to.contain(
          SERVICE_ACCOUNT_ADMIN_ROLE_MESSAGE,
        );
      }
    });

    it('allows a service account with the member role', async () => {
      const account = new Users({
        orgId,
        email: 'svc-y@service.pipeshub.internal',
        kind: 'service',
        role: 'member',
        slug: 'user-2',
      });
      await account.validate();
      expect(account.role).to.equal('member');
    });
  });

  describe('the update hooks', () => {
    it('refuses promoting an existing service account through findOneAndUpdate', async () => {
      // The update does not mention kind, so the stored records decide: the
      // hook asks whether any match is a service account, and one comes back.
      sinon.stub(Users, 'findOne').returns({
        select: sinon.stub().returns({
          lean: sinon.stub().returns({
            exec: sinon.stub().resolves({ _id: new mongoose.Types.ObjectId() }),
          }),
        }),
      } as any);

      try {
        await Users.findOneAndUpdate(
          { _id: new mongoose.Types.ObjectId() },
          { role: 'admin' },
        ).exec();
        expect.fail('expected the promotion to be refused');
      } catch (error) {
        expect((error as Error).message).to.contain(
          SERVICE_ACCOUNT_ADMIN_ROLE_MESSAGE,
        );
      }
    });

    it('refuses when the update itself names kind service', async () => {
      const findOne = sinon.stub(Users, 'findOne');
      try {
        await Users.updateOne(
          { _id: new mongoose.Types.ObjectId() },
          { $set: { kind: 'service', role: 'admin' } },
        ).exec();
        expect.fail('expected the promotion to be refused');
      } catch (error) {
        expect((error as Error).message).to.contain(
          SERVICE_ACCOUNT_ADMIN_ROLE_MESSAGE,
        );
      }
      // Decided from the update itself; no need to read the record.
      expect(findOne.called).to.equal(false);
    });

    it('refuses a mixed updateMany batch that reaches any service account', async () => {
      // The invite processor promotes in bulk with
      // updateMany({ _id: { $in: ids } }, { role: 'admin' }). Sampling one
      // document could return a person and wave the whole batch through, so
      // the check asks whether any matched document is a service account.
      const findOne = sinon.stub(Users, 'findOne').returns({
        select: sinon.stub().returns({
          lean: sinon.stub().returns({
            exec: sinon.stub().resolves({ _id: new mongoose.Types.ObjectId() }),
          }),
        }),
      } as any);

      try {
        await Users.updateMany(
          { _id: { $in: [new mongoose.Types.ObjectId(), new mongoose.Types.ObjectId()] } },
          { role: 'admin' },
        ).exec();
        expect.fail('expected the batch to be refused');
      } catch (error) {
        expect((error as Error).message).to.contain(
          SERVICE_ACCOUNT_ADMIN_ROLE_MESSAGE,
        );
      }
      // The narrowed query is what makes one match enough.
      expect(findOne.firstCall.args[0]).to.include({ kind: 'service' });
    });

    it('does not interfere with promoting a person', async () => {
      // No service account among the matches.
      sinon.stub(Users, 'findOne').returns({
        select: sinon.stub().returns({
          lean: sinon.stub().returns({ exec: sinon.stub().resolves(null) }),
        }),
      } as any);
      const update = sinon
        .stub(mongoose.Query.prototype, 'exec')
        .resolves({ acknowledged: true } as any);

      await Users.findOneAndUpdate(
        { _id: new mongoose.Types.ObjectId() },
        { role: 'admin' },
      ).exec();

      expect(update.called).to.equal(true);
    });
  });
});
