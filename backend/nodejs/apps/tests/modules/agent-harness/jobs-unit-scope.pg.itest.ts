/**
 * Which rows a jobs-wrapper unit reaches on PostgreSQL. The jobs role's row policy admits every organisation, so
 * `jobsUnit` called inside an organisation's context runs as that organisation (a statement missing its `org_id`
 * predicate then still reads only the caller's rows), outside any context it is the jobs role, and the reviewed
 * deployment-wide reads (`jobsUnitAcrossOrgs`) reach every organisation wherever they are called from.
 *
 *   PG_TEST_BINS=… PG_TEST_PGBOUNCER=… PG_TEST_MIGRATE_PYTHON=… npm run test:pg-harness
 */
import 'reflect-metadata';
import { expect } from 'chai';
import {
  pendingAuditOrgsPg,
  unlinkedAuditPg,
} from '../../../src/libs/repositories/agent-harness/audit-linking/pg';
import {
  jobsUnit,
  jobsUnitAcrossOrgs,
  type PgKernelTx,
} from '../../../src/libs/repositories/agent-harness/pg-documents/pg';
import {
  PgStack,
  hexOrg,
  inOrg,
  pgBins,
  pgSuite,
  resetHarnessPg,
  startHarnessPg,
} from './support/harness-pg';

// No org_id predicate on purpose: what the unit's role and organisation admit is what comes back.
const orgsSeen = async (k: PgKernelTx): Promise<string[]> =>
  (
    await k.query<{ org_id: string }>(
      `/* pipeshub:harness.test_pending_orgs */ SELECT DISTINCT org_id FROM harness.audit_pending ORDER BY org_id`,
    )
  ).rows.map((r) => r.org_id);

for (const bin of pgBins()) {
  pgSuite(
    `jobs units and the request's organisation on PostgreSQL (${bin})`,
    function () {
      this.timeout(120_000);
      let stack: PgStack;
      const [a, b] = [hexOrg(), hexOrg()].sort();
      before(async () => {
        stack = await startHarnessPg(bin, 'harness_jobs_scope');
        await resetHarnessPg(stack);
        await stack.admin(async (c) => {
          for (const org of [a, b]) {
            await c.query(
              `INSERT INTO harness.audit_pending (org_id, id, stream_id, journal_seq, ts, doc, region)
             VALUES ($1, 'p1', 's1', 1, '2026-11-28T00:00:00Z', '{}', 'local')`,
              [org],
            );
          }
        });
      });
      after(async () => {
        await stack?.stop();
      });

      it("inside an organisation's context a jobs unit reads that organisation's rows only", async () => {
        expect(
          await inOrg(a, () => jobsUnit('test', { write: false }, orgsSeen)),
        ).to.deep.equal([a]);
        expect(
          await inOrg(b, () => jobsUnit('test', { write: false }, orgsSeen)),
        ).to.deep.equal([b]);
      });

      it('outside any context a jobs unit is the jobs role and reaches every organisation', async () => {
        expect(
          await jobsUnit('test', { write: false }, orgsSeen),
        ).to.deep.equal([a, b]);
      });

      it('the reviewed cross-organisation reads reach every organisation, inside a request too', async () => {
        expect(
          await inOrg(a, () =>
            jobsUnitAcrossOrgs('test', { write: false }, orgsSeen),
          ),
        ).to.deep.equal([a, b]);
        expect(await inOrg(a, () => pendingAuditOrgsPg())).to.have.members([
          a,
          b,
        ]);
        expect(
          (await inOrg(b, () => unlinkedAuditPg())).map((u) => u.orgId),
        ).to.deep.equal([a, b]);
      });
    },
  );
}
