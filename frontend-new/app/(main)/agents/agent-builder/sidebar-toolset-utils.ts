import type { BuilderSidebarToolset, BuilderToolsetTool } from '../toolsets-api';

export function formatToolsetTypeLabel(toolsetTypeValue: string): string {
  if (!toolsetTypeValue) return '';
  const normalized = toolsetTypeValue
    .trim()
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .toLowerCase()
    .replace(/\bshare\s+point\b/g, 'sharepoint');
  return normalized
    .split(' ')
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

export function normalizeToolsetTypeKey(value: string): string {
  return (value || '').trim().toLowerCase().replace(/\s+/g, '').replace(/[_-]+/g, '');
}

export function buildToolsetDragPayload(ts: BuilderSidebarToolset): Record<string, string> {
  const toolsetName = ts.toolsetType || ts.name;
  const reactFlowType = `toolset-${toolsetName}`.toLowerCase().replace(/\s+/g, '');
  return {
    'application/reactflow': reactFlowType,
    type: 'toolset',
    instanceId: ts.instanceId || '',
    instanceName: ts.instanceName || ts.displayName,
    toolsetType: toolsetName,
    toolsetName,
    displayName: ts.displayName,
    selectedTools: JSON.stringify(ts.tools.map((t) => t.name)),
    allTools: JSON.stringify(
      ts.tools.map((t) => ({
        toolName: t.name,
        fullName: t.fullName || `${toolsetName}.${t.name}`,
        toolsetName,
        description: t.description,
        appName: toolsetName,
      }))
    ),
    iconPath: ts.iconPath || '',
    category: ts.category || 'app',
    isConfigured: String(ts.isConfigured),
    isAuthenticated: String(ts.isAuthenticated),
    toolCount: String(ts.tools.length),
  };
}

/** Single tool from an expanded toolset — matches canvas-drop-handler `type === tool` branch. */
export function buildToolDragPayload(tool: BuilderToolsetTool, ts: BuilderSidebarToolset): Record<string, string> {
  const toolsetName = ts.toolsetType || ts.name;
  const fullName = tool.fullName || `${toolsetName}.${tool.name}`;
  const allTools = ts.tools.map((t) => ({
    toolName: t.name,
    fullName: t.fullName || `${toolsetName}.${t.name}`,
    toolsetName,
    description: t.description,
    appName: toolsetName,
  }));
  return {
    'application/reactflow': fullName,
    type: 'tool',
    instanceId: ts.instanceId || '',
    instanceName: ts.instanceName || ts.displayName,
    toolsetType: toolsetName,
    toolsetName,
    displayName: ts.displayName,
    toolName: tool.name,
    fullName,
    description: tool.description || '',
    iconPath: ts.iconPath || '',
    category: ts.category || 'app',
    isConfigured: String(ts.isConfigured),
    isAuthenticated: String(ts.isAuthenticated),
    allTools: JSON.stringify(allTools),
    toolCount: String(ts.tools.length),
  };
}

export function groupToolsetsByType(toolsets: BuilderSidebarToolset[]): Record<string, BuilderSidebarToolset[]> {
  return toolsets.reduce<Record<string, BuilderSidebarToolset[]>>((acc, ts) => {
    const key = ts.toolsetType || ts.name || 'unknown';
    if (!acc[key]) acc[key] = [];
    acc[key].push(ts);
    return acc;
  }, {});
}

export type ToolsetSidebarStatus = 'authenticated' | 'needs_authentication' | 'registry';

/** Sidebar row chrome: registry vs configured vs authenticated (matches `buildUiState` registry rule). */
export function getToolsetSidebarStatus(ts: BuilderSidebarToolset): ToolsetSidebarStatus | undefined {
  const fromRegistry = ts.isFromRegistry === true || !ts.instanceId;
  if (fromRegistry) return 'registry';
  if (ts.isConfigured && ts.isAuthenticated) return 'authenticated';
  if (ts.isConfigured && !ts.isAuthenticated) return 'needs_authentication';
  return undefined;
}
