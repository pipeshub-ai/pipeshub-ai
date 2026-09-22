import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { ServiceAccountsService } from '../../../../src/modules/user_management/services/service-accounts.service';
import { Users } from '../../../../src/modules/user_management/schema/users.schema';
import { UserGroups } from '../../../../src/modules/user_management/schema/userGroup.schema';

function makeService() {
  const logger = {
    info: sinon.stub(),
    debug: sinon.stub(),
    warn: sinon.stub(),
    error: sinon.stub(),
  };
  const events = {
    start: sinon.stub().resolves(),
    stop: sinon.stub().resolves(),
    publishEvent: sinon.stub().resolves(),
  };
  const revoker = { revokeAllForServiceAccount: sinon.stub().resolves() };
  const service = new ServiceAccountsService(logger as any, events as any);
  service.setTokenRevoker(revoker as any);
  return { service, events, revoker };
}

describe('ServiceAccountsService', () => {
  const orgId = new mongoose.Types.ObjectId().toString();
  const id = new mongoose.Types.ObjectId().toString();

  afterEach(() => sinon.restore());

  describe('create', () => {
    it('rejects a name that would not make a valid address, before touching the database', async () => {
      const { service } = makeService();
      const findOne = sinon.stub(Users, 'findOne');

      // Note that mixed case is not in this list: the slug is lowercased
      // before it is checked, so "Nightly" is accepted and stored as
      // "nightly" rather than rejected.
      for (const bad of ['-bad', 'bad-', 'double--hyphen', 'has space', 'ab', 'a'.repeat(49)]) {
        try {
          await service.create(orgId, { slug: bad, fullName: 'X' });
          expect.fail(`expected "${bad}" to be rejected`);
        } catch (error) {
          expect((error as Error).message).to.contain('lowercase letters');
        }
      }
      expect(findOne.called).to.equal(false);
    });

    it('refuses a name already taken in the organisation', async () => {
      const { service } = makeService();
      sinon.stub(Users, 'findOne').returns({
        exec: sinon.stub().resolves({ _id: id, isDeleted: false }),
      } as any);

      try {
        await service.create(orgId, { slug: 'nightly-sync', fullName: 'X' });
        expect.fail('expected a duplicate name to be refused');
      } catch (error) {
        expect((error as Error).message).to.contain('already exists');
      }
    });

    it('restores a deleted account of the same name rather than failing', async () => {
      // The address is uniquely indexed and delete only marks the row, so a
      // lookup that skipped deleted rows would call the name free and then die
      // on the index. This is the case that used to be a 500.
      const { service, events } = makeService();
      const deleted = {
        _id: id,
        email: `svc-nightly-sync-${orgId}@service.pipeshub.internal`,
        orgId: { toString: () => orgId },
        isDeleted: true,
        kind: 'service',
      };
      sinon
        .stub(Users, 'findOne')
        .returns({ exec: sinon.stub().resolves(deleted) } as any);
      // The restore itself is one conditional update, not a read-then-save.
      const restore = sinon.stub(Users, 'findOneAndUpdate').returns({
        exec: sinon.stub().resolves({
          ...deleted,
          isDeleted: false,
          isDisabled: false,
          fullName: 'Nightly sync',
        }),
      } as any);
      sinon.stub(UserGroups, 'updateOne').resolves({} as any);

      const view = await service.create(orgId, {
        slug: 'nightly-sync',
        fullName: 'Nightly sync',
      });

      expect(view.id).to.equal(id);
      expect(view.isDisabled).to.equal(false);
      // `isDeleted: true` is part of the query, which is what makes the
      // transition happen once when two administrators race.
      expect(restore.firstCall.args[0]).to.include({ isDeleted: true });
      // The graph keys its node by the address, so it has to be told the
      // account is back.
      expect(events.publishEvent.calledOnce).to.equal(true);
      expect(events.publishEvent.firstCall.args[0].eventType).to.equal(
        'userAdded',
      );
    });

    it("will not restore another organisation's record that holds the address", async () => {
      // Email uniqueness is global, so the row holding this address need not
      // belong here: another organisation could have invited a person at it
      // and deleted them. Undeleting that row would resurrect someone else's
      // user and announce it to the graph under this organisation's id.
      const { service, events } = makeService();
      const otherOrgId = new mongoose.Types.ObjectId().toString();
      sinon.stub(Users, 'findOne').returns({
        exec: sinon.stub().resolves({
          _id: id,
          email: `svc-nightly-sync-${orgId}@service.pipeshub.internal`,
          orgId: otherOrgId,
          kind: 'service',
          isDeleted: true,
        }),
      } as any);
      const restore = sinon.stub(Users, 'findOneAndUpdate');

      try {
        await service.create(orgId, {
          slug: 'nightly-sync',
          fullName: 'Nightly sync',
        });
        expect.fail("expected another organisation's row to be refused");
      } catch (error) {
        expect((error as Error).message).to.contain('already exists');
      }
      expect(restore.called).to.equal(false);
      expect(events.publishEvent.called).to.equal(false);
    });

    it('will not restore a deleted human who happens to hold the address', async () => {
      const { service } = makeService();
      sinon.stub(Users, 'findOne').returns({
        exec: sinon.stub().resolves({
          _id: id,
          email: `svc-nightly-sync-${orgId}@service.pipeshub.internal`,
          orgId,
          kind: 'human',
          isDeleted: true,
        }),
      } as any);
      const restore = sinon.stub(Users, 'findOneAndUpdate');

      try {
        await service.create(orgId, {
          slug: 'nightly-sync',
          fullName: 'Nightly sync',
        });
        expect.fail('expected a human record to be refused');
      } catch (error) {
        expect((error as Error).message).to.contain('already exists');
      }
      expect(restore.called).to.equal(false);
    });

    it('clears a description the new request did not give', async () => {
      // Setting a field to undefined is a no-op in Mongoose, so the deleted
      // account's old description would otherwise survive into the restored
      // one — which is not what "every field is reset" means.
      const { service } = makeService();
      const deleted = {
        _id: id,
        email: `svc-nightly-sync-${orgId}@service.pipeshub.internal`,
        orgId: { toString: () => orgId },
        kind: 'service',
        isDeleted: true,
      };
      sinon
        .stub(Users, 'findOne')
        .returns({ exec: sinon.stub().resolves(deleted) } as any);
      const restore = sinon.stub(Users, 'findOneAndUpdate').returns({
        exec: sinon.stub().resolves({ ...deleted, isDeleted: false }),
      } as any);
      sinon.stub(UserGroups, 'updateOne').resolves({} as any);

      await service.create(orgId, {
        slug: 'nightly-sync',
        fullName: 'Nightly sync',
      });

      const update = restore.firstCall.args[1] as any;
      expect(update.$unset).to.have.property('description');
      expect(update.$set).to.not.have.property('description');
    });

    it('revokes the old tokens before the account is live again', async () => {
      // Ordering is the point. Clearing isDeleted is what makes the auth
      // middleware start accepting this account's tokens again, so revoking
      // afterwards leaves a window where an old one works — and if that
      // revocation fails there is no rollback.
      const { service, revoker } = makeService();
      const deleted = {
        _id: id,
        email: `svc-nightly-sync-${orgId}@service.pipeshub.internal`,
        orgId: { toString: () => orgId },
        kind: 'service',
        isDeleted: true,
      };
      sinon
        .stub(Users, 'findOne')
        .returns({ exec: sinon.stub().resolves(deleted) } as any);
      const restore = sinon.stub(Users, 'findOneAndUpdate').returns({
        exec: sinon.stub().resolves({ ...deleted, isDeleted: false }),
      } as any);
      sinon.stub(UserGroups, 'updateOne').resolves({} as any);

      await service.create(orgId, {
        slug: 'nightly-sync',
        fullName: 'Nightly sync',
      });

      // After the update, not before: the request that loses this race
      // restores nothing, and must not revoke tokens the winner's client has
      // already minted.
      expect(
        restore.calledBefore(revoker.revokeAllForServiceAccount),
      ).to.equal(true);
    });

    it('puts the account back to deleted if revocation fails after the restore', async () => {
      const { service } = makeService();
      const deleted = {
        _id: id,
        email: `svc-nightly-sync-${orgId}@service.pipeshub.internal`,
        orgId: { toString: () => orgId },
        kind: 'service',
        isDeleted: true,
      };
      sinon
        .stub(Users, 'findOne')
        .returns({ exec: sinon.stub().resolves(deleted) } as any);
      sinon.stub(Users, 'findOneAndUpdate').returns({
        exec: sinon.stub().resolves({ ...deleted, isDeleted: false }),
      } as any);
      const compensate = sinon
        .stub(Users, 'updateOne')
        .returns({ exec: sinon.stub().resolves({}) } as any);
      (service as unknown as { setTokenRevoker: (r: unknown) => void }).setTokenRevoker({
        revokeAllForServiceAccount: sinon.stub().rejects(new Error('broker down')),
      });

      try {
        await service.create(orgId, {
          slug: 'nightly-sync',
          fullName: 'Nightly sync',
        });
        expect.fail('expected the restore to fail');
      } catch (error) {
        expect((error as Error).message).to.contain('broker down');
      }
      // Compensated, so the account is not left live with pre-deletion tokens.
      expect(compensate.calledOnce).to.equal(true);
      expect(compensate.firstCall.args[1]).to.deep.equal({
        $set: { isDeleted: true },
      });
    });

    it('refuses to restore at all when no revoker is wired', async () => {
      const logger = {
        info: sinon.stub(),
        debug: sinon.stub(),
        warn: sinon.stub(),
        error: sinon.stub(),
      };
      const events = {
        start: sinon.stub().resolves(),
        stop: sinon.stub().resolves(),
        publishEvent: sinon.stub().resolves(),
      };
      // Deliberately no setTokenRevoker: its absence means something is wrong
      // at startup, not that there is nothing to revoke.
      const bare = new ServiceAccountsService(logger as any, events as any);
      sinon.stub(Users, 'findOne').returns({
        exec: sinon.stub().resolves({
          _id: id,
          email: `svc-nightly-sync-${orgId}@service.pipeshub.internal`,
          orgId: { toString: () => orgId },
          kind: 'service',
          isDeleted: true,
        }),
      } as any);
      const restore = sinon.stub(Users, 'findOneAndUpdate');

      try {
        await bare.create(orgId, {
          slug: 'nightly-sync',
          fullName: 'Nightly sync',
        });
        expect.fail('expected the restore to be refused');
      } catch (error) {
        expect((error as Error).message).to.contain('token revoker');
      }
      expect(restore.called).to.equal(false);
    });

    it('refuses the loser of a restore race rather than restoring twice', async () => {
      // Both requests read the same deleted document; the conditional update
      // matches for the first and nothing for the second.
      const { service, events } = makeService();
      sinon.stub(Users, 'findOne').returns({
        exec: sinon.stub().resolves({
          _id: id,
          email: `svc-nightly-sync-${orgId}@service.pipeshub.internal`,
          orgId: { toString: () => orgId },
          kind: 'service',
          isDeleted: true,
        }),
      } as any);
      sinon
        .stub(Users, 'findOneAndUpdate')
        .returns({ exec: sinon.stub().resolves(null) } as any);

      try {
        await service.create(orgId, {
          slug: 'nightly-sync',
          fullName: 'Nightly sync',
        });
        expect.fail('expected the second restore to be refused');
      } catch (error) {
        expect((error as Error).message).to.contain('already exists');
      }
      // Nothing was announced to the permission graph for the loser.
      expect(events.publishEvent.called).to.equal(false);
    });

    it('does not let a restored account inherit the tokens it had before', async () => {
      // Restoring reuses the record, so its old tokens would otherwise come
      // back with the name — for whoever reused it, who need not be the
      // person who held them.
      const { service, revoker } = makeService();
      const deleted = {
        _id: id,
        email: `svc-nightly-sync-${orgId}@service.pipeshub.internal`,
        orgId,
        isDeleted: true,
        kind: 'service',
      };
      sinon
        .stub(Users, 'findOne')
        .returns({ exec: sinon.stub().resolves(deleted) } as any);
      sinon.stub(Users, 'findOneAndUpdate').returns({
        exec: sinon.stub().resolves({ ...deleted, isDeleted: false }),
      } as any);
      sinon.stub(UserGroups, 'updateOne').resolves({} as any);

      await service.create(orgId, {
        slug: 'nightly-sync',
        fullName: 'Nightly sync',
      });

      expect(revoker.revokeAllForServiceAccount.calledOnce).to.equal(true);
      expect(
        revoker.revokeAllForServiceAccount.firstCall.args,
      ).to.deep.equal([orgId, id]);
    });

    it('turns a duplicate-key race into a conflict rather than a 500', async () => {
      const { service } = makeService();
      sinon
        .stub(Users, 'findOne')
        .returns({ exec: sinon.stub().resolves(null) } as any);
      sinon.stub(UserGroups, 'updateOne').resolves({} as any);
      // The loser of a concurrent create: it passed the check, then the
      // unique index rejected its save.
      sinon
        .stub(Users.prototype, 'save')
        .rejects(Object.assign(new Error('E11000 duplicate key'), { code: 11000 }));

      try {
        await service.create(orgId, {
          slug: 'nightly-sync',
          fullName: 'Nightly sync',
        });
        expect.fail('expected a duplicate key error to become a conflict');
      } catch (error) {
        expect((error as Error).message).to.contain('already exists');
      }
    });
  });

  describe('lookups are confined to service accounts', () => {
    it('will not return a human user through a service-account route', async () => {
      const { service } = makeService();
      // A human's id resolves to nothing here because `kind` is part of the
      // query, not a check applied to whatever came back.
      const findOne = sinon
        .stub(Users, 'findOne')
        .returns({ exec: sinon.stub().resolves(null) } as any);

      try {
        await service.get(orgId, id);
        expect.fail('expected a non-service account to be not found');
      } catch (error) {
        expect((error as Error).message).to.equal('Service account not found');
      }
      expect(findOne.firstCall.args[0]).to.include({
        kind: 'service',
        orgId,
        isDeleted: false,
      });
    });

    it('treats a malformed id as not found rather than throwing a cast error', async () => {
      const { service } = makeService();
      const findOne = sinon.stub(Users, 'findOne');

      try {
        await service.get(orgId, 'not-an-object-id');
        expect.fail('expected a malformed id to be not found');
      } catch (error) {
        expect((error as Error).message).to.equal('Service account not found');
      }
      expect(findOne.called).to.equal(false);
    });
  });

  describe('remove', () => {
    it('revokes every token the account held', async () => {
      const { service, revoker } = makeService();
      const account: any = {
        _id: id,
        orgId,
        email: `svc-x-${orgId}@service.pipeshub.internal`,
        isDeleted: false,
        save: sinon.stub().resolvesThis(),
      };
      sinon
        .stub(Users, 'findOne')
        .returns({ exec: sinon.stub().resolves(account) } as any);
      sinon.stub(UserGroups, 'updateMany').resolves({} as any);

      await service.remove(orgId, id);

      expect(account.isDeleted).to.equal(true);
      expect(revoker.revokeAllForServiceAccount.calledOnce).to.equal(true);
    });
  });

  describe('update', () => {
    it('disables an account and leaves the record in place', async () => {
      const { service } = makeService();
      const account: any = {
        _id: id,
        email: `svc-x-${orgId}@service.pipeshub.internal`,
        fullName: 'Nightly sync',
        isDisabled: false,
        isDeleted: false,
        save: sinon.stub().resolvesThis(),
      };
      sinon
        .stub(Users, 'findOne')
        .returns({ exec: sinon.stub().resolves(account) } as any);

      const view = await service.update(orgId, id, { isDisabled: true });

      expect(view.isDisabled).to.equal(true);
      expect(account.isDeleted).to.equal(false);
      expect(account.save.calledOnce).to.equal(true);
    });

    it('tells the permission graph about a rename, but not about a disable', async () => {
      const account: any = {
        _id: id,
        email: `svc-x-${orgId}@service.pipeshub.internal`,
        fullName: 'Old name',
        isDisabled: false,
        save: sinon.stub().resolvesThis(),
      };
      sinon
        .stub(Users, 'findOne')
        .returns({ exec: sinon.stub().resolves(account) } as any);

      const disabling = makeService();
      await disabling.service.update(orgId, id, { isDisabled: true });
      expect(disabling.events.publishEvent.called).to.equal(false);

      const renaming = makeService();
      await renaming.service.update(orgId, id, { fullName: 'New name' });
      expect(renaming.events.publishEvent.calledOnce).to.equal(true);
      expect(renaming.events.publishEvent.firstCall.args[0].eventType).to.equal(
        'userUpdated',
      );
    });
  });
});
