import type { CatalogItem } from '../../../../src/modules/control-plane/domain/catalog-item';
import { descriptorSetDigest, toolDescriptorDigest } from './digests';
import type { CatalogWriteTx } from '../../../../src/modules/control-plane/ports/catalog-store';
import { ORG_A } from './cp-objects';

let counter = 0;

/** A valid item with one tool descriptor; `overrides` win, digests follow the descriptors. */
export function catalogItem(
  overrides: Partial<CatalogItem> & { toolDescription?: string } = {},
): CatalogItem {
  counter += 1;
  const { toolDescription, ...rest } = overrides;
  const kind = rest.kind ?? 'skill';
  const publisher = rest.publisher ?? 'org';
  const name = rest.name ?? `item-${String(counter).padStart(4, '0')}`;
  const version = rest.version ?? '1.0.0';
  const tool = {
    name: 'run',
    description: toolDescription ?? `tool of ${name}`,
    inputSchema: { type: 'object', properties: {}, required: [] },
    effect: 'read' as const,
    sink: null,
  };
  const descriptors = {
    kind: 'tool' as const,
    items: [{ ...tool, digest: toolDescriptorDigest(tool) }],
  };
  return {
    schemaVersion: 1,
    ref: `${kind}:${publisher}/${name}@${version}`,
    kind,
    publisher,
    name,
    version,
    orgId: ORG_A,
    provenance: 'org',
    source: {
      type: 'upload',
      locator: `${name}.zip`,
      fetchedAt: 1_000,
      digest: `sha256:${'c'.repeat(64)}`,
    },
    display: {
      name: name.toUpperCase(),
      description: `about ${name}`,
      icon: null,
      categories: [],
      hidden: false,
    },
    riskClass: 'read',
    configSchema: null,
    credentialRequirements: [],
    descriptors,
    descriptorSetDigest: descriptorSetDigest(
      descriptors.items.map((d) => [d.name, d.digest] as const),
    ),
    deprecated: false,
    createdAt: 1_000 + counter,
    ...rest,
  };
}

export const releaseItem = (
  kind: string,
  name: string,
  displayName = name,
): CatalogItem =>
  catalogItem({
    kind,
    publisher: 'pipeshub',
    name,
    version: '1',
    orgId: null,
    provenance: 'release',
    source: { type: 'release' },
    display: {
      name: displayName,
      description: name,
      icon: null,
      categories: [],
      hidden: false,
    },
    createdAt: 0,
  });

/** A transaction that runs each write at once: the catalog contract needs no rollback of its own. */
export const immediateTx = (
  orgId: string,
  session?: unknown,
): CatalogWriteTx => ({
  orgId,
  session,
  enlist: async (write) => write(),
});
