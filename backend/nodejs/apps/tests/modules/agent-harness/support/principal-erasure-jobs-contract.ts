/**
 * What every `PrincipalErasureJobStore` must do (CLEANUP-G1, G2): one job per deleted org or user, a user's repeated
 * deletion adding to the waiting job, progress guarded by the step the writer read, and the org steps' own state kept
 * on the job. `MongoPrincipalErasureJobs` (`principal-erasure.mongo.itest.ts`) and `PgPrincipalErasureJobs`
 * (`erasure-stores.pg.itest.ts`). `transact(orgId, work)` runs `work` in the backend's durable transaction as `orgId`.
 */
import { expect } from 'chai';
import { randomBytes } from 'crypto';
import {
  ORG_ERASURE_STEPS,
  PrincipalErasureJob,
  USER_ERASURE_STEPS,
} from '../../../../src/modules/agent-harness/domain/principal-erasure';
import type { PrincipalErasureJobStore } from '../../../../src/modules/agent-harness/ports/principal-erasure';

const hex = (): string => randomBytes(12).toString('hex');

export interface PrincipalErasureJobsFixture<Tx> {
  jobs: PrincipalErasureJobStore<Tx>;
  transact: <T>(orgId: string, work: (tx: Tx) => Promise<T>) => Promise<T>;
}

export function describePrincipalErasureJobsContract<Tx>(
  name: string,
  create: () => Promise<PrincipalErasureJobsFixture<Tx>>,
): void {
  describe(`PrincipalErasureJobStore contract: ${name}`, () => {
    const org = (orgId: string, requestedAt = 1_000) => ({
      orgId,
      by: 'p_admin',
      requestedAt,
      auditRetentionDays: 30,
    });

    it('PJ-1: an org deletion is one job at the first step, due at once; asking again keeps the first', async () => {
      const { jobs, transact } = await create();
      const orgId = hex();
      expect(await jobs.findOrg(orgId)).to.equal(null);
      expect(await transact(orgId, (tx) => jobs.requestOrg(org(orgId), tx))).to.equal(true);
      expect(await transact(orgId, (tx) => jobs.requestOrg({ ...org(orgId, 5_000), by: 'p_other' }, tx))).to.equal(
        false,
      );
      expect(await jobs.findOrg(orgId)).to.deep.equal({
        kind: 'org',
        orgId,
        userId: null,
        by: 'p_admin',
        step: ORG_ERASURE_STEPS[0],
        requestedAt: 1_000,
        auditRetentionDays: 30,
        auditRemoveAfter: null,
        cursor: null,
        transfers: [],
        journaled: 0,
        counts: {},
        extension: null,
        failures: 0,
        nextAttemptAt: 1_000,
        lastErrorType: null,
      });
    });

    it("PJ-2: a user deleted again adds the new transfers to the waiting job; the org's own job is apart", async () => {
      const { jobs, transact } = await create();
      const orgId = hex();
      const user = { orgId, userId: hex(), by: 'p_admin', requestedAt: 2_000 };
      await transact(orgId, (tx) => jobs.requestUser({ ...user, transfers: [{ sessionId: 's1', to: 'u-a' }] }, tx));
      await transact(orgId, (tx) =>
        jobs.requestUser({ ...user, requestedAt: 9_000, transfers: [{ sessionId: 's2', to: 'u-b' }] }, tx),
      );
      const due = await jobs.due(Number.MAX_SAFE_INTEGER, 100);
      const mine = due.filter((job) => job.orgId === orgId);
      expect(mine).to.have.length(1);
      expect(mine[0]).to.deep.include({
        kind: 'user',
        userId: user.userId,
        step: USER_ERASURE_STEPS[0],
        requestedAt: 2_000,
        transfers: [
          { sessionId: 's1', to: 'u-a' },
          { sessionId: 's2', to: 'u-b' },
        ],
      });
      expect(await jobs.findOrg(orgId)).to.equal(null);
    });

    it('PJ-3: due answers what is due, soonest first, at most the limit', async () => {
      const { jobs, transact } = await create();
      const [a, b, c] = [hex(), hex(), hex()];
      await transact(a, (tx) => jobs.requestOrg(org(a, 3_000), tx));
      await transact(b, (tx) => jobs.requestOrg(org(b, 1_000), tx));
      await transact(c, (tx) => jobs.requestOrg(org(c, 50_000), tx));
      const mine = (jobs_: PrincipalErasureJob[]) => jobs_.map((j) => j.orgId).filter((o) => [a, b, c].includes(o));
      expect(mine(await jobs.due(10_000, 100))).to.deep.equal([b, a]);
      expect(mine(await jobs.due(60_000, 100))).to.deep.equal([b, a, c]);
      expect(await jobs.due(60_000, 1)).to.have.length(1);
    });

    it('PJ-4: progress is written only from the step the writer read; a stale writer changes nothing', async () => {
      const { jobs, transact } = await create();
      const orgId = hex();
      await transact(orgId, (tx) => jobs.requestOrg(org(orgId), tx));
      const job = (await jobs.findOrg(orgId))!;
      const next = ORG_ERASURE_STEPS[1]!;
      const moved = { ...job, step: next, counts: { runs: 2 }, failures: 1, lastErrorType: 'E', nextAttemptAt: 7_000 };
      expect(await jobs.writeProgress(moved, job.step)).to.equal(true);
      expect(await jobs.writeProgress({ ...moved, counts: { runs: 99 } }, job.step)).to.equal(false);
      expect(await jobs.findOrg(orgId)).to.deep.include({
        step: next,
        counts: { runs: 2 },
        failures: 1,
        lastErrorType: 'E',
        nextAttemptAt: 7_000,
        by: 'p_admin',
      });
      expect(await transact(orgId, (tx) => jobs.writeProgress({ ...moved, cursor: 'c-1' }, next, tx))).to.equal(true);
      expect((await jobs.findOrg(orgId))?.cursor).to.equal('c-1');
    });

    it("PJ-5: the org steps' own state is kept on the job and written with its progress; remove takes the job away", async () => {
      const { jobs, transact } = await create();
      const orgId = hex();
      await transact(orgId, (tx) => jobs.requestOrg({ ...org(orgId), extension: { until: 11_000 } }, tx));
      const job = (await jobs.findOrg(orgId))!;
      expect(job.extension).to.deep.equal({ until: 11_000 });
      const next = ORG_ERASURE_STEPS[1]!;
      expect(await jobs.writeProgress({ ...job, step: next, extension: { until: 11_000, sent: [7] } }, job.step)).to.equal(
        true,
      );
      expect((await jobs.findOrg(orgId))?.extension).to.deep.equal({ until: 11_000, sent: [7] });
      await jobs.remove((await jobs.findOrg(orgId))!);
      expect(await jobs.findOrg(orgId)).to.equal(null);
    });

    it('PJ-6: a request whose transaction aborts leaves no job', async () => {
      const { jobs, transact } = await create();
      const orgId = hex();
      const failure = await transact(orgId, async (tx) => {
        await jobs.requestOrg(org(orgId), tx);
        throw new Error('abort');
      }).catch((error: Error) => error);
      expect((failure as Error).message).to.equal('abort');
      expect(await jobs.findOrg(orgId)).to.equal(null);
    });
  });
}
