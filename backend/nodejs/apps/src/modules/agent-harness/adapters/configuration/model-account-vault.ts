import { z } from 'zod/v3';
import {
  SecretCipher,
  vaultDocumentSchema,
  VaultKeyValueStore,
  VersionedSecretDocuments,
  lastVersionOf,
} from '../../../../libs/vault/versioned-secret-documents';
import { HarnessUnavailableError } from '../../domain/errors';
import {
  accountKindOf,
  accountName,
  lastFour,
  ModelAccountOwner,
  ModelAccountView,
  ownerDirectory,
  vaultPath,
} from '../../domain/model-accounts';
import {
  ModelAccountEraser,
  ModelAccountStore,
  ModelAccountWrite,
} from '../../ports/model-accounts';
import { configurationCipher } from './encrypted-config-document-store';

export type { VaultKeyValueStore };

const accountSchema = z
  .object({
    kind: z.enum(['user_api_key', 'org_cloud']),
    provider: z.string(),
    label: z.string().nullable(),
    last4: z.string(),
    createdAt: z.string(),
    createdBy: z.string(),
    rotatedAt: z.string(),
    rotatedBy: z.string(),
    validation: z.literal('unvalidated'),
  })
  .passthrough();
type VaultAccount = z.infer<typeof accountSchema>;

const documentSchema = vaultDocumentSchema.extend({
  account: accountSchema.optional(),
});
type VaultDocument = z.infer<typeof documentSchema>;

/**
 * Model accounts in the credential vault, in the format the Python vault
 * reads (`app/harness/credentials/vault.py`): each version encrypted with a
 * data key of the vault keyring, the document and the keyring encrypted
 * again with the platform cipher. Writes are compare-and-set on the stored
 * ciphertext, so concurrent writers never lose a version; the keyring is
 * provisioned the same way, so replicas converge on one.
 */
export class ModelAccountVault
  implements ModelAccountStore, ModelAccountEraser
{
  private readonly documents: VersionedSecretDocuments;

  constructor(
    store: VaultKeyValueStore,
    cipher: SecretCipher,
    now: () => number = () => Date.now(),
    dataCipher?: (keyHex: string) => SecretCipher,
    newKey?: () => string,
  ) {
    this.documents = new VersionedSecretDocuments({
      store,
      cipher,
      now,
      dataCipher,
      newKey,
      unavailable: (message) => new HarnessUnavailableError(message),
    });
  }

  private read(path: string): Promise<VaultDocument | null> {
    return this.documents
      .read(path, documentSchema)
      .then((stored) => stored.value);
  }

  private view(
    owner: ModelAccountOwner,
    id: string,
    document: VaultDocument | null,
  ): ModelAccountView | null {
    if (
      document === null ||
      document.revoked !== undefined ||
      document.versions.length === 0 ||
      document.account === undefined
    ) {
      return null;
    }
    const account = document.account;
    return {
      id,
      accountRef: { kind: account.kind, accountId: accountName(owner, id) },
      provider: account.provider,
      label: account.label,
      last4: account.last4,
      version: lastVersionOf(document),
      createdAt: account.createdAt,
      rotatedAt: account.rotatedAt,
      validation: account.validation,
    };
  }

  async list(
    orgId: string,
    owner: ModelAccountOwner,
  ): Promise<ModelAccountView[]> {
    const directory = `${vaultPath(orgId, ownerDirectory(owner))}/`;
    const views: ModelAccountView[] = [];
    for (const path of await this.documents.list(directory)) {
      const id = path.slice(directory.length);
      if (id.includes('/')) continue;
      const view = this.view(owner, id, await this.read(path));
      if (view !== null) views.push(view);
    }
    return views.sort((a, b) =>
      a.createdAt.localeCompare(b.createdAt) !== 0
        ? a.createdAt.localeCompare(b.createdAt)
        : a.id.localeCompare(b.id),
    );
  }

  async get(
    orgId: string,
    owner: ModelAccountOwner,
    id: string,
  ): Promise<ModelAccountView | null> {
    const path = vaultPath(orgId, accountName(owner, id));
    return this.view(owner, id, await this.read(path));
  }

  async put(
    orgId: string,
    owner: ModelAccountOwner,
    id: string,
    write: ModelAccountWrite,
    actorId: string,
  ): Promise<ModelAccountView> {
    const path = vaultPath(orgId, accountName(owner, id));
    const sealed = await this.documents.seal(write.material);
    const stored = await this.documents.update(
      path,
      documentSchema,
      (current) => {
        const now = this.documents.now();
        const at = new Date(now).toISOString();
        const previous: VaultAccount | undefined =
          current?.revoked === undefined ? current?.account : undefined;
        const { document } = this.documents.appended(current, sealed, now);
        return {
          ...document,
          account: {
            kind: accountKindOf(owner),
            provider: write.provider,
            label: write.label,
            last4: lastFour(write.hint),
            createdAt: previous?.createdAt ?? at,
            createdBy: previous?.createdBy ?? actorId,
            rotatedAt: at,
            rotatedBy: actorId,
            validation: 'unvalidated' as const,
          },
        };
      },
      'A model account changed concurrently',
    );
    const view = this.view(owner, id, stored);
    if (view === null) {
      throw new HarnessUnavailableError('The model account was not stored');
    }
    return view;
  }

  async revoke(
    orgId: string,
    owner: ModelAccountOwner,
    id: string,
    actorId: string,
  ): Promise<boolean> {
    const path = vaultPath(orgId, accountName(owner, id));
    const stored = await this.documents.update(
      path,
      documentSchema,
      (current) =>
        current === null ||
        current.revoked !== undefined ||
        current.versions.length === 0
          ? null
          : this.documents.tombstoned(current, actorId, this.documents.now()),
    );
    return stored !== null;
  }

  private async erase(directory: string): Promise<number> {
    const paths = await this.documents.list(`${directory}/`);
    for (const path of paths) {
      await this.documents.delete(path);
    }
    return paths.length;
  }

  eraseUser(orgId: string, userId: string): Promise<number> {
    return this.erase(
      vaultPath(orgId, ownerDirectory({ kind: 'user', userId })),
    );
  }

  eraseOrg(orgId: string): Promise<number> {
    return this.erase(vaultPath(orgId));
  }
}

/**
 * The vault over the deployment's KV store, under the platform cipher. The
 * cipher is looked up at first use: user management builds the vault (for
 * erasure) before anything has read the configuration secret.
 */
export function createModelAccountVault(
  store: VaultKeyValueStore,
  cipher?: SecretCipher,
): ModelAccountVault {
  const platform = (): SecretCipher => cipher ?? configurationCipher();
  return new ModelAccountVault(store, {
    encrypt: (text) => platform().encrypt(text),
    decrypt: (text) => platform().decrypt(text),
  });
}
