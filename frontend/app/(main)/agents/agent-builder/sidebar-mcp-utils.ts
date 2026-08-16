import type { McpMyServerEntry, McpToolInfo } from '../../workspace/mcp-servers/types';

/** Minimal node shape for agent-builder canvas + drop handler (avoids circular imports). */
export type McpInstanceIdFlowNode = {
  data?: { type?: string; config?: Record<string, unknown> };
};

/**
 * `instanceId`s of every `mcp-*` node already on the flow — an agent may attach several
 * distinct MCP instances, but never two instances of the same `typeId` (enforced
 * server-side by `_parse_mcp_servers` in `app/api/routes/agent.py`) — see
 * `collectActiveMcpTypeIdsFromNodes` for that check.
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

/**
 * `typeId -> instanceId` for every `mcp-*` node already on the flow that has a `typeId`
 * (catalog-registered instances only — custom/self-hosted instances have none). The server
 * rejects attaching a second instance of the same `typeId` to one agent
 * (`_parse_mcp_servers`'s `seen_type_ids` check), so the palette must block the drag before
 * it round-trips to a save-time error — see `isMcpTypeIdConflict`.
 */
export function collectActiveMcpTypeIdsFromNodes(nodes: McpInstanceIdFlowNode[]): Map<string, string> {
  const typeIdToInstanceId = new Map<string, string>();
  for (const node of nodes) {
    const nodeType = String(node.data?.type ?? '');
    if (!nodeType.startsWith('mcp-')) continue;
    const config = node.data?.config as Record<string, unknown> | undefined;
    const typeId = String(config?.typeId ?? '').trim();
    if (!typeId) continue;
    const instanceId = String(config?.instanceId ?? '').trim();
    if (instanceId) typeIdToInstanceId.set(typeId, instanceId);
  }
  return typeIdToInstanceId;
}

/**
 * True when attaching `instanceId`/`typeId` would collide with a DIFFERENT instance of the
 * same `typeId` already on the flow. Attaching the exact same instance again is reported by
 * `collectActiveMcpInstanceIdsFromNodes` instead — this only covers the cross-instance case.
 */
export function isMcpTypeIdConflict(
  typeIdToInstanceId: Map<string, string>,
  instanceId: string,
  typeId: string | null | undefined
): boolean {
  if (!typeId) return false;
  const existingInstanceId = typeIdToInstanceId.get(typeId);
  return Boolean(existingInstanceId && existingInstanceId !== instanceId);
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
