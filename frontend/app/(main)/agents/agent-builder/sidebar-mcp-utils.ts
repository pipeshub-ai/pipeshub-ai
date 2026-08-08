import type { McpMyServerEntry, McpToolInfo } from '../../workspace/mcp-servers/types';

/** Minimal node shape for agent-builder canvas + drop handler (avoids circular imports). */
export type McpInstanceIdFlowNode = {
  data?: { type?: string; config?: Record<string, unknown> };
};

/**
 * `instanceId`s of every `mcp-*` node already on the flow — an agent may attach several
 * distinct MCP instances, but never the same instance twice (unlike toolsets, there is no
 * "logical type" merge target: each instance is its own node).
 */
export function collectActiveMcpInstanceIdsFromNodes(nodes: McpInstanceIdFlowNode[]): Set<string> {
  const ids = new Set<string>();
  for (const node of nodes) {
    const nodeType = String(node.data?.type ?? '');
    if (!nodeType.startsWith('mcp-')) continue;
    const instanceId = String(
      (node.data?.config as Record<string, unknown> | undefined)?.instanceId ?? ''
    ).trim();
    if (instanceId) ids.add(instanceId);
  }
  return ids;
}

export function buildMcpServerDragPayload(entry: McpMyServerEntry): Record<string, string> {
  const displayName = entry.name || entry.typeId || 'MCP Server';
  return {
    'application/reactflow': `mcp-${entry._id}`,
    type: 'mcp-server',
    instanceId: entry._id,
    name: entry.name,
    displayName,
    typeId: entry.typeId || '',
    isAuthenticated: String(Boolean(entry.isAuthenticated)),
    tools: JSON.stringify(
      (entry.tools || []).map((tool) => ({
        name: tool.name,
        fullName: tool.namespacedName || tool.name,
        description: tool.description || '',
      }))
    ),
    toolCount: String((entry.tools || []).length),
  };
}

export type McpSidebarStatus = 'authenticated' | 'needs_authentication';

export function getMcpSidebarStatus(entry: McpMyServerEntry): McpSidebarStatus {
  return entry.isAuthenticated ? 'authenticated' : 'needs_authentication';
}

/** Node-config shaped tool row (post-drop) — see `canvas-drop-handler.ts`'s `mcp-server` branch. */
export interface McpFlowTool {
  name: string;
  fullName: string;
  description?: string;
}

export function mcpToolInfoToFlowTool(tool: McpToolInfo): McpFlowTool {
  return {
    name: tool.name,
    fullName: tool.namespacedName || tool.name,
    description: tool.description || '',
  };
}
