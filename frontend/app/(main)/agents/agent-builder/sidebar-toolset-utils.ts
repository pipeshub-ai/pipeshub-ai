import type { BuilderSidebarToolset, BuilderToolsetTool } from '@/app/(main)/toolsets/api';
import { normalizePaletteLabel } from './display-utils';

/** @deprecated Prefer {@link normalizePaletteLabel} from `./display-utils`; kept for call-site imports. */
export function formatToolsetTypeLabel(toolsetTypeValue: string): string {
  return normalizePaletteLabel(toolsetTypeValue);
}

export function normalizeToolsetTypeKey(value: string): string {
  return (value || '').trim().toLowerCase().replace(/\s+/g, '').replace(/[_-]+/g, '');
}

/** Minimal node shape for agent-builder canvas + drop handler (avoids circular imports). */
export type ToolsetTypeKeyFlowNode = {
  data?: { type?: string; config?: Record<string, unknown> };
};

/**
 * Normalized logical toolset types already present on the flow (legacy parity: one type per canvas).
 * Resolution: `config.toolsetType` → `config.toolsetName` → `toolset-` prefix stripped from node `type`.
 * Unifies checks that previously lived in `activeToolsetInstanceIds` and `activeToolsetTypeKeysWithoutInstance`.
 */
export function collectActiveToolsetTypeKeysFromNodes(nodes: ToolsetTypeKeyFlowNode[]): Set<string> {
  const keys = new Set<string>();
  for (const node of nodes) {
    const nodeType = String(node.data?.type ?? '');
    if (!nodeType.startsWith('toolset-')) continue;
    const config = (node.data?.config || {}) as Record<string, unknown>;
    const raw =
      (config.toolsetType as string) ||
      (config.toolsetName as string) ||
      nodeType.replace(/^toolset-/, '');
    const k = normalizeToolsetTypeKey(raw);
    if (k) keys.add(k);
  }
  return keys;
}

/**
 * Existing toolset node a drop would merge into — same rules as
 * `canvas-drop-handler` `findExistingToolsetNodeForMerge` (instanceId first, else legacy name + no id).
 */
export function findMergeTargetToolsetNode(
  nodes: ToolsetTypeKeyFlowNode[],
  toolsetName: string,
  instanceIdRaw: string | undefined
): ToolsetTypeKeyFlowNode | undefined {
  const instanceId = instanceIdRaw?.trim();
  if (instanceId) {
    return nodes.find(
      (n) =>
        String(n.data?.type ?? '').startsWith('toolset-') &&
        String((n.data?.config as Record<string, unknown> | undefined)?.instanceId ?? '').trim() ===
          instanceId
    );
  }
  return nodes.find(
    (n) =>
      String(n.data?.type ?? '').startsWith('toolset-') &&
      (n.data?.config as Record<string, unknown> | undefined)?.toolsetName === toolsetName &&
      !String((n.data?.config as Record<string, unknown> | undefined)?.instanceId ?? '').trim()
  );
}

export function buildToolsetDragPayload(ts: BuilderSidebarToolset): Record<string, string> {
  const toolsetName = ts.toolsetType || ts.name;
  /** Node `type` prefix is the logical toolset name; `instanceId` in payload distinguishes instances for merge-on-drop. */
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
