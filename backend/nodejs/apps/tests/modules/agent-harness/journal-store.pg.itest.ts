/**
 * The JournalStore contract (J-1..J-4), the payload store, the leader lease and the dispatch outbox on PostgreSQL
 * (Alembic head, row-level security, PgBouncer in transaction mode). The invariants the Mongo suite proves on a
 * replica set are proved here on the same terms: gap-free seq in commit order under concurrent writers, nothing left
 * by an aborted unit, one lease holder, a dispatch that exists if and only if its unit committed, and no row of one
 * organisation visible to another.
 *
 *   PG_TEST_BINS=… PG_TEST_PGBOUNCER=… PG_TEST_MIGRATE_PYTHON=… npm run test:pg-harness
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { PgJournalStore } from '../../../src/libs/repositories/agent-harness/journal/pg';
import { PgPayloadStore } from '../../../src/libs/repositories/agent-harness/payload-store/pg';
import { PgLeaderLease } from '../../../src/libs/repositories/agent-harness/leader-lease/pg';
import { PgDispatchOutbox } from '../../../src/libs/repositories/agent-harness/dispatch-outbox/pg';
import { fixedPgTime } from '../../../src/libs/repositories/agent-harness/time/pg';
import type { Tx } from '../../../src/libs/repositories/uow';
import { describeJournalStoreContract } from './support/journal-store-contract';
import { position, turnStarted } from './support/journal-fixtures';
import { testCipher } from './support/data-protection';
import { AuditedJournalStore } from '../../../src/modules/agent-harness/application/audited-journal-store';
import { PayloadReferencingJournalStore } from '../../../src/modules/agent-harness/application/payload-referencing-journal-store';
import { SealedJournalStore } from '../../../src/modules/agent-harness/application/sealed-journal-store';
import { SnapshotRecordingJournalStore } from '../../../src/modules/agent-harness/application/snapshot-recording-journal-store';
import { PgAuditChainStore, pgAuditLinksIdle } from '../../../src/libs/repositories/agent-harness/audit/pg';
import { PgSandboxSnapshotIndex } from '../../../src/libs/repositories/agent-harness/sandbox-snapshots/pg';
import {
  PgStack,
  hexOrg,
  inOrg,
  pgBins,
  pgSuite,
  resetHarnessPg,
  scopedStore,
  startHarnessPg,
  transactAs,
} from './support/harness-pg';

const READS = { readStream: 0, readRun: 0, lastSeq: 0, get: 0, put: 0 };

for (const bin of pgBins()) {
  pgSuite(`agent harness journal on PostgreSQL (${bin}) behind PgBouncer`, function () {
    this.timeout(120_000);
    let stack: PgStack;
    before(async () => {
      stack = await startHarnessPg(bin, 'harness_journal');
    });
    after(async () => {
      await stack?.stop();
    });

    const payloadId = (ref: string): string => ref.replace(/^pg:/, '');

    describeJournalStoreContract<Tx>('postgres', () => ({
      journal: scopedStore(new PgJournalStore(), READS),
      payloads: scopedStore(new PgPayloadStore(), READS),
      transact: transactAs,
      corruptPayload: async (orgId, ref) => {
        await stack.admin((c) =>
          c.query(`UPDATE harness.journal_payloads SET bytes = $3 WHERE org_id = $1 AND id = $2`, [
            orgId,
            payloadId(ref),
            Buffer.from('{"tampered":true}'),
          ]),
        );
      },
      deletePayload: async (orgId, ref) => {
        await stack.admin((c) =>
          c.query(`DELETE FROM harness.journal_payloads WHERE org_id = $1 AND id = $2`, [orgId, payloadId(ref)]),
        );
      },
      reset: () => resetHarnessPg(stack),
    }));

    describe('PostgreSQL journal invariants', () => {
      beforeEach(() => resetHarnessPg(stack));
      const journal = scopedStore(new PgJournalStore(), READS);
      const service = new JournalService(scopedStore(new PgPayloadStore(), READS), testCipher());

      it('J-1: concurrent units on one stream stay gap-free, each batch contiguous (seq order is commit order)', async () => {
        const orgId = hexOrg();
        await Promise.all(
          Array.from({ length: 8 }, async (_, writer) => {
            const prepared = await service.prepare({
              ...position({ orgId, runId: `run-${String(writer)}` }),
              writer: 'worker',
              events: [turnStarted(1), turnStarted(2), turnStarted(3)],
            });
            await transactAs((tx) => journal.append(prepared, tx), orgId);
          }),
        );
        const events = await journal.readStream(orgId, 'session-1', 0, 100);
        expect(events.map((event) => event.seq)).to.deep.equal(Array.from({ length: 24 }, (_, i) => i + 1));
        for (let index = 0; index < 24; index += 3) {
          expect(new Set(events.slice(index, index + 3).map((event) => event.runId)).size).to.equal(1);
        }
        expect(await journal.lastSeq(orgId, ['session-1'])).to.deep.equal({ 'session-1': 24 });
      });

      it('an aborted unit leaves no events and no counter advance', async () => {
        const orgId = hexOrg();
        const prepared = await service.prepare({ ...position({ orgId }), writer: 'worker', events: [turnStarted(1)] });
        let failure: unknown;
        try {
          await transactAs(async (tx) => {
            await journal.append(prepared, tx);
            throw new Error('abort');
          }, orgId);
        } catch (error) {
          failure = error;
        }
        expect((failure as Error).message).to.equal('abort');
        const left = await stack.admin((c) => c.query(`SELECT count(*)::int AS n FROM harness.session_events`));
        expect(left.rows[0].n).to.equal(0);
        expect(await journal.lastSeq(orgId, ['session-1'])).to.deep.equal({ 'session-1': 0 });
      });

      it('two appends to one stream in one unit continue the range at the first one\'s time', async () => {
        const orgId = hexOrg();
        const first = await service.prepare({ ...position({ orgId }), writer: 'worker', events: [turnStarted(1)] });
        const second = await service.prepare({ ...position({ orgId }), writer: 'worker', events: [turnStarted(2)] });
        const [a, b] = await transactAs(
          async (tx) => [await journal.append(first, tx), await journal.append(second, tx)],
          orgId,
        );
        expect([a.firstSeq, b.firstSeq, b.lastSeq]).to.deep.equal([1, 2, 2]);
        expect(b.ts).to.equal(a.ts);
      });

      it('org isolation: another organisation reads no event, counter or payload of this one', async () => {
        const mine = hexOrg();
        const theirs = hexOrg();
        const prepared = await service.prepare({ ...position({ orgId: mine }), writer: 'worker', events: [turnStarted(1)] });
        await transactAs((tx) => journal.append(prepared, tx), mine);
        const raw = new PgJournalStore();
        const seen = await inOrg(theirs, () => raw.readStream(mine, 'session-1', 0, 10).catch((e: Error) => e));
        expect(seen).to.be.instanceOf(Error); // the org of the call differs from the unit's: refused, never read
        const rows = await inOrg(theirs, () =>
          transactAs(async () => {
            const k = (await import('../../../src/libs/db')).activeUnit();
            const r = await k!.query<{ n: number }>(`/* pipeshub:harness.test_count */ SELECT count(*)::int AS n FROM harness.session_events`);
            return r.rows[0]!.n;
          }, theirs),
        );
        expect(rows).to.equal(0);
      });
    });

    describe('leader lease (deployment-wide)', () => {
      beforeEach(() => resetHarnessPg(stack));

      it('one holder at a time; a lapsed lease is taken over on database time; release frees it', async () => {
        let now = 1_000_000;
        const lease = new PgLeaderLease(fixedPgTime(() => now));
        expect(await lease.tryAcquire('sweeper', 'a', 10_000)).to.equal(true);
        expect(await lease.tryAcquire('sweeper', 'b', 10_000)).to.equal(false);
        expect(await lease.tryAcquire('sweeper', 'a', 10_000)).to.equal(true);
        now += 10_001;
        expect(await lease.tryAcquire('sweeper', 'b', 10_000)).to.equal(true);
        await lease.release('sweeper', 'a');
        expect(await lease.tryAcquire('sweeper', 'a', 10_000)).to.equal(false);
        await lease.release('sweeper', 'b');
        expect(await lease.tryAcquire('sweeper', 'a', 10_000)).to.equal(true);
      });

      it('racing candidates for a free lease: exactly one wins', async () => {
        const lease = new PgLeaderLease();
        const won = await Promise.all(
          Array.from({ length: 8 }, (_, i) => lease.tryAcquire('firer', `h${String(i)}`, 60_000)),
        );
        expect(won.filter(Boolean)).to.have.length(1);
      });
    });

    describe('dispatch outbox', () => {
      beforeEach(() => resetHarnessPg(stack));

      const record = (orgId: string, runId: string) => ({
        orgId,
        runId,
        kind: 'run_ready' as const,
        commandId: `cmd-${runId}`,
        createdAt: 1_700_000_000_000,
      });
      const outboxRows = () =>
        stack.admin(async (c) => (await c.query(`SELECT org_id, msg_key FROM platform.outbox ORDER BY id`)).rows);

      it('a dispatch exists if and only if its unit commits; the ready notice goes out after the commit', async () => {
        const orgId = hexOrg();
        const notified: string[][] = [];
        const outbox = new PgDispatchOutbox({ notify: (n) => Promise.resolve(void notified.push(n.map((x) => x.runId))) });
        await transactAs((tx) => outbox.enqueue([record(orgId, 'run-1')], tx), orgId);
        try {
          await transactAs(async (tx) => {
            await outbox.enqueue([record(orgId, 'run-2')], tx);
            throw new Error('abort');
          }, orgId);
        } catch {
          // the aborted unit's dispatch must not exist
        }
        expect(await outboxRows()).to.deep.equal([{ org_id: orgId, msg_key: 'run-1' }]);
        expect(notified).to.deep.equal([['run-1']]);
      });

      it('refuses a record of another organisation than the unit runs as', async () => {
        const orgId = hexOrg();
        const outbox = new PgDispatchOutbox();
        let failure: unknown;
        try {
          await transactAs((tx) => outbox.enqueue([record(hexOrg(), 'run-x')], tx), orgId);
        } catch (error) {
          failure = error;
        }
        expect(failure).to.be.instanceOf(Error);
        expect(await outboxRows()).to.deep.equal([]);
      });
    });
  });
}

for (const bin of pgBins()) {
  pgSuite(`agent harness journal behind its production decorators on PostgreSQL (${bin})`, function () {
    this.timeout(120_000);
    let stack: PgStack;
    before(async () => {
      stack = await startHarnessPg(bin, 'harness_journal_stack');
    });
    after(async () => {
      await pgAuditLinksIdle();
      await stack?.stop();
    });

    const payloadId = (ref: string): string => ref.replace(/^pg:/, '');

    describeJournalStoreContract<Tx>('production decorator stack on postgres (O-1c, O-2a, T5, OWD-12)', () => {
      const payloads = scopedStore(new PgPayloadStore(), READS);
      return {
        journal: scopedStore(
          new AuditedJournalStore(
            new SnapshotRecordingJournalStore(
              new PayloadReferencingJournalStore(new SealedJournalStore(new PgJournalStore(), testCipher()), payloads),
              new PgSandboxSnapshotIndex(),
              'default',
            ),
            new PgAuditChainStore(),
          ),
          READS,
        ),
        payloads,
        transact: transactAs,
        corruptPayload: async (orgId, ref) => {
          await stack.admin((c) =>
            c.query(`UPDATE harness.journal_payloads SET bytes = $3 WHERE org_id = $1 AND id = $2`, [
              orgId,
              payloadId(ref),
              Buffer.from('{"tampered":true}'),
            ]),
          );
        },
        deletePayload: async (orgId, ref) => {
          await stack.admin((c) =>
            c.query(`DELETE FROM harness.journal_payloads WHERE org_id = $1 AND id = $2`, [orgId, payloadId(ref)]),
          );
        },
        reset: async () => {
          await pgAuditLinksIdle();
          await resetHarnessPg(stack);
        },
      };
    });
  });
}
