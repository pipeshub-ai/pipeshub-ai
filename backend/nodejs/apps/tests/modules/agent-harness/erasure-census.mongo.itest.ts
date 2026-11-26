/**
 * QA-15: erasure proven by behaviour. A session with data in every store it
 * writes to is erased by the worker `bindAgentHarness` composes, on a real
 * MongoDB replica set (and Redis when REDIS_IT_URL is set); then every store
 * is scanned for anything of it (support/erasure-census.ts). The artifact
 * registry is the query service's (its real-graph census is Python's
 * test_erasure_real_graph.py); the sandbox broker is a folder tree behind
 * the production HTTP client. Run instructions in execution-store.mongo.itest.ts.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import mongoose, { Types } from 'mongoose';
import { connectHarnessMongo, disconnectHarnessMongo, resetHarnessMongo } from './support/harness-mongo';
import { workerEvent } from './support/journal-fixtures';
import { enqueue, markDeleted, redis, world } from './support/session-erasure-world';
import { Trace, scanFolder } from './support/erasure-census';
import { FolderSandboxBroker } from './support/folder-sandbox-broker';
import { census, composedWorker, drain, expectErasedByCensus, needlesFor, Rig, seeded } from './support/erasure-census-rig';
import { Seeded } from './support/session-erasure-world';
import { sweeperWithAbandonedForks } from './support/agent-erasure-sweeper';
import { MongoAbandonedForks } from '../../../src/libs/repositories/agent-harness/abandoned-forks/mongo';
import { AgentSessionForkModel } from '../../../src/libs/repositories/agent-harness/session-forks/schema/session-forks.schema';
import { inMongoTransaction } from '../../../src/libs/repositories/agent-harness/durable-transaction/mongo';
import { ABANDONED_FORK_AFTER_MS } from '../../../src/modules/agent-harness/domain/session-retention';
import { ChatSessionMessage } from '../../../src/modules/enterprise_search/schema/chat.session.message.schema';
import { MongoErasureJobStore } from '../../../src/libs/repositories/agent-harness/session-erasure/mongo';
import { SESSION_DATA_STORES } from '../../../src/modules/agent-harness/domain/session-erasure';
import { HarnessConflictError } from '../../../src/modules/agent-harness/domain/errors';
import { AgentRunModel } from '../../../src/libs/repositories/agent-harness/execution/schema/execution.schema';
import { testRunKeys } from './support/data-protection';
import { ChatSession } from '../../../src/modules/enterprise_search/schema/chat.session.schema';
import {
  AUDIT_LINK_DEFER_MS,
  auditLinkDefer,
  auditLinksIdle,
  MongoAuditChainStore,
} from '../../../src/libs/repositories/agent-harness/audit/mongo';
import { AgentAuditRecordModel } from '../../../src/libs/repositories/agent-harness/audit/schema/audit.schema';
import { ChainWorkspaceDownloadAudit } from '../../../src/modules/agent-harness/adapters/chain-workspace-download-audit';
import {
  BackfillCliIo,
  backfillBackend,
  runErasureBackfillCli,
} from '../../../src/modules/enterprise_search/ops/erasure-backfill-cli';

/**
 * Stores this rig cannot write, with where they are proven instead: the
 * workers' object store is sealed under the session key and shredded
 * (ERA-20), the artifact registry runs in the query service (QA15-9).
 */
const NOT_IN_THIS_RIG: Readonly<Record<string, string>> = {
  artifactBlobs: 'query service: tests/unit/services/artifact_registry/test_erasure_real_graph.py',
  agentSkillCandidates: 'query service: test_erasure_real_graph.py CLEANUP-G7 (Neo4j and Arango)',
  opikTraces: 'Opik, through the query service: session-erasure-traces.test.ts and test_org_and_trace_erase_routes.py (fake Opik)',
  workspaceObjects: 'sealed with the session key; shred proven by workspace-service.test.ts NW-7',
  skillBundles: 'sealed with the session key; shred proven by workspace-service.test.ts NW-7',
  engineState: 'sealed with the session key; shred proven by workspace-service.test.ts NW-7',
  sandboxSnapshots: 'sealed with the session key; shred proven by test_docker_snapshots.py dk5',
};

const storesOf = (traces: readonly Trace[]): string[] => [...new Set(traces.map((t) => t.store))].sort();

