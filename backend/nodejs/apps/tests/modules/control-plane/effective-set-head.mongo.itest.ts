/**
 * CPD-7 ESR-12 (NFR-10): the next admission never pins a stale effective
 * set. Every installation, grant and binding write is a control-plane write,
 * which moves the org head in its own transaction; the pinner's snapshot
 * cache reads that head on every pin and reloads when it moved. Two caches
 * stand for two Node instances: the one that made the write and one that only
 * sees the head. On the replica set, with the Mongo stores, transaction and
 * head. Needs MONGO_IT_URI (rs0).
 */
import 'reflect-metadata';
import { expect } from 'chai';
import mongoose, { Model } from 'mongoose';
import { policySnapshotLoader } from '../../../src/modules/agent-harness/adapters/control-plane/policy-snapshot-loader';
import { ChainConfigAuditLog } from '../../../src/modules/agent-harness/adapters/chain-config-audit-log';
import { DURABLE_TRANSACTION_OPTIONS } from '../../../src/libs/repositories/agent-harness/durable-transaction/mongo';
import { MongoAuditChainStore } from '../../../src/libs/repositories/agent-harness/audit/mongo';
import { releaseLayers } from '../../../src/modules/agent-harness/domain/pin-shadow';
import { PARENT_DIMENSIONS, PolicyResolver } from '../../../src/modules/agent-harness/domain/policy';
import { CanonicalEffectiveSetResolver, CapabilityMigration, MongoBindingStore, MongoCapabilityLedger, MongoControlPlaneTransaction, MongoGrantStore, MongoHeadRevisions, MongoInstallationStore, PolicyRunConfigPinner, prefixSources, ResolvedSnapshotCache, runSources, type PinSubject } from '../../../src/modules/control-plane';
import { CpOrgHeadModel } from '../../../src/libs/repositories/control-plane/org-heads/schema/org-heads.schema';
import { BINDING_MIGRATION } from '../../../src/libs/repositories/control-plane/migrations/mongo';
import { CpBindingModel } from '../../../src/libs/repositories/control-plane/bindings/schema/bindings.schema';
import { INSTALLATIONS_MIGRATION } from '../../../src/libs/repositories/control-plane/migrations/mongo';
import {
  CpCapabilityLedgerModel,
  CpGrantModel,
  CpInstallationHistoryModel,
  CpInstallationModel,
} from '../../../src/libs/repositories/control-plane/installations/schema/installations.schema';
import { BindingService } from '../../../src/modules/control-plane/application/binding-service';
import { connectHarnessMongo, disconnectHarnessMongo, registerHarnessModels, resetHarnessMongo } from '../agent-harness/support/harness-mongo';
import { vaultBinderWorld } from './support/binding-world';
import { LegacyFacts, legacyFacts, LINEAR, migrationDeps } from './support/capability-world';
import { ORG_A } from './support/cp-objects';
import { InMemoryChangeOutbox } from './support/in-memory';

registerHarnessModels([
  CpOrgHeadModel,
  CpInstallationModel,
  CpInstallationHistoryModel,
  CpGrantModel,
  CpCapabilityLedgerModel,
  CpBindingModel,
] as unknown as Array<Model<never>>);

const SUBJECT = {
  orgId: ORG_A,
  actorId: 'u3',
  agentKey: 'a7',
  agentOwner: 'u1',
  projectId: null,
  chatMode: 'auto',
  surface: 'web',
  request: { schemaVersion: 1, scope: { kind: 'request' } },
} as unknown as PinSubject;

