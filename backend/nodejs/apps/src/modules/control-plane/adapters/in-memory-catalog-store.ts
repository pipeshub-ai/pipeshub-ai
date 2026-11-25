import type { CatalogItem } from '../domain/catalog-item';
import { ItemExists } from '../domain/catalog-errors';
import {
  CatalogFilter,
  CatalogSortKey,
  pageOf,
  versionsOf,
} from '../domain/catalog-order';
import type { CatalogWriteTx, OrgCatalogRows } from '../ports/catalog-store';

const key = (orgId: string, ref: string): string => `${orgId}|${ref}`;

export class InMemoryOrgCatalogStore implements OrgCatalogRows {
  private readonly rows = new Map<string, CatalogItem>();

  get(orgId: string, ref: string): Promise<CatalogItem | null> {
    return Promise.resolve(this.rows.get(key(orgId, ref)) ?? null);
  }

  page(
    orgId: string,
    filter: CatalogFilter,
    after: CatalogSortKey | null,
    limit: number,
  ): Promise<CatalogItem[]> {
    return Promise.resolve(pageOf(this.inOrg(orgId), filter, after, limit));
  }

  versions(
    orgId: string,
    kind: string,
    publisher: string,
    name: string,
  ): Promise<CatalogItem[]> {
    return Promise.resolve(
      versionsOf(
        this.inOrg(orgId).filter(
          (item) =>
            item.kind === kind &&
            item.publisher === publisher &&
            item.name === name,
        ),
      ),
    );
  }

  async insert(tx: CatalogWriteTx, item: CatalogItem): Promise<void> {
    await tx.enlist(() => {
      const id = key(tx.orgId, item.ref);
      if (this.rows.has(id)) throw new ItemExists(`${item.ref} exists`);
      this.rows.set(id, structuredClone(item));
      return Promise.resolve();
    });
  }

  async deprecate(
    tx: CatalogWriteTx,
    orgId: string,
    ref: string,
  ): Promise<boolean> {
    const id = key(orgId, ref);
    const row = this.rows.get(id);
    if (row === undefined) return false;
    await tx.enlist(() => {
      this.rows.set(id, { ...row, deprecated: true });
      return Promise.resolve();
    });
    return true;
  }

  private inOrg(orgId: string): CatalogItem[] {
    return [...this.rows.values()].filter((item) => item.orgId === orgId);
  }
}
