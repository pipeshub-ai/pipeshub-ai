import type { BindingMode, BindingOwnerRef } from '../domain/objects';
import type { SecretMaterial } from './credential-binder';

/** Where an imported binding's secret was copied from, and that record's digest. */
export interface BindingSource {
  readonly path: string;
  readonly digest: string;
}

/** A binding's vault metadata: never a secret. */
export interface BindingVaultMeta {
  readonly binding: string;
  readonly installation: string;
  readonly owner: BindingOwnerRef;
  readonly mode: BindingMode;
  readonly materialKind: SecretMaterial['kind'];
  /** The installation's kind (`toolset`, `mcp`) and provider, which pick the refresh strategy. */
  readonly kind: string | null;
  readonly provider: string | null;
  /** Set by a migration import; a revert removes only bindings that have it. */
  readonly source: BindingSource | null;
  readonly importedVersion: number | null;
}

export interface BindingVaultState {
  readonly meta: BindingVaultMeta | null;
  readonly lastVersion: number;
  readonly revoked: boolean;
}

/**
 * The binding secrets in the credential vault (02-lld §4.3): one versioned,
 * encrypted document per installation and owner, and a pointer from the
 * binding id to it for readers that hold only the pinned reference.
 */
export interface BindingVault {
  /** The document's state; null when there is none. */
  state(path: string): Promise<BindingVaultState | null>;
  /**
   * Appends `material` as version `version`, which must be the next one;
   * otherwise `ControlPlaneRetryableError` (the document moved: plan again).
   */
  write(
    path: string,
    version: number,
    meta: BindingVaultMeta,
    material: SecretMaterial,
    by: string,
  ): Promise<void>;
  /** Every version unreadable at once; numbering continues after it. */
  tombstone(path: string, by: string): Promise<void>;
  link(orgId: string, binding: string, path: string): Promise<void>;
  /** Removes a binding's pointer (it was superseded): its id no longer reaches the document. */
  unlink(orgId: string, binding: string): Promise<void>;
  /** Deletes the document and the pointer (the revert of an import). */
  erase(orgId: string, binding: string, path: string): Promise<void>;
}

/** A pending OAuth flow, single use. */
export interface OAuthStateStore {
  issue(
    state: string,
    pending: { orgId: string; binding: string },
  ): Promise<void>;
  /** The pending flow, removed; null when never issued, already used or expired. */
  take(state: string): Promise<{ orgId: string; binding: string } | null>;
}

/** Builds an installation kind's authorize URL (03-interfaces §3.1: one per domain module). */
export interface OAuthStart {
  readonly kind: string;
  authorizeUrl(
    orgId: string,
    installation: string,
    owner: BindingOwnerRef,
    state: string,
  ): Promise<string>;
}
