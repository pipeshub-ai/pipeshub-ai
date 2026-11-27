import {
  CatalogItem,
  catalogItemSchema,
  SHARED_PROVENANCES,
} from '../domain/catalog-item';
import { InvalidReleaseItem } from '../domain/catalog-errors';
import { canonicalDigest } from '../../../libs/utils/canonical-json';
import { itemDescriptorSetInput } from '../domain/digests';
import { formatItemRef } from '../domain/item-ref';
import type { ExtensionRegistry } from './extension-registry';

function problemOf(kind: string, item: CatalogItem): string | null {
  const parsed = catalogItemSchema.safeParse(item);
  if (!parsed.success)
    return parsed.error.issues[0]?.message ?? 'invalid shape';
  if (item.kind !== kind) return `listed by the ${kind} module`;
  if (item.orgId !== null) return 'carries an org id';
  if (!SHARED_PROVENANCES.includes(item.provenance))
    return `provenance ${item.provenance} is not shared`;
  if (formatItemRef(item) !== item.ref)
    return 'ref disagrees with kind, publisher, name or version';
  const recomputed = canonicalDigest(itemDescriptorSetInput(item));
  return recomputed === item.descriptorSetDigest
    ? null
    : 'set digest does not recompute';
}

/** Every registered module's release items, checked by the rules every kind shares. */
export function buildReleaseCatalog(
  registry: ExtensionRegistry,
): CatalogItem[] {
  const seen = new Set<string>();
  const items: CatalogItem[] = [];
  for (const kind of registry.kinds()) {
    for (const item of registry.catalogSource(kind).releaseItems()) {
      const problem = seen.has(item.ref)
        ? 'appears twice'
        : problemOf(kind, item);
      if (problem !== null)
        throw new InvalidReleaseItem(`${item.ref}: ${problem}`);
      seen.add(item.ref);
      items.push(item);
    }
  }
  return items;
}

/** Names of a kind's release items users may pick (not `display.hidden`), sorted. */
export const listedReleaseNames = (
  registry: ExtensionRegistry,
  kind: string,
): string[] =>
  registry
    .catalogSource(kind)
    .releaseItems()
    .filter((item) => !item.display.hidden)
    .map((item) => item.name)
    .sort();