describe('QA-15 erasure census on a MongoDB replica set', function () {
  this.timeout(180_000);
  let rig: Rig;

  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(async () => {
    await resetHarnessMongo();
    for (const name of ['conversations', 'agentconversations', 'citations']) {
      await mongoose.connection.collection(name).deleteMany({});
    }
    const broker = new FolderSandboxBroker();
    rig = { w: world(), broker, endpoint: await broker.start() };
  });
  afterEach(async () => {
    await rig.broker.stop();
  });

  it('QA15-1 (ERA-19, IX-ERA-1): the composed worker leaves nothing of a session in any store; another session stays whole', async () => {
    const a = await seeded(rig, true);
    const b = await seeded(rig, true, rig.w.harness.tenant);
    const before = await census(rig, needlesFor(a));
    const erasable = SESSION_DATA_STORES.filter(
      (entry) => 'step' in entry.coverage && NOT_IN_THIS_RIG[entry.store] === undefined && (redis !== null || entry.kind !== 'redis'),
    ).map((entry) => entry.store);
    expect(storesOf(before), 'the seed reaches every store erasure covers').to.include.members(erasable);
    const bBefore = storesOf(await census(rig, needlesFor(b)));

    await enqueue(rig.w, a);
    expect(await drain(rig, composedWorker(rig), a)).to.equal('completed');

    await expectErasedByCensus(rig, a);
    expect(storesOf(await census(rig, needlesFor(b)))).to.deep.equal(bBefore);
  });

  it("QA15F-4 (ERA-26): a run an older build stored with the caller's key keeps only its digest once erased", async () => {
    const a = await seeded(rig, false);
    const legacyKey = `legacy-${a.canary}`;
    await AgentRunModel.updateOne({ orgId: a.orgId, runId: a.runIds[0] }, { $set: { idempotencyKey: legacyKey } });
    await enqueue(rig.w, a);
    expect(await drain(rig, composedWorker(rig), a)).to.equal('completed');
    const tombstone = await AgentRunModel.findOne({ orgId: a.orgId, runId: a.runIds[0] }).lean();
    expect(tombstone?.idempotencyKey).to.equal(testRunKeys().digest(a.orgId, legacyKey));
    await expectErasedByCensus(rig, a);
  });

  it("CLEANUP-1: a run's follow-up suggestions go with the session, and a suggestion call still in flight cannot put them back", async () => {
    const a = await seeded(rig, false);
    const runId = a.runIds[0]!;
    // The run ended and its suggestions are still being written when the conversation is deleted.
    await AgentRunModel.updateOne({ orgId: a.orgId, runId }, { $set: { status: 'completed' }, $unset: { followups: '' } });
    await enqueue(rig.w, a);
    expect(await drain(rig, composedWorker(rig), a)).to.equal('completed');
    await rig.w.harness.service.storeFollowups(a.orgId, runId, [{ text: a.canary }]);
    expect((await AgentRunModel.findOne({ orgId: a.orgId, runId }).lean())?.followups).to.deep.equal([]);
    await expectErasedByCensus(rig, a);
  });

  it('CLEANUP-G4: a fork whose conversation never committed is erased an hour after it began; a fork still in its hour stays', async () => {
    // The fork's copies (workspace, key, artifacts, journal) exist; its conversation, and the messages, citations
    // and attachments that come with it, never committed.
    const abandoned = await seeded(rig, false);
    const fresh = await seeded(rig, false, rig.w.harness.tenant);
    for (const s of [abandoned, fresh]) {
      await mongoose.connection.collection('citations').deleteMany({ _id: { $in: s.citationIds.map((id) => new Types.ObjectId(id)) } });
      for (const [id, upload] of rig.w.chatAttachments.uploads) {
        if (JSON.stringify(upload).includes(s.canary)) rig.w.chatAttachments.uploads.delete(id);
      }
      await ChatSession.deleteOne({ _id: s.sessionId });
      await ChatSessionMessage.deleteMany({ sessionId: new Types.ObjectId(s.sessionId) });
      for (const legacy of ['conversations', 'agentconversations']) {
        await mongoose.connection.collection(legacy).deleteOne({ _id: new Types.ObjectId(s.sessionId) });
      }
    }
    const now = rig.w.clock.now();
    const record = (s: Seeded, createdAt: number) => ({
      _id: `${s.orgId}:fork-of:${s.sessionId}`,
      orgId: s.orgId,
      sourceSessionId: new Types.ObjectId().toHexString(),
      actorId: rig.w.harness.tenant.actorId,
      forkSessionId: s.sessionId,
      result: null,
      createdAt,
      completedAt: null,
    });
    await AgentSessionForkModel.create([
      record(abandoned, now - ABANDONED_FORK_AFTER_MS - 1),
      record(fresh, now - ABANDONED_FORK_AFTER_MS + 60_000),
    ]);
    const freshBefore = await census(rig, needlesFor(fresh));

    const sweeper = sweeperWithAbandonedForks({ forks: new MongoAbandonedForks(), erasures: rig.w.jobs, transaction: inMongoTransaction, now });
    expect((await sweeper.tick())?.abandonedForks).to.equal(1);
    expect(await drain(rig, composedWorker(rig), abandoned)).to.equal('completed');

    await expectErasedByCensus(rig, abandoned);
    expect(await census(rig, needlesFor(fresh))).to.deep.equal(freshBefore);
    expect(await rig.w.jobs.find(fresh.orgId, fresh.sessionId)).to.equal(null);
    expect((await sweeper.tick())?.abandonedForks).to.equal(0);
  });

  it('QA15-3 (ERA-06, E7B): the composed worker withdraws the job of a live conversation and erases nothing of it', async () => {
    const a = await seeded(rig, false);
    const before = await census(rig, needlesFor(a));
    await rig.w.jobs.enqueue({ orgId: a.orgId, sessionId: a.sessionId, reason: 'user_delete', by: null });
    expect(await drain(rig, composedWorker(rig), a)).to.equal('withdrawn');
    const after = await census(rig, needlesFor(a));
    expect(storesOf(after)).to.deep.equal(storesOf(before));
    expect(after.length).to.equal(before.length);
  });

  it('QA15-4 (IX-ERA-6): a run running during erasure is cancelled first; its worker cannot write once it ended; nothing of it remains', async () => {
    const a = await seeded(rig, true);
    const worker = composedWorker(rig);
    await enqueue(rig.w, a);
    await worker.tick();
    const running = (await rig.w.harness.service.get(a.orgId, a.liveRunId!))!;
    expect(running.status).to.equal('cancelling');
    expect(await drain(rig, worker, a)).to.equal('completed');
    const ended = (await rig.w.harness.service.get(a.orgId, a.liveRunId!))!;
    let refused: unknown;
    try {
      await rig.w.harness.service.commit({
        commandId: `late-${a.canary}`,
        orgId: a.orgId,
        runId: ended.runId,
        workerId: `worker-${ended.runId}`,
        attemptId: running.attemptId as string,
        fence: running.fence,
        events: [workerEvent('tool.outcome', { invocationId: 'late', status: 'ok', receipt: { text: a.canary }, labels: { integrity: 'external' } })],
        legacyEvents: [],
        outbox: [],
      });
    } catch (error) {
      refused = error;
    }
    expect(refused, 'a late commit of the cancelled run').to.be.instanceOf(HarnessConflictError);
    expect((refused as HarnessConflictError).code).to.equal('STALE_ATTEMPT');
    await expectErasedByCensus(rig, a);
  });

  it('QA15-5 (ERA-24, ERA-G4): the broker is down, then the Node process is replaced mid-job: the job waits at the sandbox step and the next process finishes it', async () => {
    const a = await seeded(rig, false);
    await enqueue(rig.w, a);
    rig.broker.down = true;
    const first = composedWorker(rig);
    for (let round = 0; round < 20; round += 1) {
      await first.tick();
      const job = (await rig.w.jobs.find(a.orgId, a.sessionId))!;
      if (job.step === 'sandbox_folders' && job.failures >= 2) break;
      rig.w.clock.set(Math.max(rig.w.clock.now(), job.nextAttemptAt));
    }
    const stuck = (await rig.w.jobs.find(a.orgId, a.sessionId))!;
    expect(stuck).to.include({ status: 'pending', step: 'sandbox_folders' });
    expect(stuck.failures).to.be.at.least(2);
    expect(scanFolder(rig.broker.root, 'sandboxFolders', needlesFor(a))).to.not.deep.equal([]);
    await first.stop();

    rig.broker.down = false;
    rig.w.clock.set(stuck.nextAttemptAt);
    expect(await drain(rig, composedWorker(rig), a)).to.equal('completed');
    await expectErasedByCensus(rig, a);
  });

  it('GAPS2-2: a download audited on the chain outlives the erasure of its chat with ids, counts and digests only', async () => {
    const a = await seeded(rig, false);
    const downloads = new ChainWorkspaceDownloadAudit(new MongoAuditChainStore(), inMongoTransaction);
    const download = {
      type: 'workspace.downloaded' as const,
      orgId: a.orgId,
      actorId: 'user-gaps2',
      sessionId: a.sessionId,
      revision: 2,
      files: 1,
      bytes: 64,
    };
    await downloads.record({ ...download, target: 'file', path: `out/${a.canary}.txt` });
    await downloads.record({ ...download, target: 'archive', path: a.canary });
    await enqueue(rig.w, a);
    expect(await drain(rig, composedWorker(rig), a)).to.equal('completed');

    await expectErasedByCensus(rig, a);
    const kept = await AgentAuditRecordModel.find({ orgId: a.orgId, kind: 'workspace.downloaded' }).sort({ seq: 1 }).lean();
    expect(kept.map((record) => [record.sessionId, record.fields['target'], record.fields['by']])).to.deep.equal([
      [a.sessionId, 'file', 'p_user-gaps2'],
      [a.sessionId, 'archive', 'p_user-gaps2'],
    ]);
  });

  it('PERFE3-B4: a session nothing touches has the same census before and after its audit link runs', async () => {
    // Held, so the link runs where this test says: between the two censuses, as it may on a slow host (QA15-6).
    auditLinkDefer.ms = 600_000;
    try {
      const live = await seeded(rig, false, rig.w.harness.tenant);
      const before = await census(rig, needlesFor(live));
      await auditLinksIdle();
      expect(await census(rig, needlesFor(live))).to.deep.equal(before);
    } finally {
      auditLinkDefer.ms = AUDIT_LINK_DEFER_MS;
    }
  });

  it('QA15-6 (ERA-17, IX-ERA-5): the operator backfill on the production backend: plan, execute while the worker is busy, execute again; every planned session passes the census', async () => {
    const tenant = rig.w.harness.tenant;
    const old = [await seeded(rig, false, tenant), await seeded(rig, false, tenant)];
    const live = await seeded(rig, false, tenant);
    const marked = await seeded(rig, false, tenant);
    for (const s of old) await ChatSession.updateOne({ _id: s.sessionId }, { $set: { isDeleted: true } });
    await markDeleted(marked);
    const liveBefore = await census(rig, needlesFor(live));
    const markedBefore = await census(rig, needlesFor(marked));

    const cli = async (argv: string[]) => {
      const lines: string[] = [];
      const io: BackfillCliIo = { out: (l) => lines.push(l), err: (l) => lines.push(l), writeFile: () => undefined };
      const code = await runErasureBackfillCli(
        [...argv, '--mongo-uri', 'open', '--db', 'open'],
        async () => ({ ...backfillBackend(), close: async () => undefined }),
        io,
      );
      return { code, out: JSON.parse(lines[0] ?? '{}') as Record<string, unknown> };
    };
    const plan = await cli(['plan', '--org', tenant.orgId]);
    expect(plan.code).to.equal(0);
    expect((plan.out['orgs'] as Array<{ sessions: number }>)[0]?.sessions).to.equal(2);
    const digest = String(plan.out['digest']);

    const busy = await cli(['execute', '--digest', digest, '--operator', 'ops@example', '--org', tenant.orgId, '--wait-seconds', '0']);
    expect(busy.code).to.equal(3);
    const jobs = new MongoErasureJobStore();
    const worker = composedWorker(rig, jobs);
    for (const s of old) expect(await drain(rig, worker, s, jobs)).to.equal('completed');

    const done = await cli(['execute', '--digest', digest, '--operator', 'ops@example', '--org', tenant.orgId]);
    expect(done).to.deep.include({ code: 0 });
    expect(done.out).to.include({ status: 'completed' });
    const status = await cli(['status', '--digest', digest]);
    expect(status.code).to.equal(0);
    expect(JSON.stringify(status.out)).to.not.include(old[0]!.sessionId);

    for (const s of old) await expectErasedByCensus(rig, s);
    expect(await census(rig, needlesFor(live))).to.deep.equal(liveBefore);
    expect(storesOf(await census(rig, needlesFor(marked)))).to.deep.equal(storesOf(markedBefore));
    expect((await ChatSession.findById(live.sessionId).lean())?.isDeleted).to.not.equal(true);
    expect(await ChatSession.countDocuments({ _id: new Types.ObjectId(marked.sessionId) })).to.equal(1);
  });
});
