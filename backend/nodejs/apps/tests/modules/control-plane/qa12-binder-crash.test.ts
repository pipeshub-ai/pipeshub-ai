/**
 * QA-12 (CG-12, TSC-G5): the binder's migration writes when the process dies
 * between the vault effect and the commit. `tx.external` runs before the
 * commit, so a crash there leaves the vault written and the binding row not
 * (or the row kept and the vault erased). A rerun must converge, never leave
 * a binding without its secret or a secret no row names. A native binding is
 * still refused by a revert (BM-6).
 */
import { expect } from 'chai';
import type { WriteContext } from '../../../src/modules/control-plane';
import type { SecretMaterial } from '../../../src/modules/control-plane/ports/credential-binder';
import { ChainConfigAuditLog } from '../../../src/modules/agent-harness/adapters/chain-config-audit-log';
import { InMemoryAuditChainStore } from '../../../src/modules/agent-harness/adapters/in-memory-audit';
import { InMemoryChangeOutbox, InMemoryControlPlaneTransaction } from './support/in-memory';
import { InMemoryBindingStore } from './support/in-memory-stores';
import { instId, ORG_A } from './support/cp-objects';
import { vaultBinderWorld } from './support/binding-world';

const migration: WriteContext = { orgId: ORG_A, actor: 'service:credential-migration' };
const record = (accessToken: string): SecretMaterial => ({
  kind: 'record',
  value: { isAuthenticated: true, credentials: { access_token: accessToken, refresh_token: 'r-1' } },
  expiresAt: 9_000,
});
const imported = {
  installation: instId(1),
  owner: 'user:u3' as const,
  mode: 'oauth' as const,
  kind: 'toolset',
  provider: 'slack',
  material: record('qa12-access-token-0123456789'),
  source: { path: `/services/toolsets/${instId(1)}/u3`, digest: 'sha256:' + 'a'.repeat(64) },
};

/** A binder world whose transaction dies once, after its vault effects and before its commit, when armed. */
function crashingWorld() {
  const crash = { armed: false };
  const world = vaultBinderWorld({
    bindings: new InMemoryBindingStore(),
    transaction: () => {
      const chain = new InMemoryAuditChainStore();
      return {
        transaction: new InMemoryControlPlaneTransaction({
          audit: new ChainConfigAuditLog(chain),
          outbox: new InMemoryChangeOutbox(),
          beforeCommit: () => {
            if (!crash.armed) return;
            crash.armed = false;
            throw new Error('process died before the commit');
          },
        }),
        configEntries: async (orgId: string) => (chain.records.get(orgId) ?? []).filter((r) => r.streamId === `cp:${orgId}`),
        changeEvents: async () => [],
      };
    },
  });
  return { world, crash };
}

const rows = async (world: ReturnType<typeof crashingWorld>['world']) =>
  world.bindings.find(ORG_A, instId(1), 'user:u3');

describe('QA-12 binder writes interrupted between the vault and the commit', () => {
  it('QA12-6a: an import that died after its vault write is completed by the rerun: one live binding, its secret readable', async () => {
    const { world, crash } = crashingWorld();
    crash.armed = true;
    let failed = false;
    await world.binder.importRecord(migration, imported).catch(() => (failed = true));
    expect(failed).to.equal(true);
    expect(await rows(world)).to.equal(null);
    const again = await world.binder.importRecord(migration, imported);
    expect(again.outcome).to.equal('created');
    const row = (await rows(world))!;
    expect(row.status).to.equal('active');
    expect((await world.vaultRead(row.secret!.path))?.value).to.deep.equal(imported.material.value);
    expect(await world.pointer(ORG_A, row.id)).to.deep.equal({ path: row.secret!.path });
  });

  it('QA12-6b: a revert that died after erasing the vault is completed by the rerun (no row left without its secret)', async () => {
    const { world, crash } = crashingWorld();
    const { binding } = await world.binder.importRecord(migration, imported);
    crash.armed = true;
    let failed = false;
    await world.binder.revertImport(migration, instId(1), 'user:u3').catch(() => (failed = true));
    expect(failed).to.equal(true);
    expect(await world.document(binding!.secret!.path)).to.equal(null);
    const finished = await world.binder.revertImport(migration, instId(1), 'user:u3');
    expect(finished).to.deep.include({ changedSinceImport: false });
    expect(await rows(world)).to.equal(null);
    expect(await world.pointer(ORG_A, binding!.id)).to.equal(null);
    expect(await world.binder.revertImport(migration, instId(1), 'user:u3')).to.equal(null);
  });

});
