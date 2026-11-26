/**
 * CPD-8: `VaultCredentialBinder` over the KV vault. CP-SKEL's binder suite
 * (CB-1..5, BV-1), the vault's format and layout (VB-1..5) and the
 * maintenance writes the migration, the legacy routes and the token
 * refresher make (BM-1..9). The Mongo half is binding-store.mongo.itest.ts.
 */
import { expect } from 'chai';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { canonicalDigest } from '../../../src/libs/utils/canonical-json';
import { ulid } from '../../../src/libs/utils/ulid';
import { VAULT_KEYRING_PATH } from '../../../src/libs/vault/versioned-secret-documents';
import {
  ControlPlaneRevisionConflictError,
  ControlPlaneUnavailableError,
  type WriteContext,
} from '../../../src/modules/control-plane';
import { KvOAuthStateStore } from '../../../src/modules/control-plane/adapters/kv/kv-binding-vault';
import { BindingNotImported, OAuthStartUnsupported } from '../../../src/modules/control-plane/domain/binding-errors';
import { UnknownOAuthState } from '../../../src/modules/control-plane/domain/store-errors';
import { bindingVersionDigestInput } from '../../../src/modules/control-plane/domain/bindings';
import type { SecretMaterial } from '../../../src/modules/control-plane/ports/credential-binder';
import { assertNoCanary, VECTOR_EXAMPLES } from '../agent-harness/support/canary-scan';
import { MemoryKv, platformCipher } from '../agent-harness/support/model-accounts';
import { describeCredentialBinderContract } from './support/credential-binder-contract';
import { anInstallation, bindingId, instId, ORG_A, rejection } from './support/cp-objects';
import { InMemoryBindingStore } from './support/in-memory-stores';
import { vaultBinderWorld } from './support/binding-world';

describeCredentialBinderContract('VaultCredentialBinder over the KV vault', {
  async make(options = {}) {
    return vaultBinderWorld({ bindings: new InMemoryBindingStore(), failingAudit: options.failingAudit });
  },
});

const user: WriteContext = { orgId: ORG_A, actor: 'user:u3' };
const migration: WriteContext = { orgId: ORG_A, actor: 'service:credential-migration' };
const refresher: WriteContext = { orgId: ORG_A, actor: 'service:token-refresher' };
const SECRET = VECTOR_EXAMPLES.find((example) => example.kind === 'github_token')!.secret;
const apiKey = (value = SECRET): SecretMaterial => ({ kind: 'api_key', value: { apiKey: value } });
const LEGACY = 'legacy-oauth-access-' + 'Z'.repeat(24);
const record = (accessToken = LEGACY): SecretMaterial => ({
  kind: 'record',
  value: { isAuthenticated: true, credentials: { access_token: accessToken, refresh_token: 'r-1', expires_in: 3600 } },
  expiresAt: 9_000,
});
const source = (digest: string) => ({ path: `/services/toolsets/${instId(1)}/u3`, digest });
const imported = (material = record(), digest = 'sha256:' + 'a'.repeat(64)) => ({
  installation: instId(1),
  owner: 'user:u3' as const,
  mode: 'oauth' as const,
  kind: 'toolset',
  provider: 'slack',
  material,
  source: source(digest),
});

