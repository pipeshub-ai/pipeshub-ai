/**
 * CPD-7 with CPD-8 (MG-11): removing an installation revokes every binding
 * made on it, through `BindingService.revokeInstallation` over the real
 * `VaultCredentialBinder` and KV vault: each revocation is the binder's own
 * audited write and tombstones the secret, before the installation goes.
 * A run that pinned the binding is then refused by the tombstone (the Python
 * broker's `CredentialRevoked`, CBR-2), an installation re-created under the
 * same id reads `credential_revoked`, and the census of what the uninstall
 * leaves finds no secret material and no live binding.
 */
import { expect } from 'chai';
import { CanonicalEffectiveSetResolver, CapabilityMigration } from '../../../src/modules/control-plane';
import { BindingService } from '../../../src/modules/control-plane/application/binding-service';
import type { Binding } from '../../../src/modules/control-plane/domain/objects';
import { vaultBinderWorld, type VaultBinderWorld } from './support/binding-world';
import { CUSTOM_MCP, LegacyFacts, legacyFacts, migrationDeps, type CapabilityWorld } from './support/capability-world';
import { aGrant, anEntry, ORG_A, ORG_B } from './support/cp-objects';
import { auditedControlPlane } from './support/in-memory';
import { InMemoryBindingStore, InMemoryCapabilityLedger, InMemoryGrantStore, InMemoryInstallationStore } from './support/in-memory-stores';
import { aSnapshot, aSubject } from './support/resolution-fixtures';

const CREDENTIALS = '/services/controlPlane/credentials';
const KEY = { kind: 'api_key', value: { apiKey: 'k1' } } as const;

function world() {
  const installations = new InMemoryInstallationStore();
  const grants = new InMemoryGrantStore();
  const controlPlane = auditedControlPlane();
  const store = new InMemoryBindingStore();
  const vault: VaultBinderWorld = vaultBinderWorld({
    bindings: store,
    transaction: () => ({
      transaction: controlPlane.transaction,
      configEntries: async (orgId) => controlPlane.configEntries(orgId),
      changeEvents: async () => controlPlane.outbox.events,
    }),
  });
  const service = new BindingService(store, vault.binder);
  const capability: CapabilityWorld = {
    installations,
    grants,
    ledger: new InMemoryCapabilityLedger(installations, grants),
    transaction: controlPlane.transaction,
    configEntries: async (orgId) => controlPlane.configEntries(orgId).length,
    credentials: service,
  };
  const bindingsOf = async (orgId = ORG_A): Promise<Binding[]> => [
    ...(await store.listByInstallation(orgId, CUSTOM_MCP, { after: null, limit: 100 })).items,
  ];
  /** What the uninstall leaves of the installation's credentials: vault documents that still hold a version, and bindings not revoked. */
  const census = async (): Promise<{ secrets: string[]; live: string[] }> => {
    const paths = (await vault.kv.raw.listKeysInDirectory(`${CREDENTIALS}/${ORG_A}/${CUSTOM_MCP}/`)) as string[];
    const secrets: string[] = [];
    for (const path of paths) {
      const document = await vault.document(path);
      if (document !== null && (document.revoked === undefined || document.versions.length > 0)) secrets.push(path);
    }
    return { secrets, live: (await bindingsOf()).filter((b) => b.status !== 'revoked').map((b) => b.id) };
  };
  return { capability, store, vault, service, controlPlane, bindingsOf, census };
}

const withoutCustomMcp = () => {
  const facts = legacyFacts();
  return { ...facts, mcpInstances: facts.mcpInstances.filter((mcp) => mcp.instanceId !== CUSTOM_MCP) };
};

async function installed(w: ReturnType<typeof world>) {
  const source = new LegacyFacts();
  source.orgs.set(ORG_A, legacyFacts());
  const migration = new CapabilityMigration(migrationDeps(w.capability, source));
  await migration.syncInstallations(ORG_A);
  const installation = (await w.capability.installations.get(ORG_A, CUSTOM_MCP))!;
  const bind = async (user: string) =>
    (await w.vault.binder.begin({ orgId: ORG_A, actor: `user:${user}` }, installation, `user:${user}`, 'api_key', KEY)).binding;
  return { source, migration, bind };
}

