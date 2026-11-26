/**
 * CPD-8 on a MongoDB replica set: CP-SKEL's BindingStore contract (BS-1..6),
 * unchanged, on `MongoBindingStore`; the index migration (BS-7); writes in
 * the control-plane transaction (BS-8); the unique index under a race
 * (BS-9); and CP-SKEL's binder contract on `VaultCredentialBinder` over the
 * Mongo store and transaction. Needs MONGO_IT_URI (rs0).
 */
import 'reflect-metadata';
import { expect } from 'chai';
import mongoose, { Model } from 'mongoose';
import { MaxKey, type Db } from 'mongodb';
import { MongoControlPlaneTransaction } from '../../../src/modules/control-plane';
import { CpOrgHeadModel } from '../../../src/libs/repositories/control-plane/org-heads/schema/org-heads.schema';
import { BINDING_MIGRATION } from '../../../src/libs/repositories/control-plane/migrations/mongo';
import { CpBindingModel } from '../../../src/libs/repositories/control-plane/bindings/schema/bindings.schema';
import { MongoBindingStore } from '../../../src/libs/repositories/control-plane/bindings/mongo';
import { BindingService } from '../../../src/modules/control-plane/application/binding-service';
import { ObjectExists, ObjectNotFound } from '../../../src/modules/control-plane/domain/store-errors';
import { ChainConfigAuditLog } from '../../../src/modules/agent-harness/adapters/chain-config-audit-log';
import { FailingConfigAuditLog, InMemoryChangeOutbox } from './support/in-memory';
import { MongoAuditChainStore } from '../../../src/libs/repositories/agent-harness/audit/mongo';
import { DURABLE_TRANSACTION_OPTIONS } from '../../../src/libs/repositories/agent-harness/durable-transaction/mongo';
import { migrateHarnessIndexes } from '../../../src/libs/repositories/agent-harness/migrations/mongo';
import {
  disconnectHarnessMongo,
  harnessModels,
  harnessMongoUri,
  registerHarnessModels,
  resetHarnessMongo,
} from '../agent-harness/support/harness-mongo';
import { describeBindingStoreContract } from './support/binding-store-contract';
import { describeCredentialBinderContract } from './support/credential-binder-contract';
import { aBinding, anInstallation, bindingId, directTx, instId, ORG_A, ORG_B, rejection } from './support/cp-objects';
import { vaultBinderWorld } from './support/binding-world';
import { committed, expectConflict, expectRefused, rolledBack, type StoreWorld } from './support/store-world';

registerHarnessModels([CpOrgHeadModel, CpBindingModel] as unknown as Array<Model<never>>);

const db = (): Db => mongoose.connection.db!;
const indexNames = async (): Promise<string[]> =>
  (await db().collection('cpBindings').indexes()).map((index) => index.name!).sort();

const mongoTransaction = (failing = false, outbox = new InMemoryChangeOutbox()) =>
  new MongoControlPlaneTransaction({
    audit: failing ? new FailingConfigAuditLog() : new ChainConfigAuditLog(new MongoAuditChainStore()),
    outbox,
    transaction: DURABLE_TRANSACTION_OPTIONS,
  });

const configEntries = async (orgId: string): Promise<unknown[]> =>
  (await new MongoAuditChainStore().read(orgId, 0, 1_000)).filter((record) => record.streamId === `cp:${orgId}`);

function mongoWorld(store: MongoBindingStore): StoreWorld<MongoBindingStore> {
  return {
    store,
    roundTrips: () => 0,
    transaction: mongoTransaction(),
    configEntries: async (orgId) => (await configEntries(orgId)).length,
  };
}

