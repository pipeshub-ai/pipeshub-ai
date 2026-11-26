/**
 * The standalone-MongoDB admin-role lease (MongoUserRepository.withAdminLease) against a stubbed Org model: how it is
 * taken, waited for, refused and given back. Real-store behaviour is in the identity contract and mongo-transactions.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { Org } from '../../../../src/modules/user_management/schema/org.schema';
import { MongoUserRepository, adminLeaseTiming } from '../../../../src/libs/repositories/user/mongo';
import { ServiceUnavailableError } from '../../../../src/libs/errors/http.errors';
import { Logger } from '../../../../src/libs/services/logger.service';

describe('MongoUserRepository.withAdminLease (standalone MongoDB)', () => {
  const orgId = new mongoose.Types.ObjectId().toString();
  const repo = new MongoUserRepository();
  const saved = { ...adminLeaseTiming };
  let take: sinon.SinonStub;
  let exists: sinon.SinonStub;
  let release: sinon.SinonStub;

  beforeEach(() => {
    take = sinon.stub(Org, 'findOneAndUpdate').resolves({ _id: orgId } as never);
    exists = sinon.stub(Org, 'exists').resolves({ _id: orgId } as never);
    release = sinon.stub(Org, 'updateOne').resolves({ matchedCount: 1 } as never);
  });
  afterEach(() => {
    sinon.restore();
    Object.assign(adminLeaseTiming, saved);
  });

  const rejection = async (run: () => Promise<unknown>): Promise<unknown> => {
    try {
      await run();
    } catch (error) {
      return error;
    }
    return expect.fail('expected a rejection');
  };

  it('takes the lease by the server clock in one pipeline update, runs the change, and gives back only its own lease', async () => {
    expect(await repo.withAdminLease(orgId, async () => 'done')).to.equal('done');
    const [filter, update, options] = take.firstCall.args;
    expect(filter).to.deep.include({ _id: orgId, isDeleted: { $ne: true } });
    expect(filter.$expr).to.deep.equal({ $lt: ['$adminRoleLease.until', '$$NOW'] });
    const holder = update[0].$set.adminRoleLease.holder;
    expect(update[0].$set.adminRoleLease.until).to.deep.equal({ $add: ['$$NOW', adminLeaseTiming.leaseMs] });
    expect(options).to.include({ updatePipeline: true });
    expect(release.firstCall.args).to.deep.equal([
      { _id: orgId, 'adminRoleLease.holder': holder },
      { $unset: { adminRoleLease: 1 } },
    ]);
  });

  it('confirm: renews the lease by the server clock while this holder still holds it, false once it is lost', async () => {
    await repo.withAdminLease(orgId, async (l) => {
      expect(await l.confirm()).to.equal(true);
      take.resolves(null);
      expect(await l.confirm()).to.equal(false);
    });
    const holder = take.firstCall.args[1][0].$set.adminRoleLease.holder;
    const [filter, update, options] = take.secondCall.args;
    expect(filter).to.deep.equal({
      _id: orgId,
      'adminRoleLease.holder': holder,
      $expr: { $gt: ['$adminRoleLease.until', '$$NOW'] },
    });
    expect(update).to.deep.equal([{ $set: { 'adminRoleLease.until': { $add: ['$$NOW', adminLeaseTiming.leaseMs] } } }]);
    expect(options).to.include({ updatePipeline: true });
  });

  it('confirm: an organisation that is gone (no lease taken) has nothing to protect, so confirm is true without a query', async () => {
    take.resolves(null);
    exists.resolves(null);
    expect(await repo.withAdminLease(orgId, (lease) => lease.confirm())).to.equal(true);
    expect(take.calledOnce).to.equal(true);
  });

  it('gives the lease back when the change throws, and the change error is what the caller gets', async () => {
    const failed = new Error('write failed');
    expect(await rejection(() => repo.withAdminLease(orgId, async () => { throw failed; }))).to.equal(failed);
    expect(release.calledOnce).to.equal(true);
  });

  it('waits while another change holds it, then runs', async () => {
    take.onFirstCall().resolves(null);
    take.onSecondCall().resolves(null);
    const change = sinon.stub().resolves('ran');
    expect(await repo.withAdminLease(orgId, change)).to.equal('ran');
    expect(take.callCount).to.equal(3);
    expect(change.calledOnce).to.equal(true);
  });

  it('busy past the wait: 503 with Retry-After, and the change never runs', async () => {
    adminLeaseTiming.waitMs = 0;
    take.resolves(null);
    const change = sinon.stub().resolves('ran');
    const error = await rejection(() => repo.withAdminLease(orgId, change));
    expect(error).to.be.instanceOf(ServiceUnavailableError);
    expect((error as ServiceUnavailableError).metadata?.retryAfter).to.equal('5');
    expect(change.called).to.equal(false);
    expect(release.called).to.equal(false);
  });

  it('an organisation that is gone is not "busy": the change runs without a lease (nothing to give back)', async () => {
    take.resolves(null);
    exists.resolves(null);
    expect(await repo.withAdminLease(orgId, async () => 'ran')).to.equal('ran');
    expect(release.called).to.equal(false);
  });

  it('not re-entrant: a nested call for the same organisation waits for itself and is refused', async () => {
    adminLeaseTiming.waitMs = 0;
    take.onFirstCall().resolves({ _id: orgId } as never);
    take.resolves(null);
    const error = await rejection(() => repo.withAdminLease(orgId, () => repo.withAdminLease(orgId, async () => 'inner')));
    expect(error).to.be.instanceOf(ServiceUnavailableError);
    expect(release.calledOnce).to.equal(true);
  });

  it('a lease that expired under the change is reported once, and the change result stands', async () => {
    release.resolves({ matchedCount: 0 } as never);
    const warn = sinon.spy(Logger.getInstance(), 'warn');
    expect(await repo.withAdminLease(orgId, async () => 'done')).to.equal('done');
    expect(warn.calledOnce).to.equal(true);
    expect(warn.firstCall.args[0]).to.match(/lease expired before the change finished/);
    expect(warn.firstCall.args[1]).to.deep.equal({ orgId });
  });

  it('a release that fails does not replace the change result; the lease expires on its own', async () => {
    release.rejects(new Error('store down'));
    const warn = sinon.spy(Logger.getInstance(), 'warn');
    expect(await repo.withAdminLease(orgId, async () => 'done')).to.equal('done');
    expect(warn.calledOnce).to.equal(true);
    expect(warn.firstCall.args[1]).to.deep.equal({ orgId, error: 'store down' });
  });
});
