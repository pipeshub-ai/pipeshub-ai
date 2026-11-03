import type { McpMyServerEntry, McpToolInfo } from '../../workspace/mcp-servers/types';

/** Minimal node shape for agent-builder canvas + drop handler (avoids circular imports). */
export type McpInstanceIdFlowNode = {
  data?: { type?: string; config?: Record<string, unknown> };
};

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
 * same `typeId` already on the flow. Dropping the same instance again merges into its node
 * (see `mergeMcpDropIntoConfig`).
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

function serverDragFields(entry: McpMyServerEntry): Record<string, string> {
  return {
    instanceId: entry._id,
    name: entry.name,
    displayName: entry.name || entry.typeId || 'MCP Server',
    typeId: entry.typeId || '',
    isAuthenticated: String(Boolean(entry.isAuthenticated)),
    tools: JSON.stringify((entry.tools || []).map(mcpToolInfoToFlowTool)),
    toolCount: String((entry.tools || []).length),
  };
}

/** Whole server: attaches all of its tools, including ones it adds later. */
export function buildMcpServerDragPayload(entry: McpMyServerEntry): Record<string, string> {
  return {
    'application/reactflow': `mcp-${entry._id}`,
    type: 'mcp-server',
    ...serverDragFields(entry),
  };
}

/** One tool: added to the server's node, or a new node with just this tool. `tools` stays the
 * server's full list so the node can offer the rest. */
export function buildMcpToolDragPayload(entry: McpMyServerEntry, tool: McpToolInfo): Record<string, string> {
  return {
    'application/reactflow': `mcp-${entry._id}`,
    type: 'mcp-tool',
    ...serverDragFields(entry),
    tool: JSON.stringify(mcpToolInfoToFlowTool(tool)),
  };
}

export type McpSidebarStatus = 'authenticated' | 'needs_authentication';

export function getMcpSidebarStatus(entry: McpMyServerEntry): McpSidebarStatus {
  return entry.isAuthenticated ? 'authenticated' : 'needs_authentication';
}

/** Node-config shaped tool row (post-drop) — see `canvas-drop-handler.ts`'s MCP branch. */
export interface McpFlowTool {
  name: string;
  fullName: string;
  description?: string;
}

function mergeToolsByName(a: McpFlowTool[], b: McpFlowTool[]): McpFlowTool[] {
  const byName = new Map<string, McpFlowTool>();
  [...a, ...b].forEach((tool) => {
    if (tool.name) byName.set(tool.name, tool);
  });
  return Array.from(byName.values());
}

/**
 * Node config after dropping this instance again: the whole server turns on `allTools`; one
 * tool is added to the selection (a no-op when the node already has everything).
 */
export function mergeMcpDropIntoConfig(
  config: Record<string, unknown>,
  serverTools: McpFlowTool[],
  droppedTool: McpFlowTool | null
): Record<string, unknown> {
  const current = (config.tools as McpFlowTool[] | undefined) ?? [];
  const availableTools = mergeToolsByName(
    (config.availableTools as McpFlowTool[] | undefined) ?? current,
    serverTools
  );
  if (!droppedTool || config.allTools === true) {
    return { ...config, allTools: true, availableTools, tools: availableTools };
  }
  const tools = current.some((t) => t.name === droppedTool.name) ? current : [...current, droppedTool];
  return { ...config, availableTools: mergeToolsByName(availableTools, [droppedTool]), tools };
}

/**
 * Names of the MCP servers wired to the agent that have "All tools" off and nothing selected:
 * saving one would give the agent none of its tools, so the server refuses it.
 */
export function findMcpServersWithoutTools(
  nodes: (McpInstanceIdFlowNode & { id: string; data?: { label?: string } })[],
  edges: { source: string; targetHandle?: string | null }[]
): string[] {
  const attached = new Set(edges.filter((e) => e.targetHandle === 'mcpServers').map((e) => e.source));
  return nodes
    .filter((n) => attached.has(n.id) && String(n.data?.type ?? '').startsWith('mcp-'))
    .filter((n) => n.data?.config?.allTools !== true && !((n.data?.config?.tools as unknown[]) || []).length)
    .map((n) => String(n.data?.config?.displayName || n.data?.label || ''));
}

export function mcpToolInfoToFlowTool(tool: McpToolInfo): McpFlowTool {
  return {
    name: tool.name,
    fullName: tool.namespacedName || tool.name,
    description: tool.description || '',
  };
}
