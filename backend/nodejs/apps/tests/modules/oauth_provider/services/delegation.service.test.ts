import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import {
  DelegationService,
  MAX_DELEGATIONS_PER_SERVICE_ACCOUNT,
  DELEGATION_EVENT_MAX_PAGE_SIZE,
} from '../../../../src/modules/oauth_provider/services/delegation.service';
import { Users } from '../../../../src/modules/user_management/schema/users.schema';
import { ServiceAccountDelegation } from '../../../../src/modules/oauth_provider/schema/service-account-delegation.schema';
import { DelegationEvent } from '../../../../src/modules/oauth_provider/schema/delegation-event.schema';

const orgId = new mongoose.Types.ObjectId().toString();
const adminId = new mongoose.Types.ObjectId().toString();
const accountId = new mongoose.Types.ObjectId().toString();
const subjectId = new mongoose.Types.ObjectId().toString();

function makeService() {
  const logger = {
    info: sinon.stub(),
    debug: sinon.stub(),
    warn: sinon.stub(),
    error: sinon.stub(),
  };
  return { service: new DelegationService(logger as any), logger };
}

/**
 * Users.findOne is used twice with different projections — once to confirm the
 * service account, once to resolve the person. The stub answers in call order.
 */
function stubUsers(...docs: (Record<string, unknown> | null)[]) {
  const stub = sinon.stub(Users, 'findOne');
  docs.forEach((doc, index) => {
    stub.onCall(index).returns({
      select: () => ({ lean: () => ({ exec: async () => doc }) }),
    } as any);
  });
  return stub;
}

const SERVICE_ACCOUNT = { email: 'svc-x@service.pipeshub.internal' };

