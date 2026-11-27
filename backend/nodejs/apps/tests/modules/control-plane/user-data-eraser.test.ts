/**
 * CLEANUP-G2c: a deleted user's control-plane data goes through the control
 * plane's own audited writes: their installations removed (with their
 * bindings), their connections on other installations revoked (secrets
 * tombstoned), their grant emptied. Another user's, the team's and the org's
 * stay; running it again changes nothing.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { createBindingService } from '../../../src/modules/control-plane';
import { ControlPlaneUserEraser } from '../../../src/modules/control-plane/application/user-data-eraser';
import { GrantAdmin } from '../../../src/modules/control-plane/application/grant-admin';
import { InstallationAdmin } from '../../../src/modules/control-plane/application/installation-admin';
import { MemoryKv, platformCipher } from '../agent-harness/support/model-accounts';
import { anInstallation, directTx, instId, ORG_A, ORG_B } from './support/cp-objects';
import { testControlPlane } from './support/in-memory';
import { InMemoryBindingStore, InMemoryGrantStore, InMemoryInstallationStore } from './support/in-memory-stores';

const SECRET = 'cleanup-g2c-api-token-0123456789abcdef';

async function world() {
  const transaction = testControlPlane();
  const installationStore = new InMemoryInstallationStore();
  const grantStore = new InMemoryGrantStore();
  const kv = new MemoryKv();
  const bindings = createBindingService({ transaction, kv, cipher: () => platformCipher, bindings: new InMemoryBindingStore() });
  const installations = new InstallationAdmin({
    catalog: {} as never,
    installations: installationStore,
    grants: grantStore,
    transaction,
    validator: () => null,
    newId: () => 'unused',
    now: () => 1,
  });
  const grants = new GrantAdmin({ grants: grantStore, installations: installationStore, transaction });
  const seed = [
    { id: instId(1), orgId: ORG_A, owner: 'user:u1' as const, visibility: 'owner' as const },
    { id: instId(2), orgId: ORG_A, owner: 'user:u1' as const, visibility: 'owner' as const },
    { id: instId(3), orgId: ORG_A, owner: 'user:u2' as const, visibility: 'owner' as const },
    { id: instId(4), orgId: ORG_A, owner: 'team:t1' as const, visibility: 'team' as const },
    { id: instId(5), orgId: ORG_A, owner: 'org' as const, visibility: 'org' as const },
    { id: instId(6), orgId: ORG_B, owner: 'user:u1' as const, visibility: 'owner' as const },
  ];
  for (const inst of seed) {
    await installationStore.create(directTx(inst.orgId), anInstallation(inst));
  }
  const connect = (orgId: string, installation: string, owner: `user:${string}`) =>
    bindings.importRecord(
      { orgId, actor: owner },
      {
        installation,
        owner,
        mode: 'api_key',
        kind: 'toolset',
        provider: null,
        material: { kind: 'record', value: { apiToken: SECRET } },
        source: null,
      },
    );
  await connect(ORG_A, instId(1), 'user:u1');
  await connect(ORG_A, instId(5), 'user:u1');
  await connect(ORG_A, instId(5), 'user:u2');
  await connect(ORG_B, instId(6), 'user:u1');
  const entry = (installation: string) => ({ installation, tools: null, required: false });
  await grants.put({ orgId: ORG_A, actor: 'system' }, 'user:u1', 0, [entry(instId(5))]);
  await grants.put({ orgId: ORG_A, actor: 'system' }, 'user:u2', 0, [entry(instId(5))]);
  return { installationStore, bindings, grants, eraser: new ControlPlaneUserEraser({ installations, bindings, grants }) };
}

describe('CLEANUP-G2c: a deleted user\'s control-plane data', () => {
  it('removes their installations, revokes their connections, empties their grant; nobody else\'s', async () => {
    const w = await world();
    expect(await w.eraser.eraseUser(ORG_A, 'u1')).to.deep.equal({ installations: 2, bindings: 2, grants: 1 });

    const status = async (orgId: string, n: number) => (await w.installationStore.get(orgId, instId(n)))?.status;
    expect(await status(ORG_A, 1)).to.equal('removed');
    expect(await status(ORG_A, 2)).to.equal('removed');
    for (const n of [3, 4, 5]) expect(await status(ORG_A, n), `inst ${String(n)}`).to.not.equal('removed');
    expect(await status(ORG_B, 6)).to.not.equal('removed');

    const mine = await w.bindings.list(ORG_A, 'user:u1');
    expect(mine.map((b) => b.status)).to.deep.equal(['revoked', 'revoked']);
    expect((await w.bindings.list(ORG_A, 'user:u2')).map((b) => b.status)).to.deep.equal(['active']);
    expect((await w.bindings.list(ORG_B, 'user:u1')).map((b) => b.status)).to.deep.equal(['active']);

    expect((await w.grants.get(ORG_A, 'user:u1')).entries).to.deep.equal([]);
    expect((await w.grants.get(ORG_A, 'user:u2')).entries).to.have.length(1);
  });

  it('a second run, as after a crash, changes nothing', async () => {
    const w = await world();
    await w.eraser.eraseUser(ORG_A, 'u1');
    expect(await w.eraser.eraseUser(ORG_A, 'u1')).to.deep.equal({ installations: 0, bindings: 0, grants: 0 });
  });
});
