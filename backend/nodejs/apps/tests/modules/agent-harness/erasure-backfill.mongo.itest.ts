/**
 * E7B on a MongoDB replica set: the store and source contracts (BF-18), the
 * sweeper off by default over real `chatSessions` (BF-1, BF-2), and the
 * operator CLI end to end with the real erasure runner, conversation eraser
 * and audit chain (BF-7..BF-9, BF-13..BF-15, BF-19).
 *
 *   MONGO_IT_URI="mongodb://localhost:<port>/x?replicaSet=rs0" REPLICA_SET_AVAILABLE=true \
 *     TS_NODE_PROJECT=tsconfig.test.json npx mocha --no-config --require reflect-metadata \
 *     --require ts-node/register --exit --timeout 60000 tests/modules/agent-harness/erasure-backfill.mongo.itest.ts
 */
import 'reflect-metadata';
import { expect } from 'chai';
import mongoose, { ClientSession, Model, Types } from 'mongoose';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  registerHarnessModels,
  resetHarnessMongo,
} from './support/harness-mongo';
import {
  describeBackfillSourceContract,
  describeErasureBackfillStoreContract,
} from './support/erasure-backfill-contract';
import {
  CONVERSATION_SENTINEL as SENTINEL,
  seedMongoConversations as seedConversations,
} from './support/conversation-seeds';
import { MongoErasureBackfillStore } from '../../../src/libs/repositories/agent-harness/erasure-backfill/mongo';
import {
  AgentErasureBackfillItemModel,
  AgentErasureBackfillMarkerModel,
  AgentErasureBackfillPlanModel,
} from '../../../src/libs/repositories/agent-harness/erasure-backfill/schema/erasure-backfill.schema';
import { MongoErasureJobStore, MongoSessionRunLookup } from '../../../src/libs/repositories/agent-harness/session-erasure/mongo';
import { AgentSessionErasureModel } from '../../../src/libs/repositories/agent-harness/session-erasure/schema/session-erasure.schema';
import { MongoAuditChainStore } from '../../../src/libs/repositories/agent-harness/audit/mongo';
import { MongoAuditAnchorSink } from '../../../src/libs/repositories/agent-harness/audit-anchor-sink/mongo';
import { AgentAuditRecordModel } from '../../../src/libs/repositories/agent-harness/audit/schema/audit.schema';
import { auditLinksIdle } from '../../../src/libs/repositories/agent-harness/audit/mongo';
import { MongoLeaderLease } from '../../../src/libs/repositories/agent-harness/leader-lease/mongo';
import { MongoRetentionStore } from '../../../src/libs/repositories/agent-harness/retention-store/mongo';
import { inMongoTransaction } from '../../../src/libs/repositories/agent-harness/durable-transaction/mongo';
import { AuditVerifier } from '../../../src/modules/agent-harness/application/audit-anchoring';
import { ErasureBackfillService } from '../../../src/modules/agent-harness/application/erasure-backfill';
import { RetentionSweeper } from '../../../src/modules/agent-harness/application/retention-sweeper';
import { SessionErasureRunner } from '../../../src/modules/agent-harness/application/session-erasure';
import { NO_RETENTION } from '../../../src/modules/agent-harness/ports/session-retention';
import { BackfillCheckpoint } from '../../../src/modules/agent-harness/domain/erasure-backfill';
import { ERASURE_STEPS } from '../../../src/modules/agent-harness/domain/session-erasure';
import { SessionDataEraser } from '../../../src/modules/agent-harness/ports/session-erasure';
import { HarnessConversationBackfillSource } from '../../../src/libs/repositories/agent-harness/conversation-backfill-source/mongo';
import { HarnessConversationEraser } from '../../../src/libs/repositories/agent-harness/conversation-eraser/mongo';
import { HarnessConversationRetention } from '../../../src/libs/repositories/agent-harness/conversation-retention/mongo';
import { ChatSession } from '../../../src/modules/enterprise_search/schema/chat.session.schema';
import { ChatSessionMessage } from '../../../src/modules/enterprise_search/schema/chat.session.message.schema';
import Citation from '../../../src/modules/enterprise_search/schema/citation.schema';
import {
  BackfillCliIo,
  backfillBackend,
  runErasureBackfillCli,
} from '../../../src/modules/enterprise_search/ops/erasure-backfill-cli';
import { FakeChatAttachments } from './support/chat-attachments';

