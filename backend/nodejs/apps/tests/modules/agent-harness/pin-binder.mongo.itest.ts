/**
 * CPD10-70 on a MongoDB replica set: a capability write and the pinner's
 * snapshot cache, through the production binder (`bindControlPlanePin`) and
 * the production change path (`createControlPlaneTransaction`, whose commits
 * the process's change outbox announces to `controlPlaneSubscribers`). The
 * binder's head reader refuses to answer without a connection, so the case
 * cannot run in the unit lane. Needs MONGO_IT_URI (rs0).
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { Container } from 'inversify';
import mongoose, { Model } from 'mongoose';
import { OutboxEvent } from '../../../src/libs/repositories/outbox/schema/outbox.schema';
import { controlPlaneSubscribers } from '../../../src/modules/agent-harness/adapters/control-plane/process-changes';
import { AGENT_HARNESS_PIN_SNAPSHOTS, bindControlPlanePin } from '../../../src/modules/agent-harness/binders/control-plane-pin';
import { createControlPlaneTransaction, type AgentHarnessDependencies } from '../../../src/modules/agent-harness/composition';
import { CONTROL_PLANE_MIGRATIONS, MongoBindingStore, MongoGrantStore, MongoInstallationStore, type ChangeEvent, type CpTx, type ResolvedSnapshotCache } from '../../../src/modules/control-plane';
import { CpOrgHeadModel } from '../../../src/libs/repositories/control-plane/org-heads/schema/org-heads.schema';
import { CpBindingModel } from '../../../src/libs/repositories/control-plane/bindings/schema/bindings.schema';
import {
  CpCapabilityLedgerModel,
  CpGrantModel,
  CpInstallationHistoryModel,
  CpInstallationModel,
} from '../../../src/libs/repositories/control-plane/installations/schema/installations.schema';
import { MigrationLedgerModel } from '../../../src/libs/repositories/control-plane/policy/schema/policy.schema';
import { aBinding, aGrant, anEntry, anInstallation, bindingId, instId, ORG_A, ORG_B, storeChange } from '../control-plane/support/cp-objects';
import { connectHarnessMongo, disconnectHarnessMongo, registerHarnessModels, resetHarnessMongo } from './support/harness-mongo';

registerHarnessModels([
  CpOrgHeadModel,
  CpInstallationModel,
  CpInstallationHistoryModel,
  CpGrantModel,
  CpCapabilityLedgerModel,
  CpBindingModel,
  MigrationLedgerModel,
  OutboxEvent,
] as unknown as Array<Model<never>>);

describe('CPD10-70: a capability write drops the org\'s cached snapshots (production binder and change path)', function () {
  this.timeout(120_000);
  const saved = process.env.CONTROL_PLANE_PIN;
  let cache: ResolvedSnapshotCache;
  const events: ChangeEvent[] = [];

  before(connectHarnessMongo);
  before(async () => {
    for (const migration of CONTROL_PLANE_MIGRATIONS) await migration.up(mongoose.connection.db!);
    process.env.CONTROL_PLANE_PIN = 'shadow';
    const container = new Container();
    bindControlPlanePin({
      container,
      dependencies: { rollout: { decide: async () => expect.fail('not asked'), decideForOrg: async () => expect.fail('not asked') } } as unknown as AgentHarnessDependencies,
    } as never);
    cache = container.get<ResolvedSnapshotCache>(AGENT_HARNESS_PIN_SNAPSHOTS);
    controlPlaneSubscribers.subscribe((event) => void events.push(event));
  });
  after(async () => {
    if (saved === undefined) delete process.env.CONTROL_PLANE_PIN;
    else process.env.CONTROL_PLANE_PIN = saved;
    await disconnectHarnessMongo();
  });
  beforeEach(async () => {
    await resetHarnessMongo();
    cache.refresh(ORG_A);
    cache.refresh(ORG_B);
    events.length = 0;
  });

  /** Three subjects of org A and one of org B, each read once: four cached entries. */
  const loadAll = async (): Promise<void> => {
    await cache.snapshot(ORG_A, 'a7', 'u3');
    await cache.snapshot(ORG_A, 'a8', 'u4');
    await cache.snapshot(ORG_A, null, 'u3');
    await cache.snapshot(ORG_B, 'a7', 'u3');
  };
  const commit = (orgId: string, type: string, id: string, work: (tx: CpTx) => Promise<unknown>): Promise<unknown> =>
    createControlPlaneTransaction().run({ orgId, actor: 'user:u-admin' }, async (tx) => {
      await work(tx);
      return { result: null, change: { ...storeChange(type, id), scope: `org:${orgId}` } };
    });

  const WRITES: Array<[string, () => Promise<unknown>]> = [
    ['an installation write', () => commit(ORG_A, 'installation', instId(1), (tx) => new MongoInstallationStore().create(tx, anInstallation()))],
    ['a grant write', () => commit(ORG_A, 'grant', 'agent:a7', (tx) => new MongoGrantStore().put(tx, aGrant({ entries: [anEntry(instId(1))] }), 0))],
    ['a binding write', () => commit(ORG_A, 'binding', bindingId(1), (tx) => new MongoBindingStore().upsert(tx, aBinding(), null))],
  ];

  for (const [name, write] of WRITES) {
    it(`${name} drops every entry of its org and none of another org's; the same event again drops nothing more`, async () => {
      await loadAll();
      expect(cache.size).to.equal(4);

      await write();

      expect(cache.size).to.equal(1);
      expect(events.map((event) => [event.orgId, event.revision])).to.deep.equal([[ORG_A, 1]]);
      const before = await CpOrgHeadModel.findById(ORG_A).lean().exec();
      expect(before?.revision).to.equal(1);

      await loadAll();
      expect(cache.size).to.equal(4);
      controlPlaneSubscribers.deliver(events[0]!);
      expect(cache.size).to.equal(4);
      expect((await cache.snapshot(ORG_A, 'a7', 'u3')).headRevision).to.equal(1);
    });
  }

  it("the reloaded snapshot holds what the write wrote, and org B's entry was never reloaded", async () => {
    await loadAll();
    const theirs = await cache.snapshot(ORG_B, 'a7', 'u3');
    await WRITES[0]![1]();
    await WRITES[1]![1]();
    const reloaded = await cache.snapshot(ORG_A, 'a7', 'u3');
    expect([[...reloaded.installations.keys()], [...reloaded.grants.keys()]]).to.deep.equal([[instId(1)], ['agent:a7']]);
    expect(await cache.snapshot(ORG_B, 'a7', 'u3')).to.equal(theirs);
  });
});
