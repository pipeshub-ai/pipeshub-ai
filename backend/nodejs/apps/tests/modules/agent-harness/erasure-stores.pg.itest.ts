/**
 * The erasure stores on PostgreSQL (Alembic head, row-level security, PgBouncer in transaction mode): session erasure
 * jobs, agent erasures, principal (org and user) deletions and the E7B backfill. Each store's
 * shared contract runs here as it does in memory and on MongoDB; the cases after it prove on PostgreSQL what the
 * replica set proves for Mongo: one claimer per job under concurrent sweepers, a stale holder refused by the fence in
 * the statement, nothing left by an aborted unit, idempotent requests answering the first, and no org seeing another's.
 *
 *   PG_TEST_BINS=… PG_TEST_PGBOUNCER=… PG_TEST_MIGRATE_PYTHON=… npm run test:pg-harness
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { activeUnit } from '../../../src/libs/db';
import { PgAgentErasureQueue } from '../../../src/libs/repositories/agent-harness/agent-erasures/pg';
import { PgErasureBackfillStore } from '../../../src/libs/repositories/agent-harness/erasure-backfill/pg';
import { jobsUnit } from '../../../src/libs/repositories/agent-harness/pg-documents/pg';
import { PgPrincipalErasureJobs } from '../../../src/libs/repositories/agent-harness/principal-erasure/pg';
import { PgErasureJobStore } from '../../../src/libs/repositories/agent-harness/session-erasure/pg';
import { fixedPgTime, pgServerTime } from '../../../src/libs/repositories/agent-harness/time/pg';
import { INITIAL_BACKFILL_CURSOR, zeroBackfillCounts } from '../../../src/modules/agent-harness/domain/erasure-backfill';
import { ErasureRequest } from '../../../src/modules/agent-harness/domain/session-erasure';
import { describeAgentErasureQueueContract } from './support/agent-erasure-queue-contract';
import { describeErasureBackfillStoreContract } from './support/erasure-backfill-contract';
import { describeErasureJobStoreContract } from './support/erasure-job-store-contract';
import { describePrincipalErasureJobsContract } from './support/principal-erasure-jobs-contract';
import {
  PgStack,
  boundStore,
  hexOrg,
  inOrg,
  pgBins,
  pgSuite,
  resetHarnessPg,
  scopedStore,
  startHarnessPg,
  transactAs,
} from './support/harness-pg';

const dbNow = (): Promise<number> => jobsUnit('test clock', { write: false }, (k) => pgServerTime.now(k));

/** The rows of `table` the unit running as `orgId` can see. */
const visibleRows = (orgId: string, table: string): Promise<number> =>
  transactAs(async () => {
    const { rows } = await activeUnit()!.query<{ n: number }>(
      `/* pipeshub:harness.test_count */ SELECT count(*)::int AS n FROM harness.${table}`,
    );
    return rows[0]!.n;
  }, orgId);

const request = (orgId: string, sessionId = 'ses-1'): ErasureRequest => ({
  orgId,
  sessionId,
  reason: 'user_delete',
  by: 'p_owner',
});

