/**
 * ADR-SEC-02 (WP-116) on PostgreSQL behind PgBouncer: a harness or control-plane store call with no org bound is
 * refused before any row is touched (there is no fallback to the org the call names, nor to the jobs role), and the
 * same call runs once its entry point binds the org. A sweep's row work, bound from its own cross-org read, runs.
 *
 *   PG_TEST_BINS=… PG_TEST_PGBOUNCER=… PG_TEST_MIGRATE_PYTHON=… npm run test:pg-harness
 */
import 'reflect-metadata';
import { expect } from 'chai';
import {
  forEnumeratedOrg,
  orgContextStorage,
} from '../../../src/libs/context/orgContext';
import { PgExecutionStore } from '../../../src/libs/repositories/agent-harness/execution/pg';
import { PgPolicyStore } from '../../../src/libs/repositories/control-plane/policy/pg';
import { PgDataKeyStore } from '../../../src/libs/repositories/agent-harness/data-protection/pg';
import { inDurableTransaction } from '../../../src/libs/repositories/agent-harness/durable-transaction';
import { UnboundOrgError } from '../../../src/libs/repositories/agent-harness/durable-transaction/bound-org';
import { pgActiveOrgIds } from '../../../src/libs/repositories/agent-harness/active-orgs/pg';
import {
  PgStack,
  hexOrg,
  inOrg,
  pgBins,
  pgSuite,
  resetHarnessPg,
  startHarnessPg,
} from './support/harness-pg';

const unbound = <T>(fn: () => T): T => orgContextStorage.exit(fn);

async function refused(work: () => Promise<unknown>): Promise<unknown> {
  try {
    await work();
  } catch (error) {
    return error;
  }
  throw new Error('the call was not refused');
}

for (const bin of pgBins()) {
  pgSuite(`ADR-SEC-02 org binding on PostgreSQL (${bin})`, function () {
    this.timeout(120_000);
    let stack: PgStack;
    before(async () => {
      stack = await startHarnessPg(bin, 'harness_org_binding');
    });
    after(async () => {
      await stack?.stop();
    });
    beforeEach(() => resetHarnessPg(stack));

    it('an execution read, a control-plane read and a data-key read with no org bound are refused, and touch no row', async () => {
      const org = hexOrg();
      const runs = new PgExecutionStore({} as never, {} as never);
      for (const call of [
        () => runs.get(org, 'run-1'),
        () => new PgPolicyStore().readMany(org, ['org']),
        () => new PgDataKeyStore().list(org),
      ]) {
        expect(await unbound(() => refused(call))).to.be.instanceOf(
          UnboundOrgError,
        );
      }
      await stack.admin(async (c) => {
        const { rows } = await c.query<{ n: string }>(
          `SELECT count(*)::text AS n FROM harness.data_keys`,
        );
        expect(rows[0]?.n).to.equal('0');
      });
    });

    it('the same calls run once the entry point binds the org', async () => {
      const org = hexOrg();
      const runs = new PgExecutionStore({} as never, {} as never);
      expect(await inOrg(org, () => runs.get(org, 'run-1'))).to.equal(null);
      const layers = await inOrg(org, () =>
        new PgPolicyStore().readMany(org, ['org']),
      );
      expect([...layers.keys()]).to.deep.equal(['org']);
      expect(
        await inOrg(org, () => new PgDataKeyStore().list(org)),
      ).to.deep.equal([]);
    });

    it('a durable transaction for an org is refused with none bound, before a unit opens', async () => {
      let ran = false;
      const error = await unbound(() =>
        refused(() =>
          inDurableTransaction(
            async () => {
              ran = true;
            },
            { orgId: hexOrg() },
          ),
        ),
      );
      expect(error).to.be.instanceOf(UnboundOrgError);
      expect(ran).to.equal(false);
    });

    it("a sweep's cross-org read runs as the jobs role, and its row work as each row's org", async () => {
      const [first, second] = [hexOrg(), hexOrg()];
      const keys = new PgDataKeyStore();
      const seen = await unbound(async () => {
        const read = await pgActiveOrgIds();
        const out: Array<[string, number]> = [];
        for (const org of [first, second, ...read]) {
          out.push([
            org,
            (await forEnumeratedOrg(org, 'test-sweep', () => keys.list(org)))
              .length,
          ]);
        }
        return out;
      });
      expect(seen.slice(0, 2)).to.deep.equal([
        [first, 0],
        [second, 0],
      ]);
    });
  });
}
