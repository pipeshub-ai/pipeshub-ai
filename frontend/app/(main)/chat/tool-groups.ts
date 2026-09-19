import type { BuilderSidebarToolset } from '@/app/(main)/toolsets/api';
import type { McpMyServerEntry } from '@/app/(main)/workspace/mcp-servers/types';
import type { ScopedToolGroupRow } from './store';

/** Toolset / MCP group row built from the caller's own catalog (my-toolsets, my-mcp-servers). */
export interface CatalogToolGroupRow extends ScopedToolGroupRow {
  instanceId: string;
  isAuthenticated: boolean;
}

/**
 * Composer-internal tool keys are `${instanceId}:${fullName}` so two instances of the same
 * toolset stay independently selectable. The wire format (and anything persisted for the
 * server to intersect against, e.g. `project.tools`) is the bare `fullName`.
 */
export function bareToolFullName(key: string): string {
  const colon = key.indexOf(':');
  return colon >= 0 ? key.slice(colon + 1) : key;
}

/**
 * Build tool groups from authenticated my-toolsets.
 *
 * Every entry in `fullNames` is prefixed with a per-entry discriminator — the API
 * `instanceId` when present, else the loop index — so multiple instances of one toolset type
 * never share selection keys even when the API omits `instanceId`.
 */
export function buildCatalogToolGroups(toolsets: BuilderSidebarToolset[]): CatalogToolGroupRow[] {
  const groups: CatalogToolGroupRow[] = [];
  for (let i = 0; i < toolsets.length; i++) {
    const ts = toolsets[i]!;
    const rawInstanceId = typeof ts.instanceId === 'string' ? ts.instanceId.trim() : '';
    const groupDiscriminator = rawInstanceId || `local-${i}`;

    const rawFullNames = (ts.tools || [])
      .map((t) => (typeof t.fullName === 'string' ? t.fullName.trim() : ''))
      .filter(Boolean);
    if (rawFullNames.length === 0) continue;

    const fullNames = rawFullNames.map((fn) => `${groupDiscriminator}:${fn}`);

    const toolDescriptions: Record<string, string> = {};
    rawFullNames.forEach((_rawFn, j) => {
      const key = fullNames[j]!;
      const t = (ts.tools || [])[j];
      const d = t && typeof t.description === 'string' ? t.description.trim() : '';
      if (d) toolDescriptions[key] = d;
    });

    const instanceLabel = typeof ts.instanceName === 'string' ? ts.instanceName.trim() : '';
    const productLabel = (ts.displayName || ts.name || 'Tools').trim();

    groups.push({
      label: instanceLabel || productLabel,
      toolsetSlug: (ts.toolsetType || ts.name || '').trim(),
      instanceId: groupDiscriminator,
      iconPath: ts.iconPath?.trim() || undefined,
      fullNames,
      toolDescriptions: Object.keys(toolDescriptions).length ? toolDescriptions : undefined,
      isAuthenticated: Boolean(ts.isAuthenticated),
    });
  }
  return groups;
}

/**
 * Build tool groups from my-mcp-servers instances. Same `${instanceId}:${fullName}` key
 * scheme as `buildCatalogToolGroups` — two MCP instances of the same server type could
 * otherwise expose identical `namespacedName`s.
 */
export function buildCatalogMcpGroups(instances: McpMyServerEntry[]): CatalogToolGroupRow[] {
  const groups: CatalogToolGroupRow[] = [];
  for (const entry of instances) {
    const rawFullNames = (entry.tools || [])
      .map((t) => (typeof t.namespacedName === 'string' ? t.namespacedName.trim() : ''))
      .filter(Boolean);
    if (rawFullNames.length === 0) continue;

    const fullNames = rawFullNames.map((fn) => `${entry._id}:${fn}`);

    const toolDescriptions: Record<string, string> = {};
    rawFullNames.forEach((_rawFn, j) => {
      const key = fullNames[j]!;
      const tool = (entry.tools || [])[j];
      const d = tool && typeof tool.description === 'string' ? tool.description.trim() : '';
      if (d) toolDescriptions[key] = d;
    });

    groups.push({
      label: (entry.name || 'MCP Server').trim(),
      toolsetSlug: 'mcp',
      instanceId: entry._id,
      fullNames,
      toolDescriptions: Object.keys(toolDescriptions).length ? toolDescriptions : undefined,
      isAuthenticated: Boolean(entry.isAuthenticated),
    });
  }
  return groups;
}

/**
 * Keep only the tools whose bare `fullName` is in `allowedBare`; drop groups left empty.
 * Used to project a catalog onto a project's persisted `tools` allow-list.
 */
export function restrictGroupsToBareNames<T extends ScopedToolGroupRow>(
  groups: T[],
  allowedBare: ReadonlySet<string>
): T[] {
  return groups
    .map((g) => {
      const fullNames = g.fullNames.filter((fn) => allowedBare.has(bareToolFullName(fn)));
      if (fullNames.length === g.fullNames.length) return g;
      const toolDescriptions = g.toolDescriptions
        ? Object.fromEntries(
            Object.entries(g.toolDescriptions).filter(([k]) => fullNames.includes(k))
          )
        : undefined;
      return { ...g, fullNames, toolDescriptions };
    })
    .filter((g) => g.fullNames.length > 0);
}
