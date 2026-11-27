/**
 * P2.4b NV: model accounts in the credential vault, in the format the Python
 * vault reads (app/harness/credentials/vault.py). The cross-language half
 * (Node writes, Python reads, real Redis) is
 * backend/python/tests/unit/harness/credentials/test_node_vault_interop.py.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { EncryptionService } from '../../../src/libs/encryptor/encryptor';
import { z } from 'zod/v3';
import { EncryptedConfigDocumentStore } from '../../../src/modules/agent-harness/adapters/configuration/encrypted-config-document-store';
import { ModelAccountVault } from '../../../src/modules/agent-harness/adapters/configuration/model-account-vault';
import { HarnessUnavailableError } from '../../../src/modules/agent-harness/domain/errors';
import {
  VAULT_KEYRING_PATH,
  VAULT_RETENTION_MS,
  vaultPath,
} from '../../../src/modules/agent-harness/domain/model-accounts';
import { ModelAccountWrite } from '../../../src/modules/agent-harness/ports/model-accounts';
import { MemoryKv, platformCipher } from './support/model-accounts';

const OPENAI_KEY = 'sk-proj-' + 'NodeVaultTestKey0123456789abcdWXYZ';
const ROTATED_KEY = 'sk-proj-' + 'NodeVaultRotated0123456789abcdQRST';
const ALICE = { kind: 'user', userId: 'u1' } as const;

const write = (apiKey: string): ModelAccountWrite => ({
  kind: 'user_api_key',
  provider: 'openAI',
  label: null,
  material: { provider: 'openAI', apiKey },
  hint: apiKey,
});

const open = (stored: string): Record<string, any> => JSON.parse(platformCipher.decrypt(stored));

describe('P2.4b model-account vault (NV)', () => {
  let kv: MemoryKv;
  let now: number;
  let vault: ModelAccountVault;

  beforeEach(() => {
    kv = new MemoryKv();
    now = Date.parse('2026-10-03T12:00:00.000Z');
    vault = new ModelAccountVault(kv, platformCipher, () => now);
  });

  it('NV-1: no plaintext key reaches the store; the version opens with its keyring key', async () => {
    const view = await vault.put('org-1', ALICE, 'openAI', write(OPENAI_KEY), 'u1');
    expect(view).to.deep.include({ id: 'openAI', provider: 'openAI', last4: 'WXYZ', version: 1, validation: 'unvalidated' });
    expect(view.accountRef).to.deep.equal({ kind: 'user_api_key', accountId: 'users/u1/openAI' });
    expect(await kv.dump()).to.not.include(OPENAI_KEY);
    const document = open((await kv.raw.getKey(vaultPath('org-1', 'users/u1/openAI')))!);
    const ring = open((await kv.raw.getKey(VAULT_KEYRING_PATH))!);
    const [entry] = document.versions;
    expect(entry).to.include({ version: '1', keyVersion: ring.active, supersededAt: null });
    const material = JSON.parse(EncryptionService.withKey('aes-256-gcm', ring.keys[entry.keyVersion]).decrypt(entry.ciphertext));
    expect(material).to.deep.equal({ provider: 'openAI', apiKey: OPENAI_KEY });
    expect(JSON.stringify(document.account)).to.not.include(OPENAI_KEY);
  });

  it('NV-2: the keyring is provisioned once, two writers converge, and a Python-written keyring reads', async () => {
    const other = new ModelAccountVault(kv, platformCipher, () => now);
    await Promise.all([
      vault.put('org-1', ALICE, 'openAI', write(OPENAI_KEY), 'u1'),
      other.put('org-1', { kind: 'user', userId: 'u2' }, 'openAI', write(ROTATED_KEY), 'u2'),
    ]);
    const ring = open((await kv.raw.getKey(VAULT_KEYRING_PATH))!);
    expect(Object.keys(ring.keys)).to.deep.equal(['1']);
    for (const name of ['users/u1/openAI', 'users/u2/openAI']) {
      const [entry] = open((await kv.raw.getKey(vaultPath('org-1', name)))!).versions;
      expect(() => EncryptionService.withKey('aes-256-gcm', ring.keys['1']).decrypt(entry.ciphertext)).to.not.throw();
    }

    const python = new MemoryKv();
    const pythonRing = { active: '3', keys: { '3': 'ab'.repeat(32) } };
    await python.raw.createKey(VAULT_KEYRING_PATH, JSON.stringify(platformCipher.encrypt(JSON.stringify(pythonRing))));
    const view = await new ModelAccountVault(python, platformCipher, () => now).put('org-1', ALICE, 'openAI', write(OPENAI_KEY), 'u1');
    expect(view.version).to.equal(1);
    expect(open((await python.raw.getKey(vaultPath('org-1', 'users/u1/openAI')))!).versions[0].keyVersion).to.equal('3');
  });

  it('NV-3: a lost compare-and-set is retried and no version is lost', async () => {
    await vault.put('org-1', ALICE, 'openAI', write(OPENAI_KEY), 'u1');
    kv.casFailures = 2;
    const view = await vault.put('org-1', ALICE, 'openAI', write(ROTATED_KEY), 'u1');
    expect(view.version).to.equal(2);
    const versions = open((await kv.raw.getKey(vaultPath('org-1', 'users/u1/openAI')))!).versions;
    expect(versions.map((v: { version: string }) => v.version)).to.deep.equal(['1', '2']);
    kv.casFailures = 10;
    let error: unknown;
    try {
      await vault.put('org-1', ALICE, 'openAI', write(OPENAI_KEY), 'u1');
    } catch (caught) {
      error = caught;
    }
    expect(error).to.be.instanceOf(HarnessUnavailableError);
  });

  it('NV-4: a revoked account is a tombstone that keeps its last version; a new key continues it', async () => {
    await vault.put('org-1', ALICE, 'openAI', write(OPENAI_KEY), 'u1');
    await vault.put('org-1', ALICE, 'openAI', write(ROTATED_KEY), 'u1');
    expect(await vault.revoke('org-1', ALICE, 'openAI', 'u1')).to.equal(true);
    expect(await vault.revoke('org-1', ALICE, 'openAI', 'u1')).to.equal(false);
    const tombstone = open((await kv.raw.getKey(vaultPath('org-1', 'users/u1/openAI')))!);
    expect(tombstone).to.deep.equal({ versions: [], lastVersion: 2, revoked: { at: '2026-10-03T12:00:00.000Z', by: 'u1' } });
    expect(await vault.list('org-1', ALICE)).to.deep.equal([]);
    const again = await vault.put('org-1', ALICE, 'openAI', write(OPENAI_KEY), 'u1');
    expect(again.version).to.equal(3);
    expect(again.createdAt).to.equal(again.rotatedAt);
  });

  it('NV-5: a replaced version stays for the retention, then goes', async () => {
    await vault.put('org-1', ALICE, 'openAI', write(OPENAI_KEY), 'u1');
    now += 1_000;
    const rotated = await vault.put('org-1', ALICE, 'openAI', write(ROTATED_KEY), 'u1');
    expect(rotated).to.deep.include({ version: 2, last4: 'QRST', createdAt: '2026-10-03T12:00:00.000Z', rotatedAt: '2026-10-03T12:00:01.000Z' });
    let versions = open((await kv.raw.getKey(vaultPath('org-1', 'users/u1/openAI')))!).versions;
    expect(versions.map((v: { version: string }) => v.version)).to.deep.equal(['1', '2']);
    now += VAULT_RETENTION_MS + 1;
    await vault.put('org-1', ALICE, 'openAI', write(OPENAI_KEY), 'u1');
    versions = open((await kv.raw.getKey(vaultPath('org-1', 'users/u1/openAI')))!).versions;
    expect(versions.map((v: { version: string }) => v.version)).to.deep.equal(['2', '3']);
  });

  it('NV-6: erasing a user or an org reaches exactly its prefix', async () => {
    await vault.put('org-1', ALICE, 'openAI', write(OPENAI_KEY), 'u1');
    await vault.put('org-1', { kind: 'user', userId: 'u10' }, 'openAI', write(OPENAI_KEY), 'u10');
    await vault.put('org-1', { kind: 'org' }, 'acct-1', { ...write(OPENAI_KEY), kind: 'org_cloud' }, 'admin');
    await vault.put('org-10', ALICE, 'openAI', write(OPENAI_KEY), 'u1');
    expect(await vault.eraseUser('org-1', 'u1')).to.equal(1);
    expect(await vault.list('org-1', { kind: 'user', userId: 'u10' })).to.have.length(1);
    expect(await vault.eraseOrg('org-1')).to.equal(2);
    expect(await kv.raw.listKeysInDirectory(`${vaultPath('org-1')}/`)).to.deep.equal([]);
    expect(await vault.list('org-10', ALICE)).to.have.length(1);
  });

  it('FB-4: every encrypted harness document reads a value the Python store wrote, and CAS keeps its raw revision', async () => {
    const store = new EncryptedConfigDocumentStore(kv, platformCipher, '/services/agentHarness/engineProfiles', z.object({ orgs: z.record(z.unknown()) }));
    const pythonWritten = JSON.stringify(platformCipher.encrypt(JSON.stringify({ orgs: {} })));
    await kv.raw.createKey('/services/agentHarness/engineProfiles', pythonWritten);
    const read = await store.read();
    expect(read).to.deep.equal({ value: { orgs: {} }, revision: pythonWritten });
    expect(await store.replace(read.revision, { orgs: { 'org-1': {} } })).to.equal(true);
    expect((await store.read()).value).to.deep.equal({ orgs: { 'org-1': {} } });
  });

  it('NR-9 (store half): a KV outage is unavailable, never an empty list', async () => {
    kv.down = true;
    for (const call of [
      () => vault.list('org-1', ALICE),
      () => vault.put('org-1', ALICE, 'openAI', write(OPENAI_KEY), 'u1'),
      () => vault.revoke('org-1', ALICE, 'openAI', 'u1'),
      () => vault.eraseUser('org-1', 'u1'),
    ]) {
      let error: unknown;
      try {
        await call();
      } catch (caught) {
        error = caught;
      }
      expect(error).to.be.instanceOf(HarnessUnavailableError);
    }
  });
});
