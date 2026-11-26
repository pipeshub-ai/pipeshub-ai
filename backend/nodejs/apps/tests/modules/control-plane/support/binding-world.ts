/**
 * CPD-8 test support: `VaultCredentialBinder` over a binding store, the
 * KV vault (an in-memory KV store under the platform cipher) and an audited
 * control-plane transaction, with readers that open what the vault stored.
 */
import { EncryptionService } from '../../../../src/libs/encryptor/encryptor';
import {
  storedCiphertext,
  VAULT_KEYRING_PATH,
} from '../../../../src/libs/vault/versioned-secret-documents';
import { ChainConfigAuditLog } from '../../../../src/modules/agent-harness/adapters/chain-config-audit-log';
import { InMemoryAuditChainStore } from '../../../../src/modules/agent-harness/adapters/in-memory-audit';
import { KvBindingVault, KvOAuthStateStore } from '../../../../src/modules/control-plane/adapters/kv/kv-binding-vault';
import { VaultCredentialBinder } from '../../../../src/modules/control-plane/application/vault-credential-binder';
import type { ChangeEvent, ControlPlaneTransaction } from '../../../../src/modules/control-plane';
import type { BindingStore } from '../../../../src/modules/control-plane/ports/binding-store';
import type { BindingVault, OAuthStart } from '../../../../src/modules/control-plane/ports/binding-vault';
import type { SecretMaterial } from '../../../../src/modules/control-plane/ports/credential-binder';
import { MemoryKv, platformCipher } from '../../agent-harness/support/model-accounts';
import { ulid } from './cp-objects';
import { FailingConfigAuditLog, InMemoryChangeOutbox, InMemoryControlPlaneTransaction } from './in-memory';
import { InMemoryBindingStateView } from './in-memory-services';
import type { CredentialBinderWorld } from './credential-binder-contract';

/** Authorize URLs of a test OAuth provider, for installations of `kind`. */
export const testOAuthStart = (kind: string): OAuthStart => ({
  kind,
  authorizeUrl: async (_orgId, installation, _owner, state) =>
    `https://auth.example.test/authorize?client_id=${installation}&state=${state}`,
});

type Json = Record<string, any>;
const open = (stored: string): Json => JSON.parse(platformCipher.decrypt(storedCiphertext(stored)));

export interface VaultBinderWorld extends CredentialBinderWorld {
  readonly binder: VaultCredentialBinder;
  readonly kv: MemoryKv;
  readonly clock: { now: number };
  /** The stored document as the KV store holds it, opened with the platform cipher; null when absent. */
  document(path: string): Promise<Json | null>;
  /** The version's material, opened with its keyring key. */
  versionMaterial(path: string, version: number): Promise<unknown>;
  pointer(orgId: string, binding: string): Promise<Json | null>;
}

export interface VaultBinderOptions {
  failingAudit?: boolean;
  bindings: BindingStore;
  /** Decorates the vault (a concurrent writer, a failing effect). */
  wrapVault?: (vault: BindingVault) => BindingVault;
  /** The transaction to write through; an audited in-memory one when absent. */
  transaction?: (audit: { failing: boolean }) => {
    transaction: ControlPlaneTransaction;
    configEntries(orgId: string): Promise<unknown[]>;
    changeEvents(): Promise<ChangeEvent[]>;
  };
}

function inMemoryTransaction({ failing }: { failing: boolean }) {
  const chain = new InMemoryAuditChainStore();
  const outbox = new InMemoryChangeOutbox();
  const audit = failing ? new FailingConfigAuditLog() : new ChainConfigAuditLog(chain);
  return {
    transaction: new InMemoryControlPlaneTransaction({ audit, outbox }),
    configEntries: async (orgId: string) =>
      (chain.records.get(orgId) ?? []).filter((record) => record.streamId === `cp:${orgId}`),
    changeEvents: async () => outbox.events,
  };
}

export function vaultBinderWorld(options: VaultBinderOptions): VaultBinderWorld {
  const kv = new MemoryKv();
  const clock = { now: Date.parse('2026-10-10T12:00:00.000Z') };
  const control = (options.transaction ?? inMemoryTransaction)({ failing: options.failingAudit === true });
  let ids = 0;
  const binder = new VaultCredentialBinder({
    transaction: control.transaction,
    bindings: options.bindings,
    vault: (options.wrapVault ?? ((vault) => vault))(new KvBindingVault(kv, platformCipher, () => clock.now)),
    states: new KvOAuthStateStore(kv, () => clock.now),
    oauthStarts: [testOAuthStart('mcp')],
    newId: () => ulid((ids += 1)),
    now: () => clock.now,
  });
  const document = async (path: string): Promise<Json | null> => {
    const stored = await kv.raw.getKey(path);
    return typeof stored === 'string' ? open(stored) : null;
  };
  const versionMaterial = async (path: string, version: number): Promise<unknown> => {
    const doc = await document(path);
    const ring = open((await kv.raw.getKey(VAULT_KEYRING_PATH))!);
    const entry = doc?.versions.find((v: Json) => v.version === String(version));
    if (entry === undefined) return null;
    return JSON.parse(EncryptionService.withKey('aes-256-gcm', ring.keys[entry.keyVersion]).decrypt(entry.ciphertext));
  };
  const vaultRead = async (path: string): Promise<SecretMaterial | null> => {
    const doc = await document(path);
    if (doc === null || doc.revoked !== undefined || doc.versions.length === 0) return null;
    const value = (await versionMaterial(path, doc.lastVersion)) as Record<string, unknown>;
    return { kind: doc.binding.materialKind, value };
  };
  return {
    binder,
    kv,
    clock,
    view: new InMemoryBindingStateView(options.bindings),
    bindings: options.bindings,
    vaultRead,
    configEntries: control.configEntries,
    changeEvents: control.changeEvents,
    document,
    versionMaterial,
    pointer: async (orgId, binding) => document(`/services/controlPlane/bindings/${orgId}/${binding}`),
  };
}
