/**
 * PIN-22 on a MongoDB replica set (CPD-5): the admission transaction reads the
 * org head and refuses a pin resolved at another revision (`CP_STALE`),
 * writing nothing; a pin at the head is admitted and kept on the run. Run as
 * execution-store.mongo.itest.ts says.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { Model, Types } from 'mongoose';
import sinon from 'sinon';
import { DEPLOYMENT_HEAD_ID } from '../../../src/modules/control-plane';
import { CpOrgHeadModel } from '../../../src/libs/repositories/control-plane/org-heads/schema/org-heads.schema';
import { AdmitRunCommand, PinStale } from '../../../src/modules/agent-harness/domain/admission';
import { connectHarnessMongo, disconnectHarnessMongo, mongoBackend, registerHarnessModels, resetHarnessMongo } from './support/harness-mongo';

registerHarnessModels([CpOrgHeadModel] as unknown as Array<Model<never>>);

const DIGEST = `sha256:${'3'.repeat(64)}` as const;
const pin = (headRevision: number) => ({
  schemaVersion: 1 as const,
  headRevision,
  routing: { path: 'durable' as const, reason: null },
  scope: { agentKey: null, projectId: null },
  effectiveSet: { items: [], digest: DIGEST },
  effectiveConfig: { schemaVersion: 1 as const, dimensions: { 'budgets.maxTurns': { value: 15, setBy: 'release' as const, ceilings: [] } } },
  llm: null,
  deploymentDigest: DIGEST,
  digest: DIGEST,
});

describe('PIN-22: the head check inside the Mongo admission transaction', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(resetHarnessMongo);

  const command = (orgId: string, headRevision: number, deployment = 0): AdmitRunCommand => ({
    orgId,
    actorId: new Types.ObjectId().toString(),
    sessionId: null,
    idempotencyKey: `k-${headRevision}-${Math.random()}`,
    payloadHash: DIGEST,
    runId: new Types.ObjectId().toString(),
    dispatchCommandId: `dispatch:${Math.random()}`,
    absoluteDeadlineAt: Date.now() + 600_000,
    input: { query: 'q', model: { chatMode: 'internal_search' }, filters: {}, attachments: [], recordIds: [], previousConversations: [] } as never,
    harnessMajor: 2,
    via: 'session',
    resolved: pin(headRevision),
    resolvedAt: { org: headRevision, deployment },
  });

  it('a pin resolved before a committed write is refused CP_STALE and nothing is written', async () => {
    const backend = mongoBackend();
    const orgId = new Types.ObjectId().toString();
    await CpOrgHeadModel.create({ _id: orgId, revision: 1, updatedAt: new Date(), lease: null });
    const stale = command(orgId, 0);
    const error = await backend.admissionStore.admit(stale, Date.now()).catch((e: unknown) => e);
    expect(error).to.be.instanceOf(PinStale);
    expect(await backend.executionStore.get(orgId, stale.runId)).to.equal(null);
  });

  it('CPD10-40: a pin resolved before an AI-model save committed (the deployment row moved, the org’s did not) is refused CP_STALE', async () => {
    const backend = mongoBackend();
    const orgId = new Types.ObjectId().toString();
    await CpOrgHeadModel.create({ _id: orgId, revision: 1, updatedAt: new Date(), lease: null });
    await CpOrgHeadModel.create({ _id: DEPLOYMENT_HEAD_ID, revision: 3, updatedAt: new Date(), lease: null });
    const stale = command(orgId, 1, 2);
    expect(await backend.admissionStore.admit(stale, Date.now()).catch((e: unknown) => e)).to.be.instanceOf(PinStale);
    expect(await backend.executionStore.get(orgId, stale.runId)).to.equal(null);
    const current = command(orgId, 1, 3);
    expect((await backend.admissionStore.admit(current, Date.now())).run.runId).to.equal(current.runId);
  });

  it('CPD10-40: both rows are read in one query of the admission', async () => {
    const backend = mongoBackend();
    const orgId = new Types.ObjectId().toString();
    await CpOrgHeadModel.create({ _id: orgId, revision: 1, updatedAt: new Date(), lease: null });
    const find = sinon.spy(CpOrgHeadModel, 'find');
    const findOne = sinon.spy(CpOrgHeadModel, 'findOne');
    try {
      await backend.admissionStore.admit(command(orgId, 1), Date.now());
      expect(find.args.map((args: unknown[]) => args[0])).to.deep.equal([{ _id: { $in: [orgId, DEPLOYMENT_HEAD_ID] } }]);
      expect(findOne.called).to.equal(false);
    } finally {
      sinon.restore();
    }
  });

  it('a pin at the head is admitted and the run keeps it', async () => {
    const backend = mongoBackend();
    const orgId = new Types.ObjectId().toString();
    await CpOrgHeadModel.create({ _id: orgId, revision: 1, updatedAt: new Date(), lease: null });
    const current = command(orgId, 1);
    const admitted = await backend.admissionStore.admit(current, Date.now());
    const subject = await backend.executionStore.readClaimSubject(orgId, admitted.run.runId);
    expect(subject?.resolved).to.deep.equal(pin(1));
  });
});
