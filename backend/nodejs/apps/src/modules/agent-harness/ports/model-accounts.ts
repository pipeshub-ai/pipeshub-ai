import {
  ModelAccountKind,
  ModelAccountOwner,
  ModelAccountView,
} from '../domain/model-accounts';

export interface ModelAccountWrite {
  kind: ModelAccountKind;
  provider: string;
  label: string | null;
  /** `{provider, ...credentials}`: what the credential service hands the transport. */
  material: Record<string, string>;
  /** The secret whose last four characters reads may show. */
  hint: string;
}

/**
 * Where model accounts live (P2.4b). Writes are compare-and-set; reads
 * answer metadata only; the secret is read by the Python credential service.
 */
export interface ModelAccountStore {
  list(orgId: string, owner: ModelAccountOwner): Promise<ModelAccountView[]>;
  get(
    orgId: string,
    owner: ModelAccountOwner,
    id: string,
  ): Promise<ModelAccountView | null>;
  /** Adds the account or rotates it (a new version; the previous one stays readable for the handle TTL). */
  put(
    orgId: string,
    owner: ModelAccountOwner,
    id: string,
    write: ModelAccountWrite,
    actorId: string,
  ): Promise<ModelAccountView>;
  /** Revokes every version at once; false when there was nothing live. */
  revoke(
    orgId: string,
    owner: ModelAccountOwner,
    id: string,
    actorId: string,
  ): Promise<boolean>;
}

/** The container token user management binds its eraser under. */
export const MODEL_ACCOUNT_ERASER = 'ModelAccountEraser';

/** What user and org deletion call: nothing of an erased owner's accounts is kept. */
export interface ModelAccountEraser {
  eraseUser(orgId: string, userId: string): Promise<number>;
  eraseOrg(orgId: string): Promise<number>;
}
