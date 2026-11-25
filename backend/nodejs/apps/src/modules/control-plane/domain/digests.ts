import type { CatalogItem } from './catalog-item';
import { DuplicateDescriptorName } from './catalog-errors';

/**
 * One digest scheme for catalog items (02-lld §5.4), the same as
 * `app.control_plane.catalog.digests`; vectors in
 * contracts/control-plane/vectors/digests.json. The domain says what each
 * digest covers; callers hash it with `canonicalDigest` (libs), since hashing
 * is I/O-free but not dependency-free. An item's set digest covers every
 * descriptor by name, so the core checks a set without knowing a kind.
 */

type Json = Readonly<Record<string, unknown>>;

const ENGINE_FIELDS = ['kind', 'version', 'binaryDigest', 'optionsDigest'];
export const SKILL_BUNDLE_FORMAT = 'skill-bundle/1';

const pick = (
  value: Json,
  fields: readonly string[],
): Record<string, unknown> =>
  Object.fromEntries(fields.map((field) => [field, value[field] ?? null]));

/** The MCP behaviour hints a tool may declare (`harness_core.mcp_hub.annotations.HINT_KEYS`). */
const HINT_KEYS = [
  'readOnlyHint',
  'destructiveHint',
  'idempotentHint',
  'openWorldHint',
];

const declaredHints = (annotations: unknown): Record<string, boolean> =>
  typeof annotations === 'object' && annotations !== null
    ? Object.fromEntries(
        HINT_KEYS.map(
          (key) => [key, (annotations as Json)[key]] as const,
        ).filter(
          (hint): hint is readonly [string, boolean] =>
            typeof hint[1] === 'boolean',
        ),
      )
    : {};

/**
 * Python's `ToolDescriptor.digest`, the one tool digest (run pins version 2,
 * MCP approvals, the catalog): name, description and input schema, plus what
 * decides approvals: the effect and sink a tool declares and the behaviour
 * hints an MCP tool declares.
 */
export function toolDigestInput(tool: Json): Record<string, unknown> {
  const covered: Record<string, unknown> = {
    name: tool.name,
    description: tool.description ?? null,
    inputSchema: tool.inputSchema ?? {},
  };
  for (const field of ['effect', 'sink']) {
    const value = tool[field];
    if (typeof value === 'string') covered[field] = value;
  }
  const hints = declaredHints(tool.annotations);
  if (Object.keys(hints).length > 0) covered.annotations = hints;
  return covered;
}

export const engineDigestInput = (engine: Json): Record<string, unknown> =>
  pick(engine, ENGINE_FIELDS);

/** A skill's bundle digest input (`skillBundleDigest`), from its file list without the bytes. */
export const skillDigestInput = (
  files: Readonly<
    Record<string, { readonly sha256: string; readonly size: number }>
  >,
): Record<string, unknown> => ({ format: SKILL_BUNDLE_FORMAT, files });

export function descriptorSetDigestInput(
  named: Iterable<readonly [string, string]>,
): Record<string, string> {
  const byName = new Map<string, string>();
  for (const [name, digest] of named) {
    if (byName.has(name)) {
      throw new DuplicateDescriptorName(`descriptor ${name} appears twice`);
    }
    byName.set(name, digest);
  }
  return Object.fromEntries(byName);
}

export const itemDescriptorSetInput = (
  item: CatalogItem,
): Record<string, string> =>
  descriptorSetDigestInput(
    item.descriptors.items.map((d) => [d.name, d.digest] as const),
  );
