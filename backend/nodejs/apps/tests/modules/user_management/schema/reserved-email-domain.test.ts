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

  /**
   * Runs `work` with mongoose's command queueing turned off.
   *
   * A case that lets an update *through* the guard goes on to the database,
   * and with no connection mongoose would queue the call and the test would
   * sit waiting. Turning queueing off makes it fail at once instead.
   *
   * Scoped to the one call rather than set for the file, because
   * `mongoose.set` is global to the process: left on, it changes how every
   * other case here — and every other file sharing the worker — fails, which
   * is how this suite passed locally and failed in CI.
   */
  async function withoutCommandBuffering(work: () => Promise<void>): Promise<void> {
    const previous = mongoose.get('bufferCommands') as boolean | undefined;
    mongoose.set('bufferCommands', false);
    try {
      await work();
    } finally {
      mongoose.set('bufferCommands', previous ?? true);
    }
  }

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
      // Also pinned as an own property on this document.
      // `users.controller.test.ts` redefines `email` as a getter on the shared
      // prototype and cannot restore it, so under `mocha --no-parallel` —
      // which is how the redis-cluster job runs the suite — every later
      // document reports that test's address instead of its own. The
      // constructor above is what mongoose validates against; this shadows the
      // borrowed getter so the hook reads the address this case is about,
      // rather than whichever file happened to run first.
      Object.defineProperty(person, 'email', {
        value: reserved,
        configurable: true,
        enumerable: true,
        writable: true,
      });

      // Queueing off so that if the guard ever stops firing, this fails on
      // the assertion instead of waiting on a database call that will not
      // come. A hook that does throw never reaches the database at all.
      await withoutCommandBuffering(async () => {
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
  });

  describe('a record that already holds a reserved address', () => {
    /**
     * A person could be given one before this rule existed, so such rows are
     * out there. Refusing every later write to them would be worse than the
     * hole it closes: `deleteUser` pulls group memberships, revokes OAuth apps
     * and knowledge-base permissions, removes project access and unsets the
     * password *before* saving `isDeleted`, so a throw at that save leaves the
     * account stripped of everything and still active. Deleting is also the
     * documented repair for a bad invite, and an administrator cannot change
     * the address first because that is owner-only.
     */
    function legacyPersonOnReservedDomain() {
      const person = new Users({
        orgId,
        email: reserved,
        fullName: 'Invited Before The Rule',
        slug: 'user-legacy',
      });
      // Pinned as an own property for the same reason as the create case
      // above: `users.controller.test.ts` leaves `Users.prototype.email` as a
      // getter returning its own address, so without this the hook would read
      // that instead. These two cases only fail when the error carries the
      // reserved-domain message, so an unpinned address would leave them green
      // even if the gate were removed — which is the opposite of their purpose.
      Object.defineProperty(person, 'email', {
        value: reserved,
        configurable: true,
        enumerable: true,
        writable: true,
      });
      // Presented as a row that came back from the database rather than a new
      // one, which is the state every later save sees.
      person.isNew = false;
      person.$locals = {};
      person.unmarkModified('email');
      person.unmarkModified('kind');
      return person;
    }

    it('can still be soft-deleted', async () => {
      const person = legacyPersonOnReservedDomain();
      person.isDeleted = true;

      await withoutCommandBuffering(async () => {
        try {
          await person.save();
        } catch (error) {
          expect((error as Error).message).to.not.contain(
            SERVICE_ACCOUNT_RESERVED_DOMAIN_MESSAGE,
          );
        }
      });
    });

    it('can still have an unrelated field edited', async () => {
      const person = legacyPersonOnReservedDomain();
      person.fullName = 'A New Display Name';

      await withoutCommandBuffering(async () => {
        try {
          await person.save();
        } catch (error) {
          expect((error as Error).message).to.not.contain(
            SERVICE_ACCOUNT_RESERVED_DOMAIN_MESSAGE,
          );
        }
      });
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

    it('allows an update that moves a person off the domain and makes them human', async () => {
      // The final state breaks no rule, and this combination is how a row that
      // should never have held a reserved address gets repaired. The stored
      // record is never consulted, because the update settles it.
      const findOne = sinon.stub(Users, 'findOne');

      await withoutCommandBuffering(async () => {
        try {
          await Users.updateOne(
            { orgId },
            { $set: { email: 'a.real.person@example.com', kind: 'human' } },
          ).exec();
        } catch (error) {
          expect((error as Error).message).to.not.contain(
            SERVICE_ACCOUNT_RESERVED_DOMAIN_MESSAGE,
          );
        }
      });

      expect(findOne.called).to.equal(false);
    });

    it('allows a service account to be given its own reserved address', async () => {
      // The update names `kind: 'service'`, so the guard is satisfied and the
      // query goes on to the database. Reaching that far is the assertion.
      await withoutCommandBuffering(async () => {
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
    });

    it('does not consult the database for an update that touches neither field', async () => {
      const findOne = sinon.stub(Users, 'findOne');

      await withoutCommandBuffering(async () => {
        try {
          await Users.updateOne({ orgId }, { $set: { fullName: 'A Person' } }).exec();
        } catch {
          // A missing connection is fine; what matters is the guard stayed out
          // of the way rather than paying for a lookup on every update.
        }
      });

      expect(findOne.called).to.equal(false);
    });
  });
});
