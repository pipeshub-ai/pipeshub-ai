import type { BuilderSidebarToolset, RegistryToolsetRow } from '@/app/(main)/toolsets/api';

/** One card in the Actions catalog (registry + optional user/org state). */
export interface ActionCatalogItem {
  /** Stable React key */
  key: string;
  toolsetType: string;
  title: string;
  description: string;
  iconPath: string;
  category: string;
  toolCount: number;
  /** Org has at least one instance of this toolset type exposed to the user. */
  hasOrgInstance: boolean;
  /** Current user has authenticated at least one instance of this type. */
  isUserAuthenticated: boolean;
  /** All matching instances for this toolset type (org catalog / browse merge). */
  instances: BuilderSidebarToolset[];
  /** First matching instance for configure / authenticate flows. */
  primaryInstance?: BuilderSidebarToolset;
  /** Registry / synthetic row auth options for `ActionSetupPanel`. */
  supportedAuthTypes?: string[];
  rowKind: ActionCatalogRowKind;
}

export type ActionsCatalogTab = 'all' | 'configured' | 'not_configured';

export type MyActionsTab = 'all' | 'authenticated' | 'not_authenticated';

/** `byToolsetType` = merged registry card; `byInstance` = one row per instance (personal “my”). */
export type ActionCatalogRowKind = 'byToolsetType' | 'byInstance';

/**
 * Admin team catalog from GET /my-toolsets with `includeRegistry=true`: flat rows are
 * real instances (non-empty `instanceId`) plus one synthetic row per catalog type with no instance.
 * Groups all instances of the same `toolsetType` into a single card so pagination/limit never splits types.
 */
export function mergedMyToolsetsCatalogFromIncludeRegistry(rows: BuilderSidebarToolset[]): ActionCatalogItem[] {
  const realRows = rows.filter((r) => (r.instanceId || '').trim().length > 0);
  const byType = new Map<string, BuilderSidebarToolset[]>();
  for (const row of realRows) {
    const raw = (row.toolsetType || row.normalized_name || row.name || '').trim();
    if (!raw) continue;
    const k = raw.toLowerCase();
    if (!byType.has(k)) byType.set(k, []);
    byType.get(k)!.push(row);
  }

  const configuredKeys = new Set(byType.keys());
  const syntheticByType = new Map<string, BuilderSidebarToolset>();
  for (const row of rows) {
    if ((row.instanceId || '').trim().length > 0) continue;
    const raw = (row.toolsetType || row.normalized_name || row.name || '').trim();
    if (!raw) continue;
    const k = raw.toLowerCase();
    if (configuredKeys.has(k)) continue;
    if (!syntheticByType.has(k)) syntheticByType.set(k, row);
  }

  const out: ActionCatalogItem[] = [];

  for (const instances of Array.from(byType.values())) {
    const sorted = [...instances].sort((a, b) =>
      (a.instanceName || '').localeCompare(b.instanceName || '', undefined, { sensitivity: 'base' })
    );
    const primary = sorted[0] ?? instances[0];
    const canonical =
      primary.toolsetType || primary.normalized_name || primary.name || instances[0].normalized_name;
    if (!canonical) continue;
    const isUserAuthenticated = instances.some((i) => i.isAuthenticated);
    const toolCount = instances.reduce((m, i) => Math.max(m, i.toolCount || 0), 0);
    const supported =
      primary.supportedAuthTypes?.length ?
        primary.supportedAuthTypes
      : instances.find((i) => i.supportedAuthTypes?.length)?.supportedAuthTypes;
    out.push({
      key: canonical,
      toolsetType: canonical,
      title: primary.displayName || canonical,
      description: primary.description || '',
      iconPath: primary.iconPath || '',
      category: primary.category || 'app',
      toolCount,
      hasOrgInstance: true,
      isUserAuthenticated,
      instances: sorted,
      primaryInstance: primary,
      supportedAuthTypes: supported,
      rowKind: 'byToolsetType',
    });
  }

  for (const syn of Array.from(syntheticByType.values())) {
    const canonical = syn.toolsetType || syn.normalized_name || syn.name;
    if (!canonical) continue;
    out.push({
      key: canonical,
      toolsetType: canonical,
      title: syn.displayName || canonical,
      description: syn.description || '',
      iconPath: syn.iconPath || '',
      category: syn.category || 'app',
      toolCount: syn.toolCount ?? 0,
      hasOrgInstance: false,
      isUserAuthenticated: false,
      instances: [],
      primaryInstance: undefined,
      supportedAuthTypes: syn.supportedAuthTypes,
      rowKind: 'byToolsetType',
    });
  }

  out.sort((a, b) => a.title.localeCompare(b.title, undefined, { sensitivity: 'base' }));
  return out;
}

