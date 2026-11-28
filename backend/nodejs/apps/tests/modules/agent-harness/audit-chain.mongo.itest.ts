/**
 * O-2 on a real MongoDB replica set: the audit chain commits and aborts with
 * the journal, stays linear under concurrent commits of one org, follows a
 * real run, and its anchors and verifier catch changes to the stored records
 * (run instructions in execution-store.mongo.itest.ts).
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { inMongoTransaction } from '../../../src/libs/repositories/agent-harness/durable-transaction/mongo';
import {
  AgentAuditHeadModel,
  AgentAuditPendingModel,
  AgentAuditRecordModel,
} from '../../../src/libs/repositories/agent-harness/audit/schema/audit.schema';
import { MongoAuditAnchorSink } from '../../../src/libs/repositories/agent-harness/audit-anchor-sink/mongo';
import {
  auditLinksIdle,
  MongoAuditChainStore,
} from '../../../src/libs/repositories/agent-harness/audit/mongo';
import { MongoJournalStore } from '../../../src/libs/repositories/agent-harness/journal/mongo';
import { MongoLeaderLease } from '../../../src/libs/repositories/agent-harness/leader-lease/mongo';
import { MongoPayloadStore } from '../../../src/libs/repositories/agent-harness/payload-store/mongo';
import {
  AuditAnchorService,
  AuditVerifier,
} from '../../../src/modules/agent-harness/application/audit-anchoring';
import { AuditedJournalStore } from '../../../src/modules/agent-harness/application/audited-journal-store';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { SealedJournalStore } from '../../../src/modules/agent-harness/application/sealed-journal-store';
import { AUDIT_GENESIS_HASH } from '../../../src/modules/agent-harness/domain/audit';
import { JournalEventInput } from '../../../src/modules/agent-harness/domain/journal';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  mongoBackend,
  resetHarnessMongo,
} from './support/harness-mongo';
import { testCipher } from './support/data-protection';
import {
  contractClock,
  createContractHarness,
} from './support/execution-store-contract';
import { position, workerEvent } from './support/journal-fixtures';
import { HARNESS_ENABLED } from './support/rollout';

const admitted = (): JournalEventInput =>
  workerEvent('run.admitted', {
    runKind: 'interactive',
    principalRef: 'p_user-1',
  });

describe('O-2 audit chain on a MongoDB replica set', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(resetHarnessMongo);

  const audit = new MongoAuditChainStore();
  const journal = new AuditedJournalStore(
    new SealedJournalStore(new MongoJournalStore(), testCipher()),
    audit,
  );
  const service = new JournalService(new MongoPayloadStore(), testCipher());
  const prepare = (runId: string, events: JournalEventInput[]) =>
    service.prepare({ ...position({ runId }), writer: 'node', events });

  it('O-2a: an aborted journal transaction leaves no audit record', async () => {
    const prepared = await prepare('run-1', [admitted()]);
    try {
      await inMongoTransaction(async (session) => {
        await journal.append(prepared, session);
        throw new Error('abort after the append');
      });
      expect.fail('expected the transaction to abort');
    } catch (error) {
      expect((error as Error).message).to.equal('abort after the append');
    }
    await auditLinksIdle();
    expect(await AgentAuditRecordModel.countDocuments({})).to.equal(0);
    expect(await AgentAuditPendingModel.countDocuments({})).to.equal(0);
    expect(await AgentAuditHeadModel.countDocuments({})).to.equal(0);
  });

  it('O-2a: concurrent commits of one org stay one linear chain', async () => {
    await Promise.all(
      Array.from({ length: 8 }, async (_, index) => {
        const prepared = await prepare(`run-${String(index)}`, [
          admitted(),
          workerEvent('run.claimed', { attempt: 1, fence: 1, leaseMs: 1_000 }),
        ]);
        await inMongoTransaction((session) =>
          journal.append(prepared, session),
        );
      }),
    );
    const verification = await new AuditVerifier(
      audit,
      new MongoAuditAnchorSink(),
    ).verify('org-1');
    expect(verification).to.deep.include({
      ok: true,
      records: 16,
      lastSeq: 16,
    });
    const records = await audit.read('org-1', 0, 100);
    expect(records[0]?.prevHash).to.equal(AUDIT_GENESIS_HASH);
    // Each run's two records are adjacent: a commit's records are contiguous.
    for (let index = 0; index < 16; index += 2) {
      expect(records[index]?.runId).to.equal(records[index + 1]?.runId);
    }
  });

  it('O-2a: a real run chains its admission, claim, pin and cancellation', async () => {
    const clock = contractClock();
    const h = createContractHarness(mongoBackend({ clock }), clock);
    h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
    const { run } = await h.admit({
      agentKey: 'agent-1',
      authorization: 'Bearer user',
    });
    const claimed = await h.claim(run);
    await h.service.control({
      orgId: run.orgId,
      runId: run.runId,
      commandId: 'cancel-1',
      dispatchCommandId: 'cancel-dispatch-1',
      action: 'cancel',
      expectedRevision: claimed.revision,
      by: `p_${run.actorId}`,
    });
    const records = await audit.read(run.orgId, 0, 100);
    expect(records.map((record) => record.kind)).to.include.members([
      'run.admitted',
      'run.claimed',
      'run.spec_pinned',
      'run.cancel_requested',
    ]);
    expect(records.every((record) => record.runId === run.runId)).to.equal(
      true,
    );
    // AUD-17 (QA-4): how the caller authenticated is part of the audited admission.
    expect(
      records.find((record) => record.kind === 'run.admitted')?.fields,
    ).to.deep.include({ via: 'session' });
    expect(
      (
        await new AuditVerifier(audit, new MongoAuditAnchorSink()).verify(
          run.orgId,
        )
      ).ok,
    ).to.equal(true);
  });

  it('O-2c: anchors heads, and the verifier catches a changed or deleted stored record', async () => {
    for (let index = 0; index < 3; index += 1) {
      const prepared = await prepare(`run-${String(index)}`, [admitted()]);
      await inMongoTransaction((session) =>
        journal.append(prepared, session),
      );
    }
    const sink = new MongoAuditAnchorSink();
    const anchoring = new AuditAnchorService({
      chain: audit,
      sink,
      lease: new MongoLeaderLease(),
      holderId: 'itest-anchorer',
    });
    expect(await anchoring.tick()).to.equal(1);
    expect(await anchoring.tick()).to.equal(0);
    expect(
      (await sink.list('org-1')).map((anchor) => anchor.seq),
    ).to.deep.equal([3]);
    const verifier = new AuditVerifier(audit, sink);
    expect((await verifier.verify('org-1')).ok).to.equal(true);

    await AgentAuditRecordModel.collection.updateOne(
      { orgId: 'org-1', seq: 2 },
      { $set: { 'fields.principalRef': 'p_someone-else' } },
    );
    expect(
      (await verifier.verify('org-1')).problems.map((problem) => problem.kind),
    ).to.deep.equal(['tampered']);

    await AgentAuditRecordModel.collection.deleteOne({
      orgId: 'org-1',
      seq: 3,
    });
    const kinds = (await verifier.verify('org-1')).problems.map(
      (problem) => problem.kind,
    );
    expect(kinds).to.include.members([
      'tampered',
      'head_mismatch',
      'anchor_missing',
    ]);
  });
});
