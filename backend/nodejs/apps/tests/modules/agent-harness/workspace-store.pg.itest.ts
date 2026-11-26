/**
 * P3.2 / P6.4 workspaces on PostgreSQL (Alembic head, row-level security, PgBouncer in transaction mode): the stores
 * contract (NW-9, with the concurrent commits of NW-10 and the racing writers of NF-2) and the head CAS the journal
 * commit shares (it runs in the caller's unit: an aborted commit moves no head, a stale revision never overwrites a
 * newer head), and another organisation sees nothing.
 *
 *   PG_TEST_BINS=… PG_TEST_PGBOUNCER=… PG_TEST_MIGRATE_PYTHON=… npm run test:pg-harness
 */
import 'reflect-metadata';
import { expect } from 'chai';
import {
  PgWorkspaceKeyStore,
  PgWorkspaceRepository,
  PgWorkspaceWriterStore,
} from '../../../src/libs/repositories/agent-harness/workspace/pg';
import { moveWorkspaceHead } from '../../../src/libs/repositories/agent-harness/workspace-head-cas/pg';
import {
  describeWorkspaceStoresContract,
  storedManifest,
} from './support/workspace-repository-contract';
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

for (const bin of pgBins()) {
  pgSuite(
    `agent harness workspaces on PostgreSQL (${bin}) behind PgBouncer`,
    function () {
      this.timeout(120_000);
      let stack: PgStack;
      before(async () => {
        stack = await startHarnessPg(bin, 'harness_workspaces');
      });
      after(async () => {
        await stack?.stop();
      });

      const ORG_ARGS = {
        head: 0,
        manifest: 0,
        revisions: 0,
        setMergeBase: 0,
        release: 0,
        get: 0,
        destroy: 0,
      };

      describeWorkspaceStoresContract('postgres', async () => {
        await resetHarnessPg(stack);
        return {
          repository: scopedStore(new PgWorkspaceRepository(), ORG_ARGS),
          keys: scopedStore(new PgWorkspaceKeyStore(), ORG_ARGS),
          writers: scopedStore(new PgWorkspaceWriterStore(), ORG_ARGS),
        };
      });

      describe('PostgreSQL workspace invariants', () => {
        beforeEach(() => resetHarnessPg(stack));
        const heads = async (orgId: string, workspaceId: string) =>
          (
            await stack.admin((c) =>
              c.query(
                `SELECT revision FROM harness.workspace_heads WHERE org_id = $1 AND workspace_id = $2`,
                [orgId, workspaceId],
              ),
            )
          ).rows.map((row: { revision: string }) => Number(row.revision));

        it("the head CAS runs in the caller's unit: an aborted commit moves nothing", async () => {
          const orgId = hexOrg();
          await transactAs(
            (tx) =>
              moveWorkspaceHead(
                {
                  orgId,
                  workspaceId: 'ws:a',
                  expectedRevision: 0,
                  revision: 1,
                  runId: 'r1',
                },
                1,
                tx,
              ),
            orgId,
          );
          const failure = await transactAs(async (tx) => {
            expect(
              await moveWorkspaceHead(
                {
                  orgId,
                  workspaceId: 'ws:a',
                  expectedRevision: 1,
                  revision: 2,
                  runId: 'r2',
                },
                2,
                tx,
              ),
            ).to.equal(true);
            throw new Error('abort');
          }, orgId).catch((e: Error) => e);
          expect((failure as Error).message).to.equal('abort');
          expect(await heads(orgId, 'ws:a')).to.deep.equal([1]);
        });

        it('a stale revision never overwrites a newer head: of racing movers from one revision exactly one moves it', async () => {
          const orgId = hexOrg();
          await transactAs(
            (tx) =>
              moveWorkspaceHead(
                {
                  orgId,
                  workspaceId: 'ws:b',
                  expectedRevision: 0,
                  revision: 1,
                  runId: 'r0',
                },
                1,
                tx,
              ),
            orgId,
          );
          const moved = await Promise.all(
            Array.from({ length: 8 }, (_, i) =>
              transactAs(
                (tx) =>
                  moveWorkspaceHead(
                    {
                      orgId,
                      workspaceId: 'ws:b',
                      expectedRevision: 1,
                      revision: 2 + i,
                      runId: `r${String(i)}`,
                    },
                    5,
                    tx,
                  ),
                orgId,
              ),
            ),
          );
          expect(moved.filter(Boolean)).to.have.length(1);
          const [revision] = await heads(orgId, 'ws:b');
          expect(revision).to.equal(2 + moved.indexOf(true));
          expect(
            await transactAs(
              (tx) =>
                moveWorkspaceHead(
                  {
                    orgId,
                    workspaceId: 'ws:b',
                    expectedRevision: 1,
                    revision: 99,
                    runId: 'late',
                  },
                  9,
                  tx,
                ),
              orgId,
            ),
          ).to.equal(false);
          expect(await heads(orgId, 'ws:b')).to.deep.equal([revision]);
        });

        it('racing creators of one head: exactly one creates it', async () => {
          const orgId = hexOrg();
          const created = await Promise.all(
            Array.from({ length: 6 }, (_, i) =>
              transactAs(
                (tx) =>
                  moveWorkspaceHead(
                    {
                      orgId,
                      workspaceId: 'ws:c',
                      expectedRevision: 0,
                      revision: 1,
                      runId: `r${String(i)}`,
                    },
                    1,
                    tx,
                  ),
                orgId,
              ),
            ),
          );
          expect(created.filter(Boolean)).to.have.length(1);
        });

        it('a conflicting append leaves no manifest and no head move behind', async () => {
          const orgId = hexOrg();
          const repository = new PgWorkspaceRepository();
          await inOrg(orgId, () =>
            repository.append({
              manifest: storedManifest('ws:d', 1, orgId),
              expectedRevision: 0,
            }),
          );
          const refused = await inOrg(orgId, () =>
            repository
              .append({
                manifest: storedManifest('ws:d', 2, orgId),
                expectedRevision: 1,
                mergedFork: { workspaceId: 'ws:d', mergeBase: 1 },
              })
              .catch((e: Error) => e),
          );
          expect(refused).to.be.instanceOf(Error);
          expect(await heads(orgId, 'ws:d')).to.deep.equal([1]);
          const manifests = await stack.admin((c) =>
            c.query(
              `SELECT revision FROM harness.workspace_manifests WHERE org_id = $1`,
              [orgId],
            ),
          );
          expect(
            manifests.rows.map((row: { revision: string }) =>
              Number(row.revision),
            ),
          ).to.deep.equal([1]);
        });

        it('another organisation bound to the unit reads, leases and shreds nothing of this one', async () => {
          const mine = hexOrg();
          const theirs = hexOrg();
          const repository = new PgWorkspaceRepository();
          const keys = new PgWorkspaceKeyStore();
          await inOrg(mine, () =>
            repository.append({
              manifest: storedManifest('ws:e', 1, mine),
              expectedRevision: 0,
            }),
          );
          await inOrg(mine, () =>
            keys.insert({
              orgId: mine,
              sessionId: 's1',
              keyId: 'k',
              wrapped: storedManifest('ws:e', 1).files,
              createdAt: 1,
              destroyedAt: null,
            }),
          );
          expect(
            await inOrg(theirs, () =>
              repository.head(mine, 'ws:e').catch((e: Error) => e),
            ),
          ).to.be.instanceOf(Error);
          expect(
            await inOrg(theirs, () =>
              keys.destroy(mine, 's1', 5).catch((e: Error) => e),
            ),
          ).to.be.instanceOf(Error);
          expect(
            await inOrg(theirs, () => repository.head(theirs, 'ws:e')),
          ).to.equal(null);
          expect(
            (await inOrg(mine, () => keys.get(mine, 's1')))?.wrapped,
          ).not.to.equal(null);
        });

        it('racing shreds of one key: exactly one drops it, the tombstone stays', async () => {
          const orgId = hexOrg();
          const keys = scopedStore(new PgWorkspaceKeyStore(), ORG_ARGS);
          await keys.insert({
            orgId,
            sessionId: 's1',
            keyId: 'k',
            wrapped: storedManifest('ws:f', 1).files,
            createdAt: 1,
            destroyedAt: null,
          });
          const dropped = await Promise.all(
            Array.from({ length: 6 }, (_, i) =>
              keys.destroy(orgId, 's1', 10 + i),
            ),
          );
          expect(dropped.filter(Boolean)).to.have.length(1);
          expect(
            await keys.insert({
              orgId,
              sessionId: 's1',
              keyId: 'k2',
              wrapped: null,
              createdAt: 2,
              destroyedAt: null,
            }),
          ).to.equal(false);
          const tombstones = await Promise.all(
            Array.from({ length: 4 }, () => keys.destroy(orgId, 'never', 3)),
          );
          expect(tombstones).to.deep.equal([false, false, false, false]);
        });
      });
    },
  );
}
