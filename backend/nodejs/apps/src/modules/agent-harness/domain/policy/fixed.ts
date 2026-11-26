import type { EffectiveRunConfig } from '../../contracts/generated/core/policy/effective-run-config';
import { POLICY_REGISTRY, type DimensionRegistry } from './registry';

/**
 * The dimensions of `section` that the resolved layers above a scope fix for
 * it (WP-23): one a layer locked (C4.02), or a `flat` row a layer below the
 * release set, which a layer below may only repeat. An editor shows them
 * read-only, with a lock.
 */
export function fixedDimensions(
  dimensions: EffectiveRunConfig['dimensions'],
  section: string,
  registry: DimensionRegistry = POLICY_REGISTRY,
): string[] {
  return Object.entries(dimensions)
    .filter(([path]) => path.startsWith(`${section}.`))
    .filter(
      ([path, entry]) =>
        entry.lockedBy !== undefined ||
        (registry.find(path)?.order?.kind === 'flat' &&
          entry.setBy !== 'release'),
    )
    .map(([path]) => path)
    .sort();
}
