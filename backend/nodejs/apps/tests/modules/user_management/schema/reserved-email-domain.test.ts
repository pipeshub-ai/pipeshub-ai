import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { Users } from '../../../../src/modules/user_management/schema/users.schema';
import {
  assertReservedEmailDomainBelongsToServiceAccount,
  reservedEmailDomainPattern,
  SERVICE_ACCOUNT_EMAIL_DOMAIN,
  SERVICE_ACCOUNT_RESERVED_DOMAIN_MESSAGE,
} from '../../../../src/modules/user_management/constants/service-account.constants';

/**
 * `service.pipeshub.internal` is meant to be reserved, not merely a naming
 * convention. A person invited there would read as a machine identity wherever
 * the address is shown, while holding a password and being able to sign in,
 * and would take a name a real service account might later need.
 *
 * Four paths set a human's address — create, bulk invite, the CSV invite
 * upload, and the change-email endpoint — so the rule is enforced at the write
 * boundary rather than at any one of them.
 */
describe('only a service account may use the reserved email domain', () => {
  const orgId = new mongoose.Types.ObjectId();
  const reserved = `someone@${SERVICE_ACCOUNT_EMAIL_DOMAIN}`;

  // The two cases below let an update through the guard, which then reaches
  // the database. With no connection, mongoose queues the call and the test
  // would sit waiting for it, so queueing is turned off and the call fails at
  // once. What is asserted is which error comes back, not that one does.
  let bufferCommands: boolean | undefined;
  before(() => {
    bufferCommands = mongoose.get('bufferCommands') as boolean | undefined;
    mongoose.set('bufferCommands', false);
  });
  after(() => mongoose.set('bufferCommands', bufferCommands ?? true));

  afterEach(() => sinon.restore());

  describe('assertReservedEmailDomainBelongsToServiceAccount', () => {
    it('refuses a reserved address on anything that is not a service account', () => {
      expect(() =>
        assertReservedEmailDomainBelongsToServiceAccount('human', reserved),
      ).to.throw(SERVICE_ACCOUNT_RESERVED_DOMAIN_MESSAGE);
      expect(() =>
        assertReservedEmailDomainBelongsToServiceAccount(undefined, reserved),
      ).to.throw(SERVICE_ACCOUNT_RESERVED_DOMAIN_MESSAGE);
    });

    it('allows a service account to hold one', () => {
      expect(() =>
        assertReservedEmailDomainBelongsToServiceAccount('service', reserved),
      ).to.not.throw();
    });

    it('leaves ordinary addresses alone, whatever the kind', () => {
      expect(() =>
        assertReservedEmailDomainBelongsToServiceAccount('human', 'a@example.com'),
      ).to.not.throw();
      expect(() =>
        assertReservedEmailDomainBelongsToServiceAccount(undefined, undefined),
      ).to.not.throw();
    });

    it('is not fooled by a lookalike domain or by casing', () => {
      // A domain that merely contains the words must not be treated as the
      // reserved one, and the real one must be caught however it is cased.
      expect(() =>
        assertReservedEmailDomainBelongsToServiceAccount(
          'human',
          'a@service-pipeshub-internal.example.com',
        ),
      ).to.not.throw();
      expect(() =>
        assertReservedEmailDomainBelongsToServiceAccount(
          'human',
          `a@${SERVICE_ACCOUNT_EMAIL_DOMAIN.toUpperCase()}`,
        ),
      ).to.throw(SERVICE_ACCOUNT_RESERVED_DOMAIN_MESSAGE);
    });
  });

  describe('reservedEmailDomainPattern', () => {
    it('matches the reserved domain and not a lookalike', () => {
      const pattern = reservedEmailDomainPattern();
      expect(pattern.test(`svc-a-1@${SERVICE_ACCOUNT_EMAIL_DOMAIN}`)).to.equal(true);
      expect(pattern.test('svc-a-1@serviceXpipeshubXinternal')).to.equal(false);
      expect(pattern.test('a@service.pipeshub.internal.example.com')).to.equal(false);
    });
  });

  describe('the save hook', () => {
    it('refuses to store a person on the reserved domain', async () => {
      const person = new Users({
        orgId,
        email: reserved,
        fullName: 'Looks Like A Robot',
        slug: 'user-1',
      });

      try {
        await person.save();
        expect.fail('expected a person on the reserved domain to be refused');
      } catch (error) {
        expect((error as Error).message).to.contain(
          SERVICE_ACCOUNT_RESERVED_DOMAIN_MESSAGE,
        );
      }
    });
  });

  describe('the update hooks', () => {
    it('refuses to move a person onto the reserved domain', async () => {
      // No stored record is consulted: the update names a kind that is not
      // `service`, which settles it on its own.
      try {
        await Users.updateOne(
          { _id: new mongoose.Types.ObjectId() },
          { $set: { email: reserved, kind: 'human' } },
        ).exec();
        expect.fail('expected the update to be refused');
      } catch (error) {
        expect((error as Error).message).to.contain(
          SERVICE_ACCOUNT_RESERVED_DOMAIN_MESSAGE,
        );
      }
    });

    it('refuses when the update sets a reserved address and any target is a person', async () => {
      sinon.stub(Users, 'findOne').returns({
        select: () => ({
          lean: () => ({ exec: async () => ({ _id: new mongoose.Types.ObjectId() }) }),
        }),
      } as never);

      try {
        await Users.updateOne(
          { orgId },
          { $set: { email: reserved } },
        ).exec();
        expect.fail('expected the update to be refused');
      } catch (error) {
        expect((error as Error).message).to.contain(
          SERVICE_ACCOUNT_RESERVED_DOMAIN_MESSAGE,
        );
      }
    });

    it('refuses to take the service kind away from a record holding a reserved address', async () => {
      sinon.stub(Users, 'findOne').returns({
        select: () => ({
          lean: () => ({ exec: async () => ({ _id: new mongoose.Types.ObjectId() }) }),
        }),
      } as never);

      try {
        await Users.updateOne({ orgId }, { $set: { kind: 'human' } }).exec();
        expect.fail('expected the downgrade to be refused');
      } catch (error) {
        expect((error as Error).message).to.contain(
          SERVICE_ACCOUNT_RESERVED_DOMAIN_MESSAGE,
        );
      }
    });

    it('allows a service account to be given its own reserved address', async () => {
      // The update names `kind: 'service'`, so the guard is satisfied and the
      // query goes on to the database. Reaching that far is the assertion.
      try {
        await Users.updateOne(
          { _id: new mongoose.Types.ObjectId() },
          { $set: { email: reserved, kind: 'service' } },
        ).exec();
      } catch (error) {
        expect((error as Error).message).to.not.contain(
          SERVICE_ACCOUNT_RESERVED_DOMAIN_MESSAGE,
        );
      }
    });

    it('does not consult the database for an update that touches neither field', async () => {
      const findOne = sinon.stub(Users, 'findOne');

      try {
        await Users.updateOne({ orgId }, { $set: { fullName: 'A Person' } }).exec();
      } catch {
        // A missing connection is fine; what matters is the guard stayed out
        // of the way rather than paying for a lookup on every update.
      }

      expect(findOne.called).to.equal(false);
    });
  });
});
