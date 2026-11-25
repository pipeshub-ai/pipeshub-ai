/**
 * WP-114: the control plane on PostgreSQL (Alembic head, row-level security, PgBouncer in transaction mode). The
 * shared contracts the in-memory adapters and the Mongo replica set run (TX, CA, HR, PS, ML, BS, IS, GS, LG,
 * CPD2 catalog) run here on `PgControlPlaneTransaction`, the harness audit chain on PostgreSQL and every Pg store; then
 * the invariants PostgreSQL proves on its own terms: an org's writes serialize on its head row at consecutive
 * revisions, a stale writer is refused (revision CAS, approver fence, refresh claim, one live binding per owner), an
 * aborted write leaves nothing, a writer held past the lock timeout is planned again, and one org sees nothing of
 * another.
 *
 *   PG_TEST_BINS=… PG_TEST_PGBOUNCER=… PG_TEST_MIGRATE_PYTHON=… mocha … tests/modules/control-plane/control-plane-stores.pg.itest.ts
 */
import 'reflect-metadata';
import { expect } from 'chai';
import {
  ControlPlaneRevisionConflictError,
  type ChangeEvent,
  type ConfigAuditLog,
  type ControlPlaneTransaction,
  type CpTx,
} from '../../../src/modules/control-plane';
import { CompositeCatalogStore } from '../../../src/modules/control-plane/adapters/composite-catalog-store';
import { ReleaseCatalogStore } from '../../../src/modules/control-plane/adapters/release-catalog-store';
import type { PolicyLayer } from '../../../src/modules/control-plane/domain/policy-alias';
import { ChainConfigAuditLog } from '../../../src/modules/agent-harness/adapters/chain-config-audit-log';
import { PgAuditChainStore } from '../../../src/libs/repositories/agent-harness/audit/pg';
import { PgControlPlaneTransaction } from '../../../src/libs/repositories/control-plane/control-plane-transaction/pg';
import { PgChangeOutbox } from '../../../src/libs/repositories/control-plane/change-outbox/pg';
import { PgDeploymentRevision, PgHeadRevisions } from '../../../src/libs/repositories/control-plane/head-revisions/pg';
import { PgPolicyStore } from '../../../src/libs/repositories/control-plane/policy/pg';
import { PgMigrationLedger } from '../../../src/libs/repositories/control-plane/migration-ledger/pg';
import { PgBindingStore } from '../../../src/libs/repositories/control-plane/bindings/pg';
import { PgInstallationStore } from '../../../src/libs/repositories/control-plane/installations/pg';
import { PgGrantStore } from '../../../src/libs/repositories/control-plane/grant-store/pg';
import { PgCapabilityLedger } from '../../../src/libs/repositories/control-plane/capability-ledger/pg';
import { PgOrgCatalogStore } from '../../../src/libs/repositories/control-plane/catalog/pg';
import { PgUnitOfWork, kernelTx } from '../../../src/libs/repositories/pg';
import type { Tx } from '../../../src/libs/repositories/uow';
import { PgStack, boundStore, hexOrg, inOrg, pgBins, pgSuite, resetHarnessPg, startHarnessPg, transactAs } from '../agent-harness/support/harness-pg';
import { getCurrentOrgId } from '../../../src/libs/context/orgContext';
import { FailingConfigAuditLog, Gate, RecordingLog } from './support/in-memory';
import { transactionContract } from './support/transaction-contract';
import { configAuditContract } from './support/config-audit-contract';
import { describeHeadRevisionsContract } from './support/head-revisions-contract';
import { describePolicyStoreContract, describePolicyStorePurgeContract, type PolicyStoreWorld } from './support/policy-store-contract';
import { describeMigrationLedgerContract } from './support/migration-ledger-contract';
import { describeBindingStoreContract } from './support/binding-store-contract';
import { describeInstallationStoreContract } from './support/installation-store-contract';
import { describeGrantStoreContract } from './support/grant-store-contract';
import { describeCapabilityLedgerContract } from './support/capability-ledger-contract';
import { describeCatalogStoreContract } from './support/catalog-store-contract';
import type { StoreWorld } from './support/store-world';
import { aBinding, anInstallation, bindingId, directTx, instId, ORG_A, ORG_B, rejection, storeChange } from './support/cp-objects';