describe('CPD-8 binding store on a MongoDB replica set', function () {
  this.timeout(120_000);

  before(async () => {
    await mongoose.connect(harnessMongoUri(), { dbName: `cp_bindings_it_${Date.now()}_${process.pid}` });
    await Promise.all(harnessModels().map((model) => model.init()));
    await migrateHarnessIndexes(db());
    await BINDING_MIGRATION.up(db());
  });
  after(disconnectHarnessMongo);
  beforeEach(resetHarnessMongo);

  describeBindingStoreContract('MongoBindingStore', async () => mongoWorld(new MongoBindingStore()));

  describeCredentialBinderContract('VaultCredentialBinder over MongoBindingStore and the Mongo transaction', {
    async make(options = {}) {
      await resetHarnessMongo();
      return vaultBinderWorld({
        bindings: new MongoBindingStore(),
        failingAudit: options.failingAudit,
        transaction: ({ failing }) => {
          const outbox = new InMemoryChangeOutbox();
          return { transaction: mongoTransaction(failing, outbox), configEntries, changeEvents: async () => outbox.events };
        },
      });
    },
  });

  it('BS-7: the migration builds the indexes, idempotently; down keeps every row', async () => {
    const built = ['_id_', 'cp_bindings_due', 'cp_bindings_for_owner', 'cp_bindings_installation', 'cp_bindings_live_owner'];
    expect(await indexNames()).to.deep.equal(built);
    expect(await BINDING_MIGRATION.up(db())).to.deep.equal({ created: [], dropped: [] });
    const store = new MongoBindingStore();
    await store.upsert(directTx(ORG_A), aBinding(), null);
    expect((await BINDING_MIGRATION.down(db())).dropped).to.have.length(4);
    expect(await indexNames()).to.deep.equal(['_id_']);
    expect(await store.get(ORG_A, bindingId(1))).to.deep.equal(aBinding());
    expect((await BINDING_MIGRATION.up(db())).created).to.have.length(4);
  });

  it('BS-17: a database the first build left with the unique index over every row: up drops it, and a revoked owner binds again', async () => {
    await BINDING_MIGRATION.down(db());
    await db().collection('cpBindings').createIndex({ orgId: 1, installation: 1, owner: 1 }, { name: 'cp_bindings_owner', unique: true });
    const store = new MongoBindingStore();
    await store.upsert(directTx(ORG_A), aBinding(), null);
    await store.upsert(directTx(ORG_A), aBinding({ status: 'revoked', secret: null }), 1);
    expect(await rejection(store.upsert(directTx(ORG_A), aBinding({ id: bindingId(2) }), null))).to.be.instanceOf(ObjectExists);

    const result = await BINDING_MIGRATION.up(db());
    expect(result.dropped).to.deep.equal(['cpBindings.cp_bindings_owner']);
    expect(result.created).to.have.length(4);
    expect(await indexNames()).to.not.include('cp_bindings_owner');
    expect(await store.upsert(directTx(ORG_A), aBinding({ id: bindingId(2) }), null)).to.equal(1);
    expect((await store.listForOwner(ORG_A, 'user:u3')).map((b) => [b.id, b.status])).to.deep.equal([
      [bindingId(1), 'revoked'],
      [bindingId(2), 'active'],
    ]);
  });

  it('BS-18: two binds racing after a revoke, in two control-plane writes: one lands, the other is ObjectExists', async () => {
    const world = mongoWorld(new MongoBindingStore());
    await world.store.upsert(directTx(ORG_A), aBinding(), null);
    await world.store.upsert(directTx(ORG_A), aBinding({ status: 'revoked', secret: null }), 1);
    const results = await Promise.allSettled([
      committed(world, (tx) => world.store.upsert(tx, aBinding({ id: bindingId(2) }), null)),
      committed({ ...world, transaction: mongoTransaction() }, (tx) => world.store.upsert(tx, aBinding({ id: bindingId(3) }), null)),
    ]);
    const refused = results.filter((r): r is PromiseRejectedResult => r.status === 'rejected');
    expect(results.filter((r) => r.status === 'fulfilled')).to.have.length(1);
    expect(refused.map((r) => r.reason instanceof ObjectExists)).to.deep.equal([true]);
    expect((await world.store.listForOwner(ORG_A, 'user:u3')).filter((b) => b.status !== 'revoked')).to.have.length(1);
  });

  it('BS-8: a binding written in a rolled-back control-plane write leaves no row; a committed one is audited once', async () => {
    const world = mongoWorld(new MongoBindingStore());
    await rolledBack(world, (tx) => world.store.upsert(tx, aBinding(), null));
    expect(await world.store.get(ORG_A, bindingId(1))).to.equal(null);
    await committed(world, (tx) => world.store.upsert(tx, aBinding(), null));
    expect(await world.store.get(ORG_A, bindingId(1))).to.deep.equal(aBinding());
    expect(await world.configEntries(ORG_A)).to.equal(1);
  });

  it('BS-9: two creates for one owner in two control-plane writes: one lands, the other is ObjectExists', async () => {
    const world = mongoWorld(new MongoBindingStore());
    const results = await Promise.allSettled([
      committed(world, (tx) => world.store.upsert(tx, aBinding(), null)),
      committed({ ...world, transaction: mongoTransaction() }, (tx) =>
        world.store.upsert(tx, aBinding({ id: bindingId(2) }), null),
      ),
    ]);
    const refused = results.filter((r): r is PromiseRejectedResult => r.status === 'rejected');
    expect(results.filter((r) => r.status === 'fulfilled')).to.have.length(1);
    expect(refused).to.have.length(1);
    expect(refused[0]!.reason).to.be.instanceOf(ObjectExists);
    expect(await world.store.listForOwner(ORG_A, 'user:u3')).to.have.length(1);
    expect(await rejection(world.store.upsert(directTx(ORG_A), aBinding({ id: bindingId(3) }), null))).to.be.instanceOf(
      ObjectExists,
    );
    expect(instId(1)).to.equal(aBinding().installation);
  });
  it('BS-10: remove deletes at the expected revision only, in the transaction, in its own org', async () => {
    const world = mongoWorld(new MongoBindingStore());
    await world.store.upsert(directTx(ORG_A), aBinding(), null);
    await expectConflict(world.store.remove(directTx(ORG_A), bindingId(1), 4), 1);
    await expectRefused(world.store.remove(directTx(ORG_B), bindingId(1), 1), ObjectNotFound);
    await rolledBack(world, (tx) => world.store.remove(tx, bindingId(1), 1));
    expect(await world.store.get(ORG_A, bindingId(1))).to.deep.equal(aBinding());
    await committed(world, (tx) => world.store.remove(tx, bindingId(1), 1));
    expect(await world.store.get(ORG_A, bindingId(1))).to.equal(null);
    await expectRefused(world.store.remove(directTx(ORG_A), bindingId(1), 1), ObjectNotFound);
  });

  it('BM-6 on Mongo: a revert removes an imported binding, its secret and its pointer', async () => {
    const world = vaultBinderWorld({
      bindings: new MongoBindingStore(),
      transaction: ({ failing }) => ({ transaction: mongoTransaction(failing), configEntries, changeEvents: async () => [] }),
    });
    const ctx = { orgId: ORG_A, actor: 'service:credential-migration' };
    const { binding } = await world.binder.importRecord(ctx, {
      installation: instId(1),
      owner: 'user:u3',
      mode: 'oauth',
      kind: 'toolset',
      provider: 'slack',
      material: { kind: 'record', value: { isAuthenticated: true } },
      source: { path: `/services/toolsets/${instId(1)}/u3`, digest: `sha256:${'a'.repeat(64)}` },
    });
    const reverted = await world.binder.revertImport(ctx, instId(1), 'user:u3');
    expect(reverted).to.deep.include({ changedSinceImport: false });
    expect(await world.bindings.get(ORG_A, binding!.id)).to.equal(null);
    expect(await world.document(binding!.secret!.path)).to.equal(null);
    expect(await world.pointer(ORG_A, binding!.id)).to.equal(null);
    expect(await configEntries(ORG_A)).to.have.length(2);
  });

  it('BS-13: listByInstallation reads its page in order from the installation index: no collection scan, no sort stage', async () => {
    const store = new MongoBindingStore();
    for (const [index, owner] of (['user:u1', 'user:u2', 'user:u3'] as const).entries()) {
      await store.upsert(directTx(ORG_A), aBinding({ id: bindingId(index + 1), owner }), null);
    }
    const plan = JSON.stringify(
      await CpBindingModel.collection
        .find(
          { orgId: ORG_A, installation: instId(1) },
          {
            hint: 'cp_bindings_installation',
            sort: { owner: 1, _id: 1 },
            limit: 3,
            min: { orgId: ORG_A, installation: instId(1), owner: 'user:u1', _id: bindingId(1) },
            max: { orgId: ORG_A, installation: instId(1), owner: new MaxKey(), _id: new MaxKey() },
          },
        )
        .explain('queryPlanner'),
    );
    expect(plan).to.contain('cp_bindings_installation');
    expect(plan).to.not.contain('COLLSCAN');
    expect(plan).to.not.contain('"SORT"');
  });

  it('MG-11 (CPD-7): revokeInstallation revokes every binding of the installation on the replica set, audited, and tombstones each secret', async () => {
    const outbox = new InMemoryChangeOutbox();
    const world = vaultBinderWorld({
      bindings: new MongoBindingStore(),
      transaction: () => ({ transaction: mongoTransaction(false, outbox), configEntries, changeEvents: async () => outbox.events }),
    });
    const installation = anInstallation();
    const made = [];
    for (const user of ['u3', 'u4', 'u5']) {
      const owner = `user:${user}` as const;
      made.push(
        (await world.binder.begin({ orgId: ORG_A, actor: owner }, installation, owner, 'api_key', { kind: 'api_key', value: { apiKey: user } }))
          .binding,
      );
    }
    const other = (
      await world.binder.begin({ orgId: ORG_A, actor: 'user:u3' }, anInstallation({ id: instId(2) }), 'user:u3', 'api_key', {
        kind: 'api_key',
        value: { apiKey: 'kept' },
      })
    ).binding;
    const before = (await configEntries(ORG_A)).length;
    const service = new BindingService(world.bindings, world.binder);

    const revoked = await service.revokeInstallation({ orgId: ORG_A, actor: 'system' }, installation.id);

    expect(revoked.map((b) => [b.id, b.status])).to.deep.equal(made.map((b) => [b.id, 'revoked']));
    expect((await configEntries(ORG_A)).length - before).to.equal(3);
    for (const binding of made) expect(await world.vaultRead(binding.secret!.path)).to.equal(null);
    expect((await world.bindings.get(ORG_A, other.id))?.status).to.equal('active');
    expect(await world.vaultRead(other.secret!.path)).to.deep.equal({ kind: 'api_key', value: { apiKey: 'kept' } });
    expect(await service.revokeInstallation({ orgId: ORG_A, actor: 'system' }, installation.id)).to.deep.equal([]);
  });
});