registerHarnessModels([
  AgentErasureBackfillPlanModel,
  AgentErasureBackfillItemModel,
  AgentErasureBackfillMarkerModel,
  AgentSessionErasureModel,
  Citation,
] as unknown as Array<Model<never>>);


describe('E7B erasure backfill on a MongoDB replica set', function () {
  this.timeout(120_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(async () => {
    await resetHarnessMongo();
    for (const name of ['conversations', 'agentconversations', 'agentRuns']) {
      await mongoose.connection.collection(name).deleteMany({});
    }
  });

  describeErasureBackfillStoreContract('mongo', async () => new MongoErasureBackfillStore() as never);
  describeBackfillSourceContract('mongo', async () => ({
    source: new HarnessConversationBackfillSource(),
    seed: seedConversations,
  }));

  const sweeper = () =>
    new RetentionSweeper<ClientSession>({
      store: new MongoRetentionStore(),
      settings: NO_RETENTION,
      conversations: new HarnessConversationRetention(),
      erasures: new MongoErasureJobStore(),
      runs: new MongoSessionRunLookup(),
      artifacts: null,
      transaction: (work) => inMongoTransaction(work),
      lease: { tryAcquire: () => Promise.resolve(true), release: () => Promise.resolve() },
      holderId: 'it',
    });

  it('BF-1, BF-2: the sweeper never enqueues a pre-upgrade deletion; it re-enqueues a marked one whose enqueue was lost', async () => {
    const org = new Types.ObjectId().toHexString();
    const old = new Types.ObjectId().toHexString();
    const lost = new Types.ObjectId().toHexString();
    await seedConversations([
      { orgId: org, sessionId: old, deleted: true, messages: 1 },
      { orgId: org, sessionId: lost, deleted: true, marked: true, deletedBy: org },
    ]);
    const first = (await sweeper().tick())!;
    expect(first.requeued).to.equal(1);
    for (let tick = 0; tick < 2; tick += 1) expect((await sweeper().tick())!.requeued).to.equal(0);
    const jobs = new MongoErasureJobStore();
    expect(await jobs.find(org, old)).to.equal(null);
    expect(await jobs.find(org, lost)).to.include({ reason: 'user_delete', by: `p_${org}` });
    expect(await ChatSessionMessage.countDocuments({ sessionId: new Types.ObjectId(old) })).to.equal(1);
    const index = (await ChatSession.collection.indexes()).find((i) => i.name === 'erasure_requested');
    expect(index?.partialFilterExpression).to.deep.equal({ erasureRequestedAt: { $exists: true } });
  });

  interface Rig {
    org: string;
    deleted: string[];
    live: string;
    run: (argv: string[], checkpoint?: (point: BackfillCheckpoint) => void | Promise<void>) => Promise<{ code: number; out: Record<string, unknown> }>;
    drain: () => Promise<void>;
  }

  /** Three pre-upgrade deletions in one org (two batches of two) and one live conversation. */
  async function rig(options: { worker?: boolean } = {}): Promise<Rig> {
    const org = new Types.ObjectId().toHexString();
    const deleted = [0, 1, 2].map(() => new Types.ObjectId().toHexString()).sort();
    const live = new Types.ObjectId().toHexString();
    await seedConversations([
      ...deleted.map((sessionId) => ({ orgId: org, sessionId, deleted: true, deletedBy: org, messages: 2, citations: 1, legacyCopies: 1 })),
      { orgId: org, sessionId: live, deleted: false, messages: 1, citations: 1 },
    ]);
    const jobs = new MongoErasureJobStore();
    const conversations = new HarnessConversationEraser(new FakeChatAttachments());
    const erasers: SessionDataEraser<ClientSession>[] = ERASURE_STEPS.map((step) => ({
      step,
      erase: async (job) =>
        step === 'conversation'
          ? { kind: 'done' as const, counts: await conversations.erase(job.orgId, job.sessionId) }
          : { kind: 'done' as const },
    }));
    const runner = new SessionErasureRunner({ jobs, erasers, conversations });
    const drain = async (): Promise<void> => {
      for (;;) {
        const job = await jobs.claimDue('it-worker', 60_000);
        if (job === null) return;
        await runner.run(job);
      }
    };
    const run: Rig['run'] = async (argv, checkpoint) => {
      const lines: string[] = [];
      const io: BackfillCliIo = { out: (l) => lines.push(l), err: (l) => lines.push(l), writeFile: () => undefined };
      const production = backfillBackend();
      const service = new ErasureBackfillService<ClientSession>({
        source: new HarnessConversationBackfillSource(),
        store: new MongoErasureBackfillStore(),
        jobs,
        audit: new MongoAuditChainStore(),
        transaction: (work) => inMongoTransaction(work),
        lease: new MongoLeaderLease(),
        holderId: `it-${String(Math.random())}`,
        batchSize: 2,
        pollMs: 10,
        sleep: async () => {
          if (options.worker !== false) await drain();
        },
        ...(checkpoint !== undefined ? { checkpoint } : {}),
      });
      const code = await runErasureBackfillCli(
        [...argv, '--mongo-uri', 'unused', '--db', 'unused'],
        async () => ({ service: service as never, prepareForExecute: production.prepareForExecute, close: async () => undefined }),
        io,
      );
      return { code, out: JSON.parse(lines[0] ?? '{}') as Record<string, unknown> };
    };
    return { org, deleted, live, run, drain };
  }

  const expectErased = async (r: Rig) => {
    for (const id of r.deleted) {
      const oid = new Types.ObjectId(id);
      expect(await ChatSession.countDocuments({ _id: oid }), id).to.equal(0);
      expect(await ChatSessionMessage.countDocuments({ sessionId: oid })).to.equal(0);
      expect(await mongoose.connection.collection('conversations').countDocuments({ _id: oid })).to.equal(0);
    }
    expect(await Citation.countDocuments({ 'metadata.orgId': r.org })).to.equal(1);
    expect((await ChatSession.findById(r.live).lean())?.isDeleted).to.equal(false);
    expect(await ChatSessionMessage.countDocuments({ sessionId: new Types.ObjectId(r.live) })).to.equal(1);
  };

  it('BF-4, BF-7, BF-8, BF-9, BF-14, BF-19: dry run writes nothing; a wrong digest is refused; the reviewed one erases exactly the plan; a second run is a no-op', async () => {
    const r = await rig();
    const plan = await r.run(['plan']);
    expect(plan.code).to.equal(0);
    expect(plan.out['orgs']).to.deep.equal([
      { orgId: r.org, sessions: 3, batches: 2, counts: { chatSessions: 3, chatSessionMessages: 6, citations: 6, legacyCopies: 3, agentRuns: 0 } },
    ]);
    expect(JSON.stringify(plan.out)).to.not.include(r.deleted[0]!);
    expect(await AgentErasureBackfillPlanModel.countDocuments({})).to.equal(0);
    expect(await AgentErasureBackfillItemModel.countDocuments({})).to.equal(0);
    expect(await AgentSessionErasureModel.countDocuments({})).to.equal(0);
    const digest = String(plan.out['digest']);

    const wrong = await r.run(['execute', '--digest', 'sha256:0', '--operator', 'ops@example']);
    expect(wrong).to.deep.include({ code: 1 });
    expect(wrong.out).to.include({ refused: 'plan_digest_mismatch' });
    expect(await AgentSessionErasureModel.countDocuments({})).to.equal(0);

    const done = await r.run(['execute', '--digest', digest, '--operator', 'ops@example']);
    expect(done.code).to.equal(0);
    expect(done.out).to.include({ status: 'completed' });
    await expectErased(r);
    await auditLinksIdle();
    const records = await AgentAuditRecordModel.find({ orgId: r.org }).sort({ seq: 1 }).lean();
    expect(records.map((x) => x.kind)).to.deep.equal(['erasure.backfill_batch', 'erasure.backfill_batch', 'erasure.backfill_completed']);
    expect(records[0]!.fields).to.include({ planDigest: digest, operator: 'ops@example', sessions: 2, batch: 0 });
    expect(JSON.stringify(records)).to.not.include(SENTINEL);
    expect((await new AuditVerifier(new MongoAuditChainStore(), new MongoAuditAnchorSink()).verify(r.org)).ok).to.equal(true);
    expect(await AgentErasureBackfillMarkerModel.findById(r.org).lean()).to.include({ digest, erased: 3, withdrawn: 0 });

    const again = await r.run(['execute', '--digest', digest, '--operator', 'ops@example']);
    expect(again).to.deep.include({ code: 0 });
    expect(again.out).to.include({ status: 'already_done' });
    await auditLinksIdle();
    expect(await AgentAuditRecordModel.countDocuments({ orgId: r.org })).to.equal(3);
    const replan = await r.run(['plan']);
    expect(replan.out['orgs']).to.deep.equal([]);
    expect((replan.out['alreadyDone'] as Array<Record<string, unknown>>)[0]).to.include({ orgId: r.org, remaining: 0 });
  });

  for (const point of ['in_enqueue_transaction', 'after_enqueue', 'after_marker'] as const) {
    it(`BF-15: a crash at ${point} resumes to the uncrashed end state, each batch audited once`, async () => {
      const r = await rig();
      const digest = String((await r.run(['plan'])).out['digest']);
      let crashed = false;
      const crash = await r
        .run(['execute', '--digest', digest, '--operator', 'ops'], (at) => {
          if (at === point && !crashed) {
            crashed = true;
            throw new Error(`crash at ${at}`);
          }
        })
        .then(
          () => 'no crash',
          () => 'crashed',
        );
      expect(crash).to.equal('crashed');
      // The crashed process's lease lapses.
      await AgentSessionErasureModel.db.collection('agentHarnessLeases').deleteMany({});
      const resumed = await r.run(['execute', '--digest', digest, '--operator', 'ops']);
      expect(resumed.out['status']).to.be.oneOf(['completed', 'already_done']);
      await expectErased(r);
      await auditLinksIdle();
      const kinds = (await AgentAuditRecordModel.find({ orgId: r.org }).sort({ seq: 1 }).lean()).map((x) => x.kind);
      expect(kinds).to.deep.equal(['erasure.backfill_batch', 'erasure.backfill_batch', 'erasure.backfill_completed']);
      expect((await new AuditVerifier(new MongoAuditChainStore(), new MongoAuditAnchorSink()).verify(r.org)).ok).to.equal(true);
    });
  }

  it('BF-15: a crash inside the enqueue transaction rolls the whole batch back', async () => {
    const r = await rig();
    const digest = String((await r.run(['plan'])).out['digest']);
    await r
      .run(['execute', '--digest', digest, '--operator', 'ops'], (at) => {
        if (at === 'in_enqueue_transaction') throw new Error('crash');
      })
      .catch(() => undefined);
    expect(await AgentSessionErasureModel.countDocuments({})).to.equal(0);
    await auditLinksIdle();
    expect(await AgentAuditRecordModel.countDocuments({ orgId: r.org })).to.equal(0);
    expect((await AgentErasureBackfillPlanModel.findById(digest).lean())?.cursor).to.deep.include({ batch: 0, phase: 'intended' });
    expect(await AgentErasureBackfillItemModel.countDocuments({ digest, state: 'planned' })).to.equal(3);
  });

  it('BF-13: a conversation restored after its batch was enqueued is withdrawn by the worker, its data intact', async () => {
    const r = await rig({ worker: false });
    const digest = String((await r.run(['plan'])).out['digest']);
    const first = await r.run(['execute', '--digest', digest, '--operator', 'ops', '--wait-seconds', '0']);
    expect(first).to.deep.include({ code: 3 });
    await ChatSession.updateOne({ _id: r.deleted[0] }, { $set: { isDeleted: false } });
    await r.drain();
    expect(await AgentSessionErasureModel.countDocuments({ sessionId: r.deleted[0] })).to.equal(0);
    expect(await ChatSessionMessage.countDocuments({ sessionId: new Types.ObjectId(r.deleted[0]) })).to.equal(2);
    expect(await ChatSession.countDocuments({ _id: r.deleted[1] })).to.equal(0);
  });
});