/** Registry row for setup flows when the catalog item was built from my-toolsets merge only. */
export function actionCatalogItemToRegistryRow(item: ActionCatalogItem): RegistryToolsetRow {
  const primary = item.primaryInstance ?? item.instances[0];
  const fromItem = item.supportedAuthTypes?.filter(Boolean) ?? [];
  const fromPrimary = primary?.supportedAuthTypes?.filter(Boolean) ?? [];
  const mergedTypes = fromItem.length > 0 ? fromItem : fromPrimary;
  const authRaw = primary?.authType ? String(primary.authType).toUpperCase() : '';
  const supportedAuthTypes =
    mergedTypes.length > 0 ?
      mergedTypes.map((a) => String(a).toUpperCase())
    : authRaw && authRaw !== 'NONE' ? [authRaw]
    : ['NONE'];
  return {
    name: item.toolsetType,
    displayName: item.title,
    description: item.description,
    category: item.category || 'app',
    appGroup: '',
    iconPath: item.iconPath || '',
    supportedAuthTypes,
    toolCount: item.toolCount,
  };
}

export function mergeRegistryWithMyToolsets(
  registry: RegistryToolsetRow[],
  myToolsets: BuilderSidebarToolset[]
): ActionCatalogItem[] {
  const byType = new Map<string, BuilderSidebarToolset[]>();
  for (const row of myToolsets) {
    const t = (row.toolsetType || row.normalized_name || '').toLowerCase();
    if (!t) continue;
    if (!byType.has(t)) byType.set(t, []);
    byType.get(t)!.push(row);
  }

  return registry.map((r) => {
    const t = r.name.toLowerCase();
    const rawList = byType.get(t) ?? [];
    const instances = [...rawList].sort((a, b) =>
      (a.instanceName || '').localeCompare(b.instanceName || '', undefined, { sensitivity: 'base' })
    );
    const primaryInstance = instances[0];
    const hasOrgInstance = instances.length > 0;
    const isUserAuthenticated = instances.some((i) => i.isAuthenticated);
    return {
      key: r.name,
      toolsetType: r.name,
      title: r.displayName || r.name,
      description: r.description || '',
      iconPath: r.iconPath || '',
      category: r.category || 'app',
      toolCount: r.toolCount,
      hasOrgInstance,
      isUserAuthenticated,
      instances,
      primaryInstance,
      supportedAuthTypes: r.supportedAuthTypes,
      rowKind: 'byToolsetType',
    };
  });
}

/**
 * Personal “Your actions”: one merged card per toolset type from **admin instances only**
 * (no GET /registry merge — avoids listing every catalog type the org has not provisioned).
 */
export function myToolsetsGroupedToCatalogItems(rows: BuilderSidebarToolset[]): ActionCatalogItem[] {
  const byType = new Map<string, BuilderSidebarToolset[]>();
  for (const row of rows) {
    const raw = (row.toolsetType || row.normalized_name || row.name || '').trim();
    if (!raw) continue;
    const k = raw.toLowerCase();
    if (!byType.has(k)) byType.set(k, []);
    byType.get(k)!.push(row);
  }

  const out: ActionCatalogItem[] = [];
  for (const instances of Array.from(byType.values())) {
    const canonical =
      instances.find((i) => i.toolsetType)?.toolsetType ||
      instances[0].normalized_name ||
      instances[0].name;
    if (!canonical) continue;
    const sorted = [...instances].sort((a, b) =>
      (a.instanceName || '').localeCompare(b.instanceName || '', undefined, { sensitivity: 'base' })
    );
    const primary = sorted[0] ?? instances[0];
    const isUserAuthenticated = instances.some((i) => i.isAuthenticated);
    const toolCount = instances.reduce((m, i) => Math.max(m, i.toolCount || 0), 0);
    out.push({
      key: canonical,
      toolsetType: canonical,
      title: primary.displayName || canonical,
      description: primary.description || '',
      iconPath: primary.iconPath || '',
      category: primary.category || 'app',
      toolCount,
      hasOrgInstance: true,
      isUserAuthenticated,
      instances: sorted,
      primaryInstance: primary,
      supportedAuthTypes: primary.supportedAuthTypes,
      rowKind: 'byToolsetType',
    });
  }
  out.sort((a, b) => a.title.localeCompare(b.title, undefined, { sensitivity: 'base' }));
  return out;
}
