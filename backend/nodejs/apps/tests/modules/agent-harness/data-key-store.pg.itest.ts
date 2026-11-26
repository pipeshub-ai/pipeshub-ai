/**
 * The DataKeyStore contract on PostgreSQL (`harness.data_keys`, row-level security, PgBouncer in transaction mode),
 * and what the row policies add: a call with no organisation bound is refused (ADR-SEC-02), and a unit of one org
 * sees no key of another.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { PgDataKeyStore } from '../../../src/libs/repositories/agent-harness/data-protection/pg';
import { activeUnit } from '../../../src/libs/db';
import { orgContextStorage } from '../../../src/libs/context/orgContext';
import { UnboundOrgError } from '../../../src/libs/repositories/agent-harness/durable-transaction/bound-org';
import {
  ORG_1,
  ORG_2,
  describeDataKeyStoreContract,
  wrappedKey,
} from './support/data-key-store-contract';
import {
  PgStack,
  inOrg,
  pgBins,
  pgSuite,
  resetHarnessPg,
  scopedStore,
  startHarnessPg,
  transactAs,
} from './support/harness-pg';

const ORG_ARGS = { list: 0, reserveSeals: 0, deleteAll: 0, replaceWrap: 0 };

for (const bin of pgBins()) {
  pgSuite(`DataKeyStore on PostgreSQL (${bin})`, function () {
    this.timeout(120_000);
    let stack: PgStack;
    before(async () => {
      stack = await startHarnessPg(bin, 'harness_data_keys');
    });
    after(async () => {
      await stack?.stop();
    });

    describeDataKeyStoreContract(
      'postgres',
      async () => {
        await resetHarnessPg(stack);
        return scopedStore(new PgDataKeyStore(), ORG_ARGS);
      },
    );

    describe('PostgreSQL data keys', () => {
      beforeEach(() => resetHarnessPg(stack));

      it("a key id another org holds is that org's alone: each org lists and counts only its own", async () => {
        const store = scopedStore(new PgDataKeyStore(), ORG_ARGS);
        await store.insert(wrappedKey());
        expect(
          await store.insert(wrappedKey({ orgId: ORG_2, wrapped: 'b3RoZXI=' })),
        ).to.equal(true);
        expect(await store.reserveSeals(ORG_1, 'kid-1', 2)).to.equal(2);
        expect(await store.reserveSeals(ORG_2, 'kid-1', 1)).to.equal(1);
        expect(
          (await store.list(ORG_2)).map((key) => key.wrapped),
        ).to.deep.equal(['b3RoZXI=']);
      });

      it('with no organisation bound every call is refused and writes nothing; bound, it touches only its org', async () => {
        const raw = new PgDataKeyStore();
        const unbound = <T>(fn: () => Promise<T>) =>
          orgContextStorage.exit(() => fn().catch((error: unknown) => error));
        expect(await unbound(() => raw.insert(wrappedKey()))).to.be.instanceOf(UnboundOrgError);
        expect(await unbound(() => raw.list(ORG_1))).to.be.instanceOf(UnboundOrgError);
        expect(await unbound(() => raw.deleteAll(ORG_2))).to.be.instanceOf(UnboundOrgError);
        expect(await inOrg(ORG_1, () => raw.insert(wrappedKey()))).to.equal(true);
        expect(
          await inOrg(ORG_2, () => raw.insert(wrappedKey({ orgId: ORG_2, kid: 'kid-9' }))),
        ).to.equal(true);
        expect((await inOrg(ORG_1, () => raw.list(ORG_1))).map((key) => key.kid)).to.deep.equal([
          'kid-1',
        ]);
        expect(await inOrg(ORG_2, () => raw.deleteAll(ORG_2))).to.equal(1);
        expect((await inOrg(ORG_1, () => raw.list(ORG_1))).map((key) => key.kid)).to.deep.equal([
          'kid-1',
        ]);
      });

      it('a unit of one org sees no key of another, and a call naming another org is refused', async () => {
        await inOrg(ORG_1, () => new PgDataKeyStore().insert(wrappedKey()));
        const seen = await transactAs(async () => {
          const k = activeUnit();
          const r = await k!.query<{ n: number }>(
            `/* pipeshub:harness.test_count */ SELECT count(*)::int AS n FROM harness.data_keys`,
          );
          return r.rows[0]!.n;
        }, ORG_2);
        expect(seen).to.equal(0);
        const refused = await inOrg(ORG_2, () =>
          new PgDataKeyStore().list(ORG_1).catch((error: Error) => error),
        );
        expect(refused).to.be.instanceOf(Error);
      });
    });
  });
}
