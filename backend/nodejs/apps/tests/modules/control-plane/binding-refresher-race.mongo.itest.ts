/**
 * QA23-F2 on a MongoDB replica set: two token refresher replicas (two
 * `BindingService`s, two holders) over one `MongoBindingStore`. Each due
 * binding goes to one replica only, so a rotating refresh token is spent
 * once; the winner's write frees it; a lapsed claim is taken over. Needs
 * MONGO_IT_URI (rs0).
 */
import 'reflect-metadata';
import { expect } from 'chai';
import mongoose, { Model } from 'mongoose';
import type { Db } from 'mongodb';
import { MongoControlPlaneTransaction } from '../../../src/modules/control-plane';
import { CpOrgHeadModel } from '../../../src/libs/repositories/control-plane/org-heads/schema/org-heads.schema';
import { BINDING_MIGRATION } from '../../../src/libs/repositories/control-plane/migrations/mongo';
import { CpBindingModel } from '../../../src/libs/repositories/control-plane/bindings/schema/bindings.schema';
import { MongoBindingStore } from '../../../src/libs/repositories/control-plane/bindings/mongo';
import { BindingService, REFRESH_CLAIM_MS } from '../../../src/modules/control-plane/application/binding-service';
import { ChainConfigAuditLog } from '../../../src/modules/agent-harness/adapters/chain-config-audit-log';
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
import { InMemoryChangeOutbox } from './support/in-memory';
import { vaultBinderWorld, type VaultBinderWorld } from './support/binding-world';
import { instId, ORG_A } from './support/cp-objects';

registerHarnessModels([CpOrgHeadModel, CpBindingModel] as unknown as Array<Model<never>>);

const db = (): Db => mongoose.connection.db!;
const REPLICA_A = 'replica-a';
const REPLICA_B = 'replica-b';
const ctx = { orgId: ORG_A, actor: 'service:token-refresher' };

describe('QA23-F2: two token refresher replicas on a MongoDB replica set', function () {
  this.timeout(120_000);
  let world: VaultBinderWorld;
  let a: BindingService;
  let b: BindingService;

  before(async () => {
    await mongoose.connect(harnessMongoUri(), { dbName: `cp_refresher_race_it_${Date.now()}_${process.pid}` });
    await Promise.all(harnessModels().map((model) => model.init()));
    await migrateHarnessIndexes(db());
    await BINDING_MIGRATION.up(db());
  });
  after(disconnectHarnessMongo);

  beforeEach(async () => {
    await resetHarnessMongo();
    world = vaultBinderWorld({
      bindings: new MongoBindingStore(),
      transaction: () => {
        const outbox = new InMemoryChangeOutbox();
        return {
          transaction: new MongoControlPlaneTransaction({
            audit: new ChainConfigAuditLog(new MongoAuditChainStore()),
            outbox,
            transaction: DURABLE_TRANSACTION_OPTIONS,
          }),
          configEntries: async () => [],
          changeEvents: async () => outbox.events,
        };
      },
    });
    // Two replicas: their own service objects over the one collection, one clock.
    a = new BindingService(new MongoBindingStore(), world.binder, () => world.clock.now);
    b = new BindingService(new MongoBindingStore(), world.binder, () => world.clock.now);
  });

  const bind = async (n: number): Promise<string> => {
    const imported = await world.binder.importRecord(ctx, {
      installation: instId(n),
      owner: 'user:u3',
      mode: 'oauth',
      kind: 'mcp',
      provider: null,
      material: { kind: 'record', value: { oauthTokens: { refreshToken: `refresh-${n}` } }, expiresAt: 1_000 + n },
      source: null,
    });
    return imported.binding!.id;
  };

  it('each due binding is claimed by exactly one of two replicas scanning at once', async () => {
    const ids = await Promise.all(Array.from({ length: 12 }, (_, i) => bind(i + 1)));
    const [ofA, ofB] = await Promise.all([a.due(5_000, 50, REPLICA_A), b.due(5_000, 50, REPLICA_B)]);
    const taken = [...ofA, ...ofB].map((d) => d.id);
    expect(taken.slice().sort()).to.deep.equal(ids.slice().sort());
    expect(new Set(taken).size).to.equal(ids.length);
    expect(await a.due(5_000, 50, REPLICA_A)).to.have.length(ofA.length, 'a replica takes its own claims again');
    expect(await b.due(5_000, 50, REPLICA_B)).to.have.length(ofB.length);
  });

  it("the winner's write frees the binding; meanwhile the loser's on-demand read answers claimed: false", async () => {
    const id = await bind(1);
    const [due] = await a.due(5_000, 50, REPLICA_A);
    expect(due).to.deep.include({ id, claimed: true });
    expect(await b.due(5_000, 50, REPLICA_B)).to.deep.equal([]);
    expect(await b.findForRefresh(ORG_A, instId(1), 'user:u3', REPLICA_B)).to.deep.include({ id, revision: 1, claimed: false });
    await a.refreshed(ctx, id, due!.revision, { kind: 'record', value: { oauthTokens: { refreshToken: 'refresh-2' } }, expiresAt: 2_000 });
    expect((await CpBindingModel.collection.findOne({ _id: id as never }))!).to.not.have.property('refreshClaim');
    expect(await b.findForRefresh(ORG_A, instId(1), 'user:u3', REPLICA_B)).to.deep.include({ id, revision: 2, claimed: true });
  });

  it('a claim that lapsed (its replica died) is taken over', async () => {
    const id = await bind(1);
    expect((await a.due(5_000, 50, REPLICA_A)).map((d) => d.id)).to.deep.equal([id]);
    world.clock.now += REFRESH_CLAIM_MS - 1;
    expect(await b.due(5_000, 50, REPLICA_B)).to.deep.equal([]);
    world.clock.now += 1;
    expect((await b.due(5_000, 50, REPLICA_B)).map((d) => d.id)).to.deep.equal([id]);
    expect(await a.due(5_000, 50, REPLICA_A)).to.deep.equal([]);
  });
});