describe('CPD-8 binding vault (VB)', () => {
  it('VB-1: no plaintext secret reaches the store; each version opens with its keyring key', async () => {
    const world = vaultBinderWorld({ bindings: new InMemoryBindingStore() });
    const { binding } = await world.binder.begin(user, anInstallation(), 'user:u3', 'api_key', apiKey());
    expect(await world.kv.dump()).to.not.include(SECRET);
    const doc = (await world.document(binding.secret!.path))!;
    const ring = JSON.parse(platformCipher.decrypt((await world.kv.raw.getKey(VAULT_KEYRING_PATH))!));
    expect(doc.versions[0]).to.include({ version: '1', keyVersion: ring.active, supersededAt: null });
    expect(await world.versionMaterial(binding.secret!.path, 1)).to.deep.equal({ apiKey: SECRET });
    assertNoCanary('vault metadata', doc.binding, { secrets: [SECRET], shapes: 'vendor' });
  });

  it('VB-2: the 02-lld §4.3 layout, a pointer from the binding, metadata without a secret', async () => {
    const world = vaultBinderWorld({ bindings: new InMemoryBindingStore() });
    const user7 = await world.binder.begin(user, anInstallation(), 'user:u7', 'api_key', apiKey());
    const agent = await world.binder.begin(user, anInstallation({ id: instId(2) }), 'agent:a1', 'api_key', apiKey());
    const org = await world.binder.begin(user, anInstallation({ id: instId(3) }), 'org', 'api_key', apiKey());
    const root = `/services/controlPlane/credentials/${ORG_A}`;
    expect(user7.binding.secret!.path).to.equal(`${root}/${instId(1)}/user/u7`);
    expect(agent.binding.secret!.path).to.equal(`${root}/${instId(2)}/agent/a1`);
    expect(org.binding.secret!.path).to.equal(`${root}/${instId(3)}/org/org`);
    expect(await world.pointer(ORG_A, user7.binding.id)).to.deep.equal({ path: user7.binding.secret!.path });
    expect((await world.document(user7.binding.secret!.path))!.binding).to.deep.equal({
      binding: user7.binding.id,
      installation: instId(1),
      owner: 'user:u7',
      mode: 'api_key',
      materialKind: 'api_key',
      kind: 'mcp',
      provider: null,
      source: null,
      importedVersion: null,
    });
  });

  it('VB-3: the version digest is canonicalDigest({binding, version}), held to the shared vector', async () => {
    const world = vaultBinderWorld({ bindings: new InMemoryBindingStore() });
    const { binding } = await world.binder.begin(user, anInstallation(), 'user:u3', 'api_key', apiKey());
    const rotated = await world.binder.setSecret(user, binding.id, apiKey('rotated-key-1'), binding.revision);
    expect(binding.secret!.versionDigest).to.equal(canonicalDigest({ binding: binding.id, version: 1 }));
    expect(rotated.secret!.versionDigest).to.equal(canonicalDigest({ binding: binding.id, version: 2 }));
    const vector = JSON.parse(
      readFileSync(join(__dirname, '../../../../../../contracts/control-plane/vectors/binding-version-digests.json'), 'utf8'),
    ) as { cases: Array<{ binding: string; version: number; digest: string }> };
    expect(vector.cases.length).to.be.greaterThan(1);
    for (const row of vector.cases) {
      expect(canonicalDigest(bindingVersionDigestInput(row.binding, row.version)), row.binding).to.equal(row.digest);
    }
  });

  it('VB-4: a revoke tombstones the document and a later write continues the numbering', async () => {
    const world = vaultBinderWorld({ bindings: new InMemoryBindingStore() });
    const { binding } = await world.binder.begin(user, anInstallation(), 'user:u3', 'api_key', apiKey());
    const rotated = await world.binder.setSecret(user, binding.id, apiKey('rotated-key-1'), binding.revision);
    const revoked = await world.binder.revoke(user, binding.id, rotated.revision);
    const doc = (await world.document(binding.secret!.path))!;
    expect(doc).to.deep.include({ versions: [], lastVersion: 2 });
    expect(doc.revoked).to.deep.equal({ at: new Date(world.clock.now).toISOString(), by: 'user:u3' });
    expect(doc.binding.binding).to.equal(binding.id);
    const reconnected = await world.binder.setSecret(user, binding.id, apiKey('after-revoke-key'), revoked.revision);
    expect(reconnected).to.deep.include({ status: 'active' });
    expect(reconnected.secret!.version).to.equal(3);
    expect((await world.document(binding.secret!.path))!.revoked).to.equal(undefined);
  });

  it('VB-5: a document that moved under a write re-plans the whole write; no version is lost', async () => {
    let raced = false;
    const world = vaultBinderWorld({
      bindings: new InMemoryBindingStore(),
      wrapVault: (vault) => ({
        state: (path) => vault.state(path),
        tombstone: (path, by) => vault.tombstone(path, by),
        link: (orgId, binding, path) => vault.link(orgId, binding, path),
        unlink: (orgId, binding) => vault.unlink(orgId, binding),
        erase: (orgId, binding, path) => vault.erase(orgId, binding, path),
        async write(path, version, meta, material, by) {
          if (version === 2 && !raced) {
            raced = true;
            await vault.write(path, version, meta, apiKey('another-writer-key'), 'user:other');
          }
          await vault.write(path, version, meta, material, by);
        },
      }),
    });
    const { binding } = await world.binder.begin(user, anInstallation(), 'user:u3', 'api_key', apiKey());
    const rotated = await world.binder.setSecret(user, binding.id, apiKey('rotated-key-1'), binding.revision);
    expect(raced).to.equal(true);
    expect(rotated.secret!.version).to.equal(3);
    expect(await world.versionMaterial(binding.secret!.path, 2)).to.deep.equal({ apiKey: 'another-writer-key' });
    expect(await world.vaultRead(binding.secret!.path)).to.deep.equal(apiKey('rotated-key-1'));
    expect(await world.configEntries(ORG_A)).to.have.length(2);
  });
});

