/**
 * E7B: the operator's one-time backfill of conversations deleted before
 * erasure existed, in memory. The erasure worker here is the real
 * `SessionErasureRunner` with one stub eraser per step (the conversation step
 * erases the in-memory conversation), so its guard is the production one.
 */
import { expect } from 'chai';
import { InMemoryConversations } from '../../../src/modules/agent-harness/adapters/in-memory-conversations';
import { InMemoryErasureBackfillStore } from '../../../src/modules/agent-harness/adapters/in-memory-erasure-backfill';
import { InMemoryAuditChainStore } from '../../../src/modules/agent-harness/adapters/in-memory-audit';
import { InMemoryErasureJobStore } from '../../../src/modules/agent-harness/adapters/in-memory-session-erasure';
import {
  ErasureBackfillDependencies,
  ErasureBackfillService,
} from '../../../src/modules/agent-harness/application/erasure-backfill';
import { SessionErasureRunner } from '../../../src/modules/agent-harness/application/session-erasure';
import { verifyAuditChain } from '../../../src/modules/agent-harness/domain/audit';
import {
  BACKFILL_CHECKPOINTS,
  BackfillCheckpoint,
  BackfillRefused,
} from '../../../src/modules/agent-harness/domain/erasure-backfill';
import { ERASURE_STEPS } from '../../../src/modules/agent-harness/domain/session-erasure';
import { SessionDataEraser } from '../../../src/modules/agent-harness/ports/session-erasure';
import {
  describeBackfillSourceContract,
  describeErasureBackfillStoreContract,
  hex,
} from './support/erasure-backfill-contract';
import { seedInMemoryConversations } from './support/conversation-seeds';

describeErasureBackfillStoreContract('in-memory', async () => new InMemoryErasureBackfillStore(() => 1));

describeBackfillSourceContract('in-memory', async () => {
  const conversations = new InMemoryConversations();
  return {
    source: conversations,
    seed: async (seeded) => seedInMemoryConversations(conversations, seeded),
  };
});

class Crash extends Error {
  constructor(readonly point: BackfillCheckpoint) {
    super(`crash at ${point}`);
  }
}

interface World {
  conversations: InMemoryConversations;
  jobs: InMemoryErasureJobStore;
  store: InMemoryErasureBackfillStore;
  audit: InMemoryAuditChainStore;
  logs: Array<{ level: string; message: string; meta: Record<string, unknown> }>;
  service: (extra?: Partial<ErasureBackfillDependencies<unknown>>) => ErasureBackfillService<unknown>;
  worker: { on: boolean };
  drain: () => Promise<void>;
  lease: { holder: string | null };
  enqueuedEver: string[];
}

function world(): World {
  let clock = 1_000_000;
  const now = () => clock;
  const conversations = new InMemoryConversations(now);
  const jobs = new InMemoryErasureJobStore(now);
  const store = new InMemoryErasureBackfillStore(now);
  const audit = new InMemoryAuditChainStore();
  const logs: World['logs'] = [];
  const enqueuedEver: string[] = [];
  const recordingJobs = {
    enqueueMany: async (requests: Parameters<InMemoryErasureJobStore['enqueueMany']>[0]) => {
      let created = 0;
      for (const request of requests) {
        if (await jobs.enqueue(request)) {
          enqueuedEver.push(request.sessionId);
          created += 1;
        }
      }
      return created;
    },
    findMany: (orgId: string, ids: readonly string[]) => jobs.findMany(orgId, ids),
  };
  const erasers: SessionDataEraser<unknown>[] = ERASURE_STEPS.map((step) => ({
    step,
    erase: async (job) =>
      step === 'conversation'
        ? { kind: 'done' as const, counts: await conversations.erase(job.orgId, job.sessionId) }
        : { kind: 'done' as const },
  }));
  const runner = new SessionErasureRunner({ jobs, erasers, conversations });
  const drain = async (): Promise<void> => {
    for (;;) {
      const job = await jobs.claimDue('worker', 60_000);
      if (job === null) return;
      await runner.run(job);
    }
  };
  const worker = { on: true };
  const lease = { holder: null as string | null };
  const logger = (level: string) => (message: string, meta?: Record<string, unknown>) =>
    logs.push({ level, message, meta: meta ?? {} });
  return {
    conversations,
    jobs,
    store,
    audit,
    logs,
    worker,
    drain,
    lease,
    enqueuedEver,
    service: (extra = {}) =>
      new ErasureBackfillService<unknown>({
        source: conversations,
        store,
        jobs: recordingJobs,
        audit,
        transaction: (work) => work(undefined),
        lease: {
          tryAcquire: async (_name, holder) => {
            if (lease.holder === null || lease.holder === holder) {
              lease.holder = holder;
              return true;
            }
            return false;
          },
          release: async (_name, holder) => {
            if (lease.holder === holder) lease.holder = null;
          },
        },
        holderId: 'op-host',
        logger: { debug: logger('debug'), info: logger('info'), warn: logger('warn'), error: logger('error') },
        sleep: async (ms) => {
          clock += ms;
          if (worker.on) await drain();
        },
        pollMs: 1_000,
        batchSize: 2,
        ...extra,
      }),
  };
}

