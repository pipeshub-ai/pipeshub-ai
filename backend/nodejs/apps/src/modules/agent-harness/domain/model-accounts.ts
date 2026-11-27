import type { ModelAccount } from '../contracts/generated/ext/pipeshub/api/model-account';
import type { ModelAccountKind } from '../contracts/generated/ext/pipeshub/enums';
import type { ErrorCodeOf } from '../contracts/generated/ext/pipeshub/errors';

/**
 * Model accounts (P2.4b): a user's own API key per provider, and an org's
 * cloud accounts (Azure OpenAI deployments, Bedrock, Vertex). The secret
 * lives in the credential vault the Python credential service reads
 * (`app/harness/credentials/vault.py`); reads here answer metadata only.
 * Python's names for the same things: `harness_core.access.types`
 * (`user_account_ref`, `org_account_ref`, `ModelAccount`).
 */

export const VAULT_ROOT = '/services/agentHarness/credentials';
export {
  VAULT_KEYRING_PATH,
  VAULT_RETENTION_MS,
} from '../../../libs/vault/versioned-secret-documents';

export type { ModelAccountKind };

export type ModelAccountOwner =
  { kind: 'user'; userId: string } | { kind: 'org' };

/** What reads answer (contracts/harness/ext/pipeshub/api/model-account.schema.json): never the secret. */
export type ModelAccountView = ModelAccount;

/** The catalogue's `model_account` group (ERR-UX). */
export type ModelAccountRefusalCode = ErrorCodeOf<'model_account'>;

const SEGMENT = /^[A-Za-z0-9._:@-]{1,128}$/;

export const isPathSegment = (value: string): boolean =>
  SEGMENT.test(value) && value !== '.' && value !== '..';

/** Python `last_four`: never more than a quarter of the secret. */
export const lastFour = (value: string): string =>
  value.length >= 16 ? value.slice(-4) : '';

/** The vault name of an owner's directory (Python `_directory`). */
export const ownerDirectory = (owner: ModelAccountOwner): string =>
  owner.kind === 'org' ? 'cloud' : `users/${owner.userId}`;

export const accountName = (owner: ModelAccountOwner, id: string): string =>
  `${ownerDirectory(owner)}/${id}`;

export const accountKindOf = (owner: ModelAccountOwner): ModelAccountKind =>
  owner.kind === 'org' ? 'org_cloud' : 'user_api_key';

/** `/services/agentHarness/credentials/{orgId}[/{name}]`, every segment path-safe. */
export function vaultPath(orgId: string, name?: string): string {
  const segments = [orgId, ...(name === undefined ? [] : name.split('/'))];
  if (!segments.every(isPathSegment)) {
    throw new Error('Vault names are path-safe segments');
  }
  return [VAULT_ROOT, ...segments].join('/');
}
