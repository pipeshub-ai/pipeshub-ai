/**
 * P7.3 governed memory on PostgreSQL (Alembic head, row-level security, PgBouncer in transaction mode): the memory
 * store contract (MS-1..MS-6), governed memory through admission, claim, the run's end and erasure with the PostgreSQL
 * store under the in-memory collaborators, and the PostgreSQL cases: a racing proposal keeps one memory and answers the
 * first, a guarded status change moves once, the sweep reads every org's unsettled runs, another org sees nothing.
 *
 *   PG_TEST_BINS=… PG_TEST_PGBOUNCER=… PG_TEST_MIGRATE_PYTHON=… npm run test:pg-harness
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { PgMemoryStore } from '../../../src/libs/repositories/agent-harness/memory/pg';
import { inMemoryCollaborators } from './support/in-memory-collaborators';
import { describeMemoryAdmissionContract } from './support/memory-admission-contract';
import {
  describeMemoryStoreContract,
  memoryFixture,
} from './support/memory-store-contract';
import {
  PgStack,
  hexOrg,
  inOrg,
  pgBins,
  pgSuite,
  resetHarnessPg,
  scopedStore,
  startHarnessPg,
} from './support/harness-pg';

const ORG_ARGS = {
  get: 0,
  byIds: 0,
  list: 0,
  update: 0,
  delete: 0,
  eraseSession: 0,
  eraseUser: 0,
  eraseOrg: 0,
  eraseAgent: 0,
};

for (const bin of pgBins()) {
  pgSuite(
    `agent harness governed memory on PostgreSQL (${bin}) behind PgBouncer`,
    function () {
      this.timeout(120_000);
      let stack: PgStack;
      before(async () => {
        stack = await startHarnessPg(bin, 'harness_memory');
      });
      after(async () => {
        await stack?.stop();
      });

      const fresh = async () => {
        await resetHarnessPg(stack);
        return scopedStore(new PgMemoryStore(), ORG_ARGS);
      };

      describeMemoryStoreContract('postgres', fresh);
      describeMemoryAdmissionContract(
        'postgres memory, in-memory collaborators',
        inMemoryCollaborators,
        fresh,
      );

      describe('PostgreSQL memory invariants', () => {
        beforeEach(() => resetHarnessPg(stack));

        it('racing proposals of one id keep one memory, and every answer is the first one stored', async () => {
          const orgId = hexOrg();
          const store = scopedStore(new PgMemoryStore(), ORG_ARGS);
          const base = memoryFixture({ orgId, status: 'pending', label: null });
          const answers = await Promise.all(
            Array.from({ length: 8 }, (_, i) =>
              store.propose({ ...base, content: `take ${String(i)}` }),
            ),
          );
          expect(answers.filter((answer) => answer.created)).to.have.length(1);
          const first = answers.find((answer) => answer.created)!.memory;
          for (const answer of answers)
            expect(answer.memory).to.deep.equal(first);
          const rows = await stack.admin((c) =>
            c.query(`SELECT count(*)::int AS n FROM harness.memories`),
          );
          expect(rows.rows[0].n).to.equal(1);
        });

        it('a guarded status change moves once under racing reviewers', async () => {
          const orgId = hexOrg();
          const store = scopedStore(new PgMemoryStore(), ORG_ARGS);
          const entry = memoryFixture({ orgId, status: 'proposed' });
          await store.propose(entry);
          const moved = await Promise.all(
            Array.from({ length: 6 }, (_, i) =>
              store.update(
                orgId,
                entry.memoryId,
                {
                  status: 'accepted',
                  reviewedBy: `r${String(i)}`,
                  updatedAt: 9,
                },
                'proposed',
              ),
            ),
          );
          expect(moved.filter((m) => m !== null)).to.have.length(1);
        });

        it("the sweep reads every org's unsettled runs as the jobs role, oldest first", async () => {
          const a = hexOrg();
          const b = hexOrg();
          const store = scopedStore(new PgMemoryStore(), ORG_ARGS);
          await store.propose(
            memoryFixture({
              orgId: a,
              runId: 'run-a',
              status: 'pending',
              createdAt: 20,
            }),
          );
          await store.propose(
            memoryFixture({
              orgId: b,
              runId: 'run-b',
              status: 'pending',
              createdAt: 10,
            }),
          );
          await store.propose(
            memoryFixture({
              orgId: b,
              runId: 'run-b',
              status: 'pending',
              createdAt: 30,
            }),
          );
          await store.propose(
            memoryFixture({
              orgId: a,
              runId: 'run-c',
              status: 'accepted',
              createdAt: 5,
            }),
          );
          expect(await new PgMemoryStore().pendingRuns(100, 10)).to.deep.equal([
            { orgId: b, runId: 'run-b' },
            { orgId: a, runId: 'run-a' },
          ]);
          expect(await new PgMemoryStore().pendingRuns(15, 10)).to.deep.equal([
            { orgId: b, runId: 'run-b' },
          ]);
        });

        it('another organisation bound to the unit reads and erases nothing of this one', async () => {
          const mine = hexOrg();
          const theirs = hexOrg();
          const raw = new PgMemoryStore();
          const entry = memoryFixture({ orgId: mine });
          await inOrg(mine, () => raw.propose(entry));
          expect(
            await inOrg(theirs, () =>
              raw.get(mine, entry.memoryId).catch((e: Error) => e),
            ),
          ).to.be.instanceOf(Error);
          expect(
            await inOrg(theirs, () =>
              raw.eraseOrg(mine).catch((e: Error) => e),
            ),
          ).to.be.instanceOf(Error);
          expect(
            await inOrg(theirs, () => raw.list(theirs, { limit: 10 })),
          ).to.deep.equal([]);
          expect(
            await inOrg(mine, () => raw.get(mine, entry.memoryId)),
          ).to.deep.equal(entry);
        });

        it('an erasure takes the superseded chain in one unit', async () => {
          const orgId = hexOrg();
          const store = scopedStore(new PgMemoryStore(), ORG_ARGS);
          const latest = memoryFixture({ orgId, sessionId: 'ses-x' });
          const middle = memoryFixture({
            orgId,
            sessionId: 'ses-y',
            supersededBy: latest.memoryId,
          });
          const oldest = memoryFixture({
            orgId,
            sessionId: 'ses-z',
            supersededBy: middle.memoryId,
          });
          for (const entry of [latest, middle, oldest])
            await store.propose(entry);
          expect(await store.eraseSession(orgId, 'ses-x')).to.equal(3);
          expect(await store.list(orgId, { limit: 10 })).to.deep.equal([]);
        });
      });
    },
  );
}
