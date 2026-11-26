import type { CatalogItem } from '../domain/catalog-item';
import {
  CatalogFilter,
  CatalogSortKey,
  pageOf,
  versionsOf,
} from '../domain/catalog-order';
import type { CatalogRows } from '../ports/catalog-store';

/** The release catalog, shared by every org and read-only: built at boot from the domain modules. */
export class ReleaseCatalogStore implements CatalogRows {
  private readonly byRef: ReadonlyMap<string, CatalogItem>;

  constructor(items: readonly CatalogItem[]) {
    this.byRef = new Map(items.map((item) => [item.ref, item]));
  }

  get(_orgId: string, ref: string): Promise<CatalogItem | null> {
    return Promise.resolve(this.byRef.get(ref) ?? null);
  }

  page(
    _orgId: string,
    filter: CatalogFilter,
    after: CatalogSortKey | null,
    limit: number,
  ): Promise<CatalogItem[]> {
    return Promise.resolve(pageOf(this.byRef.values(), filter, after, limit));
  }

  versions(
    _orgId: string,
    kind: string,
    publisher: string,
    name: string,
  ): Promise<CatalogItem[]> {
    return Promise.resolve(
      versionsOf(
        [...this.byRef.values()].filter(
          (item) =>
            item.kind === kind &&
            item.publisher === publisher &&
            item.name === name,
        ),
      ),
    );
  }
}
