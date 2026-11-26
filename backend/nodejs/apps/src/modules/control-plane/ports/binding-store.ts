import type { Binding, BindingOwnerRef } from '../domain/objects';
import type { Page, StoreTx } from './store-tx';

/** One page of a listing: after the cursor a previous page returned (`null`: from the start). */
export interface PageRequest {
  readonly after: string | null;
  readonly limit: number;
}

/**
 * Binding metadata, never a secret (03-interfaces §2.6): secret I/O is the
 * `CredentialBinder`'s, through the vault. One binding that is not revoked
 * per `(org, installation, owner)`; revoked ones stay as history, so an owner
 * who disconnected can bind again.
 */
export interface BindingStore {
  get(orgId: string, id: string): Promise<Binding | null>;
  /** The owner's binding that is not revoked; when there is none, the latest revoked one. */
  find(
    orgId: string,
    installation: string,
    owner: BindingOwnerRef,
  ): Promise<Binding | null>;
  listForOwner(orgId: string, owner: BindingOwnerRef): Promise<Binding[]>;
  /** `listForOwner` of each owner in turn, concatenated, in one store read (an admission reads three owners). */
  listForOwners(
    orgId: string,
    owners: readonly BindingOwnerRef[],
  ): Promise<Binding[]>;
  /**
   * The bindings made on one installation in the org, revoked ones included,
   * by owner and then by id: what its removal must revoke. `next` is the
   * cursor of the page after (opaque), null on the last.
   */
  listByInstallation(
    orgId: string,
    installation: string,
    page: PageRequest,
  ): Promise<Page<Binding>>;
  /**
   * `expectedRevision` null creates (revision 1); otherwise compare-and-set.
   * `ObjectExists` when the write would leave the owner two bindings that are
   * not revoked. Returns the new revision; also throws
   * `ControlPlaneRevisionConflictError`, `WrongOrg`, `InvalidObject`.
   */
  upsert(
    tx: StoreTx,
    binding: Binding,
    expectedRevision: number | null,
  ): Promise<number>;
  /**
   * Deletes the row at `expectedRevision` (compare-and-set): only the revert
   * of a migration import removes a binding; a revoke keeps it.
   */
  remove(tx: StoreTx, id: string, expectedRevision: number): Promise<void>;
  /**
   * Every org's `active` and `expiring` bindings expiring before `before`,
   * soonest first: the refresher only. With `claimable`, the ones no other
   * holder has a live refresh claim on at `now`.
   */
  dueForRefresh(
    before: number,
    limit: number,
    claimable?: RefreshClaimant,
  ): Promise<Binding[]>;
  /**
   * Claims the binding at `revision` for `holder` until `until` when nobody
   * else holds a live claim at `now` (QA23-F2: one refresher per binding, so
   * a rotating refresh token is spent once). A claim is not a change of the
   * binding: no revision, no audit; any `upsert` clears it. True when held.
   */
  claimRefresh(
    orgId: string,
    id: string,
    revision: number,
    claim: RefreshClaimant & { readonly until: number },
  ): Promise<boolean>;
}

/** A token refresher replica asking for bindings at `now` (epoch ms). */
export interface RefreshClaimant {
  readonly holder: string;
  readonly now: number;
}