for (const bin of pgBins()) {
  pgSuite(`agent harness erasure stores on PostgreSQL (${bin}) behind PgBouncer`, function () {
    this.timeout(120_000);
    let stack: PgStack;
    before(async () => {
      stack = await startHarnessPg(bin, 'harness_erasure');
    });
    after(async () => {
      await stack?.stop();
    });

    describeErasureJobStoreContract('postgres', async (clock) => {
      await resetHarnessPg(stack);
      return scopedStore(new PgErasureJobStore(fixedPgTime(() => clock.now())), { find: 0, findMany: 0 });
    });

    describeAgentErasureQueueContract('postgres', async () => {
      await resetHarnessPg(stack);
      return {
        queue: scopedStore(new PgAgentErasureQueue(), { intend: 0, enqueue: 0, settle: 0 }),
        now: dbNow,
      };
    });

    describeErasureBackfillStoreContract('postgres', async () => new PgErasureBackfillStore() as never);

    describePrincipalErasureJobsContract('postgres', async () => {
      await resetHarnessPg(stack);
      return {
        jobs: boundStore(new PgPrincipalErasureJobs()),
        transact: (orgId, work) => transactAs(work, orgId),
      };
    });

    describe('PostgreSQL session erasure job invariants', () => {
      beforeEach(() => resetHarnessPg(stack));

      it('racing deletes of one session: exactly one creates the job', async () => {
        const orgId = hexOrg();
        const jobs = boundStore(new PgErasureJobStore());
        const created = await Promise.all(Array.from({ length: 8 }, () => jobs.enqueue(request(orgId))));
        expect(created.filter(Boolean)).to.have.length(1);
        expect(await visibleRows(orgId, 'session_erasures')).to.equal(1);
      });

      it("an enqueue in the delete's unit exists if and only if that unit commits", async () => {
        const orgId = hexOrg();
        const jobs = boundStore(new PgErasureJobStore());
        await transactAs((tx) => jobs.enqueue(request(orgId, 'kept'), tx), orgId);
        const failure = await transactAs(async (tx) => {
          await jobs.enqueue(request(orgId, 'aborted'), tx);
          throw new Error('abort');
        }, orgId).catch((error: Error) => error);
        expect((failure as Error).message).to.equal('abort');
        expect([...(await jobs.findMany(orgId, ['kept', 'aborted'])).keys()]).to.deep.equal(['kept']);
      });

      it('an advance in a unit that aborts leaves the job where it was, still held by its claimer', async () => {
        const orgId = hexOrg();
        const jobs = boundStore(new PgErasureJobStore());
        await jobs.enqueue(request(orgId));
        const held = (await jobs.claimDue('a', 60_000))!;
        await transactAs(async (tx) => {
          expect(await jobs.advance(held, { runs: 1 }, 'shred_workspace_key', 60_000, tx)).to.equal(true);
          throw new Error('abort');
        }, orgId).catch(() => undefined);
        expect(await jobs.find(orgId, 'ses-1')).to.deep.include({ step: 'cancel_runs', counts: {}, attempt: 1 });
        expect(await jobs.advance(held, { runs: 1 }, 'shred_workspace_key', 60_000)).to.equal(true);
      });

      it('two sweepers racing over many orgs never claim one job twice, and claim every due job', async () => {
        const jobs = boundStore(new PgErasureJobStore());
        const orgs = [hexOrg(), hexOrg(), hexOrg()];
        for (const orgId of orgs) for (let i = 0; i < 4; i += 1) await jobs.enqueue(request(orgId, `s-${String(i)}`));
        const claimed = await Promise.all(Array.from({ length: 16 }, (_, i) => jobs.claimDue(`h-${String(i)}`, 60_000)));
        const keys = claimed.filter((job) => job !== null).map((job) => `${job.orgId}:${job.sessionId}`);
        expect(new Set(keys).size).to.equal(12);
        expect(keys).to.have.length(12);
      });

      it("another org sees none of this org's jobs, and a unit bound to it cannot read or write them", async () => {
        const mine = hexOrg();
        const theirs = hexOrg();
        const jobs = boundStore(new PgErasureJobStore());
        await jobs.enqueue(request(mine));
        expect(await visibleRows(theirs, 'session_erasures')).to.equal(0);
        expect(await visibleRows(mine, 'session_erasures')).to.equal(1);
        const read = await inOrg(theirs, () => jobs.find(mine, 'ses-1').catch((error: Error) => error));
        expect(read).to.be.instanceOf(Error);
        const write = await transactAs((tx) => jobs.enqueue(request(mine, 'other'), tx), theirs).catch(
          (error: Error) => error,
        );
        expect(write).to.be.instanceOf(Error);
        expect((await jobs.findMany(mine, ['other'])).size).to.equal(0);
      });
    });

    describe('PostgreSQL agent erasure invariants', () => {
      beforeEach(() => resetHarnessPg(stack));

      it('an intent and a confirmation racing for one agent leave one confirmed entry', async () => {
        const orgId = hexOrg();
        const queue = boundStore(new PgAgentErasureQueue());
        await Promise.all([
          ...Array.from({ length: 4 }, () => queue.intend(orgId, 'agent-x', 60_000)),
          ...Array.from({ length: 4 }, () => queue.enqueue(orgId, 'agent-x', 'E')),
        ]);
        const due = (await queue.due(Number.MAX_SAFE_INTEGER, 10)).filter((e) => e.orgId === orgId);
        expect(due).to.have.length(1);
        expect(due[0]).to.include({ confirmed: true, agentKey: 'agent-x' });
        expect(await visibleRows(hexOrg(), 'agent_erasures')).to.equal(0);
      });

      it('withdraw takes only the intent it read: one requested again since stays', async () => {
        const orgId = hexOrg();
        let now = 1_000;
        const queue = boundStore(new PgAgentErasureQueue(fixedPgTime(() => now)));
        await queue.intend(orgId, 'agent-x', 10);
        const [first] = await queue.due(Number.MAX_SAFE_INTEGER, 10);
        now = 2_000;
        await queue.intend(orgId, 'agent-x', 10);
        expect(await queue.withdraw(first!)).to.equal(false);
        const [again] = await queue.due(Number.MAX_SAFE_INTEGER, 10);
        expect(again).to.include({ requestedAt: 2_000, confirmed: false });
        expect(await queue.withdraw(again!)).to.equal(true);
        expect(await queue.due(Number.MAX_SAFE_INTEGER, 10)).to.deep.equal([]);
      });
    });

    describe('PostgreSQL principal erasure invariants', () => {
      beforeEach(() => resetHarnessPg(stack));

      it("a user's racing deletions lose none of their transfers", async () => {
        const orgId = hexOrg();
        const userId = hexOrg();
        const jobs = boundStore(new PgPrincipalErasureJobs());
        await Promise.all(
          Array.from({ length: 6 }, (_, i) =>
            transactAs(
              (tx) =>
                jobs.requestUser(
                  { orgId, userId, by: 'p', requestedAt: 1, transfers: [{ sessionId: `s-${String(i)}`, to: 'a' }] },
                  tx,
                ),
              orgId,
            ),
          ),
        );
        const [job] = await jobs.due(Number.MAX_SAFE_INTEGER, 10);
        expect(job!.transfers.map((t) => t.sessionId).sort()).to.deep.equal(
          Array.from({ length: 6 }, (_, i) => `s-${String(i)}`),
        );
      });

      it('racing writers of the same step: exactly one moves the job on', async () => {
        const orgId = hexOrg();
        const jobs = boundStore(new PgPrincipalErasureJobs());
        await transactAs(
          (tx) => jobs.requestOrg({ orgId, by: 'p', requestedAt: 1, auditRetentionDays: 1 }, tx),
          orgId,
        );
        const job = (await jobs.findOrg(orgId))!;
        const wrote = await Promise.all(
          Array.from({ length: 6 }, (_, i) =>
            jobs.writeProgress({ ...job, step: 'end_sandboxes', counts: { writer: i } }, 'cancel_runs'),
          ),
        );
        expect(wrote.filter(Boolean)).to.have.length(1);
      });
    });

    describe('PostgreSQL backfill invariants', () => {
      beforeEach(() => resetHarnessPg(stack));

      const plan = (digest: string, orgId: string) => ({
        digest,
        orgFilter: orgId,
        operator: 'op',
        createdAt: 1,
        state: 'ready' as const,
        orgs: [{ orgId, sessions: 1, batches: 1, counts: zeroBackfillCounts() }],
        items: 1,
        cursor: { ...INITIAL_BACKFILL_CURSOR },
        staleReason: null,
      });

      it('racing executors move the cursor from one place: exactly one wins', async () => {
        const store = new PgErasureBackfillStore();
        await store.createPlan(plan('sha256:race', hexOrg()));
        const to = { org: 0, batch: 0, phase: 'intended' as const };
        const won = await Promise.all(
          Array.from({ length: 8 }, () => store.moveCursor('sha256:race', INITIAL_BACKFILL_CURSOR, to)),
        );
        expect(won.filter(Boolean)).to.have.length(1);
      });

      it("the enqueue unit's cursor move, item states and marker all roll back together", async () => {
        const orgId = hexOrg();
        const store = new PgErasureBackfillStore();
        await store.createPlan(plan('sha256:abort', orgId));
        await store.putItems([
          {
            digest: 'sha256:abort',
            orgId,
            sessionId: 's1',
            batch: 0,
            fingerprint: 'f',
            by: null,
            updatedAt: 1,
            counts: zeroBackfillCounts(),
            state: 'planned',
          },
        ]);
        const enqueued = { org: 0, batch: 0, phase: 'enqueued' as const };
        await transactAs(async (tx) => {
          expect(await store.moveCursor('sha256:abort', INITIAL_BACKFILL_CURSOR, enqueued, tx)).to.equal(true);
          await store.setItemStates('sha256:abort', [{ sessionId: 's1', state: 'enqueued' }], tx);
          await store.putMarker(
            { orgId, digest: 'sha256:abort', operator: 'op', completedAt: 1, erased: 1, withdrawn: 0, skipped: 0 },
            tx,
          );
          throw new Error('abort');
        }, orgId).catch(() => undefined);
        expect((await store.plan('sha256:abort'))?.cursor).to.deep.equal(INITIAL_BACKFILL_CURSOR);
        expect((await store.items('sha256:abort', null, 10))[0]?.state).to.equal('planned');
        expect(await store.marker(orgId)).to.equal(null);
        expect(await visibleRows(hexOrg(), 'erasure_backfill_items')).to.equal(0);
      });
    });
  });
}
