import type { Connector } from '@/app/(main)/workspace/connectors/types';
import type { NodeTemplate } from './types';
import { normalizeDisplayName } from './display-utils';

export function filterTemplatesBySearch(templates: NodeTemplate[], q: string): NodeTemplate[] {
  if (!q.trim()) return templates;
  const query = q.toLowerCase();
  return templates.filter(
    (t) =>
      t.label.toLowerCase().includes(query) ||
      t.description.toLowerCase().includes(query) ||
      (t.defaultConfig?.appName as string)?.toLowerCase().includes(query)
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