const ORG_A = 'aa0000000000000000000001';
const ORG_B = 'bb0000000000000000000002';
const USER = 'cc0000000000000000000003';

/** Pre-upgrade deletions: 3 in org A (2 batches), 1 in org B; plus a live, a marked and a queued one. */
function seed(w: World): { a: string[]; b: string[]; live: string; marked: string } {
  const a = ['a1', 'a2', 'a3'].map((s) => `0000000000000000000000${s}`.slice(-24));
  const b = ['0000000000000000000000b1'];
  for (const sessionId of a) {
    w.conversations.put({ orgId: ORG_A, sessionId, deleted: true, by: `p_${USER}`, updatedAt: 5, lastActivityAt: 5, counts: { chatSessionMessages: 2, citations: 1 } });
  }
  w.conversations.put({ orgId: ORG_B, sessionId: b[0]!, deleted: true, updatedAt: 5, lastActivityAt: 5 });
  const live = '0000000000000000000000f1';
  const marked = '0000000000000000000000f2';
  w.conversations.put({ orgId: ORG_A, sessionId: live, deleted: false, updatedAt: 5, lastActivityAt: 5, counts: { chatSessionMessages: 9 } });
  w.conversations.put({ orgId: ORG_A, sessionId: marked, deleted: true, erasureRequestedAt: 4, by: `p_${USER}`, updatedAt: 5, lastActivityAt: 5 });
  return { a, b, live, marked };
}

const refusal = async (work: Promise<unknown>): Promise<string> => {
  try {
    await work;
  } catch (error) {
    if (error instanceof BackfillRefused) return error.reasonCode;
    throw error;
  }
  throw new Error('expected a refusal');
};