describe('CPD-8 OAuth starts and states (VB-7)', () => {
  it('VB-7: a kind with no registered start cannot begin an OAuth flow, and nothing is written', async () => {
    const world = vaultBinderWorld({ bindings: new InMemoryBindingStore() });
    const refused = await rejection(
      world.binder.begin(user, anInstallation({ kind: 'toolset' }), 'user:u3', 'oauth'),
    );
    expect(refused).to.be.instanceOf(OAuthStartUnsupported);
    expect(await world.bindings.listForOwner(ORG_A, 'user:u3')).to.deep.equal([]);
    expect(await world.configEntries(ORG_A)).to.deep.equal([]);
  });

  it('VB-7: an OAuth state expires ten minutes after it was issued', async () => {
    const world = vaultBinderWorld({ bindings: new InMemoryBindingStore() });
    const start = await world.binder.begin(user, anInstallation(), 'user:u3', 'oauth');
    if (!('authorizeUrl' in start)) throw new Error('expected an authorize URL');
    world.clock.now += 600_000;
    const late = await rejection(
      world.binder.complete(user, start.state, { kind: 'oauth_tokens', value: { accessToken: SECRET } }),
    );
    expect(late).to.be.instanceOf(UnknownOAuthState);
    expect((await world.bindings.get(ORG_A, start.binding.id))?.status).to.equal('pending');
  });
});

describe('CPD-8 vault outage (VB-8)', () => {
  it('VB-8: with the KV store down a write is CP_UNAVAILABLE and no binding is stored', async () => {
    const world = vaultBinderWorld({ bindings: new InMemoryBindingStore() });
    world.kv.down = true;
    const refused = await rejection(
      world.binder.begin(user, anInstallation(), 'user:u3', 'api_key', apiKey()),
    );
    expect(refused).to.be.instanceOf(ControlPlaneUnavailableError);
    world.kv.down = false;
    expect(await world.bindings.listForOwner(ORG_A, 'user:u3')).to.deep.equal([]);
    expect(await world.configEntries(ORG_A)).to.deep.equal([]);
  });

  it('VB-8: an OAuth state store on the wall clock issues a state that is taken once', async () => {
    const states = new KvOAuthStateStore(new MemoryKv());
    await states.issue('state-1', { orgId: ORG_A, binding: bindingId(1) });
    expect(await states.take('state-1')).to.deep.equal({ orgId: ORG_A, binding: bindingId(1) });
    expect(await states.take('state-1')).to.equal(null);
  });
});

describe('CPD-8 binding ids (VB-6)', () => {
  it('VB-6: a ULID is 26 Crockford characters, its time first, so ids sort by creation', () => {
    const first = ulid(1_760_000_000_000, () => Buffer.alloc(10, 255));
    const later = ulid(1_760_000_000_001, () => Buffer.alloc(10, 0));
    expect(first).to.match(/^[0-9A-HJKMNP-TV-Z]{26}$/);
    expect(first.slice(10)).to.equal('Z'.repeat(16));
    expect(later.slice(10)).to.equal('0'.repeat(16));
    expect(first < later).to.equal(true);
    expect(`bnd_${ulid()}`).to.match(/^bnd_[0-9A-HJKMNP-TV-Z]{26}$/);
  });
});

