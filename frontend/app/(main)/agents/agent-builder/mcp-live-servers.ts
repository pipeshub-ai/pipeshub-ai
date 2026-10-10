import { createContext } from 'react';
import type { McpMyServerEntry } from '../../workspace/mcp-servers/types';
import { mcpConnectionState, type McpConnectionState } from '../../workspace/mcp-servers/connection-state';
import { mcpToolInfoToFlowTool, type McpFlowTool } from './sidebar-mcp-utils';

/**
 * The MCP servers the builder loaded for this agent's context, or null while there is no list
 * to trust (still loading, or the load failed). Nodes read it rather than storing it, so a
 * server's live state never counts as an unsaved edit.
 */
export const McpLiveServersContext = createContext<McpMyServerEntry[] | null>(null);

/** The saved agent whose tool approval rules its MCP nodes edit; null before the first save. */
export const McpAgentRulesContext = createContext<string | null>(null);

export type McpLiveView =
  /** No list yet: show what was saved. */
  | { kind: 'unknown' }
  /** The list has no such server: deleted, or not one this user (or agent) can use. */
  | { kind: 'unavailable' }
  /** `tools` is what the server offers now, or null when it couldn't be listed. */
  | { kind: 'live'; state: McpConnectionState; tools: McpFlowTool[] | null };

export function mcpLiveView(servers: McpMyServerEntry[] | null, instanceId: string): McpLiveView {
  if (!servers) return { kind: 'unknown' };
  const entry = servers.find((server) => server._id === instanceId);
  if (!entry) return { kind: 'unavailable' };
  const state = mcpConnectionState(entry);
  return { kind: 'live', state, tools: state === 'ready' ? (entry.tools || []).map(mcpToolInfoToFlowTool) : null };
}

/** Names of saved tools the server no longer offers; none when its tools aren't known. */
export function toolsMissingFromServer(saved: McpFlowTool[], live: McpFlowTool[] | null): Set<string> {
  if (!live) return new Set();
  const offered = new Set(live.map((tool) => tool.name));
  return new Set(saved.filter((tool) => !offered.has(tool.name)).map((tool) => tool.name));
}
