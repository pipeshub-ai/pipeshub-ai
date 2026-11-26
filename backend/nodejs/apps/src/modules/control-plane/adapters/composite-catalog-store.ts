import { CatalogItem, catalogItemSchema } from '../domain/catalog-item';
import {
  DigestMismatch,
  InvalidCatalogItem,
  ItemExists,
  ItemNotFound,
  OrgMismatch,
  SharedItemReadOnly,
} from '../domain/catalog-errors';
import {
  compareKeys,
  decodeCursor,
  encodeCursor,
  sortKeyOf,
  versionsOf,
} from '../domain/catalog-order';
import { canonicalDigest } from '../../../libs/utils/canonical-json';
import { itemDescriptorSetInput } from '../domain/digests';
import type {
  CatalogItemSummary,
  CatalogQuery,
  CatalogRows,
  CatalogStore,
  CatalogWriteTx,
  ItemVersion,
  OrgCatalogRows,
  Page,
} from '../ports/catalog-store';

const summary = (item: CatalogItem): CatalogItemSummary => ({
  ref: item.ref,
  kind: item.kind,
  publisher: item.publisher,
  name: item.name,
  version: item.version,
  orgId: item.orgId,
  provenance: item.provenance,
  display: item.display,
  riskClass: item.riskClass,
  deprecated: item.deprecated,
});

/** `CatalogStore` over the shared release rows and the org's own rows (02-lld §4.1). */
export class CompositeCatalogStore implements CatalogStore {
  constructor(
    private readonly release: CatalogRows,
    private readonly org: OrgCatalogRows,
  ) {}

  async get(orgId: string, ref: string): Promise<CatalogItem | null> {
    return (
      (await this.release.get(orgId, ref)) ?? (await this.org.get(orgId, ref))
    );
  }

  async list(
    orgId: string,
    query: CatalogQuery,
  ): Promise<Page<CatalogItemSummary>> {
    const after =
      query.cursor === undefined || query.cursor === null
        ? null
        : decodeCursor(query.cursor);
    const filter = { kind: query.kind, q: query.q };
    const [shared, own] = await Promise.all([
      this.release.page(orgId, filter, after, query.limit + 1),
      this.org.page(orgId, filter, after, query.limit + 1),
    ]);
    const merged = [...shared, ...own].sort((a, b) =>
      compareKeys(sortKeyOf(a), sortKeyOf(b)),
    );
    const items = merged.slice(0, query.limit);
    const last = items[items.length - 1];
    return {
      items: items.map(summary),
      next:
        merged.length > query.limit && last !== undefined
          ? encodeCursor(sortKeyOf(last))
          : null,
    };
  }

  async versions(
    orgId: string,
    kind: string,
    publisher: string,
    name: string,
  ): Promise<ItemVersion[]> {
    const [shared, own] = await Promise.all([
      this.release.versions(orgId, kind, publisher, name),
      this.org.versions(orgId, kind, publisher, name),
    ]);
    return versionsOf([...shared, ...own]).map((item) => ({
      ref: item.ref,
      version: item.version,
      createdAt: item.createdAt,
      deprecated: item.deprecated,
    }));
  }

  async insert(tx: CatalogWriteTx, item: CatalogItem): Promise<void> {
    const parsed = catalogItemSchema.safeParse(item);
    if (!parsed.success)
      throw new InvalidCatalogItem(
        `${item.ref}: ${parsed.error.issues[0]?.message ?? 'invalid'}`,
      );
    if (item.orgId === null)
      throw new SharedItemReadOnly(`${item.ref} is a shared item`);
    if (item.orgId !== tx.orgId)
      throw new OrgMismatch(`${item.ref} belongs to another org`);
    const recomputed = canonicalDigest(itemDescriptorSetInput(item));
    if (recomputed !== item.descriptorSetDigest)
      throw new DigestMismatch(`${item.ref}: set digest does not recompute`);
    if ((await this.release.get(tx.orgId, item.ref)) !== null)
      throw new ItemExists(`${item.ref} is a release item`);
    await this.org.insert(tx, parsed.data);
  }

  async deprecate(
    tx: CatalogWriteTx,
    orgId: string,
    ref: string,
  ): Promise<void> {
    if (orgId !== tx.orgId)
      throw new OrgMismatch(`${ref} belongs to another org`);
    if ((await this.release.get(orgId, ref)) !== null)
      throw new SharedItemReadOnly(`${ref} is a shared item`);
    if (!(await this.org.deprecate(tx, orgId, ref)))
      throw new ItemNotFound(`${ref} not found`);
  }
}
