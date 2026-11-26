/**
 * G2 (ADR-INBOX-01) on PostgreSQL behind PgBouncer: the InboxStore contract on `PgInboxStore`
 * (`harness.inbox_preferences`, `run_end_notices`, `run_watchers`, migration `harness_0011`), each call run as the
 * org it names.
 *
 *   PG_TEST_BINS=… PG_TEST_PGBOUNCER=… PG_TEST_MIGRATE_PYTHON=… npm run test:pg-harness
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { PgInboxStore } from '../../../src/libs/repositories/agent-harness/inbox/pg';
import { describeInboxStoreContract } from './support/inbox-store-contract';
import { PgStack, boundStore, hexOrg, pgBins, pgSuite, resetHarnessPg, startHarnessPg } from './support/harness-pg';

for (const bin of pgBins()) {
  pgSuite(`G2 Inbox store on PostgreSQL (${bin}) behind PgBouncer`, function () {
    this.timeout(120_000);
    let stack: PgStack;
    before(async () => {
      stack = await startHarnessPg(bin, 'harness_inbox');
    });
    after(async () => {
      await stack?.stop();
    });

    describeInboxStoreContract('postgres', async () => {
      await resetHarnessPg(stack);
      return {
        store: boundStore(new PgInboxStore()),
        replica: () => boundStore(new PgInboxStore()),
        orgs: [hexOrg(), hexOrg()],
        users: [hexOrg(), hexOrg()],
      };
    });

    it('IBX-P1: a lease nobody renewed is dropped by the next read of that conversation', async () => {
      await resetHarnessPg(stack);
      const org = hexOrg();
      const store = boundStore(new PgInboxStore());
      await store.watch({ orgId: org, userId: 'u', sessionId: 's', watcherId: 'w', until: 1_000 });
      expect(await store.watching(org, 'u', 's', 2_000)).to.equal(false);
      const { rows } = await stack.admin((c) =>
        c.query<{ n: string }>('SELECT count(*) AS n FROM harness.run_watchers WHERE org_id = $1', [org]),
      );
      expect(Number(rows[0]?.n)).to.equal(0);
    });

    it('IBX-P2: claims past their retention are pruned as new ones are made', async () => {
      await resetHarnessPg(stack);
      const org = hexOrg();
      const store = boundStore(new PgInboxStore());
      await store.claimRunNotice(org, 'old', 'u', 1_000);
      await store.claimRunNotice(org, 'new', 'u', 1_000 + 31 * 24 * 3600 * 1000);
      expect(await store.claimRunNotice(org, 'old', 'u', 1_000 + 31 * 24 * 3600 * 1000)).to.equal(true);
    });
  });
}