const chain = (): PgAuditChainStore => new PgAuditChainStore();
const configRecords = async (orgId: string) =>
  (await inOrg(orgId, () => chain().read(orgId, 0, 1_000))).filter((record) => record.streamId === `cp:${orgId}`);
const configEntries = async (orgId: string): Promise<number> => (await configRecords(orgId)).length;

/**
 * The control-plane transaction as an entry point calls it: in the org its write is for (ADR-SEC-02; a request's auth,
 * or a job's own listing, binds it). The contracts call it as a test, with no org bound.
 */
const transaction = (options: { audit?: ConfigAuditLog; log?: RecordingLog; outbox?: PgChangeOutbox } = {}): ControlPlaneTransaction => {
  const cp = new PgControlPlaneTransaction({
    audit: options.audit ?? new ChainConfigAuditLog(chain()),
    ...(options.outbox !== undefined ? { outbox: options.outbox } : {}),
    ...(options.log !== undefined ? { log: options.log } : {}),
  });
  return { run: (ctx, work) => (getCurrentOrgId() === ctx.orgId ? cp.run(ctx, work) : inOrg(ctx.orgId, () => cp.run(ctx, work))) };
};

const write = <T>(cp: ControlPlaneTransaction, orgId: string, work: (tx: CpTx) => Promise<T>) =>
  cp.run({ orgId, actor: 'user:u-admin' }, async (tx) => ({
    result: await work(tx),
    change: { ...storeChange('store-object', 'x'), scope: `org:${orgId}` },
  }));

const layer = (kind: PolicyLayer['scope']['kind'], id: string, sections: Record<string, unknown>): PolicyLayer =>
  ({ schemaVersion: 1, scope: { kind, id }, ...sections }) as PolicyLayer;