describe('CPD-7 the next pin after a capability write (ESR-12)', function () {
  this.timeout(120_000);

  before(connectHarnessMongo);
  before(async () => {
    await INSTALLATIONS_MIGRATION.up(mongoose.connection.db!);
    await BINDING_MIGRATION.up(mongoose.connection.db!);
  });
  after(disconnectHarnessMongo);
  beforeEach(resetHarnessMongo);

  async function world() {
    const transaction = () =>
      new MongoControlPlaneTransaction({
        audit: new ChainConfigAuditLog(new MongoAuditChainStore()),
        transaction: DURABLE_TRANSACTION_OPTIONS,
      });
    const stores = { installations: new MongoInstallationStore(), grants: new MongoGrantStore(), bindings: new MongoBindingStore() };
    const heads = new MongoHeadRevisions();
    let loads = 0;
    /** One Node instance: its own snapshot cache over the shared stores and head. */
    const node = () => {
      const load = policySnapshotLoader({ heads, capabilities: stores });
      const snapshots = new ResolvedSnapshotCache({
        load: (...subject) => {
          loads += 1;
          return load(...subject);
        },
        heads,
      });
      const pinner = new PolicyRunConfigPinner({
        snapshots,
        engine: new PolicyResolver(),
        effectiveSet: new CanonicalEffectiveSetResolver(),
        releases: releaseLayers(),
        prefix: prefixSources(),
        run: runSources(PARENT_DIMENSIONS),
        parent: PARENT_DIMENSIONS,
      });
      return async () => {
        const pin = await pinner.pin(SUBJECT);
        if ('code' in pin) throw new Error(`refused: ${pin.code}`);
        const item = pin.effectiveSet.items.find((candidate) => candidate.installation === LINEAR);
        return { head: pin.headRevision, linear: item === undefined ? 'absent' : (item.reason ?? 'ready') };
      };
    };
    const source = new LegacyFacts();
    source.orgs.set(ORG_A, legacyFacts());
    const outbox = new InMemoryChangeOutbox();
    const vault = vaultBinderWorld({
      bindings: stores.bindings,
      transaction: () => ({ transaction: transaction(), configEntries: async () => [], changeEvents: async () => outbox.events }),
    });
    const migration = new CapabilityMigration({
      ...migrationDeps(
        {
          installations: stores.installations,
          grants: stores.grants,
          ledger: new MongoCapabilityLedger(),
          transaction: transaction(),
          configEntries: async () => 0,
          credentials: new BindingService(stores.bindings, vault.binder),
        },
        source,
      ),
    });
    await migration.syncInstallations(ORG_A);
    await migration.syncAgent(ORG_A, 'a7');
    const installation = (await stores.installations.get(ORG_A, LINEAR))!;
    const binding = (
      await vault.binder.begin({ orgId: ORG_A, actor: 'user:u3' }, installation, 'user:u3', 'api_key', { kind: 'api_key', value: { apiKey: 'k1' } })
    ).binding;
    return { writer: node(), other: node(), heads, source, migration, vault, binding, loads: () => loads };
  }

  it('a warm pin reads the head and nothing else; each write moves the head, and both Node instances pin the new set', async () => {
    const w = await world();
    const first = await w.writer();
    expect(first.linear).to.equal('ready');
    expect(await w.other()).to.deep.equal(first);
    const warm = w.loads();
    expect(await w.writer()).to.deep.equal(first);
    expect(await w.other()).to.deep.equal(first);
    expect(w.loads()).to.equal(warm);

    // A binding revoked: the head moves in the revocation's transaction.
    await w.vault.binder.revoke({ orgId: ORG_A, actor: 'user:u3' }, w.binding.id, w.binding.revision);
    const revoked = await w.writer();
    expect(revoked.linear).to.equal('credential_revoked');
    expect(revoked.head).to.be.greaterThan(first.head);
    expect(await w.other()).to.deep.equal(revoked);

    // The agent's grant loses the installation.
    const facts = legacyFacts();
    w.source.orgs.set(ORG_A, {
      ...facts,
      agents: facts.agents.map((agent) => (agent.agentKey === 'a7' ? { ...agent, mcpServers: [] } : agent)),
    });
    await w.migration.syncAgent(ORG_A, 'a7');
    const ungranted = await w.other();
    expect(ungranted.linear).to.equal('absent');
    expect(ungranted.head).to.be.greaterThan(revoked.head);
    expect(await w.writer()).to.deep.equal(ungranted);
  });

  it('an uninstall: the next pin on either instance no longer has the installation ready, and every binding on it is revoked', async () => {
    const w = await world();
    const before = await w.other();
    expect(before.linear).to.equal('ready');
    const facts = legacyFacts();
    w.source.orgs.set(ORG_A, { ...facts, mcpInstances: facts.mcpInstances.filter((mcp) => mcp.instanceId !== LINEAR) });
    expect(await w.migration.syncInstallations(ORG_A)).to.include({ removed: 1, failed: null });

    const after = await w.other();
    expect(after.linear).to.not.equal('ready');
    expect(after.head).to.be.greaterThan(before.head);
    expect(await w.writer()).to.deep.equal(after);
    expect(await w.heads.head(ORG_A)).to.equal(after.head);
    expect((await new MongoBindingStore().get(ORG_A, w.binding.id))?.status).to.equal('revoked');
  });
});
