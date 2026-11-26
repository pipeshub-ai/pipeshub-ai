import type { PolicyLayer } from '../domain/policy-alias';
import type { StoreTx } from './store-tx';

export interface VersionedLayer {
  readonly layer: PolicyLayer | null;
  readonly revision: number;
}

/** One scope of a `readMany`: its layer, or `CP_UNAVAILABLE` when what is stored does not parse. */
export type LayerRead =
  | ({ readonly status: 'ok' } & VersionedLayer)
  | { readonly status: 'unavailable'; readonly code: 'CP_UNAVAILABLE' };

export interface PolicyRevision {
  readonly revision: number;
  readonly layer: PolicyLayer | null;
}

/**
 * Policy layers per scope with history (03-interfaces §2.5). It only persists:
 * narrowing and reference checks are `PolicyService`'s. A scope never written
 * reads as revision 0 with no layer. Scopes are org-relative (`team:t9` of org
 * A is not org B's), so every method takes the org.
 */
export interface PolicyStore {
  /** Throws `ControlPlaneUnavailableError` when the stored layer does not parse (that scope only, NFR-13). */
  read(orgId: string, scope: string): Promise<VersionedLayer>;
  /** One round trip; keyed by scope. */
  readMany(
    orgId: string,
    scopes: readonly string[],
  ): Promise<Map<string, LayerRead>>;
  /** `null` deletes the layer; either way a history row is recorded. Returns the new revision. */
  write(
    tx: StoreTx,
    scope: string,
    layer: PolicyLayer | null,
    expectedRevision: number,
  ): Promise<number>;
  /** Newest first, at most `limit`. */
  history(
    orgId: string,
    scope: string,
    limit: number,
  ): Promise<PolicyRevision[]>;
}

/** The scopes an org has layers at, by kind (an org's agent layers, for its admins' views). */
export interface PolicyScopeIndex {
  /** Sorted; a deleted layer is not listed. */
  scopesOf(orgId: string, kind: string): Promise<string[]>;
}

/**
 * Where an org's layers are served from while the five KV documents are read
 * through (CPD-4): the store, the legacy documents, or the legacy documents
 * with a section today's readers refuse (which the store will not copy).
 */
export type PolicyOrgSource = 'store' | 'legacy' | 'legacy-invalid';

/** One layer a write replaces, at the revision its writer read. */
export interface LayerWrite {
  readonly scope: string;
  readonly layer: PolicyLayer | null;
  readonly expectedRevision: number;
}

/** The policy store the settings adapters write through. */
export interface PolicyLayers extends PolicyStore, PolicyScopeIndex {
  orgSource(orgId: string): Promise<PolicyOrgSource>;
  /** Several layers of the transaction's org in one write; scope → its new revision. */
  writeMany(
    tx: StoreTx,
    writes: readonly LayerWrite[],
  ): Promise<Map<string, number>>;
  /**
   * Every org's org layer, in org order, skipping deleted and unreadable
   * ones: the one cross-org read, for deployment-wide jobs (the retention
   * sweep). Paged underneath, so memory stays bounded.
   */
  orgLayers(): AsyncIterable<{ orgId: string; layer: PolicyLayer }>;
}

/** An org's layers as the legacy KV documents hold them, keyed by scope. */
export interface LegacyLayers {
  readonly layers: ReadonlyMap<string, PolicyLayer>;
  /** Scope → the sections at it that today's readers refuse; such a scope is never copied. */
  readonly invalid: ReadonlyMap<string, readonly string[]>;
}

/** The five KV documents (02-lld §11.1), read as policy layers. */
export interface LegacyPolicySource {
  /** Every org any document names. Throws when a document cannot be read. */
  orgs(): Promise<string[]>;
  /** Throws when a document cannot be read: an unreadable store is never "no policy". */
  layersOf(orgId: string): Promise<LegacyLayers>;
}

/** Removes every layer and history row of an org (the migration's revert only). */
export interface PolicyStorePurge {
  purge(tx: StoreTx, orgId: string): Promise<void>;
}
