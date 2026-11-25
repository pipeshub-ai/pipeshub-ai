import {
  type CatalogItem,
  catalogItemSchema,
} from '../contracts/generated/catalog-item';

/** `CatalogItem` (02-lld §2.1), generated from contracts/control-plane/catalog-item.schema.json. */
export { type CatalogItem, catalogItemSchema };

export type Provenance = CatalogItem['provenance'];
export type Descriptor = CatalogItem['descriptors']['items'][number];

/** Release and built-in items are shared by every org (`orgId` null). */
export const SHARED_PROVENANCES: readonly Provenance[] = ['builtin', 'release'];

export const descriptorsOf = (item: CatalogItem): readonly Descriptor[] =>
  item.descriptors.items;
