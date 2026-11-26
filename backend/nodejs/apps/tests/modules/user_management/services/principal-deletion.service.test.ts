/**
 * CLEANUP-G2: what a user deletion does inside the request, in
 * one transaction (the durable job runs the rest; its Mongo proof is
 * principal-erasure.mongo.itest.ts). The Mongoose models are stubbed; the
 * transaction runs its work once with a marker session.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { Types } from 'mongoose';
import {
  earliestActiveAdmin,
  NO_ADMIN_FOR_HANDOVER,
  NoAdminForHandoverError,
  PrincipalDeletionService,
} from '../../../../src/modules/user_management/services/principal-deletion.service';
import { Users } from '../../../../src/modules/user_management/schema/users.schema';
import { ChatSession } from '../../../../src/modules/enterprise_search/schema/chat.session.schema';
import type { PrincipalErasureJob } from '../../../../src/modules/agent-harness/domain/principal-erasure';

const ORG = new Types.ObjectId().toHexString();
const USER = new Types.ObjectId().toHexString();
const ADMIN = new Types.ObjectId().toHexString();
const BY = new Types.ObjectId().toHexString();
const NOW = 1_800_000_000_000;
const TX = { marker: 'tx' } as never;

const query = (value: unknown) => {
  const chain = {
    sort: () => chain,
    batchSize: () => chain,
    select: () => chain,
    read: () => chain,
    lean: () => Promise.resolve(value),
  };
  return chain as never;
};

const jobsStub = (found: Partial<PrincipalErasureJob> | null = null) => ({
  now: sinon.stub().resolves(NOW),
  requestOrg: sinon.stub().resolves(true),
  requestUser: sinon.stub().resolves(),
  findOrg: sinon.stub().resolves(found),
  cancelOrg: sinon.stub().resolves(true),
  due: sinon.stub().resolves([]),
  writeProgress: sinon.stub().resolves(true),
  remove: sinon.stub().resolves(),
});

const service = (
  jobs: ReturnType<typeof jobsStub>,
  sessions = { enqueue: sinon.stub().resolves(true), enqueueMany: sinon.stub().resolvesArg(0), find: sinon.stub().resolves(null) },
) => {
  const transaction = sinon.spy(<T>(work: (tx: never) => Promise<T>) => work(TX));
  return {
    sessions,
    transaction,
    svc: new PrincipalDeletionService(jobs as never, sessions as never, transaction as never),
  };
};

describe('CLEANUP-G2 PrincipalDeletionService', () => {
  afterEach(() => sinon.restore());

  describe('handOverUser', () => {
    const owned = (shared: boolean) => ({
      _id: new Types.ObjectId(),
      isShared: shared,
      sharedWith: shared ? [{ userId: new Types.ObjectId(ADMIN) }, { userId: new Types.ObjectId() }] : [],
    });

    it("erases the private conversations and moves the shared ones to the earliest active admin, with the user's job", async () => {
      const mine = owned(false);
      const sharedOne = owned(true);
      const sharedFlagOnly = { _id: new Types.ObjectId(), isShared: true, sharedWith: [] };
      sinon.stub(ChatSession, 'find').returns(query([mine, sharedOne, sharedFlagOnly]));
      const findAdmin = sinon.stub(Users, 'findOne').returns(query({ _id: new Types.ObjectId(ADMIN) }));
      const updates = sinon.stub(ChatSession, 'updateMany').resolves({} as never);
      const jobs = jobsStub();
      const { svc, sessions } = service(jobs);

      expect(await svc.handOverUser(ORG, USER, BY)).to.deep.equal({ erased: 2, transferred: 1 });

      const [adminFilter] = findAdmin.firstCall.args as unknown as [Record<string, unknown>];
      expect(adminFilter).to.deep.include({ role: 'admin', isDeleted: false, isDisabled: { $ne: true }, kind: { $ne: 'service' } });
      expect(String((adminFilter['_id'] as { $ne: unknown }).$ne)).to.equal(USER);
      expect(updates.callCount, 'one write moves the shared ones and one marks the private ones').to.equal(2);
      const idsOf = (call: sinon.SinonSpyCall) => (call.args[0] as unknown as { _id: { $in: unknown[] } })._id.$in.map(String);
      const moved = updates.getCalls().find((call) => idsOf(call).includes(String(sharedOne._id)))!;
      expect(idsOf(moved)).to.deep.equal([String(sharedOne._id)]);
      const [, change, options] = moved.args as unknown as [unknown, { $set: Record<string, unknown>; $pull: unknown }, unknown];
      expect(String(change.$set['userId'])).to.equal(ADMIN);
      expect(String(change.$set['initiator'])).to.equal(ADMIN);
      expect(change.$pull).to.have.property('sharedWith');
      expect(options).to.deep.equal({ session: TX });
      const marked = updates.getCalls().find((call) => !idsOf(call).includes(String(sharedOne._id)))!;
      expect(idsOf(marked)).to.deep.equal([String(mine._id), String(sharedFlagOnly._id)]);
      expect(marked.args[1]).to.deep.equal({ $set: { isDeleted: true, erasureRequestedAt: NOW } });
      expect(sessions.enqueue.called).to.equal(false);
      expect(sessions.enqueueMany.callCount).to.equal(1);
      const [requests, tx] = sessions.enqueueMany.firstCall.args as [Array<{ sessionId: string }>, unknown];
      expect(requests.map((request) => request.sessionId)).to.deep.equal([String(mine._id), String(sharedFlagOnly._id)]);
      expect(requests[0]).to.deep.include({ orgId: ORG, reason: 'user_delete', by: `p_${BY}` });
      expect(tx).to.equal(TX);
      expect(jobs.requestUser.firstCall.args).to.deep.equal([
        { orgId: ORG, userId: USER, by: `p_${BY}`, requestedAt: NOW, transfers: [{ sessionId: String(sharedOne._id), to: ADMIN }] },
        TX,
      ]);
    });

    it('a user with only private conversations needs no admin', async () => {
      sinon.stub(ChatSession, 'find').returns(query([owned(false)]));
      const findAdmin = sinon.stub(Users, 'findOne');
      sinon.stub(ChatSession, 'updateMany').resolves({} as never);
      const { svc } = service(jobsStub());
      expect(await svc.handOverUser(ORG, USER, BY)).to.deep.equal({ erased: 1, transferred: 0 });
      expect(findAdmin.called).to.equal(false);
    });

    it('shared conversations and no other active admin: refused with its reason code, nothing written', async () => {
      sinon.stub(ChatSession, 'find').returns(query([owned(true)]));
      sinon.stub(Users, 'findOne').returns(query(null));
      const updates = sinon.stub(ChatSession, 'updateMany');
      const jobs = jobsStub();
      const { svc, transaction } = service(jobs);
      const refused = await svc.handOverUser(ORG, USER, BY).catch((error: unknown) => error);
      expect(refused).to.be.instanceOf(NoAdminForHandoverError);
      expect((refused as NoAdminForHandoverError).statusCode).to.equal(400);
      expect(JSON.stringify(refused)).to.include(NO_ADMIN_FOR_HANDOVER);
      expect(transaction.called).to.equal(false);
      expect(updates.called).to.equal(false);
      expect(jobs.requestUser.called).to.equal(false);
    });
  });

  it('earliestActiveAdmin: the oldest account first, then the lowest id; null when there is none', async () => {
    const sort = sinon.stub();
    const chain = { sort, select: () => chain, read: () => chain, lean: () => Promise.resolve({ _id: new Types.ObjectId(ADMIN) }) };
    sort.returns(chain);
    sinon.stub(Users, 'findOne').returns(chain as never);
    expect(await earliestActiveAdmin(ORG, USER)).to.equal(ADMIN);
    expect(sort.firstCall.args).to.deep.equal([{ createdAt: 1, _id: 1 }]);
    sinon.restore();
    sinon.stub(Users, 'findOne').returns(query(null));
    expect(await earliestActiveAdmin(ORG, USER)).to.equal(null);
  });
});