describe('CPD-7 an installation removed revokes its bindings (MG-11)', () => {
  it('removal revokes every binding through the binder, audited, and tombstones each secret', async () => {
    const w = world();
    const { source, migration, bind } = await installed(w);
    const [u3, u4] = [await bind('u3'), await bind('u4')];
    expect(await w.vault.vaultRead(u3.secret!.path)).to.deep.equal(KEY);
    const before = w.controlPlane.configEntries(ORG_A).length;

    source.orgs.set(ORG_A, withoutCustomMcp());
    expect(await migration.syncInstallations(ORG_A)).to.include({ removed: 1, failed: null });

    expect((await w.bindingsOf()).map((b) => [b.id, b.status])).to.deep.equal([
      [u3.id, 'revoked'],
      [u4.id, 'revoked'],
    ]);
    // Two revocations and the installation's removal: one audited write each.
    expect(w.controlPlane.configEntries(ORG_A).length - before).to.equal(3);
    expect((await w.capability.installations.get(ORG_A, CUSTOM_MCP))?.status).to.equal('removed');
  });

  it('a run that pinned the binding before the uninstall is refused by its tombstone, not handed a secret', async () => {
    const w = world();
    const { source, migration, bind } = await installed(w);
    const pinned = await bind('u3');
    source.orgs.set(ORG_A, withoutCustomMcp());
    await migration.syncInstallations(ORG_A);
    // What the Python broker reads for the pin: the pointer still names the path, and the document there is a tombstone.
    const pointer = await w.vault.pointer(ORG_A, pinned.id);
    expect(pointer).to.deep.equal({ path: pinned.secret!.path });
    const document = (await w.vault.document(pinned.secret!.path))!;
    expect(document.revoked).to.have.keys(['at', 'by']);
    expect(document.versions).to.deep.equal([]);
    expect(await w.vault.vaultRead(pinned.secret!.path)).to.equal(null);
  });

  it('the census after an uninstall: no secret material and no live binding of the installation; another org is untouched', async () => {
    const w = world();
    const { source, migration, bind } = await installed(w);
    await bind('u3');
    await bind('u4');
    expect((await w.census()).secrets).to.have.length(2);
    source.orgs.set(ORG_A, withoutCustomMcp());
    await migration.syncInstallations(ORG_A);
    expect(await w.census()).to.deep.equal({ secrets: [], live: [] });
    expect(await w.bindingsOf(ORG_B)).to.deep.equal([]);
  });

  it('re-created under the same id, the installation cannot use the old binding', async () => {
    const w = world();
    const { source, migration, bind } = await installed(w);
    const old = await bind('u3');
    source.orgs.set(ORG_A, withoutCustomMcp());
    await migration.syncInstallations(ORG_A);
    source.orgs.set(ORG_A, legacyFacts());
    await migration.syncInstallations(ORG_A);
    const recreated = (await w.capability.installations.get(ORG_A, CUSTOM_MCP))!;
    expect(recreated.status).to.equal('enabled');
    const approved = {
      ...recreated,
      credentialMode: 'per_user' as const,
      approved: { descriptorSetDigest: `sha256:${'1'.repeat(64)}`, tools: { ping: `sha256:${'2'.repeat(64)}` }, approvalRef: null, at: 1, by: 'system' },
    };
    const set = new CanonicalEffectiveSetResolver().resolve(
      aSubject({ agentKey: 'a8' }),
      { schemaVersion: 1, dimensions: {} },
      aSnapshot({
        installations: [approved],
        grants: [aGrant({ subject: 'agent:a8', entries: [anEntry(CUSTOM_MCP)] })],
        bindings: await w.bindingsOf(),
        agentKey: 'a8',
      }),
    );
    expect('items' in set && set.items[0]!.reason).to.equal('credential_revoked');
    expect((await w.store.get(ORG_A, old.id))?.status).to.equal('revoked');
    expect(await w.vault.vaultRead(old.secret!.path)).to.equal(null);
  });

  it('uninstall, then the same server installed again: a new installation id (legacy instance ids are generated per creation), a user binds it and it is ready; the old tombstone stays', async () => {
    const w = world();
    const { source, migration, bind } = await installed(w);
    const old = await bind('u3');
    const REINSTALLED = '1f2e3d4c-5b6a-4978-8695-a4b3c2d1e0f9';
    const again = legacyFacts().mcpInstances.find((mcp) => mcp.instanceId === CUSTOM_MCP)!;
    source.orgs.set(ORG_A, { ...withoutCustomMcp(), mcpInstances: [...withoutCustomMcp().mcpInstances, { ...again, instanceId: REINSTALLED, authMode: 'api_key' }] });
    expect(await migration.syncInstallations(ORG_A)).to.include({ created: 1, removed: 1, failed: null });

    const fresh = (await w.capability.installations.get(ORG_A, REINSTALLED))!;
    expect([fresh.status, fresh.credentialMode]).to.deep.equal(['enabled', 'per_user']);
    const rebound = (await w.vault.binder.begin({ orgId: ORG_A, actor: 'user:u3' }, fresh, 'user:u3', 'api_key', KEY)).binding;
    expect(rebound.secret!.path).to.not.equal(old.secret!.path);
    expect(await w.vault.vaultRead(rebound.secret!.path)).to.deep.equal(KEY);
    const approved = { ...fresh, approved: { descriptorSetDigest: `sha256:${'1'.repeat(64)}`, tools: { ping: `sha256:${'2'.repeat(64)}` }, approvalRef: null, at: 1, by: 'system' } };
    const set = new CanonicalEffectiveSetResolver().resolve(
      aSubject({ agentKey: 'a8' }),
      { schemaVersion: 1, dimensions: {} },
      aSnapshot({ installations: [approved], grants: [aGrant({ subject: 'agent:a8', entries: [anEntry(REINSTALLED)] })], bindings: [rebound], agentKey: 'a8' }),
    );
    expect('items' in set && set.items.map((item) => [item.reason, item.binding?.ref])).to.deep.equal([[null, rebound.id]]);
    // The uninstalled one keeps its tombstone and its revoked row.
    expect(await w.vault.vaultRead(old.secret!.path)).to.equal(null);
    expect((await w.store.get(ORG_A, old.id))?.status).to.equal('revoked');
    expect((await w.capability.installations.get(ORG_A, CUSTOM_MCP))?.status).to.equal('removed');
  });

  it('MG-18: where an id does come back (a revert, then apply again), the user binds again: a new binding at a new version; the old one stays revoked and its id reaches nothing', async () => {
    const w = world();
    const { migration, bind } = await installed(w);
    await migration.runFor('apply', [ORG_A]);
    const old = await bind('u3');
    await migration.runFor('revert', [ORG_A]);
    await migration.runFor('apply', [ORG_A]);
    const recreated = (await w.capability.installations.get(ORG_A, CUSTOM_MCP))!;
    const SECOND = { kind: 'api_key', value: { apiKey: 'k2' } } as const;
    const rebound = (await w.vault.binder.begin({ orgId: ORG_A, actor: 'user:u3' }, recreated, 'user:u3', 'api_key', SECOND)).binding;
    expect(rebound.id).to.not.equal(old.id);
    expect(rebound.secret!.version).to.equal(old.secret!.version + 1);
    expect(await w.vault.vaultRead(rebound.secret!.path)).to.deep.equal(SECOND);
    expect((await w.bindingsOf()).map((b) => [b.id, b.status])).to.deep.equal([
      [old.id, 'revoked'],
      [rebound.id, 'active'],
    ]);
    // A run that pinned the old binding follows its pointer: there is none now, so it cannot reach the new secret.
    expect(await w.vault.pointer(ORG_A, old.id)).to.equal(null);
    expect(await w.vault.pointer(ORG_A, rebound.id)).to.deep.equal({ path: rebound.secret!.path });
    const document = (await w.vault.document(rebound.secret!.path))!;
    expect(document.revoked).to.equal(undefined);
    expect(document.versions.map((v: { version: string }) => v.version)).to.deep.equal([String(rebound.secret!.version)]);
  });

  it('revert revokes the bindings of every installation it removes', async () => {
    const w = world();
    const { migration, bind } = await installed(w);
    await migration.runFor('apply', [ORG_A]);
    await bind('u3');
    await migration.runFor('revert', [ORG_A]);
    expect(await w.capability.installations.get(ORG_A, CUSTOM_MCP)).to.equal(null);
    expect((await w.bindingsOf()).map((b) => b.status)).to.deep.equal(['revoked']);
    expect(await w.census()).to.deep.equal({ secrets: [], live: [] });
  });

  it('a revocation that fails part-way leaves the installation installed; the next sync finishes it, revoking nothing twice', async () => {
    const w = world();
    const { source, bind } = await installed(w);
    const [u3, u4] = [await bind('u3'), await bind('u4')];
    let failures = 1;
    const flaky = {
      revokeInstallation: (ctx: Parameters<BindingService['revokeInstallation']>[0], installation: string) =>
        new BindingService(w.store, {
          revoke: (c: typeof ctx, id: string, revision: number) => {
            if (id === u4.id && failures > 0) {
              failures -= 1;
              return Promise.reject(new Error('vault down'));
            }
            return w.vault.binder.revoke(c, id, revision);
          },
        } as never).revokeInstallation(ctx, installation),
    };
    const migration = new CapabilityMigration({ ...migrationDeps(w.capability, source), credentials: flaky });
    source.orgs.set(ORG_A, withoutCustomMcp());
    const failed = await migration.syncInstallations(ORG_A);
    expect(failed.failed).to.not.equal(null);
    expect((await w.capability.installations.get(ORG_A, CUSTOM_MCP))?.status).to.equal('enabled');
    expect((await w.bindingsOf()).map((b) => b.status)).to.deep.equal(['revoked', 'active']);
    const revision = (await w.store.get(ORG_A, u3.id))!.revision;

    expect(await migration.syncInstallations(ORG_A)).to.include({ removed: 1, failed: null });
    expect(await w.census()).to.deep.equal({ secrets: [], live: [] });
    expect((await w.store.get(ORG_A, u3.id))!.revision).to.equal(revision);
  });

  it('revokeInstallation pages through more bindings than one read takes', async () => {
    const w = world();
    const { bind } = await installed(w);
    for (let user = 0; user < 105; user += 1) await bind(`u${String(user).padStart(3, '0')}`);
    const revoked = await w.service.revokeInstallation({ orgId: ORG_A, actor: 'system' }, CUSTOM_MCP);
    expect(revoked).to.have.length(105);
    expect(await w.census()).to.deep.equal({ secrets: [], live: [] });
    expect(await w.service.revokeInstallation({ orgId: ORG_A, actor: 'system' }, CUSTOM_MCP)).to.deep.equal([]);
  });
});
