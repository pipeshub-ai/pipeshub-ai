import { z } from 'zod/v3';
import {
  lastVersionOf,
  SecretCipher,
  vaultDocumentSchema,
  VaultKeyValueStore,
  VersionedSecretDocuments,
} from '../../../../libs/vault/versioned-secret-documents';
import { bindingPointerPath } from '../../domain/bindings';
import {
  ControlPlaneRetryableError,
  ControlPlaneUnavailableError,
} from '../../domain/errors';
import type {
  BindingVault,
  BindingVaultMeta,
  BindingVaultState,
  OAuthStateStore,
} from '../../ports/binding-vault';
import type { SecretMaterial } from '../../ports/credential-binder';

const metaSchema = z
  .object({
    binding: z.string(),
    installation: z.string(),
    owner: z.string(),
    mode: z.string(),
    materialKind: z.string(),
    kind: z.string().nullable(),
    provider: z.string().nullable(),
    source: z.object({ path: z.string(), digest: z.string() }).nullable(),
    importedVersion: z.number().int().nullable(),
  })
  .passthrough();

const documentSchema = vaultDocumentSchema.extend({
  binding: metaSchema.optional(),
});

const pointerSchema = z.object({ path: z.string() }).passthrough();

const unavailable = (message: string): Error =>
  new ControlPlaneUnavailableError(message);

/** Binding secrets in the vault: the P2.4b envelope with `binding` metadata (never a secret). */
export class KvBindingVault implements BindingVault {
  private readonly documents: VersionedSecretDocuments;

  constructor(
    store: VaultKeyValueStore,
    cipher: SecretCipher,
    now: () => number = () => Date.now(),
    dataCipher?: (keyHex: string) => SecretCipher,
  ) {
    this.documents = new VersionedSecretDocuments({
      store,
      cipher,
      now,
      dataCipher,
      unavailable,
    });
  }

  async state(path: string): Promise<BindingVaultState | null> {
    const { value } = await this.documents.read(path, documentSchema);
    if (value === null) return null;
    return {
      meta: (value.binding as BindingVaultMeta | undefined) ?? null,
      lastVersion: lastVersionOf(value),
      revoked: value.revoked !== undefined,
    };
  }

  async write(
    path: string,
    version: number,
    meta: BindingVaultMeta,
    material: SecretMaterial,
  ): Promise<void> {
    const sealed = await this.documents.seal(material.value);
    await this.documents.update(path, documentSchema, (current) => {
      const next = lastVersionOf(current ?? { versions: [] }) + 1;
      if (next !== version) {
        throw new ControlPlaneRetryableError(
          `vault ${path} is at version ${next - 1}, not ${version - 1}`,
        );
      }
      const { document } = this.documents.appended(
        current,
        sealed,
        this.documents.now(),
      );
      return { ...document, binding: { ...meta } };
    });
  }

  async tombstone(path: string, by: string): Promise<void> {
    await this.documents.update(path, documentSchema, (current) =>
      current === null || current.revoked !== undefined
        ? null
        : {
            ...this.documents.tombstoned(current, by, this.documents.now()),
            binding: current.binding,
          },
    );
  }

  async link(orgId: string, binding: string, path: string): Promise<void> {
    const pointer = bindingPointerPath(orgId, binding);
    if (await this.documents.create(pointer, { path })) return;
    const { value } = await this.documents.read(pointer, pointerSchema);
    if (value?.path !== path) {
      throw new ControlPlaneUnavailableError(
        `binding ${binding} points elsewhere`,
      );
    }
  }

  async unlink(orgId: string, binding: string): Promise<void> {
    await this.documents.delete(bindingPointerPath(orgId, binding));
  }

  async erase(orgId: string, binding: string, path: string): Promise<void> {
    await this.documents.delete(path);
    await this.documents.delete(bindingPointerPath(orgId, binding));
  }
}

export const OAUTH_STATES_ROOT = '/services/controlPlane/oauthStates';
/** How long an OAuth flow may take from the authorize redirect to the callback. */
export const OAUTH_STATE_TTL_MS = 600_000;

const stateSchema = z
  .object({ orgId: z.string(), binding: z.string(), expiresAt: z.number() })
  .strict();

/** Pending OAuth flows in the KV store; a state is taken once, by compare-and-set. */
export class KvOAuthStateStore implements OAuthStateStore {
  constructor(
    private readonly store: Pick<
      VaultKeyValueStore,
      'get' | 'compareAndSet' | 'delete'
    >,
    private readonly now: () => number = () => Date.now(),
  ) {}

  private path(state: string): string {
    return `${OAUTH_STATES_ROOT}/${state}`;
  }

  async issue(
    state: string,
    pending: { orgId: string; binding: string },
  ): Promise<void> {
    const value = JSON.stringify({
      ...pending,
      expiresAt: this.now() + OAUTH_STATE_TTL_MS,
    });
    if (
      !(await this.store.compareAndSet<string>(this.path(state), null, value))
    ) {
      throw new ControlPlaneUnavailableError('an OAuth state was issued twice');
    }
  }

  async take(
    state: string,
  ): Promise<{ orgId: string; binding: string } | null> {
    const path = this.path(state);
    const stored = await this.store.get<string>(path);
    if (typeof stored !== 'string' || stored === '') return null;
    if (!(await this.store.compareAndSet<string>(path, stored, '')))
      return null;
    await this.store.delete(path);
    const parsed = stateSchema.safeParse(JSON.parse(stored));
    if (!parsed.success || parsed.data.expiresAt <= this.now()) return null;
    return { orgId: parsed.data.orgId, binding: parsed.data.binding };
  }
}