describe('CPD-8 binding maintenance (BM)', () => {
  it('BM-1: an import holds the record as is, remembers its source and is audited once', async () => {
    const world = vaultBinderWorld({ bindings: new InMemoryBindingStore() });
    const { binding, outcome } = await world.binder.importRecord(migration, imported());
    expect(outcome).to.equal('created');
    expect(binding).to.deep.include({ status: 'active', mode: 'oauth', expiresAt: 9_000 });
    expect(binding!.secret!.version).to.equal(1);
    expect(await world.versionMaterial(binding!.secret!.path, 1)).to.deep.equal(record().value);
    expect((await world.document(binding!.secret!.path))!.binding).to.deep.include({
      kind: 'toolset',
      provider: 'slack',
      materialKind: 'record',
      source: source('sha256:' + 'a'.repeat(64)),
      importedVersion: 1,
    });
    const entries = (await world.configEntries(ORG_A)) as Array<{ actor?: string; payload?: unknown }>;
    expect(entries).to.have.length(1);
    expect(JSON.stringify(entries[0])).to.include('service:credential-migration');
    assertNoCanary('audit', entries, { secrets: [LEGACY], shapes: 'vendor' });
  });

  it('BM-2, BM-3: the same source again writes nothing; a changed one writes a new version', async () => {
    const world = vaultBinderWorld({ bindings: new InMemoryBindingStore() });
    await world.binder.importRecord(migration, imported());
    expect((await world.binder.importRecord(migration, imported())).outcome).to.equal('unchanged');
    expect(await world.configEntries(ORG_A)).to.have.length(1);
    const changed = await world.binder.importRecord(migration, imported(record('changed-access-token-1234567'), 'sha256:' + 'b'.repeat(64)));
    expect(changed.outcome).to.equal('updated');
    expect(changed.binding!.secret!.version).to.equal(2);
    expect((await world.document(changed.binding!.secret!.path))!.binding).to.deep.include({
      source: source('sha256:' + 'b'.repeat(64)),
      importedVersion: 2,
    });
  });

  it('BM-4: an import never resurrects a revoked binding, nor overwrites one it did not import', async () => {
    const world = vaultBinderWorld({ bindings: new InMemoryBindingStore() });
    const { binding } = await world.binder.importRecord(migration, imported());
    await world.binder.revoke(user, binding!.id, binding!.revision);
    const again = await world.binder.importRecord(migration, imported(record(), 'sha256:' + 'c'.repeat(64)));
    expect(again.outcome).to.equal('unchanged');
    expect(again.binding!.status).to.equal('revoked');
    const native = await world.binder.begin(user, anInstallation({ id: instId(2) }), 'user:u3', 'api_key', apiKey());
    const over = await world.binder.importRecord(migration, { ...imported(), installation: instId(2) });
    expect(over.outcome).to.equal('conflict');
    expect(await world.vaultRead(native.binding.secret!.path)).to.deep.equal(apiKey());
  });

  it('BM-5: a legacy save (no source) always writes; after a revoke it is a new binding, the revoked one kept and unreachable; the source is kept', async () => {
    const world = vaultBinderWorld({ bindings: new InMemoryBindingStore() });
    const { binding } = await world.binder.importRecord(migration, imported());
    const revoked = await world.binder.revoke(user, binding!.id, binding!.revision);
    const saved = await world.binder.importRecord(user, { ...imported(record('reconnected-access-token-123')), source: null });
    expect(saved.outcome).to.equal('created');
    expect(saved.binding).to.deep.include({ status: 'active', revision: 1 });
    expect(saved.binding!.id).to.not.equal(binding!.id);
    expect(saved.binding!.secret!.version).to.equal(binding!.secret!.version + 1);
    expect(await world.bindings.get(ORG_A, binding!.id)).to.deep.include({ status: 'revoked', revision: revoked.revision });
    // A run that pinned the revoked binding follows its pointer; the new binding has its own.
    expect(await world.pointer(ORG_A, binding!.id)).to.equal(null);
    expect(await world.pointer(ORG_A, saved.binding!.id)).to.deep.equal({ path: saved.binding!.secret!.path });
    expect((await world.document(binding!.secret!.path))!.binding).to.deep.include({
      binding: saved.binding!.id,
      source: source('sha256:' + 'a'.repeat(64)),
      importedVersion: 1,
    });
    const observed = source('sha256:' + 'e'.repeat(64));
    const again = await world.binder.importRecord(user, { ...imported(record('saved-again-token-0123456789')), source: null, observed });
    expect(again).to.deep.include({ outcome: 'updated' });
    expect(again.binding!.id).to.equal(saved.binding!.id);
    expect((await world.document(binding!.secret!.path))!.binding).to.deep.include({ source: observed, importedVersion: 1 });
  });

  it('BM-6: a revert removes an imported binding whole, and refuses a native one', async () => {
    const world = vaultBinderWorld({ bindings: new InMemoryBindingStore() });
    const { binding } = await world.binder.importRecord(migration, imported());
    await world.binder.refreshed(refresher, binding!.id, binding!.revision, record('refreshed-access-token-12345'));
    const reverted = await world.binder.revertImport(migration, instId(1), 'user:u3');
    expect(reverted).to.deep.include({ changedSinceImport: true });
    expect(await world.bindings.get(ORG_A, binding!.id)).to.equal(null);
    expect(await world.document(binding!.secret!.path)).to.equal(null);
    expect(await world.pointer(ORG_A, binding!.id)).to.equal(null);
    expect(await world.configEntries(ORG_A)).to.have.length(3);
    expect(await world.binder.revertImport(migration, instId(1), 'user:u3')).to.equal(null);
    const native = await world.binder.begin(user, anInstallation({ id: instId(2) }), 'user:u3', 'api_key', apiKey());
    expect(await rejection(world.binder.revertImport(migration, instId(2), 'user:u3'))).to.be.instanceOf(BindingNotImported);
    expect(await world.bindings.get(ORG_A, native.binding.id)).to.not.equal(null);
  });

  it('BM-7: refreshed writes a new version and clears the error; a stale revision leaves the vault alone', async () => {
    const world = vaultBinderWorld({ bindings: new InMemoryBindingStore() });
    const { binding } = await world.binder.importRecord(migration, imported());
    const failed = await world.binder.refreshFailed(refresher, binding!.id, binding!.revision, 'server_error', false);
    world.clock.now += 1_000;
    const fresh = await world.binder.refreshed(refresher, binding!.id, failed.revision, {
      ...record('refreshed-access-token-12345'),
      expiresAt: 20_000,
    });
    expect(fresh).to.deep.include({ status: 'active', lastError: null, expiresAt: 20_000, refreshedAt: world.clock.now });
    expect(fresh.secret!.version).to.equal(2);
    const stale = await rejection(world.binder.refreshed(refresher, binding!.id, failed.revision, record('lost-token-123456789')));
    expect(stale).to.be.instanceOf(ControlPlaneRevisionConflictError);
    expect((await world.document(binding!.secret!.path))!.lastVersion).to.equal(2);
    expect(JSON.stringify(await world.configEntries(ORG_A))).to.include('service:token-refresher');
  });

  it('BM-8: a permanent refresh failure takes the binding out of the scan; a transient one does not', async () => {
    const world = vaultBinderWorld({ bindings: new InMemoryBindingStore() });
    const { binding } = await world.binder.importRecord(migration, imported());
    const transient = await world.binder.refreshFailed(refresher, binding!.id, binding!.revision, 'server_error', false);
    expect(transient).to.deep.include({ status: 'active', lastError: { code: 'server_error', at: world.clock.now } });
    expect((await world.bindings.dueForRefresh(10_000, 10)).map((b) => b.id)).to.deep.equal([binding!.id]);
    const permanent = await world.binder.refreshFailed(refresher, binding!.id, transient.revision, 'invalid_grant', true);
    expect(permanent).to.deep.include({ status: 'error', lastError: { code: 'invalid_grant', at: world.clock.now } });
    expect(await world.bindings.dueForRefresh(10_000, 10)).to.deep.equal([]);
    const stale = await rejection(world.binder.refreshFailed(refresher, binding!.id, transient.revision, 'invalid_grant', true));
    expect(stale).to.be.instanceOf(ControlPlaneRevisionConflictError);
    expect((await world.changeEvents()).at(-1)).to.deep.include({ narrowing: true });
  });

  it('BM-9: revokeOwner revokes the owner\'s binding; none is null', async () => {
    const world = vaultBinderWorld({ bindings: new InMemoryBindingStore() });
    const { binding } = await world.binder.importRecord(migration, imported());
    const revoked = await world.binder.revokeOwner(user, instId(1), 'user:u3');
    expect(revoked).to.deep.include({ id: binding!.id, status: 'revoked' });
    expect(await world.vaultRead(binding!.secret!.path)).to.equal(null);
    expect(await world.binder.revokeOwner(user, instId(1), 'user:u9')).to.equal(null);
    expect(bindingId(1)).to.equal(binding!.id);
  });
});