describe('DelegationService', () => {
  afterEach(() => sinon.restore());

  describe('resolveSubject', () => {
    it('refuses an administrator, which is the rule the whole feature rests on', async () => {
      // Service accounts are clamped to member precisely so no machine
      // credential holds admin rights. A delegated admin token would walk
      // around that clamp by construction.
      stubUsers({
        _id: subjectId,
        email: 'boss@example.com',
        role: 'admin',
        kind: 'human',
      });
      const { service } = makeService();

      const result = await service.resolveSubject(orgId, 'boss@example.com');

      expect(result.ok).to.equal(false);
      expect(result.refusedReason).to.equal('subject_is_admin');
    });

    it('refuses another service account, so machine identities cannot chain', async () => {
      stubUsers({ _id: subjectId, email: 'svc@x', kind: 'service', role: 'member' });
      const { service } = makeService();

      const result = await service.resolveSubject(orgId, 'svc@x');

      expect(result.refusedReason).to.equal('subject_is_service_account');
    });

    it('refuses a disabled person', async () => {
      stubUsers({
        _id: subjectId,
        email: 'left@example.com',
        kind: 'human',
        role: 'member',
        isDisabled: true,
      });
      const { service } = makeService();

      expect((await service.resolveSubject(orgId, 'left@example.com')).refusedReason).to.equal(
        'subject_disabled',
      );
    });

    it('refuses a deleted person even though the record is still there', async () => {
      stubUsers({
        _id: subjectId,
        email: 'gone@example.com',
        kind: 'human',
        role: 'member',
        isDeleted: true,
      });
      const { service } = makeService();

      expect((await service.resolveSubject(orgId, 'gone@example.com')).refusedReason).to.equal(
        'subject_not_found',
      );
    });

    it('accepts an ordinary colleague', async () => {
      stubUsers({
        _id: subjectId,
        email: 'person@example.com',
        fullName: 'A Person',
        kind: 'human',
        role: 'member',
      });
      const { service } = makeService();

      const result = await service.resolveSubject(orgId, 'person@example.com');

      expect(result.ok).to.equal(true);
      expect(result.userId).to.equal(subjectId);
    });

    it('looks an email up in lower case, so casing in the request does not matter', async () => {
      const stub = stubUsers({
        _id: subjectId,
        email: 'person@example.com',
        kind: 'human',
        role: 'member',
      });
      const { service } = makeService();

      await service.resolveSubject(orgId, '  Person@Example.COM ');

      // Wrapped in `$eq` so the address can only ever be compared as a value.
      // A bare object would be read by Mongo as a set of operators.
      expect(stub.firstCall.args[0]).to.deep.include({
        email: { $eq: 'person@example.com' },
      });
    });

    it('refuses a value that is not a string instead of crashing on it', async () => {
      // Reachable from the token endpoint, where the subject is whatever was
      // in the request body. The validators refuse a non-string first; this is
      // what happens if one ever gets past them.
      const { service } = makeService();
      const result = await service.resolveSubject(orgId, {
        $ne: null,
      } as unknown as string);

      expect(result.ok).to.equal(false);
      expect(result.refusedReason).to.equal('subject_missing');
    });
  });

  describe('grant', () => {
    it('refuses to put an administrator on the allow-list, with a reason a person can act on', async () => {
      stubUsers(SERVICE_ACCOUNT, {
        _id: subjectId,
        email: 'boss@example.com',
        role: 'admin',
        kind: 'human',
      });
      const { service } = makeService();

      try {
        await service.grant(orgId, adminId, accountId, 'boss@example.com');
        expect.fail('expected an administrator to be refused');
      } catch (error) {
        expect((error as Error).message).to.contain('administrator');
      }
    });

    it('caps the allow-list rather than letting it become a master key', async () => {
      stubUsers(SERVICE_ACCOUNT, {
        _id: subjectId,
        email: 'person@example.com',
        kind: 'human',
        role: 'member',
      });
      sinon
        .stub(ServiceAccountDelegation, 'countDocuments')
        .resolves(MAX_DELEGATIONS_PER_SERVICE_ACCOUNT as any);
      const { service } = makeService();

      try {
        await service.grant(orgId, adminId, accountId, 'person@example.com');
        expect.fail('expected the cap to be enforced');
      } catch (error) {
        expect((error as Error).message).to.contain(
          String(MAX_DELEGATIONS_PER_SERVICE_ACCOUNT),
        );
      }
    });

    it('treats granting the same person twice as success, not failure', async () => {
      // The unique index refuses the duplicate. Failing a script over a right
      // the account already holds would be noise, not safety.
      stubUsers(SERVICE_ACCOUNT, {
        _id: subjectId,
        email: 'person@example.com',
        kind: 'human',
        role: 'member',
      });
      sinon.stub(ServiceAccountDelegation, 'countDocuments').resolves(0 as any);
      // Mongo raises `code: 11000` for a unique-index collision, and only
      // that code is forgiven — a bare Error now surfaces instead.
      const duplicate = Object.assign(new Error('E11000 duplicate key'), {
        code: 11000,
      });
      sinon.stub(ServiceAccountDelegation, 'create').rejects(duplicate);
      sinon.stub(ServiceAccountDelegation, 'findOne').returns({
        exec: async () => ({
          _id: new mongoose.Types.ObjectId(),
          serviceAccountId: accountId,
          subjectUserId: subjectId,
          grantedBy: adminId,
          createdAt: new Date(),
        }),
      } as any);
      const { service } = makeService();

      const result = await service.grant(
        orgId,
        adminId,
        accountId,
        'person@example.com',
      );

      expect(result.subjectUserId).to.equal(subjectId);
    });

    it('surfaces a failure that is not a duplicate, rather than inventing a grant', async () => {
      // Reporting success here would claim a grant this call never made, and
      // skip the audit record that is supposed to prove who made it.
      stubUsers(SERVICE_ACCOUNT, {
        _id: subjectId,
        email: 'person@example.com',
        kind: 'human',
        role: 'member',
      });
      sinon.stub(ServiceAccountDelegation, 'countDocuments').resolves(0 as any);
      sinon
        .stub(ServiceAccountDelegation, 'create')
        .rejects(new Error('connection reset'));
      const findOne = sinon.stub(ServiceAccountDelegation, 'findOne');
      const { service } = makeService();

      try {
        await service.grant(orgId, adminId, accountId, 'person@example.com');
        expect.fail('expected the write failure to surface');
      } catch (error) {
        expect((error as Error).message).to.equal('connection reset');
      }
      expect(findOne.called).to.equal(false);
    });

    it('refuses a service account that is not in this organisation', async () => {
      stubUsers(null);
      const { service } = makeService();

      try {
        await service.grant(orgId, adminId, accountId, 'person@example.com');
        expect.fail('expected the service account lookup to fail');
      } catch (error) {
        expect((error as Error).message).to.contain('Service account not found');
      }
    });
  });

  describe('the allow-list is audited, not only the exchange', () => {
    it('records who granted a permission and why', async () => {
      // Revoking deletes the row, taking the administrator and their reason
      // with it. Without this record the log could say an exchange was
      // permitted but never why.
      stubUsers(SERVICE_ACCOUNT, {
        _id: subjectId,
        email: 'person@example.com',
        kind: 'human',
        role: 'member',
      });
      sinon.stub(ServiceAccountDelegation, 'countDocuments').resolves(0 as any);
      sinon.stub(ServiceAccountDelegation, 'create').resolves({
        _id: new mongoose.Types.ObjectId(),
        serviceAccountId: accountId,
        subjectUserId: subjectId,
        grantedBy: adminId,
        createdAt: new Date(),
      } as any);
      const created = sinon.stub(DelegationEvent, 'create').resolves({} as any);
      const { service } = makeService();

      await service.grant(
        orgId,
        adminId,
        accountId,
        'person@example.com',
        'nightly digest',
      );

      const record = created.firstCall.args[0] as Record<string, unknown>;
      expect(record.outcome).to.equal('granted');
      expect(String(record.actedBy)).to.equal(adminId);
      expect(record.note).to.equal('nightly digest');
    });

    it('records a revocation too', async () => {
      stubUsers(SERVICE_ACCOUNT);
      sinon.stub(ServiceAccountDelegation, 'findOneAndDelete').returns({
        exec: async () => ({ _id: new mongoose.Types.ObjectId() }),
      } as any);
      const created = sinon.stub(DelegationEvent, 'create').resolves({} as any);
      const { service } = makeService();

      await service.revoke(orgId, adminId, accountId, subjectId);

      const record = created.firstCall.args[0] as Record<string, unknown>;
      expect(record.outcome).to.equal('revoked');
      expect(String(record.actedBy)).to.equal(adminId);
    });
  });

  describe('listDelegations', () => {
    it('shows an entry whose person can no longer be acted as, rather than hiding it', async () => {
      // The exchange already refuses these. Hiding them would leave the
      // review screen disagreeing with what the system does.
      stubUsers(SERVICE_ACCOUNT);
      sinon.stub(ServiceAccountDelegation, 'find').returns({
        sort: () => ({
          lean: () => ({
            exec: async () => [
              {
                _id: new mongoose.Types.ObjectId(),
                serviceAccountId: accountId,
                subjectUserId: subjectId,
                grantedBy: adminId,
                createdAt: new Date(),
              },
            ],
          }),
        }),
      } as any);
      sinon.stub(Users, 'find').returns({
        select: () => ({
          lean: () => ({
            exec: async () => [
              { _id: subjectId, email: 'boss@example.com', role: 'admin' },
            ],
          }),
        }),
      } as any);
      const { service } = makeService();

      const [entry] = await service.listDelegations(orgId, accountId);

      expect(entry.inactive).to.equal(true);
      expect(entry.inactiveReason).to.contain('administrator');
    });
  });

  describe('recordEvent', () => {
    it('never lets a failed audit write fail the request, but says so loudly', async () => {
      sinon.stub(DelegationEvent, 'create').rejects(new Error('mongo down'));
      const { service, logger } = makeService();

      await service.recordEvent({
        orgId,
        serviceAccountId: accountId,
        requestedSubject: 'person@example.com',
        outcome: 'issued',
      });

      expect(logger.error.calledOnce).to.equal(true);
    });
  });

  describe('listEvents', () => {
    it('clamps an outsized page request instead of reading the whole log', async () => {
      const find = sinon.stub(DelegationEvent, 'find').returns({
        sort: () => ({
          limit: (n: number) => ({ lean: () => ({ exec: async () => [] }) }),
        }),
      } as any);
      const limitSpy = sinon.spy();
      find.returns({
        sort: () => ({
          limit: (n: number) => {
            limitSpy(n);
            return { lean: () => ({ exec: async () => [] }) };
          },
        }),
      } as any);
      const { service } = makeService();

      await service.listEvents(orgId, { limit: 100000 });

      expect(limitSpy.firstCall.args[0]).to.equal(DELEGATION_EVENT_MAX_PAGE_SIZE);
    });
  });
});