for (const bin of pgBins()) {
  pgSuite(`control plane stores on PostgreSQL (${bin}) behind PgBouncer`, function () {
    this.timeout(180_000);
    let stack: PgStack;
    let reads = 0;
    before(async () => {
      stack = await startHarnessPg(bin, 'harness_control_plane');
      const record = stack.catalog.record.bind(stack.catalog);
      // One read of a store is one statement: what the contracts' round-trip checks count.
      stack.catalog.record = (sql: string, args: unknown[]) => {
        if (/pipeshub:harness\.cp_(policy_read|grant_get|installation_(get|list))\b/.test(sql)) reads += 1;
        record(sql, args);
      };
    });
    after(async () => {
      await stack?.stop();
    });
    beforeEach(() => resetHarnessPg(stack));

    const events = async (): Promise<ChangeEvent[]> =>
      stack.admin(async (c) => {
        const { rows } = await c.query<{ value: string }>(`SELECT value FROM platform.outbox ORDER BY id`);
        return rows.map((row) => (JSON.parse(row.value) as { payload: ChangeEvent }).payload);
      });
    const world = <S>(store: S): StoreWorld<S> => ({ store, roundTrips: () => reads, transaction: transaction(), configEntries });

    transactionContract('postgres (one unit per write)', {
      atomic: true,
      make: async (options = {}) => {
        const log = new RecordingLog();
        return {
          transaction: transaction({ ...options, log, outbox: new PgChangeOutbox({ announce: () => undefined }) }),
          events,
          log,
          head: (orgId) => boundStore(new PgHeadRevisions()).head(orgId),
          configRecords,
          // Poisons the unit after its effect ran, so its commit fails.
          failCommit: async (tx: CpTx) => {
            await kernelTx(tx.session as Tx)
              .query(`/* pipeshub:harness.cp_test_poison */ SELECT 1 / 0`)
              .catch(() => undefined);
          },
        };
      },
    });

    configAuditContract('chain over PgAuditChainStore', async () => ({
      transaction: transaction(),
      chain: (orgId) => inOrg(orgId, () => chain().read(orgId, 0, 1_000)),
      appendRun: async (orgId, entries) => {
        await transactAs((tx) => chain().append(orgId, entries, new Date().toISOString(), tx), orgId);
      },
    }));

    describeHeadRevisionsContract('PgHeadRevisions', async () => ({
      heads: boundStore(new PgHeadRevisions()),
      commit: async (orgId) => {
        await write(transaction(), orgId, async () => null);
      },
      moveDeployment: async () => {
        await write(transaction(), hexOrg(), (tx) => boundStore(new PgDeploymentRevision()).move(tx));
      },
    }));

    const policyWorld = (): PolicyStoreWorld => ({
      ...world(boundStore(new PgPolicyStore())),
      corrupt: async (orgId, scope) => {
        await stack.admin((c) =>
          c.query(`UPDATE harness.cp_policy_layers SET body = '{"scope":"not-a-scope"}' WHERE org_id = $1 AND scope = $2`, [orgId, scope]),
        );
      },
    });
    describePolicyStoreContract('PgPolicyStore', async () => policyWorld());
    describePolicyStorePurgeContract('PgPolicyStore', async () => ({ store: boundStore(new PgPolicyStore()) }));
    describeMigrationLedgerContract('PgMigrationLedger', async () => ({ ledger: boundStore(new PgMigrationLedger()), transaction: transaction() }));
    describeBindingStoreContract('PgBindingStore', async () => world(boundStore(new PgBindingStore())));
    describeInstallationStoreContract('PgInstallationStore', async () => world(boundStore(new PgInstallationStore())));
    describeGrantStoreContract('PgGrantStore', async () => world(boundStore(new PgGrantStore())));
    describeCapabilityLedgerContract('PgCapabilityLedger', async () => ({
      installations: boundStore(new PgInstallationStore()),
      grants: boundStore(new PgGrantStore()),
      ledger: boundStore(new PgCapabilityLedger()),
      transaction: transaction(),
      configEntries,
    }));
    describeCatalogStoreContract('release + PostgreSQL', async (release) =>
      new CompositeCatalogStore(new ReleaseCatalogStore(release), boundStore(new PgOrgCatalogStore())),
    );

    describe('PostgreSQL control-plane invariants', () => {
      it('the head: eight concurrent writes of one org commit at eight consecutive revisions, each audited once', async () => {
        const orgId = hexOrg();
        const committed = await Promise.all(Array.from({ length: 8 }, () => write(transaction(), orgId, async () => null)));
        expect(committed.map((c) => c.headRevision).sort((a, b) => a - b)).to.deep.equal([1, 2, 3, 4, 5, 6, 7, 8]);
        expect((await configRecords(orgId)).map((record) => record.journalSeq)).to.deep.equal([1, 2, 3, 4, 5, 6, 7, 8]);
        expect(await boundStore(new PgHeadRevisions()).heads(orgId)).to.deep.equal({ org: 8, deployment: 0 });
      });

      it('the deployment revision: moves from two orgs at once both land, and no org head moves for it', async () => {
        const [a, b] = [hexOrg(), hexOrg()];
        const move = (orgId: string) => write(transaction(), orgId, (tx) => boundStore(new PgDeploymentRevision()).move(tx));
        await Promise.all([move(a), move(b), move(a)]);
        expect(await boundStore(new PgHeadRevisions()).heads(a)).to.deep.equal({ org: 2, deployment: 3 });
        expect(await boundStore(new PgHeadRevisions()).heads(hexOrg())).to.deep.equal({ org: 0, deployment: 3 });
      });

      it('a writer held past the lock timeout behind another write of its org is planned again and commits next', async () => {
        const orgId = hexOrg();
        const gate = new Gate();
        const entered = new Gate();
        const slow = write(transaction(), orgId, async () => {
          entered.open();
          await gate.opened;
        });
        await entered.opened;
        const fast = write(transaction(), orgId, async () => 'second');
        await new Promise((resolve) => setTimeout(resolve, 2_600));
        gate.open();
        const [first, second] = await Promise.all([slow, fast]);
        expect([first.headRevision, second.headRevision]).to.deep.equal([1, 2]);
        expect(second.result).to.equal('second');
      });

      it('policy CAS: two writes of one scope at the same revision, in two units at once: one lands, the other is a conflict', async () => {
        const store = boundStore(new PgPolicyStore());
        const scope = `org:${ORG_A}`;
        const outcomes = await Promise.allSettled(
          [5, 9].map((max) => write(transaction(), ORG_A, (tx) => store.write(tx, scope, layer('org', ORG_A, { engineProfile: { limits: { maxUsd: max } } }), 0))),
        );
        expect(outcomes.filter((o) => o.status === 'fulfilled')).to.have.length(1);
        const refused = outcomes.find((o) => o.status === 'rejected') as PromiseRejectedResult;
        expect(refused.reason).to.be.instanceOf(ControlPlaneRevisionConflictError);
        expect((await store.read(ORG_A, scope)).revision).to.equal(1);
        expect(await store.history(ORG_A, scope, 10)).to.have.length(1);
      });

      it('an aborted write leaves no head, layer, binding, installation, audit entry or event', async () => {
        const outbox = new PgChangeOutbox({ announce: () => undefined });
        const cp = transaction({ outbox });
        const boom = new Error('refused after the writes');
        const failed = await rejection(
          cp.run({ orgId: ORG_A, actor: 'user:u-admin' }, async (tx) => {
            await boundStore(new PgPolicyStore()).write(tx, `org:${ORG_A}`, layer('org', ORG_A, { engineProfile: { limits: { maxUsd: 1 } } }), 0);
            await boundStore(new PgBindingStore()).upsert(tx, aBinding(), null);
            await boundStore(new PgInstallationStore()).create(tx, anInstallation());
            throw boom;
          }),
        );
        expect(failed).to.equal(boom);
        expect(await boundStore(new PgHeadRevisions()).head(ORG_A)).to.equal(0);
        expect((await boundStore(new PgPolicyStore()).read(ORG_A, `org:${ORG_A}`)).revision).to.equal(0);
        expect(await boundStore(new PgBindingStore()).get(ORG_A, bindingId(1))).to.equal(null);
        expect(await boundStore(new PgInstallationStore()).get(ORG_A, instId(1))).to.equal(null);
        expect(await configEntries(ORG_A)).to.equal(0);
        expect(await events()).to.deep.equal([]);
      });

      it('the change event: announced once its write committed, never for a write that rolled back', async () => {
        const announced: ChangeEvent[] = [];
        const outbox = new PgChangeOutbox({ announce: (event) => announced.push(event) });
        const cp = transaction({ outbox });
        await rejection(
          cp.run({ orgId: ORG_A, actor: 'user:u-admin' }, async () => {
            throw new Error('refused');
          }),
        );
        const failing = transaction({ outbox, audit: new FailingConfigAuditLog() });
        await rejection(write(failing, ORG_A, async () => null));
        expect(announced).to.deep.equal([]);
        const committed = await write(cp, ORG_A, async () => null);
        expect(announced.map((event) => event.revision)).to.deep.equal([committed.headRevision]);
        expect(await events()).to.deep.equal(announced);
      });

      it('one live binding per owner: eight creates racing in their own writes leave exactly one', async () => {
        const store = boundStore(new PgBindingStore());
        const outcomes = await Promise.allSettled(
          Array.from({ length: 8 }, (_, n) => write(transaction(), ORG_A, (tx) => store.upsert(tx, aBinding({ id: bindingId(n + 1) }), null))),
        );
        expect(outcomes.filter((o) => o.status === 'fulfilled')).to.have.length(1);
        expect((await store.listForOwner(ORG_A, 'user:u3')).filter((b) => b.status !== 'revoked')).to.have.length(1);
      });

      it('the refresh claim: of eight replicas claiming one binding at once exactly one holds it; a live claim refuses another, its own holder renews', async () => {
        const store = boundStore(new PgBindingStore());
        await store.upsert(directTx(ORG_A), aBinding({ expiresAt: 1_000 }), null);
        const now = 10_000;
        const claims = await Promise.all(
          Array.from({ length: 8 }, (_, n) => store.claimRefresh(ORG_A, bindingId(1), 1, { holder: `r${String(n)}`, now, until: now + 60_000 })),
        );
        expect(claims.filter(Boolean)).to.have.length(1);
        const holder = `r${String(claims.indexOf(true))}`;
        const other = holder === 'r0' ? 'r1' : 'r0';
        expect(await store.claimRefresh(ORG_A, bindingId(1), 1, { holder: other, now: now + 1, until: now + 2 })).to.equal(false);
        expect(await store.claimRefresh(ORG_A, bindingId(1), 1, { holder, now: now + 1, until: now + 90_000 })).to.equal(true);
        expect(await store.dueForRefresh(5_000, 10, { holder: other, now: now + 1 })).to.deep.equal([]);
        expect((await store.dueForRefresh(5_000, 10, { holder: other, now: now + 90_001 })).map((b) => b.id)).to.deep.equal([bindingId(1)]);
        // A write clears the claim; a stale revision claims nothing.
        await store.upsert(directTx(ORG_A), aBinding({ expiresAt: 1_000 }), 1);
        expect(await store.claimRefresh(ORG_A, bindingId(1), 1, { holder: other, now, until: now + 1 })).to.equal(false);
        expect(await store.claimRefresh(ORG_A, bindingId(1), 2, { holder: other, now, until: now + 1 })).to.equal(true);
      });

      it('the refresher scan reads every org (the jobs role); each org reads only its own', async () => {
        const store = boundStore(new PgBindingStore());
        await store.upsert(directTx(ORG_A), aBinding({ expiresAt: 1_000 }), null);
        await store.upsert(directTx(ORG_B), aBinding({ orgId: ORG_B, id: bindingId(2), expiresAt: 2_000 }), null);
        expect((await store.dueForRefresh(5_000, 10)).map((b) => [b.orgId, b.id])).to.deep.equal([
          [ORG_A, bindingId(1)],
          [ORG_B, bindingId(2)],
        ]);
        expect(await store.get(ORG_B, bindingId(1))).to.equal(null);
        expect((await store.listByInstallation(ORG_B, instId(1), { after: null, limit: 10 })).items.map((b) => b.id)).to.deep.equal([bindingId(2)]);
      });

      it('another org sees nothing: its unit counts none of the rows, and a store call naming another org is refused', async () => {
        await write(transaction(), ORG_A, async (tx) => {
          await boundStore(new PgPolicyStore()).write(tx, `org:${ORG_A}`, layer('org', ORG_A, { engineProfile: { limits: { maxUsd: 1 } } }), 0);
          await boundStore(new PgInstallationStore()).create(tx, anInstallation());
        });
        const count = (orgId: string) =>
          inOrg(orgId, () =>
            new PgUnitOfWork().run(async (tx) => {
              const { rows } = await kernelTx(tx).query<{ n: number }>(
                `/* pipeshub:harness.cp_test_count */ SELECT (SELECT count(*) FROM harness.cp_policy_layers)::int
                   + (SELECT count(*) FROM harness.cp_installations)::int + (SELECT count(*) FROM harness.cp_org_heads)::int AS n`,
              );
              return rows[0]?.n;
            }),
          );
        expect(await count(ORG_A)).to.equal(3);
        expect(await count(ORG_B)).to.equal(0);
        expect(await rejection(inOrg(ORG_B, () => boundStore(new PgPolicyStore()).read(ORG_A, `org:${ORG_A}`)))).to.be.an('error');
        expect(await rejection(inOrg(ORG_B, () => boundStore(new PgHeadRevisions()).head(ORG_A)))).to.be.an('error');
      });
    });
  });
}
