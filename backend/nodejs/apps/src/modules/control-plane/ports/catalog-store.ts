import type { CatalogItem } from '../domain/catalog-item';
import type { CatalogFilter, CatalogSortKey } from '../domain/catalog-order';
import type { Page, StoreTx } from './store-tx';

export type { Page };

/** What a catalog write needs of the control-plane transaction. */
export type CatalogWriteTx = StoreTx;

export interface CatalogQuery extends CatalogFilter {
  readonly cursor?: string | null;
  readonly limit: number;
}

export type CatalogItemSummary = Pick<
  CatalogItem,
  | 'ref'
  | 'kind'
  | 'publisher'
  | 'name'
  | 'version'
  | 'orgId'
  | 'provenance'
  | 'display'
  | 'riskClass'
  | 'deprecated'
>;

export interface ItemVersion {
  readonly ref: string;
  readonly version: string;
  readonly createdAt: number;
  readonly deprecated: boolean;
}

/**
 * Immutable catalog item versions of every kind (03-interfaces §2.2): shared
 * release rows plus each org's own. No update: a new version is a new ref;
 * `deprecate` is the one flag a version may gain.
 */
export interface CatalogStore {
  get(orgId: string, ref: string): Promise<CatalogItem | null>;
  list(orgId: string, query: CatalogQuery): Promise<Page<CatalogItemSummary>>;
  versions(
    orgId: string,
    kind: string,
    publisher: string,
    name: string,
  ): Promise<ItemVersion[]>;
  /** Throws `ItemExists`, `DigestMismatch`, `SharedItemReadOnly`, `OrgMismatch`, `InvalidCatalogItem`. */
  insert(tx: CatalogWriteTx, item: CatalogItem): Promise<void>;
  /** Throws `ItemNotFound`, `SharedItemReadOnly`, `OrgMismatch`. */
  deprecate(tx: CatalogWriteTx, orgId: string, ref: string): Promise<void>;
}

/** Rows one adapter holds, read in catalog order (`CatalogStore` composes them). */
export interface CatalogRows {
  get(orgId: string, ref: string): Promise<CatalogItem | null>;
  /** Items matching `filter` after `after`, in catalog order, at most `limit`. */
  page(
    orgId: string,
    filter: CatalogFilter,
    after: CatalogSortKey | null,
    limit: number,
  ): Promise<CatalogItem[]>;
  /** Oldest first. */
  versions(
    orgId: string,
    kind: string,
    publisher: string,
    name: string,
  ): Promise<CatalogItem[]>;
}

/** An org's own rows: the only writable part of the catalog. */
export interface OrgCatalogRows extends CatalogRows {
  /** Throws `ItemExists` when the ref is already stored for the org. */
  insert(tx: CatalogWriteTx, item: CatalogItem): Promise<void>;
  /** False when the org has no such row. */
  deprecate(tx: CatalogWriteTx, orgId: string, ref: string): Promise<boolean>;
}