describe('E7B erasure backfill (in memory)', () => {
  it('BF-4, BF-5: the dry run counts per org and store, digests, leaves out live, marked and queued conversations, and writes nothing', async () => {
    const w = world();
    const { a } = seed(w);
    await w.jobs.enqueue({ orgId: ORG_A, sessionId: a[2]!, reason: 'user_delete', by: null });
    const report = await w.service().plan(null);
    expect(report.digest).to.match(/^sha256:[0-9a-f]{64}$/);
    expect(report.orgs).to.deep.equal([
      { orgId: ORG_A, sessions: 2, batches: 1, counts: { chatSessions: 2, chatSessionMessages: 4, citations: 2, legacyCopies: 0, agentRuns: 0 } },
      { orgId: ORG_B, sessions: 1, batches: 1, counts: { chatSessions: 1, chatSessionMessages: 0, citations: 0, legacyCopies: 0, agentRuns: 0 } },
    ]);
    expect(report.totals).to.deep.include({ sessions: 3, batches: 2 });
    expect(JSON.stringify(report)).to.not.include(a[0]!);
    expect(await w.store.plan(report.digest)).to.equal(null);
    expect(await w.store.markers()).to.deep.equal([]);
    expect(w.audit.records.size).to.equal(0);
    expect(w.enqueuedEver).to.deep.equal([]);
  });

  it('BF-6: the digest is a function of the data and the org filter', async () => {
    const w = world();
    const { a } = seed(w);
    const first = (await w.service().plan(null)).digest;
    expect((await w.service().plan(null)).digest).to.equal(first);
    expect((await w.service().plan(ORG_A)).digest).to.not.equal(first);
    w.conversations.update(a[0]!, { deleted: false });
    const restored = (await w.service().plan(null)).digest;
    expect(restored).to.not.equal(first);
    w.conversations.update(a[0]!, { deleted: true });
    expect((await w.service().plan(null)).digest, 'updatedAt moved').to.not.equal(first);
  });

  it('BF-7: execute refuses a digest the data does not match, and writes nothing', async () => {
    const w = world();
    const { a } = seed(w);
    const { digest } = await w.service().plan(null);
    expect(await refusal(w.service().execute({ digest: 'sha256:0', orgFilter: null, operator: 'ops' }))).to.equal('plan_digest_mismatch');
    w.conversations.update(a[1]!, { counts: { chatSessionMessages: 3 } });
    expect(await refusal(w.service().execute({ digest, orgFilter: null, operator: 'ops' }))).to.equal('plan_digest_mismatch');
    expect(await w.store.plan(digest)).to.equal(null);
    expect(w.enqueuedEver).to.deep.equal([]);
    expect(w.audit.records.size).to.equal(0);
    expect(w.lease.holder).to.equal(null);
  });

  it('BF-8, BF-9, BF-13: execute erases exactly the plan, in batches, audits each batch and org, and never a live or marked conversation', async () => {
    const w = world();
    const { a, b, live, marked } = seed(w);
    const { digest } = await w.service().plan(null);
    const report = await w.service().execute({ digest, orgFilter: null, operator: 'ops@example' });
    expect(report.status).to.equal('completed');
    expect(report.orgs).to.deep.equal([
      { orgId: ORG_A, state: 'done', erased: 3, withdrawn: 0, skipped: 0 },
      { orgId: ORG_B, state: 'done', erased: 1, withdrawn: 0, skipped: 0 },
    ]);
    for (const id of [...a, ...b]) expect(w.conversations.get(id), id).to.equal(undefined);
    expect(w.conversations.get(live)).to.include({ deleted: false });
    expect(w.conversations.get(marked)).to.include({ deleted: true });
    expect([...w.enqueuedEver].sort()).to.deep.equal([...a, ...b].sort());
    expect((await w.jobs.find(ORG_A, a[0]!))).to.include({ reason: 'user_delete', by: `p_${USER}`, status: 'completed' });
    const recordsA = w.audit.records.get(ORG_A)!;
    expect(recordsA.map((r) => r.kind)).to.deep.equal(['erasure.backfill_batch', 'erasure.backfill_batch', 'erasure.backfill_completed']);
    expect(recordsA[0]!.fields).to.deep.include({ planDigest: digest, operator: 'ops@example', batch: 0, sessions: 2, chatSessionMessages: 4 });
    expect(recordsA[2]!.fields).to.deep.include({ erased: 3, withdrawn: 0 });
    expect(JSON.stringify(recordsA)).to.not.include(a[0]!);
    expect(verifyAuditChain(recordsA).ok).to.equal(true);
    expect((await w.store.plan(digest))?.state).to.equal('completed');
    expect(await w.store.marker(ORG_B)).to.include({ digest, erased: 1 });
  });

  it('BF-14: a second execute of a completed plan is a no-op, and a new dry run plans nothing for done orgs', async () => {
    const w = world();
    seed(w);
    const { digest } = await w.service().plan(null);
    await w.service().execute({ digest, orgFilter: null, operator: 'ops' });
    const records = w.audit.records.get(ORG_A)!.length;
    const again = await w.service().execute({ digest, orgFilter: null, operator: 'ops' });
    expect(again.status).to.equal('already_done');
    expect(w.audit.records.get(ORG_A)!.length).to.equal(records);
    // A deletion an older node made after the backfill (no marker): kept, reported.
    w.conversations.put({ orgId: ORG_A, sessionId: '0000000000000000000000a9', deleted: true, updatedAt: 9, lastActivityAt: 9 });
    const next = await w.service().plan(null);
    expect(next.orgs).to.deep.equal([]);
    expect(next.alreadyDone.map((d) => [d.orgId, d.remaining])).to.deep.equal([[ORG_A, 1], [ORG_B, 0]]);
    const empty = await w.service().execute({ digest: next.digest, orgFilter: null, operator: 'ops' });
    expect(empty.status).to.equal('already_done');
    expect(w.conversations.get('0000000000000000000000a9')).to.include({ deleted: true });
  });

  it('BF-10: a planned conversation restored after execution began stales the plan before its batch; earlier batches stay', async () => {
    const w = world();
    const { a } = seed(w);
    const { digest } = await w.service().plan(null);
    let restored = false;
    const service = w.service({
      checkpoint: (point) => {
        if (point === 'after_settle' && !restored) {
          restored = true;
          w.conversations.update(a[2]!, { deleted: false });
        }
      },
    });
    expect(await refusal(service.execute({ digest, orgFilter: null, operator: 'ops' }))).to.equal('plan_stale');
    expect(w.conversations.get(a[0]!)).to.equal(undefined);
    expect(w.conversations.get(a[2]!)).to.include({ deleted: false });
    expect(w.enqueuedEver).to.not.include(a[2]);
    expect(w.audit.records.get(ORG_A)!.map((r) => r.kind)).to.deep.equal(['erasure.backfill_batch']);
    expect(await w.store.plan(digest)).to.include({ state: 'stale', staleReason: 'conversation_live' });
    expect(await refusal(w.service().execute({ digest, orgFilter: null, operator: 'ops' }))).to.equal('plan_stale');
    expect(w.logs.some((l) => l.message === 'erasure_backfill.stale' && l.level === 'warn')).to.equal(true);
  });

  it('BF-11: a planned conversation gone, or queued by someone else, is skipped without staling the plan', async () => {
    const w = world();
    const { a } = seed(w);
    const { digest } = await w.service().plan(null);
    await w.service({
      checkpoint: async (point) => {
        if (point === 'after_plan_persisted') {
          await w.conversations.erase(ORG_A, a[0]!);
          await w.jobs.enqueue({ orgId: ORG_A, sessionId: a[1]!, reason: 'user_delete', by: null });
        }
      },
    }).execute({ digest, orgFilter: null, operator: 'ops' });
    expect(await w.store.marker(ORG_A)).to.include({ erased: 1, skipped: 2 });
    expect(w.audit.records.get(ORG_A)![0]!.fields).to.include({ sessions: 0, skipped: 2 });
  });

  it('BF-13: restored after its batch was enqueued: the worker withdraws it and erases nothing of it', async () => {
    const w = world();
    const { a } = seed(w);
    const { digest } = await w.service().plan(ORG_A);
    w.worker.on = false;
    const service = w.service({
      checkpoint: (point) => {
        if (point === 'after_enqueue') w.conversations.update(a[0]!, { deleted: false, updatedAt: 99 });
      },
      sleep: async () => {
        w.worker.on = true;
        await w.drain();
      },
    });
    const report = await service.execute({ digest, orgFilter: ORG_A, operator: 'ops' });
    expect(report.orgs[0]).to.include({ erased: 2, withdrawn: 1 });
    expect(w.conversations.get(a[0]!)).to.include({ deleted: false });
    expect(await w.jobs.find(ORG_A, a[0]!)).to.equal(null);
    expect(w.audit.records.get(ORG_A)!.at(-1)!.fields).to.include({ withdrawn: 1 });
  });

  it('BF-19 (wait): while the worker has not erased a batch, execute reports in_progress; running it again resumes and completes', async () => {
    const w = world();
    seed(w);
    const { digest } = await w.service().plan(null);
    w.worker.on = false;
    const first = await w.service().execute({ digest, orgFilter: null, operator: 'ops', waitMs: 5_000 });
    expect(first.status).to.equal('in_progress');
    expect(w.lease.holder).to.equal(null);
    w.worker.on = true;
    const second = await w.service().execute({ digest, orgFilter: null, operator: 'ops' });
    expect(second.status).to.equal('completed');
    expect(w.audit.records.get(ORG_A)!.filter((r) => r.kind === 'erasure.backfill_batch')).to.have.length(2);
  });

  describe('BF-15: a crash at every journaled point resumes to the same end state', () => {
    const uncrashed = async () => {
      const w = world();
      seed(w);
      const { digest } = await w.service().plan(null);
      await w.service().execute({ digest, orgFilter: null, operator: 'ops' });
      return w;
    };
    // In memory there is no transaction to roll back; the Mongo itest crashes inside it.
    for (const point of BACKFILL_CHECKPOINTS.filter((p) => p !== 'in_enqueue_transaction')) {
      for (const occurrence of [1, 2]) {
        it(`${point} (occurrence ${String(occurrence)})`, async () => {
          const reference = await uncrashed();
          const w = world();
          const { a, b } = seed(w);
          const { digest } = await w.service().plan(null);
          let seen = 0;
          const crashing = w.service({
            checkpoint: (at) => {
              if (at === point && ++seen === occurrence) throw new Crash(at);
            },
          });
          let crashed = false;
          try {
            await crashing.execute({ digest, orgFilter: null, operator: 'ops' });
          } catch (error) {
            if (!(error instanceof Crash)) throw error;
            crashed = true;
          }
          w.lease.holder = null; // the crashed process's lease lapses
          const report = await w.service().execute({ digest, orgFilter: null, operator: 'ops' });
          expect(report.status).to.be.oneOf(crashed ? ['completed'] : ['completed', 'already_done']);
          for (const id of [...a, ...b]) expect(w.conversations.get(id)).to.equal(undefined);
          expect(new Set(w.enqueuedEver).size).to.equal(w.enqueuedEver.length);
          for (const org of [ORG_A, ORG_B]) {
            const kinds = w.audit.records.get(org)!.map((r) => r.kind);
            expect(kinds, org).to.deep.equal(reference.audit.records.get(org)!.map((r) => r.kind));
            expect(verifyAuditChain(w.audit.records.get(org)!).ok).to.equal(true);
          }
          expect(await w.store.markers()).to.have.length(2);
        });
      }
    }
  });

  it('BF-16: a second executor is refused while another holds the lease; a stale cursor move is refused', async () => {
    const w = world();
    seed(w);
    const { digest } = await w.service().plan(null);
    w.lease.holder = 'someone-else';
    expect(await refusal(w.service().execute({ digest, orgFilter: null, operator: 'ops' }))).to.equal('backfill_in_progress');
    w.lease.holder = null;
    const moved = w.service({
      checkpoint: async (point) => {
        if (point === 'after_intent') {
          await w.store.moveCursor(digest, { org: 0, batch: 0, phase: 'intended' }, { org: 0, batch: 0, phase: 'enqueued' });
        }
      },
    });
    expect(await refusal(moved.execute({ digest, orgFilter: null, operator: 'ops' }))).to.equal('backfill_in_progress');
    expect(w.audit.records.get(ORG_A)).to.equal(undefined);
  });

  it('BF-17: stored items that no longer hash to the digest are refused before any batch', async () => {
    const w = world();
    const { a } = seed(w);
    const { digest } = await w.service().plan(null);
    await w.service({ checkpoint: (p) => { if (p === 'after_plan_persisted') throw new Crash(p); } })
      .execute({ digest, orgFilter: null, operator: 'ops' })
      .catch((error: unknown) => { if (!(error instanceof Crash)) throw error; });
    w.lease.holder = null;
    w.store.tamper(digest, a[0]!, { by: 'p_someone_else' });
    expect(await refusal(w.service().execute({ digest, orgFilter: null, operator: 'ops' }))).to.equal('plan_tampered');
    w.store.tamper(digest, a[0]!, { by: `p_${USER}`, batch: 1 });
    expect(await refusal(w.service().execute({ digest, orgFilter: null, operator: 'ops' }))).to.equal('plan_tampered');
    expect(w.enqueuedEver).to.deep.equal([]);
  });

  it('BF-22: log lines carry digests, orgs and counts, never a session id', async () => {
    const w = world();
    const { a } = seed(w);
    const { digest } = await w.service().plan(null);
    await w.service().execute({ digest, orgFilter: null, operator: 'ops' });
    const names = w.logs.map((l) => l.message);
    expect(names).to.include.members(['erasure_backfill.planned', 'erasure_backfill.batch_enqueued', 'erasure_backfill.batch_settled', 'erasure_backfill.org_completed', 'erasure_backfill.completed']);
    expect(JSON.stringify(w.logs)).to.not.include(a[0]!);
  });

  it('status reports the plan cursor and the markers', async () => {
    const w = world();
    seed(w);
    const { digest } = await w.service().plan(ORG_B);
    await w.service().execute({ digest, orgFilter: ORG_B, operator: 'ops' });
    const status = await w.service().status(digest);
    expect(status.plan).to.include({ state: 'completed', orgFilter: ORG_B, items: 1 });
    expect(status.markers.map((m) => m.orgId)).to.deep.equal([ORG_B]);
    expect((await w.service().status(null)).plan).to.equal(null);
    expect(hex()).to.match(/^[0-9a-f]{24}$/);
  });
});
