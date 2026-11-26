/**
 * PERF-C (PERFB-F1) on a MongoDB replica set (run as
 * execution-store.mongo.itest.ts says): sessions of one org acting in the
 * same instant do not abort each other. Read from MongoDB's own profile of
 * the operations: none failed with a write conflict, and no transaction of
 * an admission, claim or commit wrote a document the org shares.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import mongoose, { Types } from 'mongoose';
import { orgQuotaKey } from '../../../src/modules/agent-harness/domain/admission-quotas';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { AuditVerifier } from '../../../src/modules/agent-harness/application/audit-anchoring';
import { MongoAuditAnchorSink } from '../../../src/libs/repositories/agent-harness/audit-anchor-sink/mongo';
import { auditLinksIdle, MongoAuditChainStore } from '../../../src/libs/repositories/agent-harness/audit/mongo';
import { AUDIT_HEADS, AUDIT_PENDING, AUDIT_RECORDS } from '../../../src/libs/repositories/agent-harness/audit-linking/mongo';
import { AgentAdmissionQuotaModel, AgentRunModel } from '../../../src/libs/repositories/agent-harness/execution/schema/execution.schema';
import { contractClock, ContractHarness, createContractHarness } from './support/execution-store-contract';
import { connectHarnessMongo, disconnectHarnessMongo, mongoBackend, resetHarnessMongo } from './support/harness-mongo';
import { HARNESS_ENABLED } from './support/rollout';

const SESSIONS = 16;
const WRITE_CONFLICT = 112;
const QUOTAS = AgentAdmissionQuotaModel.collection.collectionName;

interface Profiled {
  collection: string;
  /** The transaction it ran in, or null. */
  transaction: string | null;
  errCode: number | null;
  /** The `_id` a write of the quota collection asked for. */
  quotaKey: string | null;
  write: boolean;
}

const WRITES = new Set(['insert', 'update', 'remove']);

describe('PERF-C: sessions of one org in the same instant (MongoDB replica set)', function () {
  this.timeout(180_000);
  let h: ContractHarness;

  before(connectHarnessMongo);
  after(async () => {
    const db = mongoose.connection.db!;
    await db.command({ profile: 0 });
    await db.collection('system.profile').drop().catch(() => undefined);
    await disconnectHarnessMongo();
  });
  beforeEach(async () => {
    await resetHarnessMongo();
    const clock = contractClock();
    h = createContractHarness(mongoBackend({ clock }), clock);
    h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
  });

  /** Every operation MongoDB ran on this database while `work` ran, and the links it set off. */
  const profiled = async (work: () => Promise<unknown>): Promise<Profiled[]> => {
    const db = mongoose.connection.db!;
    await db.command({ profile: 0 });
    await db.collection('system.profile').drop().catch(() => undefined);
    await db.createCollection('system.profile', { capped: true, size: 64 * 1024 * 1024 });
    await db.command({ profile: 2 });
    try {
      await work();
      await auditLinksIdle();
    } finally {
      await db.command({ profile: 0 });
    }
    const entries = await db.collection('system.profile').find({}).sort({ ts: 1 }).toArray();
    return entries.map((entry) => {
      const command = (entry['command'] ?? {}) as Record<string, unknown>;
      const query = (command['query'] ?? (command['q'] as unknown)) as { _id?: unknown } | undefined;
      const inTransaction = command['autocommit'] === false;
      return {
        collection: String(entry['ns']).split('.').slice(1).join('.'),
        transaction: inTransaction ? `${JSON.stringify(command['lsid'])}:${String(command['txnNumber'])}` : null,
        errCode: typeof entry['errCode'] === 'number' ? entry['errCode'] : null,
        quotaKey: typeof query?._id === 'string' ? query._id : null,
        write: WRITES.has(String(entry['op'])) || command['findAndModify'] !== undefined,
      };
    });
  };

  const conflicts = (ops: Profiled[]): string[] =>
    ops.filter((op) => op.errCode === WRITE_CONFLICT).map((op) => op.collection);

  /** The transactions that wrote `collection`. */
  const writers = (ops: Profiled[], collection: string): Set<string> =>
    new Set(ops.filter((op) => op.write && op.collection === collection && op.transaction !== null).map((op) => op.transaction as string));

  const actor = () => ({ orgId: h.tenant.orgId, actorId: new Types.ObjectId().toString() });
  const agentRun = { agentKey: 'agent-1', authorization: 'Bearer user' };

  it('PERFC-6: 16 actors admitted at once: no write conflict, and no transaction writes the org window', async () => {
    let runs: HarnessRun[] = [];
    const ops = await profiled(async () => {
      runs = (await Promise.all(Array.from({ length: SESSIONS }, () => h.admit({ ...actor(), ...agentRun })))).map(
        (admitted) => admitted.run,
      );
    });
    expect(runs).to.have.length(SESSIONS);
    expect(conflicts(ops), 'operations that failed with a write conflict').to.deep.equal([]);
    const orgWrites = ops.filter((op) => op.collection === QUOTAS && op.write && op.quotaKey === orgQuotaKey(h.tenant.orgId));
    expect(orgWrites.length, 'a reservation and a release for each admission').to.equal(2 * SESSIONS);
    expect(orgWrites.filter((op) => op.transaction !== null)).to.deep.equal([]);
    expect(await AgentRunModel.countDocuments({ orgId: h.tenant.orgId })).to.equal(SESSIONS);
  });

  it('PERFC-25: 16 runs claimed, then cancelled, at once: no write conflict, and no commit writes the chain or its head', async () => {
    const runs = (await Promise.all(Array.from({ length: SESSIONS }, () => h.admit({ ...actor(), ...agentRun })))).map(
      (admitted) => admitted.run,
    );
    await auditLinksIdle();
    const ops = await profiled(async () => {
      const claimed = await Promise.all(runs.map((run, index) => h.claim(run, `worker-${String(index)}`)));
      await Promise.all(
        claimed.map((run) =>
          h.service.control({
            orgId: run.orgId,
            runId: run.runId,
            commandId: `cancel-${run.runId}`,
            dispatchCommandId: `cancel-dispatch-${run.runId}`,
            action: 'cancel',
            expectedRevision: run.revision,
            by: `p_${run.actorId}`,
          }),
        ),
      );
    });
    expect(conflicts(ops), 'operations that failed with a write conflict').to.deep.equal([]);
    const journalWriters = writers(ops, 'sessionEvents');
    expect(journalWriters.size, 'a transaction for every claim and every cancellation').to.be.at.least(2 * SESSIONS);
    const pendingWriters = writers(ops, AUDIT_PENDING);
    for (const collection of [AUDIT_HEADS, AUDIT_RECORDS]) {
      const shared = [...writers(ops, collection)].filter((transaction) => journalWriters.has(transaction));
      expect(shared, `commit transactions that wrote ${collection}`).to.deep.equal([]);
    }
    expect([...journalWriters].filter((transaction) => pendingWriters.has(transaction)).length).to.be.at.least(2 * SESSIONS);

    const audit = new MongoAuditChainStore();
    const verification = await new AuditVerifier(audit, new MongoAuditAnchorSink()).verify(h.tenant.orgId);
    expect(verification.ok).to.equal(true);
    const kinds = (await audit.read(h.tenant.orgId, 0, 1_000)).map((record) => record.kind);
    for (const kind of ['run.admitted', 'run.claimed', 'run.spec_pinned', 'run.cancel_requested']) {
      expect(kinds.filter((each) => each === kind), kind).to.have.length(SESSIONS);
    }
  });
});
