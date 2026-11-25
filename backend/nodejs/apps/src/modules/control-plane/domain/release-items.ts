import { z } from 'zod/v3';
import { RELEASE_CATALOG_JSON } from '../contracts/release-catalog.generated';
import { type CatalogItem, catalogItemSchema } from './catalog-item';

const parsed = new Map<string, readonly CatalogItem[]>();

/**
 * A kind's release items from the generated data
 * (`python -m app.control_plane.catalog.dump --write`), parsed once.
 */
export function releaseItemsOf(kind: string): readonly CatalogItem[] {
  let items = parsed.get(kind);
  if (items === undefined) {
    items = z
      .array(catalogItemSchema)
      .parse(JSON.parse(RELEASE_CATALOG_JSON[kind] ?? '[]'));
    parsed.set(kind, items);
  }
  return items;
}
