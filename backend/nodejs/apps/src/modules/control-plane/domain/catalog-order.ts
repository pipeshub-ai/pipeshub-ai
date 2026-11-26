import type { CatalogItem } from './catalog-item';

/**
 * How the catalog lists: by kind, display name (case-insensitive) and ref,
 * paged by a keyset cursor, so a page never repeats or skips an item that
 * existed when the first page was read.
 */

export type CatalogSortKey = readonly [
  kind: string,
  nameKey: string,
  ref: string,
];

export interface CatalogFilter {
  readonly kind?: string;
  readonly q?: string;
}

export const nameKey = (item: CatalogItem): string =>
  item.display.name.toLowerCase();

export const sortKeyOf = (item: CatalogItem): CatalogSortKey => [
  item.kind,
  nameKey(item),
  item.ref,
];

export function compareKeys(a: CatalogSortKey, b: CatalogSortKey): number {
  for (let i = 0; i < a.length; i++) {
    const [left, right] = [a[i], b[i]] as [string, string];
    if (left !== right) return left < right ? -1 : 1;
  }
  return 0;
}

export const matchesFilter = (
  item: CatalogItem,
  filter: CatalogFilter,
): boolean =>
  (filter.kind === undefined || item.kind === filter.kind) &&
  (filter.q === undefined || nameKey(item).includes(filter.q.toLowerCase()));

/** Items matching `filter`, after `after`, in order, at most `limit`. */
export function pageOf(
  items: Iterable<CatalogItem>,
  filter: CatalogFilter,
  after: CatalogSortKey | null,
  limit: number,
): CatalogItem[] {
  return [...items]
    .filter(
      (item) =>
        matchesFilter(item, filter) &&
        (after === null || compareKeys(sortKeyOf(item), after) > 0),
    )
    .sort((a, b) => compareKeys(sortKeyOf(a), sortKeyOf(b)))
    .slice(0, limit);
}

export const encodeCursor = (key: CatalogSortKey): string =>
  Buffer.from(JSON.stringify(key), 'utf8').toString('base64url');

export function decodeCursor(cursor: string): CatalogSortKey {
  const key: unknown = JSON.parse(
    Buffer.from(cursor, 'base64url').toString('utf8'),
  );
  if (
    !Array.isArray(key) ||
    key.length !== 3 ||
    !key.every((part) => typeof part === 'string')
  ) {
    throw new TypeError('malformed catalog cursor');
  }
  return key as unknown as CatalogSortKey;
}

export const versionsOf = (items: Iterable<CatalogItem>): CatalogItem[] =>
  [...items].sort((a, b) =>
    a.createdAt !== b.createdAt
      ? a.createdAt - b.createdAt
      : compareKeys(sortKeyOf(a), sortKeyOf(b)),
  );
