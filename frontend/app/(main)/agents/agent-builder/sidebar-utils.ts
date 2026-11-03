import type { Connector } from '@/app/(main)/workspace/connectors/types';
import type { NodeTemplate } from './types';
import type { BuilderSidebarToolset } from '@/app/(main)/toolsets/api';
import type { McpMyServerEntry } from '../../workspace/mcp-servers/types';
import { normalizeDisplayName, normalizePaletteLabel } from './display-utils';

type SearchText = string | null | undefined;

/** Case-insensitive substring match of the palette search query against any of `texts`. An empty query matches. */
export function matchesPaletteSearch(query: string, ...texts: SearchText[]): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return texts.some((text) => typeof text === 'string' && text.toLowerCase().includes(q));
}

/**
 * Items a palette group shows for `query`: all of them when the query is empty or names the group,
 * else the ones that match. `null` means the group has no match and should be hidden.
 */
export function filterPaletteGroup<T>(
  query: string,
  groupTexts: SearchText[],
  items: T[],
  itemTexts: (item: T) => SearchText[]
): T[] | null {
  if (matchesPaletteSearch(query, ...groupTexts)) return items;
  const hits = items.filter((item) => matchesPaletteSearch(query, ...itemTexts(item)));
  return hits.length > 0 ? hits : null;
}

/** Tools of `toolset` to list for `query` (all when it names the toolset); `null` hides the toolset. */
export function filterToolsetTools(
  toolset: BuilderSidebarToolset,
  query: string
): BuilderSidebarToolset['tools'] | null {
  const type = toolset.toolsetType || toolset.name;
  return filterPaletteGroup(
    query,
    [toolset.instanceName, toolset.displayName, toolset.name, type, type && normalizePaletteLabel(type)],
    toolset.tools ?? [],
    (tool) => [tool.name, tool.fullName, tool.description]
  );
}

/** Tools of an MCP server to list for `query` (all when it names the server); `null` hides the server. */
export function filterMcpServerTools(entry: McpMyServerEntry, query: string): McpMyServerEntry['tools'] | null {
  return filterPaletteGroup(query, [entry.name, entry.typeId], entry.tools ?? [], (tool) => [tool.name, tool.description]);
}

export function filterTemplatesBySearch(templates: NodeTemplate[], q: string): NodeTemplate[] {
  return templates.filter((t) =>
    matchesPaletteSearch(q, t.label, t.description, t.defaultConfig?.appName as string | undefined)
  );
}

export function groupToolsByAppName(templates: NodeTemplate[]): Record<string, NodeTemplate[]> {
  const individual = templates.filter(
    (t) => t.category === 'tools' && t.type.startsWith('tool-') && !t.type.startsWith('tool-group-')
  );
  const grouped: Record<string, NodeTemplate[]> = {};
  individual.forEach((template) => {
    const app = (template.defaultConfig?.appName as string) || 'Other';
    if (!grouped[app]) grouped[app] = [];
    grouped[app].push(template);
  });
  return grouped;
}

export function groupConnectorInstances(connectors: Connector[]): Record<
  string,
  { instances: Connector[]; icon: string }
> {
  const grouped: Record<string, { instances: Connector[]; icon: string }> = {};
  connectors.forEach((connector) => {
    const connectorType = connector.type || connector.appGroup || 'Other';
    const displayName = normalizeDisplayName(connectorType);
    if (!grouped[displayName]) {
      grouped[displayName] = { instances: [], icon: connector.iconPath || '' };
    }
    grouped[displayName].instances.push(connector);
  });
  return grouped;
}

export function prepareDragData(
  template: NodeTemplate,
  sectionType?: 'tools' | 'apps' | 'kbs' | 'connectors',
  additional?: Record<string, string>
): Record<string, string> {
  const data: Record<string, string> = { 'application/reactflow': template.type };
  if (sectionType === 'tools' && template.defaultConfig?.appName) {
    data.toolAppName = String(template.defaultConfig.appName);
  }
  if (sectionType === 'connectors' && template.defaultConfig?.id) {
    data.connectorId = String(template.defaultConfig.id);
    data.connectorType = String(template.defaultConfig.type || '');
  }
  if (additional) {
    Object.entries(additional).forEach(([k, v]) => {
      data[k] = v;
    });
  }
  return data;
}

export function createToolGroupDragData(
  tools: NodeTemplate[],
  instance: Connector,
  connectorType: string,
  connectorIcon: string,
  isConfigured: boolean,
  isAgentActive: boolean
) {
  return {
    connectorId: instance._key || '',
    connectorType: instance.type || connectorType,
    connectorName: instance.name,
    connectorIconPath: connectorIcon,
    scope: instance.scope || 'personal',
    toolCount: String(tools.length),
    isConfigured: String(isConfigured),
    isAgentActive: String(isAgentActive),
    allTools: JSON.stringify(
      tools.map((t) => ({
        toolId: t.defaultConfig?.toolId,
        fullName: t.defaultConfig?.fullName,
        toolName: t.label,
        appName: t.defaultConfig?.appName,
      }))
    ),
  };
}
